"""Tests for the Tier 1 volume estimator (D13 Batch C task C5).

The estimator is pure: frames in, dict out. Every constant it rests on
arrives as a declared input (`WindowPolicy`, `CollectionSelection`,
`YearRange`) and is echoed into the output, so the estimate is recomputable
from its own record.
"""

import math

import pandas as pd
import pytest

from wa_mine_monitor.dea_volume import (
    PROVISIONAL_FIGURES,
    CollectionSelection,
    VolumePopulationError,
    WindowPolicy,
    YearRange,
    derive_volume_estimate,
)

DEFAULT_POLICY = WindowPolicy()

GM_ASSETS = ("nbart_nir", "nbart_swir_1", "nbart_swir_2", "count")
FC_ASSETS = ("bs_pc_50", "pv_pc_50", "npv_pc_50")

SELECTIONS = (
    CollectionSelection(
        source_id="dea_gm_ls5t",
        metric_ids=("nbr", "ndmi"),
        asset_keys=GM_ASSETS,
        assumed_bytes_per_pixel=2,
        assumed_tile_pixels_per_side=3200,
    ),
    CollectionSelection(
        source_id="dea_gm_ls7e",
        metric_ids=("nbr", "ndmi"),
        asset_keys=GM_ASSETS,
        assumed_bytes_per_pixel=2,
        assumed_tile_pixels_per_side=3200,
    ),
    CollectionSelection(
        source_id="dea_fc_pc",
        metric_ids=("bare_soil",),
        asset_keys=FC_ASSETS,
        assumed_bytes_per_pixel=1,
        assumed_tile_pixels_per_side=3200,
    ),
)

YEAR_RANGES = {
    "dea_gm_ls5t": YearRange(1986, 2011),
    "dea_gm_ls7e": YearRange(1999, 2021),
    "dea_fc_pc": YearRange(1987, 2025),
}


def _crosswalk(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "site_id",
            "maus_id",
            "match_method",
            "distance_m",
            "confidence",
            "ambiguity_n",
            "shared_by_n",
            "manual_review_status",
        ],
    )


def _enriched_register(rows):
    frame = pd.DataFrame(rows)
    for column in (
        "n_dea_gm_ls5t_epochs",
        "n_dea_gm_ls7e_epochs",
        "n_dea_gm_ls8cls9c_epochs",
        "n_dea_fc_pc_epochs",
    ):
        frame[column] = pd.array(frame[column], dtype="Int64")
    return frame


def _footprints(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "site_id",
            "maus_id",
            "footprint_area_m2",
            "footprint_bbox_width_m",
            "footprint_bbox_height_m",
        ],
    )


def _index_row(source_id, collection_id, item_id, year, *, tile="x11y22", assets="count"):
    return {
        "source_id": source_id,
        "collection_id": collection_id,
        "item_id": item_id,
        "year": year,
        "bbox_west": 116.0,
        "bbox_south": -33.0,
        "bbox_east": 117.0,
        "bbox_north": -32.0,
        "tile_id": tile,
        "product_version": "4.0.0",
        "asset_identity": assets,
    }


def _item_index():
    """TWO collections over the SAME tile-years -- so per-collection pricing
    of the upper bound is actually exercised (an LS5-only index cannot tell
    per-collection pricing apart from geomedian-pricing-for-everything)."""
    return pd.DataFrame(
        [
            _index_row("dea_gm_ls5t", "ga_ls5t_gm_cyear_3", "a-1990", 1990),
            _index_row("dea_gm_ls5t", "ga_ls5t_gm_cyear_3", "a-1991", 1991),
            _index_row("dea_fc_pc", "ga_ls_fc_pc_cyear_3", "f-1990", 1990),
            _index_row("dea_fc_pc", "ga_ls_fc_pc_cyear_3", "f-1991", 1991),
        ]
    )


def _asset_index(*, block=512, file_size=1000, dtype_bytes=2):
    rows = []
    for source_id, collection_id, item_id, keys, per_sample in (
        ("dea_gm_ls5t", "ga_ls5t_gm_cyear_3", "a-1990", GM_ASSETS, dtype_bytes),
        ("dea_fc_pc", "ga_ls_fc_pc_cyear_3", "f-1990", FC_ASSETS, 1),
    ):
        for key in keys:
            rows.append(
                {
                    "source_id": source_id,
                    "collection_id": collection_id,
                    "item_id": item_id,
                    "asset_key": key,
                    "file_size_bytes": file_size,
                    "raster_width_px": 3200,
                    "raster_height_px": 3200,
                    "block_width_px": block,
                    "block_height_px": block,
                    "data_type": "int16",
                    "bytes_per_sample": per_sample,
                    "metadata_source": "stac-item-asset",
                }
            )
    return pd.DataFrame(rows)


def _inputs():
    crosswalk = _crosswalk(
        [
            {
                "site_id": "S1",
                "maus_id": "M1",
                "match_method": "contains",
                "distance_m": 0.0,
                "confidence": "high",
                "ambiguity_n": 1,
                "shared_by_n": 1,
                "manual_review_status": "unreviewed",
            },
            {
                "site_id": "S2",
                "maus_id": "M2",
                "match_method": "contains",
                "distance_m": 0.0,
                "confidence": "high",
                "ambiguity_n": 1,
                "shared_by_n": 1,
                "manual_review_status": "unreviewed",
            },
            {
                "site_id": "S3",
                "maus_id": None,
                "match_method": "none",
                "distance_m": None,
                "confidence": "none",
                "ambiguity_n": 0,
                "shared_by_n": 1,
                "manual_review_status": "unreviewed",
            },
        ]
    )
    register = _enriched_register(
        [
            {
                "site_id": "S1",
                "lon": 116.5,
                "lat": -32.5,
                "n_dea_gm_ls5t_epochs": 2,
                "n_dea_gm_ls7e_epochs": 0,
                "n_dea_gm_ls8cls9c_epochs": 0,
                "n_dea_fc_pc_epochs": 2,
            },
            {
                "site_id": "S2",
                "lon": 116.6,
                "lat": -32.6,
                "n_dea_gm_ls5t_epochs": 2,
                "n_dea_gm_ls7e_epochs": 0,
                "n_dea_gm_ls8cls9c_epochs": 0,
                "n_dea_fc_pc_epochs": 2,
            },
            {
                "site_id": "S3",
                "lon": None,
                "lat": None,
                "n_dea_gm_ls5t_epochs": None,
                "n_dea_gm_ls7e_epochs": None,
                "n_dea_gm_ls8cls9c_epochs": None,
                "n_dea_fc_pc_epochs": None,
            },
        ]
    )
    # Both footprints small enough that the floor window applies, unless a
    # test says otherwise.
    footprints = _footprints(
        [
            {
                "site_id": "S1",
                "maus_id": "M1",
                "footprint_area_m2": 250_000.0,
                "footprint_bbox_width_m": 500.0,
                "footprint_bbox_height_m": 500.0,
            },
            {
                "site_id": "S2",
                "maus_id": "M2",
                "footprint_area_m2": 250_000.0,
                "footprint_bbox_width_m": 500.0,
                "footprint_bbox_height_m": 500.0,
            },
        ]
    )
    return crosswalk, register, footprints


def _estimate(**overrides):
    crosswalk, register, footprints = _inputs()
    kwargs = {
        "crosswalk_df": crosswalk,
        "register_df": register,
        "footprints_df": footprints,
        "item_index": _item_index(),
        "asset_index": _asset_index(),
        "selections": SELECTIONS,
        "year_ranges": YEAR_RANGES,
        "window_policy": DEFAULT_POLICY,
    }
    kwargs.update(overrides)
    return derive_volume_estimate(**kwargs)


# ---------------------------------------------------------------- population


def test_population_counts_reconcile_to_high_confidence_crosswalk_rows():
    estimate = _estimate()
    assert estimate["population"]["n_sites_eligible"] == 2
    assert estimate["population"]["n_sites_unmatched"] == 1
    assert estimate["population"]["n_distinct_footprints"] == 2


def test_null_coverage_never_becomes_zero():
    _, register, _ = _inputs()
    register.loc[0, "n_dea_gm_ls5t_epochs"] = pd.NA
    estimate = _estimate(register_df=register)
    assert estimate["population"]["n_eligible_sites_coverage_not_computed"] == 1


def test_missing_eligible_site_in_register_is_a_refusal():
    _, register, _ = _inputs()
    with pytest.raises(VolumePopulationError, match="S2"):
        _estimate(register_df=register[register["site_id"] != "S2"])


def test_missing_footprint_for_an_eligible_site_is_a_refusal():
    """D13 C5 names Maus footprints as an input; a site with no footprint
    must not silently fall back to the floor window."""
    _, _, footprints = _inputs()
    with pytest.raises(VolumePopulationError, match="S2"):
        _estimate(footprints_df=footprints[footprints["site_id"] != "S2"])


def test_two_sites_sharing_one_footprint_count_one_distinct_footprint():
    _, _, footprints = _inputs()
    footprints.loc[1, "maus_id"] = "M1"
    estimate = _estimate(footprints_df=footprints)
    assert estimate["population"]["n_sites_eligible"] == 2
    assert estimate["population"]["n_distinct_footprints"] == 1


# -------------------------------------------------------------- window sizing


def test_a_small_footprint_gets_the_declared_floor_window():
    estimate = _estimate()
    windows = estimate["windows"]["by_site"]
    assert windows["S1"]["window_side_px"] == DEFAULT_POLICY.minimum_side_px
    assert windows["S1"]["window_side_m"] == (
        DEFAULT_POLICY.minimum_side_px * DEFAULT_POLICY.pixel_metres
    )
    assert windows["S1"]["window_sizing"] == "floor"


def test_a_large_footprint_grows_the_window_beyond_the_floor():
    _, _, footprints = _inputs()
    footprints.loc[0, "footprint_bbox_width_m"] = 9_000.0
    footprints.loc[0, "footprint_bbox_height_m"] = 1_000.0
    footprints.loc[0, "footprint_area_m2"] = 9_000.0 * 1_000.0
    estimate = _estimate(footprints_df=footprints)
    policy = DEFAULT_POLICY
    expected_px = (
        math.ceil((9_000.0 + 2 * policy.reference_buffer_metres) / policy.pixel_metres)
        + policy.alignment_pad_px
    )
    assert estimate["windows"]["by_site"]["S1"]["window_side_px"] == expected_px
    assert estimate["windows"]["by_site"]["S1"]["window_sizing"] == "footprint"
    # S2 is untouched: sizing is PER SITE, not one window for the population.
    assert estimate["windows"]["by_site"]["S2"]["window_side_px"] == policy.minimum_side_px


def test_an_elongated_footprint_is_sized_by_its_long_span_not_sqrt_area():
    """A 9,000 x 1,000 m strip and a 3,000 x 3,000 m square have the same
    area; only the span rule covers the strip."""
    _, _, footprints = _inputs()
    footprints.loc[0, "footprint_bbox_width_m"] = 9_000.0
    footprints.loc[0, "footprint_bbox_height_m"] = 1_000.0
    footprints.loc[0, "footprint_area_m2"] = 9_000_000.0
    strip = _estimate(footprints_df=footprints)["windows"]["by_site"]["S1"]
    equivalent_square_side_m = math.sqrt(9_000_000.0)
    assert strip["window_side_m"] > equivalent_square_side_m


def test_the_window_covers_the_footprint_plus_two_buffers():
    _, _, footprints = _inputs()
    footprints.loc[0, "footprint_bbox_width_m"] = 4_000.0
    footprints.loc[0, "footprint_bbox_height_m"] = 4_000.0
    footprints.loc[0, "footprint_area_m2"] = 16_000_000.0
    window = _estimate(footprints_df=footprints)["windows"]["by_site"]["S1"]
    assert window["window_side_m"] >= 4_000.0 + 2 * DEFAULT_POLICY.reference_buffer_metres


# ------------------------------------------------------------------- selection


def test_band_selection_is_an_input_not_a_hard_coded_count():
    fewer = tuple(
        selection
        if selection.source_id != "dea_gm_ls5t"
        else CollectionSelection(
            source_id="dea_gm_ls5t",
            metric_ids=("nbr",),
            asset_keys=("nbart_nir", "nbart_swir_2"),
            assumed_bytes_per_pixel=2,
            assumed_tile_pixels_per_side=3200,
        )
        for selection in SELECTIONS
    )
    baseline = _estimate()
    reduced = _estimate(selections=fewer)
    assert reduced["selections"]["dea_gm_ls5t"]["n_assets_selected"] == 2
    assert baseline["selections"]["dea_gm_ls5t"]["n_assets_selected"] == len(GM_ASSETS)
    assert (
        reduced["bytes"]["windowed_read_bytes_estimate"]
        < (baseline["bytes"]["windowed_read_bytes_estimate"])
    )


def test_an_asset_outside_the_pinned_spec_is_refused():
    bad = (
        CollectionSelection(
            source_id="dea_gm_ls5t",
            metric_ids=("nbr",),
            asset_keys=("not_an_asset",),
            assumed_bytes_per_pixel=2,
            assumed_tile_pixels_per_side=3200,
        ),
    )
    with pytest.raises(ValueError, match="not_an_asset"):
        _estimate(selections=bad)


def test_a_duplicated_selected_asset_is_refused():
    bad = (
        CollectionSelection(
            source_id="dea_gm_ls5t",
            metric_ids=("nbr",),
            asset_keys=("nbart_nir", "nbart_nir"),
            assumed_bytes_per_pixel=2,
            assumed_tile_pixels_per_side=3200,
        ),
    )
    with pytest.raises(ValueError, match="duplicate"):
        _estimate(selections=bad)


# ----------------------------------------------------------------- arithmetic


def test_windowed_byte_arithmetic_is_per_collection_and_reproducible():
    estimate = _estimate()
    window_px = DEFAULT_POLICY.minimum_side_px**2
    # Two eligible sites, 2 LS5 epochs each, 4 assets, 2 B/px;
    # 2 FC epochs each, 3 assets, 1 B/px. LS7 has 0 epochs.
    gm_raw = 2 * 2 * len(GM_ASSETS) * window_px * 2
    fc_raw = 2 * 2 * len(FC_ASSETS) * window_px * 1
    expected = int((gm_raw + fc_raw) * estimate["assumptions"]["compression_ratio"])
    assert estimate["bytes"]["windowed_read_bytes_estimate"] == expected
    by_collection = estimate["bytes"]["windowed_read_bytes_by_collection"]
    assert by_collection["dea_gm_ls7e"] == 0


def test_shared_tiles_are_not_counted_as_repeated_full_downloads():
    estimate = _estimate()
    assert estimate["tiles"]["n_distinct_tiles"] == 1
    assert estimate["tiles"]["n_distinct_tile_years_by_collection"] == {
        "dea_gm_ls5t": 2,
        "dea_gm_ls7e": 0,
        "dea_fc_pc": 2,
    }


def test_the_upper_bound_is_priced_per_collection_never_geomedian_for_all():
    """FC is uint8 with 3 selected assets; geomedian is int16 with 4. Pricing
    every tile-year at geomedian rates would silently overstate FC and, in
    the reverse case, drop collections from the bound entirely."""
    estimate = _estimate()
    tile_px = 3200**2
    expected_gm = 2 * tile_px * len(GM_ASSETS) * 2
    expected_fc = 2 * tile_px * len(FC_ASSETS) * 1
    by_collection = estimate["bytes"]["upper_bound_bytes_by_collection"]
    assert by_collection["dea_gm_ls5t"] == expected_gm
    assert by_collection["dea_fc_pc"] == expected_fc
    assert estimate["bytes"]["upper_bound_bytes"] == expected_gm + expected_fc
    assert (
        estimate["bytes"]["upper_bound_bytes"] > (estimate["bytes"]["windowed_read_bytes_estimate"])
    )


def test_sensor_overlap_years_are_counted_per_collection_never_merged():
    _, register, _ = _inputs()
    register.loc[0, "n_dea_gm_ls7e_epochs"] = 2
    register.loc[1, "n_dea_gm_ls7e_epochs"] = 2
    per_collection = _estimate(register_df=register)["site_year_windows"]["per_collection"]
    assert per_collection["dea_gm_ls5t"] == 4
    assert per_collection["dea_gm_ls7e"] == 4


def test_years_outside_a_declared_range_are_excluded_with_a_count():
    estimate = _estimate(year_ranges={**YEAR_RANGES, "dea_gm_ls5t": YearRange(1991, 2011)})
    assert estimate["tiles"]["n_distinct_tile_years_by_collection"]["dea_gm_ls5t"] == 1
    assert estimate["year_range_disclosure"]["dea_gm_ls5t"]["n_item_years_outside_range"] == 1


# ------------------------------------------------------- asset-metadata nulls


def test_range_requests_come_from_observed_block_metadata():
    estimate = _estimate()
    # 67-px window against 512-px blocks: 1 x 1 block per band.
    per_window_band = 1
    total_bands = 2 * 2 * len(GM_ASSETS) + 2 * 2 * len(FC_ASSETS)
    assert estimate["expected_range_requests"] == total_bands * per_window_band
    assert estimate["asset_metadata_disclosure"]["n_assets_block_size_missing"] == 0


def test_missing_block_metadata_yields_null_range_requests_with_a_count():
    """No implicit 4-requests-per-window-band: the number is null and the
    absence is counted."""
    asset_index = _asset_index()
    asset_index["block_width_px"] = pd.NA
    asset_index["block_height_px"] = pd.NA
    estimate = _estimate(asset_index=asset_index)
    assert estimate["expected_range_requests"] is None
    assert estimate["asset_metadata_disclosure"]["n_assets_block_size_missing"] > 0


def test_observed_bytes_per_sample_overrides_the_declared_assumption_and_says_so():
    asset_index = _asset_index(dtype_bytes=4)
    estimate = _estimate(asset_index=asset_index)
    gm = estimate["selections"]["dea_gm_ls5t"]
    assert gm["bytes_per_pixel"] == 4
    assert gm["bytes_per_pixel_source"] == "observed"
    assert estimate["selections"]["dea_gm_ls7e"]["bytes_per_pixel_source"] == "assumed"


def test_a_collection_with_no_asset_metadata_falls_back_to_the_declared_assumption():
    estimate = _estimate(asset_index=_asset_index().iloc[0:0])
    for source_id in ("dea_gm_ls5t", "dea_fc_pc"):
        assert estimate["selections"][source_id]["bytes_per_pixel_source"] == "assumed"
    assert estimate["expected_range_requests"] is None


# --------------------------------------------------------------- the record


def test_provisional_figures_are_comparison_fields_only():
    estimate = _estimate()
    comparison = estimate["provisional_figures_comparison_only"]
    assert comparison == PROVISIONAL_FIGURES
    assert comparison == {
        "provisional_n_tiles": 367,
        "provisional_bytes_estimate": 350 * 10**9,
        "provisional_bytes_upper_bound": int(2.3 * 10**12),
    }
    assert estimate["bytes"]["upper_bound_bytes"] != comparison["provisional_bytes_upper_bound"]


def test_every_declared_input_is_echoed_into_the_output():
    estimate = _estimate()
    assert estimate["window_policy"] == {
        "pixel_metres": DEFAULT_POLICY.pixel_metres,
        "minimum_side_px": DEFAULT_POLICY.minimum_side_px,
        "reference_buffer_metres": DEFAULT_POLICY.reference_buffer_metres,
        "alignment_pad_px": DEFAULT_POLICY.alignment_pad_px,
    }
    assert estimate["year_ranges"]["dea_gm_ls5t"] == [1986, 2011]
    assert estimate["selections"]["dea_fc_pc"]["metric_ids"] == ["bare_soil"]
    assert "windowed_read_bytes_estimate" in estimate["formulas"]
    assert "upper_bound_bytes" in estimate["formulas"]
