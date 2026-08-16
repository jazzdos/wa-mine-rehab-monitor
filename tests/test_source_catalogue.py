"""Pin tests for the declarative source catalogue (D13 Batch C task C2).

The four DEA collection names were VERIFIED LIVE on 2026-08-15 (jarrah repo
research note `dea-probe-geomedian-fc-percentile_2026-08-15.md`): the
`*_nbart_gm_cyear_3` family resolves HTTP 200 as EMPTY STUBS (global
unbounded extent, zero items), so a name test is load-bearing, not
decorative.
"""

import re

import pytest

from wa_mine_monitor import licence
from wa_mine_monitor.source_catalogue import (
    DEA_COLLECTIONS,
    EMPTY_STUB_COLLECTION_PATTERN,
    SourceSpec,  # noqa: F401 -- kept to match the task spec's test verbatim
    spec_for_collection,
    spec_for_source,
)

EXPECTED_COLLECTION_IDS = (
    "ga_ls5t_gm_cyear_3",
    "ga_ls7e_gm_cyear_3",
    "ga_ls8cls9c_gm_cyear_3",
    "ga_ls_fc_pc_cyear_3",
)


def test_exactly_the_four_verified_collections_are_pinned():
    assert tuple(s.collection_id for s in DEA_COLLECTIONS) == EXPECTED_COLLECTION_IDS


def test_no_pinned_collection_matches_the_empty_stub_naming_pattern():
    for spec in DEA_COLLECTIONS:
        assert not re.search(EMPTY_STUB_COLLECTION_PATTERN, spec.collection_id)


def test_the_stub_pattern_itself_catches_a_known_stub_name():
    # Positive control: a pattern that matches nothing guards nothing.
    assert re.search(EMPTY_STUB_COLLECTION_PATTERN, "ga_ls5t_nbart_gm_cyear_3")


def test_every_spec_source_id_exists_in_licence_sources_with_matching_licence():
    for spec in DEA_COLLECTIONS:
        record = licence.SOURCES[spec.source_id]
        assert spec.licence_state == record.licence_id
        assert spec.collection_id in record.source_url


def test_spec_is_frozen():
    spec = DEA_COLLECTIONS[0]
    with pytest.raises(AttributeError):
        spec.collection_id = "something-else"  # type: ignore[misc]


def test_spec_for_collection_round_trips_and_refuses_unknown():
    assert spec_for_collection("ga_ls_fc_pc_cyear_3").source_id == "dea_fc_pc"
    with pytest.raises(KeyError):
        spec_for_collection("ga_ls5t_nbart_gm_cyear_3")


def test_spec_for_source_round_trips_and_refuses_unknown():
    # The reverse lookup the coverage index needs: it keys items by
    # `source_id` but D13 C3 requires the COLLECTION identity in the frame.
    assert spec_for_source("dea_fc_pc").collection_id == "ga_ls_fc_pc_cyear_3"
    with pytest.raises(KeyError):
        spec_for_source("not_a_source")


def test_required_assets_cover_the_metrics_the_project_computes():
    gm = spec_for_collection("ga_ls5t_gm_cyear_3").asset_roles
    # NBR needs nir+swir_2, NDMI needs nir+swir_1; count is the support band.
    for asset in ("nbart_nir", "nbart_swir_1", "nbart_swir_2", "count"):
        assert asset in gm
    fc = spec_for_collection("ga_ls_fc_pc_cyear_3").asset_roles
    for asset in ("bs_pc_50", "pv_pc_50", "npv_pc_50", "qa"):
        assert asset in fc


def test_licence_for_collection_finds_all_four_and_refuses_unknown():
    for spec in DEA_COLLECTIONS:
        assert licence.licence_for_collection(spec.collection_id).licence_id == "CC-BY-4.0"
    with pytest.raises(KeyError):
        licence.licence_for_collection("ga_ls5t_nbart_gm_cyear_3")
