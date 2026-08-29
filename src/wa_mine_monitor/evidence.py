"""Committed evidence ledger (D13 §8 P2) -- safe digests and relative
identifiers only; raw evidence bytes stay in the data root (D9 item 4).

`evidence/provenance.yaml` is checked into the repo. Every entry names a
claim (a licence grant, an attribution string, a units label) and the
digest of the raw evidence file(s) that support it, but never embeds the
evidence bytes themselves -- those live only under
`<data_root>/raw/<source_id>/<snapshot_date>/`, which is not committed. This
lets the ledger travel with the repo (reviewable in a PR diff) while the
actual evidence stays where D9 item 4 requires it: in the dated, hashed
snapshot layout `snapshots.py` already governs.

Two distinct failure classes, deliberately not conflated:

- `EvidenceError` -- the ledger itself is unusable: missing file, invalid
  YAML, an entry that fails its own schema, or an entry naming a
  `source_id` this project has never registered in `licence.SOURCES`. These
  are load-time/shape failures and always raise -- a caller cannot proceed
  with a ledger that does not even parse.
- A `VerificationReport` failure -- the ledger loaded fine, but re-checking
  one entry's claim against the actual data on disk did not hold up (a
  missing or tampered file, a licence-state drift against the registry, a
  gating entry that cannot in fact gate). These are REPORTED, never raised:
  `main()`'s whole purpose is to print a full account of every entry's
  outcome, not stop at the first one.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from wa_mine_monitor import licence
from wa_mine_monitor.provenance import sha256_file


class EvidenceError(Exception):
    """The ledger itself could not be loaded -- never raised for a
    verification failure against one entry's evidence; see module
    docstring."""


class EvidenceEntry(BaseModel):
    """One committed evidence-ledger entry.

    `extra="forbid"` so a typo'd key (e.g. `licence_sate`) fails loudly at
    load time rather than silently being ignored and the entry verifying
    against defaults nobody wrote. `frozen=True` because an entry is a
    committed claim -- nothing downstream should be able to mutate it in
    place after `load_ledger` returns it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim_id: str
    source_id: str
    resource_url: str
    snapshot_date: str
    licence_state: licence.LicenceState
    evidence_files: dict[str, str]
    context: str | None
    units: str | None
    status: Literal["verified", "digest_only", "closed"]
    delegated_verifier: str | None
    offline_runnable: bool
    required_for_public_gate: bool


@dataclass
class VerificationReport:
    """Outcome of `verify_ledger`: a count per terminal state plus a
    human-readable reason for every entry that failed.

    Every entry lands in exactly one of the five counts -- there is no
    silent "not counted" outcome, per CLAUDE.md's rule that a diagnostic
    that could not be computed is not one that fired.
    """

    counts: dict[str, int] = field(
        default_factory=lambda: {
            "verified": 0,
            "digest_only": 0,
            "closed": 0,
            "failed": 0,
            "skipped_offline": 0,
        }
    )
    failures: list[str] = field(default_factory=list)


def load_ledger(path: Path) -> list[EvidenceEntry]:
    """Load and validate every entry in the committed ledger at `path`.

    Raises `EvidenceError` for anything that stops the ledger from being a
    usable list of `EvidenceEntry` -- a missing file, invalid YAML, a
    non-dict/no-`entries` top level, a per-entry schema violation, or a
    `source_id` this project has never registered in `licence.SOURCES`.
    Never returns a partial list on error: either every entry validates, or
    nothing is returned.
    """
    path = Path(path)
    if not path.is_file():
        raise EvidenceError(f"ledger missing: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EvidenceError(f"ledger is not valid YAML: {path}: {exc}") from exc

    if not isinstance(raw, dict) or "entries" not in raw:
        raise EvidenceError(f"ledger missing top-level 'entries' key: {path}")

    raw_entries = raw["entries"]
    if not isinstance(raw_entries, list):
        raise EvidenceError(f"ledger 'entries' is not a list: {path}")

    entries: list[EvidenceEntry] = []
    for index, raw_entry in enumerate(raw_entries):
        claim_id = raw_entry.get("claim_id") if isinstance(raw_entry, dict) else None
        try:
            entry = EvidenceEntry.model_validate(raw_entry)
        except ValidationError as exc:
            raise EvidenceError(
                f"ledger entry {index} ({claim_id!r}) failed validation: {exc}"
            ) from exc
        if entry.source_id not in licence.SOURCES:
            raise EvidenceError(
                f"ledger entry {index} ({entry.claim_id!r}) names an "
                f"unregistered source_id: {entry.source_id!r}"
            )
        entries.append(entry)

    return entries


def verify_ledger(ledger: list[EvidenceEntry], data_root: Path) -> VerificationReport:
    """Re-check every entry's claim against the actual data on disk.

    First-failure-wins per entry: the first check an entry fails is the
    only reason recorded for it, and no further checks run against that
    entry. See the module docstring for why this reports rather than
    raises.
    """
    data_root = Path(data_root)
    report = VerificationReport()

    for entry in ledger:
        registered = licence.SOURCES[entry.source_id]
        if registered.licence_state != entry.licence_state:
            report.counts["failed"] += 1
            report.failures.append(
                f"{entry.claim_id}: licence_state {entry.licence_state.value!r} does not "
                f"match the registry's {registered.licence_state.value!r} for "
                f"{entry.source_id!r}"
            )
            continue

        if entry.status == "closed":
            if entry.required_for_public_gate or entry.licence_state == licence.LicenceState.PUBLIC:
                report.counts["failed"] += 1
                report.failures.append(
                    f"{entry.claim_id}: a closed entry can never be required_for_public_gate "
                    "or claim PUBLIC state -- closure is exclusion evidence, never permission"
                )
                continue
            report.counts["closed"] += 1
            continue

        if entry.required_for_public_gate and entry.status == "digest_only":
            report.counts["failed"] += 1
            report.failures.append(
                f"{entry.claim_id}: index-only evidence cannot satisfy a required public gate"
            )
            continue

        if not entry.evidence_files:
            if entry.delegated_verifier is None:
                report.counts["failed"] += 1
                report.failures.append(
                    f"{entry.claim_id}: no evidence files and no delegated verifier"
                )
                continue
            if entry.required_for_public_gate:
                report.counts["failed"] += 1
                report.failures.append(
                    f"{entry.claim_id}: a gating entry needs verifiable files, a delegated "
                    "verifier alone is insufficient"
                )
                continue
            report.counts[entry.status] += 1
            continue

        snap = data_root / "raw" / entry.source_id / entry.snapshot_date
        if not snap.is_dir():
            if entry.required_for_public_gate:
                report.counts["failed"] += 1
                report.failures.append(f"{entry.claim_id}: snapshot directory not found: {snap}")
            else:
                report.counts["skipped_offline"] += 1
            continue

        snap_resolved = snap.resolve()
        file_texts: list[str] = []
        failed = False
        for rel, digest in entry.evidence_files.items():
            resolved = (snap / rel).resolve()
            if not (resolved == snap_resolved or snap_resolved in resolved.parents):
                report.counts["failed"] += 1
                report.failures.append(
                    f"{entry.claim_id}: evidence path escapes the snapshot: {rel!r}"
                )
                failed = True
                break
            if not resolved.is_file():
                report.counts["failed"] += 1
                report.failures.append(f"{entry.claim_id}: evidence file missing: {rel!r}")
                failed = True
                break
            if sha256_file(resolved) != digest:
                report.counts["failed"] += 1
                report.failures.append(f"{entry.claim_id}: evidence file digest mismatch: {rel!r}")
                failed = True
                break
            file_texts.append(resolved.read_bytes().decode("utf-8", errors="replace"))
        if failed:
            continue

        if entry.status == "verified":
            if entry.context is None:
                report.counts["failed"] += 1
                report.failures.append(f"{entry.claim_id}: status 'verified' requires context")
                continue
            text = "".join(file_texts)
            if entry.context not in text:
                report.counts["failed"] += 1
                report.failures.append(
                    f"{entry.claim_id}: context {entry.context!r} not found in evidence files"
                )
                continue
            if entry.units is not None and entry.units not in text:
                report.counts["failed"] += 1
                report.failures.append(
                    f"{entry.claim_id}: units {entry.units!r} not found in evidence files"
                )
                continue
            report.counts["verified"] += 1
            continue

        report.counts[entry.status] += 1

    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: load and verify the committed ledger, print a JSON
    report, and return 0 only when nothing failed."""
    parser = argparse.ArgumentParser(description="Verify the committed evidence ledger.")
    parser.add_argument("--ledger", type=Path, default=Path("evidence/provenance.yaml"))
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args(argv)

    data_root = args.data_root.expanduser()

    try:
        entries = load_ledger(args.ledger)
    except EvidenceError as exc:
        print(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        return 1

    report = verify_ledger(entries, data_root)
    print(
        json.dumps({"counts": report.counts, "failures": report.failures}, indent=2, sort_keys=True)
    )
    return 0 if report.counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
