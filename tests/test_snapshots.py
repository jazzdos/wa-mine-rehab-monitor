"""Tests for `wa_mine_monitor.snapshots`: the dated-snapshot raw layout.

Every external source this project fetches lands under
`<root>/raw/<source_id>/<date>/`, with a `metadata.txt` recording what was
fetched and why, and a `SHA256SUMS.txt` fixing the exact bytes captured --
so a later re-fetch of the same source is a new dated directory, never a
silent overwrite, and tampering or loss in a captured snapshot is
detectable rather than assumed away.

`verify_snapshot` returns three counts (`n_ok, n_bad, n_missing`), never a
bool, per CLAUDE.md's rule that a boolean diagnostic collapses a reportable
population into a flag.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wa_mine_monitor.snapshots import (
    SHA256SUMS_FILENAME,
    create_snapshot_dir,
    finalize_snapshot,
    snapshot_entries,
    update_snapshot_entry,
    verify_snapshot,
    write_snapshot_metadata,
)

# --- create_snapshot_dir -------------------------------------------------


def test_create_snapshot_dir_builds_the_dated_raw_layout(tmp_path: Path) -> None:
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")

    assert snapshot_dir == tmp_path / "raw" / "minedex" / "2026-08-15"
    assert snapshot_dir.is_dir()


def test_create_snapshot_dir_is_idempotent(tmp_path: Path) -> None:
    """Calling twice with the same date must not raise -- a re-run against
    an already-created snapshot dir is a legitimate resume, not an error."""
    first = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    second = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")

    assert first == second
    assert second.is_dir()


def test_create_snapshot_dir_never_computes_the_date_itself(tmp_path: Path) -> None:
    """The date is an explicit caller-supplied string, never `date.today()`
    or similar computed inside the library -- a snapshot's date is a fact
    about when the fetch happened, and that fact belongs to the caller."""
    snapshot_dir = create_snapshot_dir(tmp_path, "tenements", "2020-01-01")

    assert snapshot_dir == tmp_path / "raw" / "tenements" / "2020-01-01"


# --- write_snapshot_metadata ---------------------------------------------


def test_write_snapshot_metadata_writes_all_four_fields(tmp_path: Path) -> None:
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")

    metadata_path = write_snapshot_metadata(
        snapshot_dir,
        source="DMIRS-001 MINEDEX",
        endpoint="https://catalogue.data.wa.gov.au/dataset/minedex-dmirs-001",
        licence_note="CONFLICT: cc-nc catalogue label vs DASC blanket CC-BY-4.0",
        purpose="operator-as-at-snapshot join input",
    )

    assert metadata_path == snapshot_dir / "metadata.txt"
    text = metadata_path.read_text()
    assert "source: DMIRS-001 MINEDEX" in text
    assert "endpoint: https://catalogue.data.wa.gov.au/dataset/minedex-dmirs-001" in text
    assert "licence_note: CONFLICT: cc-nc catalogue label vs DASC blanket CC-BY-4.0" in text
    assert "purpose: operator-as-at-snapshot join input" in text


def test_write_snapshot_metadata_is_one_field_per_line(tmp_path: Path) -> None:
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")

    metadata_path = write_snapshot_metadata(
        snapshot_dir,
        source="s",
        endpoint="e",
        licence_note="l",
        purpose="p",
    )

    lines = metadata_path.read_text().splitlines()
    assert lines == ["source: s", "endpoint: e", "licence_note: l", "purpose: p"]


# --- finalize_snapshot / verify_snapshot ----------------------------------


def _populate(snapshot_dir: Path) -> None:
    (snapshot_dir / "data.csv").write_text("a,b\n1,2\n")
    (snapshot_dir / "extra.txt").write_text("some extra captured file\n")


def test_finalize_snapshot_writes_sha256sums_covering_every_file(tmp_path: Path) -> None:
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    write_snapshot_metadata(snapshot_dir, source="s", endpoint="e", licence_note="l", purpose="p")
    _populate(snapshot_dir)

    sums_path = finalize_snapshot(snapshot_dir)

    assert sums_path == snapshot_dir / SHA256SUMS_FILENAME
    # Assert on the parsed relative-path list, not on the raw text: a
    # text-level exclusion check (stripping the offending line before
    # searching for it) passes on the exact defect it exists to catch --
    # see CLAUDE.md on mechanisms that look present and enforce nothing.
    # An exact set (via sorted-list equality) covers both "every captured
    # file is listed" and "SHA256SUMS.txt is not" in one assertion.
    paths = [line.split("  ", 1)[1] for line in sums_path.read_text().splitlines() if line.strip()]
    assert paths == ["data.csv", "extra.txt", "metadata.txt"]


def test_finalize_snapshot_covers_a_captured_sums_file_in_a_subdirectory(
    tmp_path: Path,
) -> None:
    """Regression for the finding that the self-exclusion matched on BASENAME,
    so a captured file named `SHA256SUMS.txt` anywhere below the snapshot root
    (the realistic trigger: a vendor bundle shipping its own checksum file) was
    silently omitted from the manifest and invisible to `verify_snapshot` --
    a clean verdict over a snapshot holding an unhashed captured file."""
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    (snapshot_dir / "sub").mkdir()
    (snapshot_dir / "sub" / SHA256SUMS_FILENAME).write_text("vendor-supplied checksums\n")
    (snapshot_dir / "a.txt").write_text("a\n")

    sums_path = finalize_snapshot(snapshot_dir)

    paths = [line.split("  ", 1)[1] for line in sums_path.read_text().splitlines() if line.strip()]
    assert paths == ["a.txt", f"sub/{SHA256SUMS_FILENAME}"]
    assert verify_snapshot(snapshot_dir) == (2, 0, 0)


def test_finalize_snapshot_lines_are_sorted_by_relative_path(tmp_path: Path) -> None:
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    write_snapshot_metadata(snapshot_dir, source="s", endpoint="e", licence_note="l", purpose="p")
    _populate(snapshot_dir)

    sums_path = finalize_snapshot(snapshot_dir)

    relative_paths = [line.split("  ", 1)[1] for line in sums_path.read_text().splitlines()]
    assert relative_paths == sorted(relative_paths)


def test_finalize_then_verify_reports_all_ok(tmp_path: Path) -> None:
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    write_snapshot_metadata(snapshot_dir, source="s", endpoint="e", licence_note="l", purpose="p")
    _populate(snapshot_dir)
    finalize_snapshot(snapshot_dir)

    n_ok, n_bad, n_missing = verify_snapshot(snapshot_dir)

    # metadata.txt + data.csv + extra.txt = 3 files.
    assert (n_ok, n_bad, n_missing) == (3, 0, 0)


def test_verify_snapshot_detects_one_byte_tampering(tmp_path: Path) -> None:
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    write_snapshot_metadata(snapshot_dir, source="s", endpoint="e", licence_note="l", purpose="p")
    _populate(snapshot_dir)
    finalize_snapshot(snapshot_dir)

    # Tamper with one byte of one captured file, after finalization.
    data_path = snapshot_dir / "data.csv"
    data_path.write_text("a,b\n1,9\n")  # "2" -> "9"

    n_ok, n_bad, n_missing = verify_snapshot(snapshot_dir)

    assert (n_ok, n_bad, n_missing) == (2, 1, 0)


def test_verify_snapshot_detects_a_deleted_file(tmp_path: Path) -> None:
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    write_snapshot_metadata(snapshot_dir, source="s", endpoint="e", licence_note="l", purpose="p")
    _populate(snapshot_dir)
    finalize_snapshot(snapshot_dir)

    (snapshot_dir / "extra.txt").unlink()

    n_ok, n_bad, n_missing = verify_snapshot(snapshot_dir)

    assert (n_ok, n_bad, n_missing) == (2, 0, 1)


def test_verify_snapshot_never_returns_a_bool(tmp_path: Path) -> None:
    """CLAUDE.md's rule: a diagnostic that could not be computed is not one
    that fired -- report OK/BAD/MISSING as three counts, never a flag."""
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    write_snapshot_metadata(snapshot_dir, source="s", endpoint="e", licence_note="l", purpose="p")
    finalize_snapshot(snapshot_dir)

    result = verify_snapshot(snapshot_dir)

    assert isinstance(result, tuple)
    assert len(result) == 3
    assert all(isinstance(count, int) and not isinstance(count, bool) for count in result)


def test_finalize_snapshot_refuses_a_second_call_by_default(tmp_path: Path) -> None:
    """Regression for the finding that a second `finalize_snapshot` call
    silently re-baselined SHA256SUMS.txt, undoing the post-finalize
    immutability guarantee `licence.minedex_evidence_is_hashed` rests on."""
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    _populate(snapshot_dir)
    finalize_snapshot(snapshot_dir)

    # Simulate a post-finalize drop-in, then a stray re-run of finalize.
    (snapshot_dir / "dropped_in.html").write_text("late arrival")
    with pytest.raises(FileExistsError):
        finalize_snapshot(snapshot_dir)

    # The original manifest must be untouched -- the dropped-in file must
    # still be absent from it.
    assert "dropped_in.html" not in snapshot_entries(snapshot_dir)


def test_finalize_snapshot_allows_a_deliberate_refinalize(tmp_path: Path) -> None:
    """A resuming fetch that genuinely adds files passes `allow_refinalize=True`
    knowingly, and the new content is covered by the re-written manifest."""
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    _populate(snapshot_dir)
    finalize_snapshot(snapshot_dir)

    (snapshot_dir / "resumed.txt").write_text("added on a resumed fetch\n")
    finalize_snapshot(snapshot_dir, allow_refinalize=True)

    assert "resumed.txt" in snapshot_entries(snapshot_dir)


# --- snapshot_entries ----------------------------------------------------


def test_snapshot_entries_maps_every_hashed_path_to_its_digest(tmp_path: Path) -> None:
    """The path-level view `licence.minedex_redistribution_allowed` builds
    its post-finalize guarantee on: one entry per hashed file, keyed by the
    relative POSIX path exactly as SHA256SUMS.txt records it."""
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    write_snapshot_metadata(snapshot_dir, source="s", endpoint="e", licence_note="l", purpose="p")
    _populate(snapshot_dir)
    finalize_snapshot(snapshot_dir)

    entries = snapshot_entries(snapshot_dir)

    assert set(entries) == {"metadata.txt", "data.csv", "extra.txt"}
    assert all(len(digest) == 64 for digest in entries.values())
    # A file added AFTER finalize has no entry -- that absence is the whole
    # point of the accessor.
    (snapshot_dir / "dropped_in.html").write_text("late arrival")
    assert "dropped_in.html" not in snapshot_entries(snapshot_dir)


def test_snapshot_entries_raises_when_snapshot_was_never_finalized(tmp_path: Path) -> None:
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    with pytest.raises(OSError):
        snapshot_entries(snapshot_dir)


def test_snapshot_entries_raises_on_an_unparseable_line(tmp_path: Path) -> None:
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    (snapshot_dir / SHA256SUMS_FILENAME).write_text("not a digest line\n")
    with pytest.raises(ValueError):
        snapshot_entries(snapshot_dir)


# --- update_snapshot_entry -------------------------------------------------


def test_update_snapshot_entry_re_signs_a_revised_file_and_verify_passes(
    tmp_path: Path,
) -> None:
    """The declared, narrow exception to post-finalize immutability: a file
    legitimately revised after finalize (e.g. `licence_evidence.json` via
    `adjudicate-minedex-licence`) must have its ONE `SHA256SUMS.txt` line
    re-signed, or `verify_snapshot` reports it tampered forever after."""
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    write_snapshot_metadata(snapshot_dir, source="s", endpoint="e", licence_note="l", purpose="p")
    _populate(snapshot_dir)
    finalize_snapshot(snapshot_dir)

    # Without a re-sign, a legitimate content revision reads as tampering.
    (snapshot_dir / "data.csv").write_text("a,b\n1,9\n")
    assert verify_snapshot(snapshot_dir) == (2, 1, 0)

    old_digest, new_digest = update_snapshot_entry(snapshot_dir, "data.csv")

    assert old_digest != new_digest
    assert len(old_digest) == len(new_digest) == 64
    assert verify_snapshot(snapshot_dir) == (3, 0, 0)
    assert snapshot_entries(snapshot_dir)["data.csv"] == new_digest


def test_update_snapshot_entry_touches_only_the_named_line(tmp_path: Path) -> None:
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    write_snapshot_metadata(snapshot_dir, source="s", endpoint="e", licence_note="l", purpose="p")
    _populate(snapshot_dir)
    finalize_snapshot(snapshot_dir)
    before = snapshot_entries(snapshot_dir)

    (snapshot_dir / "data.csv").write_text("a,b\n1,9\n")
    update_snapshot_entry(snapshot_dir, "data.csv")

    after = snapshot_entries(snapshot_dir)
    assert after["metadata.txt"] == before["metadata.txt"]
    assert after["extra.txt"] == before["extra.txt"]
    assert after["data.csv"] != before["data.csv"]
    # Still sorted by path.
    relative_paths = [
        line.split("  ", 1)[1]
        for line in (snapshot_dir / SHA256SUMS_FILENAME).read_text().splitlines()
    ]
    assert relative_paths == sorted(relative_paths)


def test_update_snapshot_entry_raises_key_error_for_an_unrecorded_path(tmp_path: Path) -> None:
    """This function revises an existing entry; it never adds a new one --
    otherwise it could be used to sneak an unhashed file into the manifest."""
    snapshot_dir = create_snapshot_dir(tmp_path, "minedex", "2026-08-15")
    _populate(snapshot_dir)
    finalize_snapshot(snapshot_dir)
    (snapshot_dir / "never_hashed.txt").write_text("dropped in after finalize\n")

    with pytest.raises(KeyError):
        update_snapshot_entry(snapshot_dir, "never_hashed.txt")
