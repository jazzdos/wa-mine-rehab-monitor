"""D13 E3: per-site-year metric rows from decoded band arrays.

Formulas are the frozen D3 ones (d3_inputs.GEOMEDIAN_METRIC_BANDS /
FC_METRIC_ASSETS); this module adds the E3 row contract: every metric
returns a value OR a not_computable_reason, never a fabricated zero.
"""

import numpy as np
import pytest

from wa_mine_monitor import spectral_metrics as sm


def _gm(nir, swir1, swir2):
    return {
        "nbart_nir": np.asarray(nir, dtype=np.float64),
        "nbart_swir_1": np.asarray(swir1, dtype=np.float64),
        "nbart_swir_2": np.asarray(swir2, dtype=np.float64),
    }


def test_geomedian_nbr_ndmi_formula_fixture():
    rows = sm.geomedian_site_year_metrics(_gm([0.5, 0.5], [0.1, 0.1], [0.3, 0.3]))
    by = {r.metric: r for r in rows}
    assert set(by) == {"nbr", "ndmi"}
    assert by["nbr"].value == pytest.approx((0.5 - 0.3) / (0.5 + 0.3))
    assert by["ndmi"].value == pytest.approx((0.5 - 0.1) / (0.5 + 0.1))
    assert by["nbr"].n_member_pixels == 2
    assert by["nbr"].n_valid_pixels == 2
    assert by["nbr"].computable is True
    assert by["nbr"].not_computable_reason is None


def test_geomedian_null_pixels_reduce_valid_count_not_value():
    # 20 members, 1 invalid: 19 valid meets the ceil(0.95 * 20) == 19 floor.
    nir = [0.5] * 19 + [np.nan]
    swir1 = [0.1] * 20
    swir2 = [0.3] * 20
    rows = sm.geomedian_site_year_metrics(_gm(nir, swir1, swir2))
    nbr = next(r for r in rows if r.metric == "nbr")
    assert nbr.n_member_pixels == 20
    assert nbr.n_valid_pixels == 19
    assert nbr.value == pytest.approx(0.25)


def test_geomedian_zero_denominator_is_not_computable():
    rows = sm.geomedian_site_year_metrics(_gm([0.0], [0.0], [0.0]))
    for r in rows:
        assert r.computable is False
        assert r.value is None
        assert r.not_computable_reason == "zero_valid_pixels"
        assert r.n_valid_pixels == 0


def test_geomedian_empty_member_set_is_not_computable_with_reason():
    rows = sm.geomedian_site_year_metrics(_gm([], [], []))
    assert {r.not_computable_reason for r in rows} == {"zero_member_pixels"}
    assert all(r.n_member_pixels == 0 for r in rows)


def test_geomedian_unrelated_extra_band_does_not_corrupt_valid_mask():
    bands = _gm([0.5, 0.5], [0.1, 0.1], [0.3, 0.3])
    bands["unrelated_metadata_band"] = np.asarray([np.nan, np.nan], dtype=np.float64)
    rows = sm.geomedian_site_year_metrics(bands)
    by = {r.metric: r for r in rows}
    assert by["nbr"].computable is True
    assert by["nbr"].not_computable_reason is None
    assert by["nbr"].n_valid_pixels == 2
    assert by["nbr"].value == pytest.approx((0.5 - 0.3) / (0.5 + 0.3))
    assert by["ndmi"].computable is True
    assert by["ndmi"].n_valid_pixels == 2


def test_geomedian_missing_band_refuses():
    with pytest.raises(sm.SpectralMetricsError, match="nbart_swir_2"):
        sm.geomedian_site_year_metrics(
            {"nbart_nir": np.array([0.5]), "nbart_swir_1": np.array([0.1])}
        )


def _fc(bs, pv, npv):
    return {
        "bs_pc_50": np.asarray(bs, dtype=np.float64),
        "pv_pc_50": np.asarray(pv, dtype=np.float64),
        "npv_pc_50": np.asarray(npv, dtype=np.float64),
    }


def test_fc_metrics_map_assets_to_metric_names_without_clipping():
    rows = sm.fc_site_year_metrics(_fc([10.0, 120.0], [50.0, 50.0], [40.0, 40.0]))
    by = {r.metric: r for r in rows}
    assert set(by) == {"bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"}
    assert by["bare_soil"].value == pytest.approx(65.0)  # 120 retained, not clipped
    assert by["bare_soil"].value_out_of_documented_range == 1
    assert by["photosynthetic_vegetation"].value_out_of_documented_range == 0
    assert by["bare_soil"].n_valid_pixels == 2


def test_fc_null_pixel_excluded_from_all_three_metrics():
    # 20 members, 1 invalid: 19 valid meets the ceil(0.95 * 20) == 19 floor.
    bs = [10.0] * 19 + [np.nan]
    pv = [50.0] * 19 + [60.0]
    npv = [40.0] * 20
    rows = sm.fc_site_year_metrics(_fc(bs, pv, npv))
    assert all(r.n_member_pixels == 20 and r.n_valid_pixels == 19 for r in rows)
    pv_row = next(r for r in rows if r.metric == "photosynthetic_vegetation")
    assert pv_row.value == pytest.approx(50.0)


def test_fc_all_null_is_not_computable():
    rows = sm.fc_site_year_metrics(_fc([np.nan], [np.nan], [np.nan]))
    assert {r.not_computable_reason for r in rows} == {"zero_valid_pixels"}
    assert all(r.value is None and r.value_out_of_documented_range is None for r in rows)


def test_geomedian_below_valid_fraction_floor_is_not_computable_with_true_count():
    # 20 members, ceil(0.95 * 20) == 19; 18 valid is one short of the floor.
    nir = [0.5] * 18 + [np.nan] * 2
    swir1 = [0.1] * 20
    swir2 = [0.3] * 20
    rows = sm.geomedian_site_year_metrics(_gm(nir, swir1, swir2))
    for r in rows:
        assert r.computable is False
        assert r.value is None
        assert r.not_computable_reason == "insufficient_valid_fraction"
        assert r.n_valid_pixels == 18
        assert r.n_member_pixels == 20


def test_geomedian_at_valid_fraction_ceiling_boundary_stays_computable():
    # 20 members, ceil(0.95 * 20) == 19; exactly 19 valid meets the floor.
    nir = [0.5] * 19 + [np.nan]
    swir1 = [0.1] * 20
    swir2 = [0.3] * 20
    rows = sm.geomedian_site_year_metrics(_gm(nir, swir1, swir2))
    for r in rows:
        assert r.computable is True
        assert r.not_computable_reason is None
        assert r.n_valid_pixels == 19
        assert r.n_member_pixels == 20


def test_fc_below_valid_fraction_floor_is_not_computable_with_true_count():
    bs = [10.0] * 18 + [np.nan] * 2
    pv = [50.0] * 20
    npv = [40.0] * 20
    rows = sm.fc_site_year_metrics(_fc(bs, pv, npv))
    for r in rows:
        assert r.computable is False
        assert r.value is None
        assert r.not_computable_reason == "insufficient_valid_fraction"
        assert r.n_valid_pixels == 18
        assert r.value_out_of_documented_range is None


def test_fc_at_valid_fraction_ceiling_boundary_stays_computable():
    bs = [10.0] * 19 + [np.nan]
    pv = [50.0] * 20
    npv = [40.0] * 20
    rows = sm.fc_site_year_metrics(_fc(bs, pv, npv))
    for r in rows:
        assert r.computable is True
        assert r.not_computable_reason is None
        assert r.n_valid_pixels == 19


def test_fc_unrelated_extra_band_does_not_corrupt_valid_mask():
    values = _fc([10.0, 20.0], [50.0, 50.0], [40.0, 40.0])
    values["unrelated_metadata_band"] = np.asarray([np.nan, np.nan], dtype=np.float64)
    rows = sm.fc_site_year_metrics(values)
    by = {r.metric: r for r in rows}
    assert by["bare_soil"].computable is True
    assert by["bare_soil"].not_computable_reason is None
    assert by["bare_soil"].n_valid_pixels == 2
    assert by["bare_soil"].value == pytest.approx(15.0)
    assert by["photosynthetic_vegetation"].computable is True
    assert by["photosynthetic_vegetation"].n_valid_pixels == 2
