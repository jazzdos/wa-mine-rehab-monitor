"""Tests for the committed evidence ledger (D13 §8 P2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wa_mine_monitor import snapshots
from wa_mine_monitor.evidence import EvidenceError, load_ledger, verify_ledger
from wa_mine_monitor.provenance import sha256_file

_LICENCE_TEXT = "Creative Commons Attribution 4.0 International (CC BY 4.0)\n"


def _seed_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    snap = root / "raw" / "dmirs_003_tenements" / "2026-08-16"
    snap.mkdir(parents=True)
    (snap / "Licence_CCBY4.pdf.txt").write_text(_LICENCE_TEXT)
    snapshots.finalize_snapshot(snap)
    return root


def _entry(root: Path, **overrides: object) -> dict:
    digest = sha256_file(
        root / "raw" / "dmirs_003_tenements" / "2026-08-16" / "Licence_CCBY4.pdf.txt"
    )
    entry = {
        "claim_id": "dmirs-003-cc-by",
        "source_id": "dmirs_003_tenements",
        "resource_url": "https://dasc.dmirs.wa.gov.au/Download/File/2056",
        "snapshot_date": "2026-08-16",
        "licence_state": "public",
        "evidence_files": {"Licence_CCBY4.pdf.txt": digest},
        "context": "Creative Commons Attribution 4.0",
        "units": None,
        "status": "verified",
        "delegated_verifier": None,
        "offline_runnable": True,
        "required_for_public_gate": True,
    }
    entry.update(overrides)
    return entry


def _write_ledger(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "provenance.yaml"
    path.write_text(yaml.safe_dump({"entries": entries}))
    return path


class TestLoadLedger:
    def test_loads_a_valid_ledger(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger_path = _write_ledger(tmp_path, [_entry(root)])
        entries = load_ledger(ledger_path)
        assert entries[0].source_id == "dmirs_003_tenements"

    def test_missing_ledger_refuses(self, tmp_path: Path) -> None:
        with pytest.raises(EvidenceError, match="missing"):
            load_ledger(tmp_path / "does-not-exist.yaml")

    def test_malformed_yaml_refuses(self, tmp_path: Path) -> None:
        path = tmp_path / "provenance.yaml"
        path.write_text("entries: [unclosed")
        with pytest.raises(EvidenceError):
            load_ledger(path)

    def test_unknown_status_refuses(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger_path = _write_ledger(tmp_path, [_entry(root, status="assumed_fine")])
        with pytest.raises(EvidenceError, match="status"):
            load_ledger(ledger_path)

    def test_unknown_source_id_refuses(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger_path = _write_ledger(tmp_path, [_entry(root, source_id="not_registered")])
        with pytest.raises(EvidenceError, match="not_registered"):
            load_ledger(ledger_path)


class TestVerifyLedger:
    def test_verifies_a_clean_entry(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(_write_ledger(tmp_path, [_entry(root)]))
        report = verify_ledger(ledger, root)
        assert report.counts["verified"] == 1
        assert report.failures == []

    def test_missing_evidence_file_fails(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(
            _write_ledger(
                tmp_path,
                [_entry(root, evidence_files={"gone.txt": "0" * 64})],
            )
        )
        report = verify_ledger(ledger, root)
        assert report.counts["failed"] == 1

    def test_changed_evidence_file_fails(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(_write_ledger(tmp_path, [_entry(root)]))
        tampered = root / "raw" / "dmirs_003_tenements" / "2026-08-16" / "Licence_CCBY4.pdf.txt"
        tampered.write_text("tampered\n")
        report = verify_ledger(ledger, root)
        assert report.counts["failed"] == 1

    def test_out_of_root_evidence_path_fails(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(
            _write_ledger(
                tmp_path,
                [_entry(root, evidence_files={"../../../etc/passwd": "0" * 64})],
            )
        )
        report = verify_ledger(ledger, root)
        assert report.counts["failed"] == 1

    def test_right_number_wrong_context_fails(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(
            _write_ledger(
                tmp_path,
                [_entry(root, context="Attribution-NonCommercial 4.0")],
            )
        )
        report = verify_ledger(ledger, root)
        assert report.counts["failed"] == 1

    def test_licence_state_mismatch_with_registry_fails(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(
            _write_ledger(tmp_path, [_entry(root, licence_state="gated_internal")])
        )
        report = verify_ledger(ledger, root)
        assert report.counts["failed"] == 1

    def test_digest_only_cannot_satisfy_a_public_gate(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(
            _write_ledger(
                tmp_path,
                [_entry(root, status="digest_only", context=None, required_for_public_gate=True)],
            )
        )
        report = verify_ledger(ledger, root)
        assert report.counts["failed"] == 1

    def test_digest_only_is_disclosed_when_not_gating(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(
            _write_ledger(
                tmp_path,
                [
                    _entry(
                        root,
                        status="digest_only",
                        context=None,
                        required_for_public_gate=False,
                    )
                ],
            )
        )
        report = verify_ledger(ledger, root)
        assert report.counts["digest_only"] == 1

    def test_closed_entry_verifies_as_closed_never_permission(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(
            _write_ledger(
                tmp_path,
                [
                    _entry(
                        root,
                        source_id="dmirs_001_minedex",
                        licence_state="gated_internal",
                        status="closed",
                        context=None,
                        required_for_public_gate=False,
                        evidence_files={},
                        offline_runnable=False,
                    )
                ],
            )
        )
        report = verify_ledger(ledger, root)
        assert report.counts["closed"] == 1
        assert report.counts["failed"] == 0

    def test_offline_entry_skips_with_disclosure_when_root_absent(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(
            _write_ledger(tmp_path, [_entry(root, required_for_public_gate=False)])
        )
        report = verify_ledger(ledger, tmp_path / "no_such_root")
        assert report.counts["skipped_offline"] == 1

    def test_gating_entry_fails_when_root_absent(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(_write_ledger(tmp_path, [_entry(root)]))
        report = verify_ledger(ledger, tmp_path / "no_such_root")
        assert report.counts["failed"] == 1

    def test_no_evidence_and_no_verifier_fails(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(
            _write_ledger(
                tmp_path,
                [
                    _entry(
                        root,
                        evidence_files={},
                        delegated_verifier=None,
                        status="digest_only",
                        required_for_public_gate=False,
                    )
                ],
            )
        )
        report = verify_ledger(ledger, root)
        assert report.counts["failed"] == 1

    def test_gating_entry_with_only_a_delegated_verifier_fails(self, tmp_path: Path) -> None:
        root = _seed_root(tmp_path)
        ledger = load_ledger(
            _write_ledger(
                tmp_path,
                [
                    _entry(
                        root,
                        evidence_files={},
                        delegated_verifier="CKAN capture",
                        required_for_public_gate=True,
                    )
                ],
            )
        )
        report = verify_ledger(ledger, root)
        assert report.counts["failed"] == 1
