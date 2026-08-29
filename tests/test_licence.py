import json

import pytest  # noqa: F401 -- kept to match the task spec's test verbatim

from wa_mine_monitor.licence import SOURCES, minedex_redistribution_allowed


def test_every_source_has_required_fields():
    for s in SOURCES.values():
        assert s.source_url and s.licence_id and s.attribution_text
        assert s.redistribute_public in (True, False)


def test_minedex_defaults_blocked(tmp_path):
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_unblocks_only_on_explicit_ccby_evidence(tmp_path):
    # The named evidence files must actually exist inside evidence_dir --
    # see test_minedex_stays_blocked_on_named_file_missing below for the
    # case this guards against -- and the snapshot must be finalized so the
    # evidence files appear, OK, in SHA256SUMS.txt (the default
    # require_hashed=True).
    from wa_mine_monitor.snapshots import finalize_snapshot

    (tmp_path / "landing.html").write_text("<html>captured landing page</html>")
    (tmp_path / "bundle_readme.txt").write_text("captured readme")
    (tmp_path / "licence_evidence.json").write_text(
        '{"resource": "MINEDEX DASC download", "explicit_grant": "CC-BY-4.0", '
        '"contrary_notice": false, "captured": "2026-08-15", '
        '"evidence_files": ["landing.html", "bundle_readme.txt"]}'
    )
    finalize_snapshot(tmp_path)
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is True


def test_minedex_prefinalize_check_needs_explicit_opt_out(tmp_path):
    """Before `finalize_snapshot` runs there is no SHA256SUMS.txt, so the
    default `require_hashed=True` fails closed; the capture-time sanity
    check must opt out explicitly, and a True obtained that way covers
    nothing once the snapshot is finalized."""
    (tmp_path / "landing.html").write_text("<html>captured landing page</html>")
    (tmp_path / "licence_evidence.json").write_text(
        '{"resource": "MINEDEX DASC download", "explicit_grant": "CC-BY-4.0", '
        '"contrary_notice": false, "captured": "2026-08-15", '
        '"evidence_files": ["landing.html"]}'
    )
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False
    assert minedex_redistribution_allowed(evidence_dir=tmp_path, require_hashed=False) is True


def test_minedex_stays_blocked_on_contrary_notice(tmp_path):
    (tmp_path / "licence_evidence.json").write_text(
        '{"resource": "MINEDEX DASC download", "explicit_grant": "CC-BY-4.0", '
        '"contrary_notice": true, "captured": "2026-08-15", "evidence_files": []}'
    )
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_stays_blocked_on_empty_evidence_files():
    """`contrary_notice: false` and an explicit grant are not enough on their
    own -- the captured evidence must actually name files, or the "captured"
    claim is unverifiable."""
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        evidence_dir = Path(tmp)
        (evidence_dir / "licence_evidence.json").write_text(
            json.dumps(
                {
                    "resource": "MINEDEX DASC download",
                    "explicit_grant": "CC-BY-4.0",
                    "contrary_notice": False,
                    "captured": "2026-08-15",
                    "evidence_files": [],
                }
            )
        )
        assert minedex_redistribution_allowed(evidence_dir=evidence_dir) is False


def test_minedex_stays_blocked_on_wrong_grant(tmp_path):
    (tmp_path / "licence_evidence.json").write_text(
        '{"resource": "MINEDEX DASC download", "explicit_grant": "CC-BY-NC-4.0", '
        '"contrary_notice": false, "captured": "2026-08-15", '
        '"evidence_files": ["landing.html"]}'
    )
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_stays_blocked_on_malformed_json(tmp_path):
    (tmp_path / "licence_evidence.json").write_text("{not valid json")
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_stays_blocked_on_missing_keys(tmp_path):
    (tmp_path / "licence_evidence.json").write_text('{"resource": "MINEDEX DASC download"}')
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_stays_blocked_on_non_dict_json(tmp_path):
    (tmp_path / "licence_evidence.json").write_text("[1, 2, 3]")
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_stays_blocked_on_named_file_missing(tmp_path):
    """A named `evidence_files` entry that does not actually exist on disk
    must not count as captured evidence -- the "captured" claim is only
    checkable if the named files are real. Regression for the finding that
    `evidence_files` was accepted as a bare non-empty list with no
    existence check."""
    (tmp_path / "licence_evidence.json").write_text(
        '{"resource": "MINEDEX DASC download", "explicit_grant": "CC-BY-4.0", '
        '"contrary_notice": false, "captured": "2026-08-15", '
        '"evidence_files": ["a_file_that_does_not_exist.html"]}'
    )
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_stays_blocked_on_evidence_file_escaping_the_directory(tmp_path):
    """An `evidence_files` entry that resolves outside `evidence_dir` (path
    traversal, or an absolute path elsewhere on disk) must not be honoured,
    even if a file happens to exist at that location."""
    outside_dir = tmp_path.parent / "outside_evidence"
    outside_dir.mkdir(exist_ok=True)
    (outside_dir / "escaped.html").write_text("not actually captured here")
    (tmp_path / "licence_evidence.json").write_text(
        json.dumps(
            {
                "resource": "MINEDEX DASC download",
                "explicit_grant": "CC-BY-4.0",
                "contrary_notice": False,
                "captured": "2026-08-15",
                "evidence_files": ["../outside_evidence/escaped.html"],
            }
        )
    )
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_stays_blocked_on_post_finalize_drop_in(tmp_path):
    """Reproduces the measured end-to-end defect: writing
    `licence_evidence.json` (naming a file that does not exist) into an
    already-`finalize_snapshot`-d snapshot directory must not flip
    `minedex_redistribution_allowed` to True, even though `verify_snapshot`
    still reports a clean `(n_ok, 0, 0)` for the files it already knows
    about -- SHA256SUMS.txt has no opinion on a file it never hashed."""
    from wa_mine_monitor.snapshots import finalize_snapshot, verify_snapshot

    (tmp_path / "data.csv").write_text("a,b\n1,2\n")
    finalize_snapshot(tmp_path)
    n_ok, n_bad, n_missing = verify_snapshot(tmp_path)
    assert (n_ok, n_bad, n_missing) == (1, 0, 0)

    (tmp_path / "licence_evidence.json").write_text(
        json.dumps(
            {
                "explicit_grant": "CC-BY-4.0",
                "contrary_notice": False,
                "evidence_files": ["a_file_that_does_not_exist.html"],
            }
        )
    )
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_stays_blocked_on_post_finalize_two_file_drop_in(tmp_path):
    """The measured review finding, closed: writing `licence_evidence.json`
    PLUS the file it names (both really existing on disk) into an
    already-`finalize_snapshot`-d directory must not flip
    `minedex_redistribution_allowed` to True -- the dropped-in files are
    absent from SHA256SUMS.txt, and `verify_snapshot` still reports a clean
    `(n_ok, 0, 0)` for the files it already knows about, so only the
    per-path hashed-evidence lookup can catch this."""
    from wa_mine_monitor.snapshots import finalize_snapshot, verify_snapshot

    (tmp_path / "data.csv").write_text("a,b\n1,2\n")
    finalize_snapshot(tmp_path)

    (tmp_path / "landing.html").write_text("<html>dropped in after finalize</html>")
    (tmp_path / "licence_evidence.json").write_text(
        json.dumps(
            {
                "resource": "MINEDEX DASC download",
                "explicit_grant": "CC-BY-4.0",
                "contrary_notice": False,
                "captured": "2026-08-15",
                "evidence_files": ["landing.html"],
            }
        )
    )
    assert verify_snapshot(tmp_path) == (1, 0, 0)  # the hole verify alone can't see
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_stays_blocked_on_single_file_drop_in_naming_hashed_files(tmp_path):
    """The measured review finding, closed: dropping `licence_evidence.json`
    ALONE into an already-`finalize_snapshot`-d directory, naming files
    finalize already hashed (`metadata.txt`, a downloaded `.gpkg`), must not
    flip `minedex_redistribution_allowed` to True. Those named files
    genuinely appear, OK, in SHA256SUMS.txt -- the evidence JSON itself does
    not, because it was never finalized, so the listing check on
    `EVIDENCE_FILENAME` must catch this even though every `evidence_files`
    entry passes its own per-path lookup."""
    from wa_mine_monitor.snapshots import finalize_snapshot, verify_snapshot

    (tmp_path / "metadata.txt").write_text("source: s\nendpoint: e\n")
    (tmp_path / "minedex.gpkg").write_text("fake gpkg bytes")
    finalize_snapshot(tmp_path)
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False

    (tmp_path / "licence_evidence.json").write_text(
        json.dumps(
            {
                "resource": "MINEDEX DASC download",
                "explicit_grant": "CC-BY-4.0",
                "contrary_notice": False,
                "captured": "2026-08-15",
                "evidence_files": ["metadata.txt", "minedex.gpkg"],
            }
        )
    )
    assert verify_snapshot(tmp_path) == (2, 0, 0)  # the hole verify alone can't see
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_stays_blocked_on_tampered_evidence_file(tmp_path):
    """A listed-but-since-tampered evidence file is as unhashed as an
    unlisted one: 'appears, OK, in SHA256SUMS.txt' requires the current
    sha256 to match the recorded digest, not mere listing."""
    from wa_mine_monitor.snapshots import finalize_snapshot

    (tmp_path / "landing.html").write_text("<html>captured landing page</html>")
    (tmp_path / "licence_evidence.json").write_text(
        json.dumps(
            {
                "resource": "MINEDEX DASC download",
                "explicit_grant": "CC-BY-4.0",
                "contrary_notice": False,
                "captured": "2026-08-15",
                "evidence_files": ["landing.html"],
            }
        )
    )
    finalize_snapshot(tmp_path)
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is True

    (tmp_path / "landing.html").write_text("<html>rewritten after finalize</html>")
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_adjudication_edit_of_evidence_json_is_permitted_when_resigned(tmp_path):
    """The legitimate adjudication flow: capture writes the evidence JSON
    with null grant fields, the snapshot finalizes, and a later ruling
    edits exactly that JSON AND re-signs its one `SHA256SUMS.txt` line via
    `snapshots.update_snapshot_entry` -- the declared, narrow exception to
    post-finalize immutability, and exactly what the
    `adjudicate-minedex-licence` CLI command does. The evidence ARTEFACTS
    the JSON names stay byte-identical to what was captured and
    hash-verified; the re-signed adjudication record passes the
    hashed-evidence check."""
    from wa_mine_monitor.snapshots import finalize_snapshot, update_snapshot_entry

    (tmp_path / "landing.html").write_text("<html>captured landing page</html>")
    (tmp_path / "licence_evidence.json").write_text(
        json.dumps(
            {
                "resource": "MINEDEX DASC download",
                "explicit_grant": None,
                "contrary_notice": None,
                "adjudicated": False,
                "captured": "2026-08-15",
                "evidence_files": ["landing.html"],
            }
        )
    )
    finalize_snapshot(tmp_path)
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False  # unadjudicated

    (tmp_path / "licence_evidence.json").write_text(
        json.dumps(
            {
                "resource": "MINEDEX DASC download",
                "explicit_grant": "CC-BY-4.0",
                "contrary_notice": False,
                "adjudicated": True,
                "captured": "2026-08-15",
                "evidence_files": ["landing.html"],
            }
        )
    )
    update_snapshot_entry(tmp_path, "licence_evidence.json")
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is True


def test_minedex_stays_blocked_on_unsigned_edit_of_evidence_json(tmp_path):
    """The measured review finding, closed: an UNSIGNED hand-edit of
    `licence_evidence.json` inside a finalized snapshot -- here flipping
    `contrary_notice` from true to false without re-signing its
    `SHA256SUMS.txt` line -- must not flip `minedex_redistribution_allowed`
    open. Before the digest check on `EVIDENCE_FILENAME` itself, exactly
    this edit flipped the gate from False to True while `verify_snapshot`
    correctly reported the file as tampered: the gate's own stated
    guarantee (a post-finalize edit cannot flip the answer) contradicted
    by the gate. No legitimate state is excluded -- the adjudication flow
    re-signs, so a digest mismatch on the evidence JSON is always either
    tampering or a bypassed `update_snapshot_entry`."""
    from wa_mine_monitor.snapshots import finalize_snapshot, verify_snapshot

    (tmp_path / "landing.html").write_text("<html>captured landing page</html>")
    (tmp_path / "licence_evidence.json").write_text(
        json.dumps(
            {
                "resource": "MINEDEX DASC download",
                "explicit_grant": "CC-BY-4.0",
                "contrary_notice": True,
                "adjudicated": True,
                "captured": "2026-08-15",
                "evidence_files": ["landing.html"],
            }
        )
    )
    finalize_snapshot(tmp_path)
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False  # contrary notice

    (tmp_path / "licence_evidence.json").write_text(
        json.dumps(
            {
                "resource": "MINEDEX DASC download",
                "explicit_grant": "CC-BY-4.0",
                "contrary_notice": False,
                "adjudicated": True,
                "captured": "2026-08-15",
                "evidence_files": ["landing.html"],
            }
        )
    )
    _n_ok, n_bad, _n_missing = verify_snapshot(tmp_path)
    assert n_bad == 1  # verify sees the tamper...
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False  # ...and so does the gate


def test_minedex_source_pinned_blocked_and_conflicted():
    """`SOURCES["dmirs_001_minedex"]` is the fail-closed entry the design's
    §6 conflict binds -- pinned regardless of the evidence-file mechanism
    above, which unblocks a public *export*, not this static registry entry."""
    minedex = SOURCES["dmirs_001_minedex"]
    assert minedex.redistribute_public is False
    assert "CONFLICT" in minedex.licence_id


def test_hansen_attribution_carries_both_credit_strings():
    hansen = SOURCES["hansen_gfc"]
    assert "Source: Hansen/UMD/Google/USGS/NASA" in hansen.attribution_text
    assert "Hansen" in hansen.attribution_text
    assert "2013" in hansen.attribution_text
    assert "https://glad.earthengine.app/view/global-forest-change." in hansen.attribution_text


def test_sources_is_not_empty():
    assert len(SOURCES) >= 8


def test_dea_landsat_attribution_does_not_credit_copernicus():
    """Regression for the finding that all four DEA entries credited
    Copernicus (the ESA/EU Sentinel attribution) in `attribution_text` while
    their own `title` names Landsat -- a record failing to reconcile against
    itself. Every DEA entry whose title names Landsat must attribute Landsat
    (USGS/NASA) provenance, never Copernicus."""
    for source in SOURCES.values():
        if "Landsat" in source.title:
            assert "Copernicus" not in source.attribution_text
            assert "Landsat" in source.attribution_text


def test_source_urls_pin_a_distinct_resource_per_source():
    """No two `SOURCES` entries should share a `source_url` -- a shared URL
    means at least one entry is pinned to a general page rather than to the
    exact primary record it claims to measure (regression for the four DEA
    entries that all pointed at the bare STAC catalog root instead of their
    own collection record)."""
    urls = [source.source_url for source in SOURCES.values()]
    assert len(urls) == len(set(urls)), (
        "two or more SOURCES entries share a source_url; each must pin its own "
        "distinct primary record"
    )


def test_dea_source_urls_point_at_their_own_collection():
    """Each DEA geomedian/FC entry's `source_url` must resolve to that
    specific STAC collection record, not the catalog root -- so the CC-BY-4.0
    pin is directly re-verifiable from the URL as written."""
    dea_collection_ids = {
        "dea_gm_ls5t": "ga_ls5t_gm_cyear_3",
        "dea_gm_ls7e": "ga_ls7e_gm_cyear_3",
        "dea_gm_ls8cls9c": "ga_ls8cls9c_gm_cyear_3",
        "dea_fc_pc": "ga_ls_fc_pc_cyear_3",
    }
    for source_id, collection_id in dea_collection_ids.items():
        source = SOURCES[source_id]
        assert source.source_url.endswith(f"/collections/{collection_id}")


def test_wa_rdc_regions_licence_is_pinned_cc_by():
    record = SOURCES["wa_rdc_regions"]
    assert record.licence_id == "CC-BY-4.0"
    assert record.redistribute_public is True
    assert "DPIRD-020" in record.title
    assert "catalogue.data.wa.gov.au" in record.source_url


def test_wa_rdc_regions_licence_notes_record_the_2026_08_21_repin():
    record = SOURCES["wa_rdc_regions"]
    assert "public-services.slip.wa.gov.au" in record.notes
    assert "2026-08-21" in record.notes


def test_silo_licence_records_the_anonymous_gridded_route() -> None:
    """This project consumes SILO's GRIDDED product from the anonymous
    AWS open-data bucket (CC BY 4.0), not the account-gated point/Data
    Drill API. The licence entry must say so: a reader deciding whether
    an export is redistributable reasons from these fields, and
    "open-with-account" would send them chasing a credential that does
    not exist on this route. O7 closes on this fact rather than on a
    registration -- see docs/decisions/2026-08-26-silo-gridded-feed.md.
    """
    entry = SOURCES["silo"]
    assert entry.licence_id == "CC-BY-4.0"
    assert entry.licence_url == "https://creativecommons.org/licenses/by/4.0/"
    assert "silo-open-data" in entry.notes
    assert "anonymous" in entry.notes.lower()
    assert entry.redistribute_public is True
    assert "SILO" in entry.attribution_text


def test_dbca_060_fire_entry_is_cc_by_with_catalogue_evidence() -> None:
    entry = SOURCES["dbca_060_fire"]
    assert entry.licence_id == "CC-BY-4.0"
    assert entry.licence_url == "https://creativecommons.org/licenses/by/4.0/"
    assert entry.redistribute_public is True
    assert "3ce8a891-b050-4c38-952b-c40ca8bdc042" in entry.notes
    assert "NEVER a known-negative" in entry.notes
