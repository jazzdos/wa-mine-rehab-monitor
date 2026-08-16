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


def _write_geotiff(path, array, *, origin=(0.0, 300.0), nodata=None):
    import rasterio
    from rasterio.transform import from_origin

    height, width = array.shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=array.dtype,
        crs="EPSG:3577",
        transform=from_origin(origin[0], origin[1], 30, 30),
        nodata=nodata,
    ) as dst:
        dst.write(array, 1)


def test_grid_spec_from_dataset_reads_identity(tmp_path):
    import rasterio

    path = tmp_path / "band.tif"
    _write_geotiff(path, np.zeros((10, 10), dtype=np.int16))
    with rasterio.open(path) as dataset:
        grid = d3_inputs.grid_spec_from_dataset(dataset, tile_id="fixture-a")
    assert grid.crs == "EPSG:3577"
    assert grid.width == 10 and grid.height == 10
    assert grid.transform[0] == 30.0 and grid.transform[4] == -30.0


def test_read_member_values_multi_tile_in_canonical_order(tmp_path):
    import rasterio

    a = tmp_path / "a.tif"
    b = tmp_path / "b.tif"
    _write_geotiff(a, np.arange(100, dtype=np.int16).reshape(10, 10))
    _write_geotiff(b, (np.arange(100, dtype=np.int16) + 1000).reshape(10, 10))
    members = (("tile-a", 2, 3), ("tile-b", 0, 1), ("tile-a", 4, 7))
    with rasterio.open(a) as da, rasterio.open(b) as db:
        values = d3_inputs.read_member_values({"tile-a": da, "tile-b": db}, members)
    # canonical member order is sorted(set(members)):
    # (tile-a,2,3)=23, (tile-a,4,7)=47, (tile-b,0,1)=1001
    assert values.tolist() == [23, 47, 1001]


def test_read_member_values_refuses_unknown_tile(tmp_path):
    import rasterio

    a = tmp_path / "a.tif"
    _write_geotiff(a, np.zeros((10, 10), dtype=np.int16))
    with rasterio.open(a) as da, pytest.raises(d3_inputs.D3InputsError, match="tile"):
        d3_inputs.read_member_values({"tile-a": da}, (("tile-b", 0, 0),))


def test_read_member_values_refuses_out_of_bounds_row_col(tmp_path):
    import rasterio

    a = tmp_path / "a.tif"
    _write_geotiff(a, np.arange(100, dtype=np.int16).reshape(10, 10))
    with rasterio.open(a) as da, pytest.raises(d3_inputs.D3InputsError, match="out of bounds"):
        d3_inputs.read_member_values({"tile-a": da}, (("tile-a", 0, 0), ("tile-a", 10, 5)))


def _bands(n):
    return {
        "nbart_nir": np.linspace(0.2, 0.8, n),
        "nbart_swir_1": np.full(n, 0.2),
        "nbart_swir_2": np.full(n, 0.1),
    }


def test_simulate_footprint_year_produces_rows_and_series():
    members = tuple(sorted(("x0y0", r, c) for r in range(12) for c in range(12)))
    result = d3_inputs.simulate_footprint_year(
        maus_id="M1",
        year=2005,
        source_id="dea_gm_ls5t",
        members=members,
        band_values=_bands(144),
        kind="geomedian",
        supports=(9, 16),
        replicates=25,
        protocol_digest="d" * 64,
    )
    assert result is not None
    rows, reduced_series = result
    frame = pd.DataFrame(rows)
    assert set(frame["metric_id"]) == {"nbr", "ndmi"}
    assert set(frame["support_px"]) == {9, 16}
    assert (frame["n_replicates"] == 25).all()
    # errors persisted as the full sorted replicate list (decision 6)
    lengths = frame["replicate_abs_errors"].map(len)
    assert (lengths == 25).all()
    assert frame["replicate_abs_errors"].map(lambda v: v == sorted(v)).all()
    assert set(reduced_series.keys()) == {
        ("nbr", 9),
        ("nbr", 16),
        ("ndmi", 9),
        ("ndmi", 16),
    }
    assert all(len(v) == 25 for v in reduced_series.values())


def test_simulate_footprint_year_requires_canonical_member_order():
    members = tuple(("x0y0", r, c) for r in range(12) for c in range(12))
    shuffled = members[1:] + members[:1]
    with pytest.raises(d3_inputs.D3InputsError, match="sorted"):
        d3_inputs.simulate_footprint_year(
            maus_id="M1",
            year=2005,
            source_id="dea_gm_ls5t",
            members=shuffled,
            band_values=_bands(144),
            kind="geomedian",
            supports=(9,),
            replicates=5,
            protocol_digest="d" * 64,
        )


def test_simulate_footprint_year_refuses_below_144_support():
    members = tuple(sorted(("x0y0", 0, c) for c in range(100)))
    with pytest.raises(d3_inputs.D3InputsError, match="144"):
        d3_inputs.simulate_footprint_year(
            maus_id="M1",
            year=2005,
            source_id="dea_gm_ls5t",
            members=members,
            band_values=_bands(100),
            kind="geomedian",
            supports=(9,),
            replicates=5,
            protocol_digest="d" * 64,
        )


def test_simulate_footprint_year_invalid_pixel_returns_none():
    members = tuple(sorted(("x0y0", r, c) for r in range(12) for c in range(12)))
    bands = _bands(144)
    bands["nbart_nir"][3] = np.nan
    result = d3_inputs.simulate_footprint_year(
        maus_id="M1",
        year=2005,
        source_id="dea_gm_ls5t",
        members=members,
        band_values=bands,
        kind="geomedian",
        supports=(9,),
        replicates=5,
        protocol_digest="d" * 64,
    )
    assert result is None  # not full-support computable


def test_year_computable_matches_simulate_none_result():
    bands = _bands(144)
    assert d3_inputs.year_computable(bands, kind="geomedian") is True
    bands["nbart_nir"][3] = np.nan
    assert d3_inputs.year_computable(bands, kind="geomedian") is False
