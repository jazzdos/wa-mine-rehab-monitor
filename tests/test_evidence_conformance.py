"""Conformance checks for the committed evidence ledger, D13 §8 P2.

Unlike `test_evidence.py` (unit tests against synthetic fixtures), this
module reads the REAL committed `evidence/provenance.yaml` and checks it
against the real `licence.SOURCES` registry: every registered PUBLIC
source has ledger coverage, MINEDEX stays closed, no absolute path leaked
into the committed file, and no gating entry is digest-only. It never
touches the live data root -- that check is a separate, explicitly live
command (see the task's STEP 5), not something this suite runs.
"""

from __future__ import annotations

from pathlib import Path

from wa_mine_monitor import evidence
from wa_mine_monitor.licence import SOURCES, LicenceState

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER = REPO_ROOT / "evidence" / "provenance.yaml"


def test_committed_ledger_parses() -> None:
    entries = evidence.load_ledger(LEDGER)
    assert entries


def test_every_public_source_has_a_ledger_entry() -> None:
    entries = evidence.load_ledger(LEDGER)
    source_ids = {entry.source_id for entry in entries}
    for source_id, registered in SOURCES.items():
        if registered.licence_state == LicenceState.PUBLIC:
            assert source_id in source_ids, f"no ledger entry for public source {source_id!r}"


def test_minedex_entry_is_closed() -> None:
    entries = evidence.load_ledger(LEDGER)
    minedex_entries = [entry for entry in entries if entry.source_id == "dmirs_001_minedex"]
    assert minedex_entries
    assert all(entry.status == "closed" for entry in minedex_entries)


def test_ledger_carries_no_absolute_paths() -> None:
    raw_text = LEDGER.read_text(encoding="utf-8")
    for forbidden in ("/Users/", "/home/", ":\\"):
        assert forbidden not in raw_text, f"ledger contains an absolute path marker: {forbidden!r}"


def test_gating_entries_are_never_digest_only() -> None:
    entries = evidence.load_ledger(LEDGER)
    for entry in entries:
        if entry.required_for_public_gate:
            assert entry.status == "verified", (
                f"{entry.claim_id}: a gating entry must be 'verified', got {entry.status!r}"
            )
