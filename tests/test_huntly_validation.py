"""D13 E5: validation of the monitor's own zonal engine against the jarrah
Huntly pilot cube (see the 2026-08-25 engine-parity re-scope decision)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import rasterio
from rasterio.transform import from_origin

from wa_mine_monitor import huntly_validation

# A 4x4, 30 m grid with its north-west corner at (0, 120) in a fake
# EPSG:3577-shaped CRS -- pixel (row, col) = (0, 0) covers x in [0, 30),
# y in [90, 120); (row, col) = (3, 3) covers x in [90, 120), y in [0, 30).
_TRANSFORM = from_origin(0, 120, 30, 30)


def _write_cog(path, band_arrays: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = next(iter(band_arrays.values())).shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=len(band_arrays),
        dtype="float64",
        crs="EPSG:3577",
        transform=_TRANSFORM,
    ) as dst:
        for i, (name, array) in enumerate(band_arrays.items(), start=1):
            dst.write(array, i)
            dst.set_band_description(i, name)


def _nbart_arrays(fill: float = 1.0) -> dict[str, np.ndarray]:
    return {
        "nbr": np.full((4, 4), fill, dtype=np.float64),
        "ndmi": np.full((4, 4), fill * 2, dtype=np.float64),
        "ndvi": np.full((4, 4), fill * 3, dtype=np.float64),
    }


def _fc_arrays(fill: float = 10.0) -> dict[str, np.ndarray]:
    return {
        "bare": np.full((4, 4), fill, dtype=np.float64),
        "pv": np.full((4, 4), fill * 2, dtype=np.float64),
        "npv": np.full((4, 4), fill * 3, dtype=np.float64),
    }


def _write_reference(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=huntly_validation.HUNTLY_REFERENCE_SCHEMA)
    pq.write_table(table, path)


# --- reference reader (unchanged by the re-scope) --------------------------


def test_reference_schema_matches_the_declared_jarrah_columns():
    assert huntly_validation.HUNTLY_REFERENCE_SCHEMA.names == [
        "site_id",
        "year",
        "bare",
        "pv",
        "npv",
        "nbr",
        "ndmi",
        "ndvi",
    ]


def test_read_reference_cube_returns_the_declared_columns(tmp_path):
    path = tmp_path / "series_incumbent_w1.parquet"
    _write_reference(
        path,
        [
            {
                "site_id": "H0001",
                "year": 2011,
                "bare": 13.0,
                "pv": 41.0,
                "npv": 45.0,
                "nbr": 0.284016,
                "ndmi": 0.059689,
                "ndvi": 0.477765,
            }
        ],
    )
    frame = huntly_validation.read_reference_cube(path)
    assert list(frame.columns) == huntly_validation.HUNTLY_REFERENCE_SCHEMA.names
    assert frame.loc[0, "site_id"] == "H0001"


def test_read_reference_cube_refuses_a_table_missing_a_metric_column(tmp_path):
    path = tmp_path / "bad.parquet"
    pd.DataFrame({"site_id": ["H0001"], "year": [2011]}).to_parquet(path)
    with pytest.raises(huntly_validation.HuntlyValidationError, match="missing column"):
        huntly_validation.read_reference_cube(path)


def _write_reference_with_counts(path, rows: list[dict]) -> None:
    """Like `_write_reference`, but for a reference table that ALSO carries
    `n_member_pixels`/`n_valid_pixels` -- the shape a counts-bearing jarrah
    reference is expected to take once one exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = huntly_validation.HUNTLY_REFERENCE_SCHEMA.append(
        pa.field("n_member_pixels", pa.int64(), nullable=False)
    ).append(pa.field("n_valid_pixels", pa.int64(), nullable=False))
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path)


def test_read_reference_cube_keeps_pixel_counts_when_the_file_carries_them(tmp_path):
    path = tmp_path / "series_with_counts.parquet"
    _write_reference_with_counts(
        path,
        [
            {
                "site_id": "H0001",
                "year": 2011,
                "bare": 13.0,
                "pv": 41.0,
                "npv": 45.0,
                "nbr": 0.284016,
                "ndmi": 0.059689,
                "ndvi": 0.477765,
                "n_member_pixels": 9,
                "n_valid_pixels": 8,
            }
        ],
    )
    frame = huntly_validation.read_reference_cube(path)
    assert frame.loc[0, "n_member_pixels"] == 9
    assert frame.loc[0, "n_valid_pixels"] == 8
    assert frame["n_member_pixels"].dtype == np.int64
    assert frame["n_valid_pixels"].dtype == np.int64


def test_reference_metric_names_map_onto_the_monitor_vocabulary():
    assert huntly_validation.REFERENCE_METRIC_COLUMNS == {
        "nbr": "nbr",
        "ndmi": "ndmi",
        "bare_soil": "bare",
        "photosynthetic_vegetation": "pv",
        "non_photosynthetic_vegetation": "npv",
    }


# --- sample_pilot_cube -------------------------------------------------


def test_sample_pilot_cube_reads_bands_by_description_name_not_index(tmp_path):
    composites = tmp_path / "composites"
    canonical = _nbart_arrays(fill=1.0)
    reordered = {
        "ndvi": canonical["ndvi"],
        "nbr": canonical["nbr"],
        "ndmi": canonical["ndmi"],
    }
    _write_cog(composites / "nbart" / "nbart_2011.tif", reordered)
    sites = pd.DataFrame({"site_id": ["H0001"], "x": [45.0], "y": [75.0]})

    frame = huntly_validation.sample_pilot_cube(composites, sites)

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["nbr"] == pytest.approx(1.0)
    assert row["ndmi"] == pytest.approx(2.0)
    assert row["ndvi"] == pytest.approx(3.0)


def test_sample_pilot_cube_clips_the_window_at_the_raster_edge(tmp_path):
    composites = tmp_path / "composites"
    _write_cog(composites / "nbart" / "nbart_2011.tif", _nbart_arrays())
    # (row, col) = (0, 0), the north-west corner pixel: a 3x3 window
    # centred here is clipped to a 2x2 block, never padded.
    sites = pd.DataFrame({"site_id": ["H0001"], "x": [15.0], "y": [105.0]})

    frame = huntly_validation.sample_pilot_cube(composites, sites)

    assert frame.iloc[0]["n_member_pixels"] == 4


def test_sample_pilot_cube_all_nan_block_is_not_computable_for_that_metric_only(tmp_path):
    composites = tmp_path / "composites"
    arrays = _nbart_arrays(fill=1.0)
    arrays["ndmi"] = np.full((4, 4), np.nan, dtype=np.float64)
    _write_cog(composites / "nbart" / "nbart_2011.tif", arrays)
    sites = pd.DataFrame({"site_id": ["H0001"], "x": [45.0], "y": [75.0]})

    frame = huntly_validation.sample_pilot_cube(composites, sites)

    row = frame.iloc[0]
    assert row["nbr"] == pytest.approx(1.0)
    assert pd.isna(row["ndmi"])
    assert row["ndvi"] == pytest.approx(3.0)


def test_sample_pilot_cube_missing_year_file_yields_no_row_for_that_year(tmp_path):
    composites = tmp_path / "composites"
    _write_cog(composites / "nbart" / "nbart_2011.tif", _nbart_arrays())
    sites = pd.DataFrame({"site_id": ["H0001"], "x": [45.0], "y": [75.0]})

    frame = huntly_validation.sample_pilot_cube(composites, sites)

    assert set(frame["year"]) == {2011}
    assert 2012 not in set(frame["year"])


def test_sample_pilot_cube_interior_site_counts_member_and_valid_pixels(tmp_path):
    composites = tmp_path / "composites"
    arrays = _nbart_arrays(fill=1.0)
    arrays["nbr"][0, 0] = np.nan  # one masked pixel inside the 3x3 block
    _write_cog(composites / "nbart" / "nbart_2011.tif", arrays)
    sites = pd.DataFrame({"site_id": ["H0001"], "x": [45.0], "y": [75.0]})

    frame = huntly_validation.sample_pilot_cube(composites, sites)

    row = frame.iloc[0]
    assert row["n_member_pixels"] == 9
    assert row["n_valid_pixels"] == 8


# --- melt_sampled_frame --------------------------------------------------


def test_melt_sampled_frame_produces_one_row_per_metric():
    sampled = pd.DataFrame(
        [
            {
                "site_id": "H0001",
                "year": 2011,
                "bare": 13.0,
                "pv": 41.0,
                "npv": 45.0,
                "nbr": 0.284016,
                "ndmi": float("nan"),
                "ndvi": 0.477765,
                "n_member_pixels": 9,
                "n_valid_pixels": 8,
            }
        ]
    )

    long = huntly_validation.melt_sampled_frame(sampled)

    assert sorted(long["metric"]) == sorted(huntly_validation.REFERENCE_METRIC_COLUMNS)
    assert set(long.columns) == {
        "site_id",
        "year",
        "metric",
        "value",
        "n_member_pixels",
        "n_valid_pixels",
        "computable",
        "not_computable_reason",
    }
    ndmi_row = long[long["metric"] == "ndmi"].iloc[0]
    assert bool(ndmi_row["computable"]) is False
    assert ndmi_row["not_computable_reason"] == "zero_valid_pixels"
    assert pd.isna(ndmi_row["value"])
    nbr_row = long[long["metric"] == "nbr"].iloc[0]
    assert bool(nbr_row["computable"]) is True
    assert pd.isna(nbr_row["not_computable_reason"])
    assert nbr_row["value"] == pytest.approx(0.284016)


# --- Tolerances and compare() --------------------------------------------


def _extracted(**overrides) -> pd.DataFrame:
    row = {
        "site_id": "H0001",
        "year": 2011,
        "metric": "nbr",
        "value": 0.284016,
        "collection_id": "ga_ls5t_gm_cyear_3",
        "n_member_pixels": 230,
        "n_valid_pixels": 230,
        "computable": True,
        "not_computable_reason": None,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _reference(**overrides) -> pd.DataFrame:
    row = {
        "site_id": "H0001",
        "year": 2011,
        "bare": 13.0,
        "pv": 41.0,
        "npv": 45.0,
        "nbr": 0.284016,
        "ndmi": 0.059689,
        "ndvi": 0.477765,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _full_extracted(
    *,
    site_id: str = "H0001",
    year: int = 2011,
    n_member_pixels: int = 230,
    n_valid_pixels: int = 230,
    collection_id: str = "ga_ls5t_gm_cyear_3",
    **metric_value_overrides: float,
) -> pd.DataFrame:
    """Full reference-side coverage for one (site_id, year): one row per
    `REFERENCE_METRIC_COLUMNS` metric, matching `_reference()`'s default
    values exactly, so `compare()`'s coverage check finds every reference
    metric accounted for. `metric_value_overrides` overrides individual
    metric values by name (e.g. `bare_soil=13.05`)."""
    values: dict[str, float] = {
        "nbr": 0.284016,
        "ndmi": 0.059689,
        "bare_soil": 13.0,
        "photosynthetic_vegetation": 41.0,
        "non_photosynthetic_vegetation": 45.0,
    }
    values.update(metric_value_overrides)
    rows = [
        {
            "site_id": site_id,
            "year": year,
            "metric": metric,
            "value": value,
            "collection_id": collection_id,
            "n_member_pixels": n_member_pixels,
            "n_valid_pixels": n_valid_pixels,
            "computable": True,
            "not_computable_reason": None,
        }
        for metric, value in values.items()
    ]
    return pd.DataFrame(rows)


def test_default_tolerances_are_the_d13_e5_gate():
    tol = huntly_validation.Tolerances()
    assert tol.spectral_abs == 1e-6
    assert tol.fc_abs == 0.1
    assert tol.require_pixel_counts is False


def test_default_tolerances_succeed_against_a_read_reference_cube_shaped_input(tmp_path):
    # Pins the real `validate-huntly` path: the default Tolerances() must
    # actually be able to run to completion against a reference shaped
    # exactly as `read_reference_cube` produces it -- which carries no
    # n_member_pixels / n_valid_pixels columns at all.
    path = tmp_path / "series_incumbent_w1.parquet"
    _write_reference(
        path,
        [
            {
                "site_id": "H0001",
                "year": 2011,
                "bare": 13.0,
                "pv": 41.0,
                "npv": 45.0,
                "nbr": 0.284016,
                "ndmi": 0.059689,
                "ndvi": 0.477765,
            }
        ],
    )
    reference = huntly_validation.read_reference_cube(path)
    assert not any(c in reference.columns for c in ("n_member_pixels", "n_valid_pixels"))

    report = huntly_validation.compare(_full_extracted(), reference, huntly_validation.Tolerances())

    assert report.passed is True
    assert report.n_compared == 5
    assert report.failures == []
    assert report.n_reference_rows == 1


def test_comparison_passes_within_tolerance():
    report = huntly_validation.compare(
        _full_extracted(), _reference(), huntly_validation.Tolerances(require_pixel_counts=False)
    )
    assert report.passed is True
    assert report.n_compared == 5
    assert report.failures == []


def test_comparison_fails_a_spectral_metric_outside_tolerance():
    report = huntly_validation.compare(
        _extracted(value=0.284016 + 1e-3),
        _reference(),
        huntly_validation.Tolerances(require_pixel_counts=False),
    )
    assert report.passed is False
    assert report.failures[0]["reason"] == "value_outside_tolerance"
    assert report.failures[0]["metric"] == "nbr"


def test_comparison_compares_fc_metrics_unscaled():
    # Same rasters, same units: reference bare=13.0 vs extracted 13.05 is
    # inside the 0.1 pp FC tolerance; 14.0 is not. No scaling applied.
    inside = huntly_validation.compare(
        _full_extracted(bare_soil=13.05),
        _reference(),
        huntly_validation.Tolerances(require_pixel_counts=False),
    )
    assert inside.passed is True
    outside = huntly_validation.compare(
        _extracted(metric="bare_soil", value=14.0),
        _reference(),
        huntly_validation.Tolerances(require_pixel_counts=False),
    )
    assert outside.passed is False


def test_comparison_fails_when_the_reference_has_no_row_for_a_site_year():
    report = huntly_validation.compare(
        _extracted(year=1987, computable=True),
        _reference(),
        huntly_validation.Tolerances(require_pixel_counts=False),
    )
    assert report.passed is False
    assert report.failures[0]["reason"] == "reference_row_missing"


def test_comparison_agrees_when_a_not_computable_row_has_no_reference_row():
    # The jarrah series contract drops a site-year row only when EVERY
    # metric is NaN. A not-computable extracted row (an all-NaN window)
    # whose (site_id, year) is absent from the reference is therefore
    # AGREEMENT, not a defect: both sides found no data. It must not be
    # reported as `reference_row_missing`, and it still counts toward
    # `n_compared`. `_full_extracted()` covers `_reference()`'s own row so
    # the only thing this comparison can fail on is the not-computable row
    # under test.
    extracted = pd.concat(
        [
            _full_extracted(),
            _extracted(
                year=1987, value=None, computable=False, not_computable_reason="zero_valid_pixels"
            ),
        ],
        ignore_index=True,
    )
    report = huntly_validation.compare(
        extracted,
        _reference(),
        huntly_validation.Tolerances(require_pixel_counts=False),
    )
    assert report.passed is True
    assert report.failures == []
    assert report.n_compared == 6


def test_comparison_mixed_computability_only_flags_the_computable_row_as_missing():
    # Same absent (site_id, year) key, two metric rows -- one computable,
    # one not. Only the computable one is a real defect; the not-computable
    # one is agreement under the reference's all-NaN drop contract.
    extracted = pd.concat(
        [
            _extracted(year=1987, metric="nbr", computable=True),
            _extracted(
                year=1987,
                metric="ndmi",
                value=None,
                computable=False,
                not_computable_reason="zero_valid_pixels",
            ),
        ],
        ignore_index=True,
    )
    report = huntly_validation.compare(
        extracted,
        _reference(),
        huntly_validation.Tolerances(require_pixel_counts=False),
    )
    assert report.passed is False
    year_1987_failures = [f for f in report.failures if f["year"] == 1987]
    assert len(year_1987_failures) == 1
    assert year_1987_failures[0]["metric"] == "nbr"
    assert year_1987_failures[0]["reason"] == "reference_row_missing"
    assert report.n_compared == 2


def test_comparison_fails_a_not_computable_row_the_reference_does_carry():
    report = huntly_validation.compare(
        _extracted(value=None, computable=False, not_computable_reason="read_failed"),
        _reference(),
        huntly_validation.Tolerances(require_pixel_counts=False),
    )
    assert report.passed is False
    assert report.failures[0]["reason"] == "computability_mismatch"


def test_comparison_refuses_when_pixel_counts_are_required_but_absent():
    with pytest.raises(huntly_validation.HuntlyValidationError, match="pixel count"):
        huntly_validation.compare(
            _extracted(), _reference(), huntly_validation.Tolerances(require_pixel_counts=True)
        )


def test_comparison_passes_when_pixel_counts_are_required_and_agree():
    report = huntly_validation.compare(
        _full_extracted(n_member_pixels=9, n_valid_pixels=9),
        _reference(n_member_pixels=9, n_valid_pixels=9),
        huntly_validation.Tolerances(require_pixel_counts=True),
    )
    assert report.passed is True
    assert report.failures == []


def test_comparison_fails_when_pixel_counts_disagree():
    # Repro from the finding: extracted 9/9 vs reference 999/1 must not
    # pass silently -- require_pixel_counts=True must actually enforce
    # exact member/valid pixel agreement, not merely check the columns
    # exist.
    report = huntly_validation.compare(
        _extracted(n_member_pixels=9, n_valid_pixels=9),
        _reference(n_member_pixels=999, n_valid_pixels=1),
        huntly_validation.Tolerances(require_pixel_counts=True),
    )
    assert report.passed is False
    assert report.failures[0]["reason"] == "pixel_count_mismatch"
    assert report.failures[0]["mismatched"]["n_member_pixels"] == {
        "extracted": 9,
        "reference": 999,
    }
    assert report.failures[0]["mismatched"]["n_valid_pixels"] == {
        "extracted": 9,
        "reference": 1,
    }


def test_default_require_pixel_counts_passes_against_a_counts_bearing_read_reference(tmp_path):
    """`require_pixel_counts=True` (the CLI default) must be reachable
    through `read_reference_cube` itself once the reference file carries
    counts -- not just against a hand-built in-memory DataFrame."""
    path = tmp_path / "series_with_counts.parquet"
    _write_reference_with_counts(
        path,
        [
            {
                "site_id": "H0001",
                "year": 2011,
                "bare": 13.0,
                "pv": 41.0,
                "npv": 45.0,
                "nbr": 0.284016,
                "ndmi": 0.059689,
                "ndvi": 0.477765,
                "n_member_pixels": 230,
                "n_valid_pixels": 230,
            }
        ],
    )
    reference = huntly_validation.read_reference_cube(path)

    report = huntly_validation.compare(
        _full_extracted(n_member_pixels=230, n_valid_pixels=230),
        reference,
        huntly_validation.Tolerances(require_pixel_counts=True),
    )

    assert report.passed is True
    assert report.failures == []


def test_default_require_pixel_counts_reports_mismatch_against_a_counts_bearing_read_reference(
    tmp_path,
):
    path = tmp_path / "series_with_counts.parquet"
    _write_reference_with_counts(
        path,
        [
            {
                "site_id": "H0001",
                "year": 2011,
                "bare": 13.0,
                "pv": 41.0,
                "npv": 45.0,
                "nbr": 0.284016,
                "ndmi": 0.059689,
                "ndvi": 0.477765,
                "n_member_pixels": 999,
                "n_valid_pixels": 1,
            }
        ],
    )
    reference = huntly_validation.read_reference_cube(path)

    report = huntly_validation.compare(
        _extracted(n_member_pixels=9, n_valid_pixels=9),
        reference,
        huntly_validation.Tolerances(require_pixel_counts=True),
    )

    assert report.passed is False
    assert report.failures[0]["reason"] == "pixel_count_mismatch"


# --- Reference-side coverage (the E5 sole-unlock gate must never pass on a
# zero or incomplete comparison: docs/decisions/2026-08-25-e5-engine-parity-
# rescope.md) -------------------------------------------------------------


def test_comparison_fails_every_reference_metric_against_an_empty_extracted_frame():
    """An empty (or partially-copied) `--composites-dir` yields zero
    extracted rows. Iterating only the extracted side would see no
    failures at all and pass vacuously; `compare()` must instead report one
    `extracted_row_missing` failure per reference `(site_id, year, metric)`."""
    empty_extracted = pd.DataFrame(
        columns=[
            "site_id",
            "year",
            "metric",
            "value",
            "collection_id",
            "n_member_pixels",
            "n_valid_pixels",
            "computable",
            "not_computable_reason",
        ]
    )

    report = huntly_validation.compare(
        empty_extracted, _reference(), huntly_validation.Tolerances(require_pixel_counts=False)
    )

    assert report.passed is False
    assert report.n_compared == 0
    assert report.n_reference_rows == 1
    assert len(report.failures) == len(huntly_validation.REFERENCE_METRIC_COLUMNS)
    assert all(f["reason"] == "extracted_row_missing" for f in report.failures)
    assert {f["metric"] for f in report.failures} == set(huntly_validation.REFERENCE_METRIC_COLUMNS)
    assert all(f["site_id"] == "H0001" and f["year"] == 2011 for f in report.failures)


def test_comparison_fails_only_the_uncovered_years_metrics():
    """A reference year with no matching extracted rows at all (e.g. a
    composite year missing from `--composites-dir`) must fail by name --
    the already-covered year must stay clean."""
    reference = pd.concat(
        [_reference(), _reference(year=2012)],
        ignore_index=True,
    )

    report = huntly_validation.compare(
        _full_extracted(year=2011),
        reference,
        huntly_validation.Tolerances(require_pixel_counts=False),
    )

    assert report.passed is False
    assert report.n_reference_rows == 2
    assert len(report.failures) == len(huntly_validation.REFERENCE_METRIC_COLUMNS)
    assert all(f["reason"] == "extracted_row_missing" for f in report.failures)
    assert all(f["year"] == 2012 for f in report.failures)
    assert {f["metric"] for f in report.failures} == set(huntly_validation.REFERENCE_METRIC_COLUMNS)


def test_comparison_refuses_an_empty_reference():
    with pytest.raises(huntly_validation.HuntlyValidationError, match="zero rows"):
        huntly_validation.compare(
            _full_extracted(),
            pd.DataFrame(columns=["site_id", "year", "bare", "pv", "npv", "nbr", "ndmi", "ndvi"]),
            huntly_validation.Tolerances(),
        )
