"""Attribution rendering checks for the Tier 0 public-RC lane (D13 §8 P3).

`public_rc.render_release_notes` must pull every licence fact from
`licence.SOURCES` at render time, never hardcode it -- the same discipline
`release.attribution_block` already carries for `export-release`'s
`ATTRIBUTION.txt`. These tests pin that both packages' sections are
registry-driven, that the CC-BY-SA/ShareAlike obligations survive on the
Maus side, that the existing `release.attribution_block` mechanism they
share still renders (a regression lock, not new behaviour), and that the
two packages' licence facts never leak into each other's section.
"""

from __future__ import annotations

from wa_mine_monitor import licence, public_rc, release


def test_tenements_release_notes_attribution_is_registry_driven():
    notes = public_rc.render_release_notes("2026.08.29", "2026-08-16", "2026-08-16")
    source = licence.SOURCES["dmirs_003_tenements"]

    assert source.attribution_text in notes
    assert "CC-BY-4.0" in notes
    assert source.source_url in notes


def test_maus_attribution_carries_share_alike_and_modification():
    notes = public_rc.render_release_notes("2026.08.29", "2026-08-16", "2026-08-16")
    source = licence.SOURCES["maus_v2"]

    assert source.attribution_text in notes
    assert "CC-BY-SA-4.0" in notes
    assert public_rc.MAUS_MODIFICATION_STATEMENT in notes


def test_existing_footprint_areas_attribution_block_still_renders():
    block = release.attribution_block(release.PACKAGES["footprint-areas"])
    source = licence.SOURCES["maus_v2"]

    assert source.attribution_text in block
    assert source.licence_id in block
    assert release.PACKAGES["footprint-areas"].modification_statement in block


def test_package_attributions_do_not_cross():
    notes = public_rc.render_release_notes("2026.08.29", "2026-08-16", "2026-08-16")

    tenements_source = licence.SOURCES["dmirs_003_tenements"]
    maus_source = licence.SOURCES["maus_v2"]
    tenements_heading = f"## {tenements_source.title} (tier0-tenements.parquet)"
    maus_heading = f"## {maus_source.title} (tier0-maus-wa.parquet)"

    tenements_idx = notes.index(tenements_heading)
    maus_idx = notes.index(maus_heading)
    assert tenements_idx < maus_idx

    tenements_section = notes[tenements_idx:maus_idx]
    maus_section = notes[maus_idx:]

    assert "CC-BY-SA" not in tenements_section
    assert "dasc.dmirs" not in maus_section
