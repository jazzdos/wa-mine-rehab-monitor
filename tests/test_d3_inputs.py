"""Deterministic reduced-support simulation inputs (D13 Batch D task D3)."""

import numpy as np
import pandas as pd
import pytest

from wa_mine_monitor import d3_inputs

MEMBERS = tuple(("x0y0", r, c) for r in range(12) for c in range(12))  # 144


def test_sample_support_is_deterministic_and_input_order_free():
    a = d3_inputs.sample_support(MEMBERS, 16, replicate=3, seed_material="seed")
    b = d3_inputs.sample_support(tuple(reversed(MEMBERS)), 16, replicate=3, seed_material="seed")
    assert a == b
    assert len(a) == 16


def test_sample_support_is_nested_across_supports():
    small = d3_inputs.sample_support(MEMBERS, 9, replicate=7, seed_material="seed")
    large = d3_inputs.sample_support(MEMBERS, 100, replicate=7, seed_material="seed")
    assert set(small) <= set(large)


def test_sample_support_has_no_repeats_and_varies_by_replicate():
    one = d3_inputs.sample_support(MEMBERS, 64, replicate=0, seed_material="seed")
    two = d3_inputs.sample_support(MEMBERS, 64, replicate=1, seed_material="seed")
    assert len(set(one)) == 64
    assert one != two


def test_sample_support_refuses_more_than_available():
    with pytest.raises(d3_inputs.D3InputsError, match="requested"):
        d3_inputs.sample_support(MEMBERS[:10], 16, replicate=0, seed_material="s")


def test_geomedian_metrics_full_vs_reduced():
    n = 144
    nir = np.linspace(0.1, 0.9, n)
    bands = {
        "nbart_nir": nir,
        "nbart_swir_1": np.full(n, 0.2),
        "nbart_swir_2": np.full(n, 0.1),
    }
    full = d3_inputs.geomedian_metrics(bands)
    assert full["nbr"] == pytest.approx(np.mean((nir - 0.1) / (nir + 0.1)))
    assert full["ndmi"] == pytest.approx(np.mean((nir - 0.2) / (nir + 0.2)))
    reduced = d3_inputs.geomedian_metrics({k: v[:9] for k, v in bands.items()})
    assert reduced["nbr"] != full["nbr"]


def test_geomedian_validity_rejects_zero_denominator():
    # nir = -swir2 at one pixel -> nbr denominator zero -> pixel invalid.
    bands = {
        "nbart_nir": np.array([0.5, -0.1]),
        "nbart_swir_1": np.array([0.2, 0.2]),
        "nbart_swir_2": np.array([0.1, 0.1]),
    }
    valid = d3_inputs.geomedian_valid_mask(bands)
    assert valid.tolist() == [True, False]


def test_fc_metrics_are_means_of_the_median_percentile_assets():
    values = {
        "bs_pc_50": np.array([10.0, 20.0]),
        "pv_pc_50": np.array([30.0, 50.0]),
        "npv_pc_50": np.array([5.0, 15.0]),
    }
    metrics = d3_inputs.fc_metrics(values)
    assert metrics == {
        "bare_soil": 15.0,
        "photosynthetic_vegetation": 40.0,
        "non_photosynthetic_vegetation": 10.0,
    }


def test_spearman_matches_rank_pearson():
    full = pd.Series([0.1, 0.4, 0.2, 0.9, 0.7])
    reduced = pd.Series([0.15, 0.35, 0.25, 0.85, 0.75])
    rho = d3_inputs.spearman(full, reduced)
    expected = full.rank().corr(reduced.rank())
    assert rho == pytest.approx(expected)


def test_spearman_returns_none_for_constant_series():
    # A constant series has undefined rank correlation: not-computable,
    # disclosed by the caller -- never silently 0 or NaN in a table.
    assert d3_inputs.spearman(pd.Series([1.0, 1.0, 1.0]), pd.Series([1.0, 2.0, 3.0])) is None


def test_spearman_refuses_fewer_than_min_years():
    with pytest.raises(d3_inputs.D3InputsError, match="years"):
        d3_inputs.spearman(pd.Series([1.0]), pd.Series([2.0]))
