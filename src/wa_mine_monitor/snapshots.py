"""The dated-snapshot raw layout: `<root>/raw/<source_id>/<date>/`.

Every external source this project fetches (MINEDEX, tenements, SILO, ...)
lands in its own dated directory rather than overwriting a shared path, so a
re-fetch is a new, independently verifiable snapshot and never a silent
mutation of what an earlier run measured against. Each snapshot carries:

- `metadata.txt` -- source, endpoint, licence note and purpose, one field
  per line, written at fetch time;
- `SHA256SUMS.txt` -- the sha256 of every other file in the directory
  (recursively; the only exclusion is this file itself, matched by path and
  not by basename, so a captured `sub/SHA256SUMS.txt` from a vendor bundle is
  covered like anything else), written once fetching is complete
  (`finalize_snapshot`), reusing `wa_mine_monitor.provenance.sha256_file`
  rather than reimplementing hashing here.

`verify_snapshot` re-hashes every file `SHA256SUMS.txt` names and reports
three counts -- ok, bad (hash mismatch), missing (named but absent) -- never
a bool. CLAUDE.md's rule: a diagnostic that could not be computed is not one
that fired, so collapsing this into a single pass/fail flag would hide
*which* files a tamper or partial-delete affected and how many.

The snapshot date is always an explicit `YYYY-MM-DD` string the caller
supplies, never computed inside this module (no `date.today()` here): the
date is a fact about when a fetch happened, and that fact belongs to
whoever ran the fetch, not to whoever re-imports this module later.
"""

from __future__ import annotations

from pathlib import Path

from wa_mine_monitor.provenance import sha256_file

#: Filename `finalize_snapshot`/`verify_snapshot` read and write within a
#: snapshot directory. Fixed rather than parameterised, same reasoning as
#: `licence.EVIDENCE_FILENAME`: this is the one shape this module
#: recognises as "the checksum manifest for this snapshot".
SHA256SUMS_FILENAME = "SHA256SUMS.txt"

#: Filename `write_snapshot_metadata` writes.
METADATA_FILENAME = "metadata.txt"

#: Separator between a hex digest and its relative path in `SHA256SUMS.txt`,
#: matching the conventional `sha256sum`/`shasum -c` two-space text-mode form.
_SUMS_SEPARATOR = "  "


def create_snapshot_dir(root: Path, source_id: str, date: str) -> Path:
    """Create and return `<root>/raw/<source_id>/<date>/`.

    Idempotent: calling this twice for the same `(root, source_id, date)`
    returns the same path without raising -- a re-run against an
    already-created snapshot directory is a legitimate resume.
    """
    snapshot_dir = Path(root) / "raw" / source_id / date
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    return snapshot_dir


def write_snapshot_metadata(
    snapshot_dir: Path,
    *,
    source: str,
    endpoint: str,
    licence_note: str,
    purpose: str,
) -> Path:
    """Write `metadata.txt` into `snapshot_dir`, one field per line.

    Returns the path written. Overwrites any existing `metadata.txt` in
    this directory -- the caller is expected to call this once per snapshot,
    before `finalize_snapshot`.
    """
    metadata_path = Path(snapshot_dir) / METADATA_FILENAME
    metadata_path.write_text(
        f"source: {source}\n"
        f"endpoint: {endpoint}\n"
        f"licence_note: {licence_note}\n"
        f"purpose: {purpose}\n"
    )
    return metadata_path


def finalize_snapshot(snapshot_dir: Path, *, allow_refinalize: bool = False) -> Path:
    """Write `SHA256SUMS.txt` covering every file in `snapshot_dir`.

    Walks the directory recursively, hashes every file except this
    directory's own top-level `SHA256SUMS.txt` (so a re-run of
    `finalize_snapshot` never hashes its own prior output), and writes one
    `<sha256>  <relative-path>` line per file, sorted by relative path --
    deterministic output for two otherwise-identical snapshots, and a stable
    diff when one file changes. Relative paths use `/` (POSIX) separators
    regardless of platform, so the manifest is portable.

    The exclusion compares the PATH, not the basename: a vendor bundle that
    ships its own checksum file lands as `sub/SHA256SUMS.txt`, which is a
    captured file like any other and is hashed. Excluding it by name would
    drop it from the manifest silently, and `verify_snapshot` -- which counts
    only what the manifest names -- would then return a clean verdict over a
    snapshot holding an unhashed file.

    Raises `FileExistsError` when `SHA256SUMS.txt` already exists in
    `snapshot_dir` and `allow_refinalize` is not passed. This is the
    post-finalize immutability guarantee `licence.minedex_evidence_is_hashed`
    rests on: without it, calling `finalize_snapshot` a second time -- which
    `create_snapshot_dir`'s documented idempotency invites, since a resuming
    fetch legitimately reaches this path again -- silently re-baselines
    SHA256SUMS.txt over whatever the directory currently holds, including
    anything dropped in after the first finalize, with no record that the
    manifest ever named different content. A resuming fetch that genuinely
    adds files and needs to re-finalize passes `allow_refinalize=True`
    knowingly; a stray second call fails loudly instead of silently
    laundering a post-finalize drop-in into "always was this way".
    """
    snapshot_dir = Path(snapshot_dir)
    sums_path = snapshot_dir / SHA256SUMS_FILENAME

    if sums_path.exists() and not allow_refinalize:
        raise FileExistsError(
            f"{sums_path} already exists -- finalize_snapshot() was already called "
            "for this snapshot. Pass allow_refinalize=True if this is a deliberate "
            "re-finalize (e.g. a resuming fetch that added files), not a stray "
            "second call."
        )

    relative_paths = sorted(
        path.relative_to(snapshot_dir).as_posix()
        for path in snapshot_dir.rglob("*")
        if path.is_file() and path != sums_path
    )

    lines = [
        f"{sha256_file(snapshot_dir / relative_path)}{_SUMS_SEPARATOR}{relative_path}"
        for relative_path in relative_paths
    ]
    sums_path.write_text("".join(f"{line}\n" for line in lines))
    return sums_path


def snapshot_entries(snapshot_dir: Path) -> dict[str, str]:
    """Parse `SHA256SUMS.txt` and return `{relative POSIX path: recorded digest}`.

    This is the path-level view of what `finalize_snapshot` actually hashed,
    for consumers that need to ask "is THIS file covered, and by WHICH
    digest" rather than the three aggregate counts `verify_snapshot`
    returns. `licence.minedex_redistribution_allowed` needs exactly that:
    its fail-closed answer must not be flippable by files dropped into a
    directory after `finalize_snapshot` ran, and only a per-path lookup can
    distinguish a hashed file from an unhashed newcomer.

    Raises `OSError` when `SHA256SUMS.txt` does not exist (the snapshot was
    never finalized) and `ValueError` on a line that does not parse as
    `<digest><two spaces><path>` -- callers that must fail closed catch
    both; this accessor itself reports rather than adjudicates.
    """
    snapshot_dir = Path(snapshot_dir)
    sums_path = snapshot_dir / SHA256SUMS_FILENAME

    entries: dict[str, str] = {}
    for line in sums_path.read_text().splitlines():
        if not line.strip():
            continue
        digest, _, relative_path = line.partition(_SUMS_SEPARATOR)
        if not relative_path:
            raise ValueError(
                f"unparseable line in {sums_path}: {line!r} "
                f"(expected '<sha256>{_SUMS_SEPARATOR}<relative path>')"
            )
        entries[relative_path] = digest
    return entries


def verify_snapshot(snapshot_dir: Path) -> tuple[int, int, int]:
    """Re-hash every file `SHA256SUMS.txt` names and return `(n_ok, n_bad, n_missing)`.

    - `n_ok`: file exists and its current sha256 matches the recorded digest.
    - `n_bad`: file exists but its current sha256 does not match (tampered
      or truncated since `finalize_snapshot` ran).
    - `n_missing`: file is named in `SHA256SUMS.txt` but no longer exists.

    Three counts, never a bool: collapsing this into pass/fail would hide
    which of "nothing changed", "one byte flipped" and "a file vanished"
    actually happened, and how many files each affected.
    """
    snapshot_dir = Path(snapshot_dir)

    n_ok = n_bad = n_missing = 0
    for relative_path, digest in snapshot_entries(snapshot_dir).items():
        target_path = snapshot_dir / relative_path
        if not target_path.is_file():
            n_missing += 1
            continue
        if sha256_file(target_path) == digest:
            n_ok += 1
        else:
            n_bad += 1

    return n_ok, n_bad, n_missing


def update_snapshot_entry(snapshot_dir: Path, relative_path: str) -> tuple[str, str]:
    """Re-hash `relative_path` inside `snapshot_dir` and rewrite its one line
    in `SHA256SUMS.txt` to match the file's current content, returning
    `(old_digest, new_digest)`.

    This is the declared, narrow exception to `finalize_snapshot`'s
    immutability guarantee: a file whose CONTENT is legitimately revised
    after finalize (today, only `licence.EVIDENCE_FILENAME`, via the
    `adjudicate-minedex-licence` CLI command) must have that revision made
    VISIBLE to the integrity gate rather than silently breaking it.
    `verify_snapshot` re-hashes every recorded entry against the digest
    `finalize_snapshot` captured and has no notion of a declared-revisable
    path; a legitimate post-finalize rewrite that does not also call this
    function makes `verify_snapshot` report that file as tampered forever
    after, and `cli._verify_snapshot_or_refuse` then refuses every later
    command that reads the snapshot -- with re-fetch, its only offered
    remedy, itself refused in turn by `_refuse_if_snapshot_already_
    finalized`, since `SHA256SUMS.txt` already exists. This is CLAUDE.md's
    "a transformation applied to a provenance record must record that it
    was applied" rule: the manifest is rewritten to match the legitimate
    edit, never bypassed or left stale.

    Every other line in `SHA256SUMS.txt` is left byte-for-byte unchanged;
    only the one line naming `relative_path` is touched, and the file stays
    sorted by path (the path itself does not change, only its digest).

    Raises `KeyError` if `relative_path` is not already a recorded entry --
    this function revises an existing entry, it never adds a new one, so a
    caller cannot use it to sneak an unhashed file into the manifest -- and
    `OSError`/`ValueError` under the same conditions as `snapshot_entries`
    (`SHA256SUMS.txt` missing or unparseable).
    """
    snapshot_dir = Path(snapshot_dir)
    entries = snapshot_entries(snapshot_dir)
    if relative_path not in entries:
        raise KeyError(
            f"{relative_path!r} is not a recorded entry in "
            f"{snapshot_dir / SHA256SUMS_FILENAME} -- update_snapshot_entry revises "
            "an existing entry, it does not add a new one"
        )

    old_digest = entries[relative_path]
    new_digest = sha256_file(snapshot_dir / relative_path)
    entries[relative_path] = new_digest

    lines = [f"{digest}{_SUMS_SEPARATOR}{path}" for path, digest in sorted(entries.items())]
    (snapshot_dir / SHA256SUMS_FILENAME).write_text("".join(f"{line}\n" for line in lines))
    return old_digest, new_digest
