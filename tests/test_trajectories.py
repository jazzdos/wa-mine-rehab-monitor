"""D13 E3 trajectory schema: site x year x metric x product variant."""

import pandas as pd
import pyarrow as pa
import pytest
from shapely.geometry import box

from wa_mine_monitor import tables
from wa_mine_monitor import trajectories as tj


def test_schema_has_exactly_the_e3_fields_in_order():
    assert tj.TRAJECTORY_SCHEMA.names == [
        "site_id",
        "maus_id",
        "year",
        "metric",
        "value",
        "sensor",
        "collection_id",
        "item_id",
        "product_version",
        "geomad_count",
        "n_member_pixels",
        "n_valid_pixels",
        "effective_pixel_support_px",
        "computable",
        "not_computable_reason",
        "value_out_of_documented_range",
        "transition_adjacent",
        "source_snapshot_date",
        "geometry",
    ]


def test_schema_nullability_matches_e3():
    f = {fld.name: fld for fld in tj.TRAJECTORY_SCHEMA}
    for name in (
        "site_id",
        "maus_id",
        "year",
        "metric",
        "collection_id",
        "item_id",
        "n_member_pixels",
        "computable",
        "transition_adjacent",
        "source_snapshot_date",
        "geometry",
    ):
        assert not f[name].nullable, name
    for name in (
        "value",
        "geomad_count",
        "not_computable_reason",
        "value_out_of_documented_range",
        "n_valid_pixels",
        "sensor",
        "product_version",
        "effective_pixel_support_px",
    ):
        assert f[name].nullable, name
    assert f["year"].type == pa.int32()
    assert f["value"].type == pa.float64()
    assert f["geomad_count"].type == pa.int64()
    assert f["computable"].type == pa.bool_()
    assert f["geometry"].type == pa.binary()  # WKB, EPSG:3577


def test_metric_vocabulary_is_closed_and_matches_spectral_metrics():
    assert tj.METRICS == (
        "nbr",
        "ndmi",
        "bare_soil",
        "photosynthetic_vegetation",
        "non_photosynthetic_vegetation",
    )
    assert tj.GEOMETRY_CRS == "EPSG:3577"


def _row(**over):
    base = {
        "site_id": "S1",
        "maus_id": "M1",
        "year": 2015,
        "metric": "nbr",
        "value": 0.25,
        "sensor": "ls8",
        "collection_id": "dea_gm_ls8cls9c",
        "item_id": "item-1",
        "product_version": "3.1",
        "geomad_count": 12,
        "n_member_pixels": 230,
        "n_valid_pixels": 228,
        "effective_pixel_support_px": 230,
        "computable": True,
        "not_computable_reason": None,
        "value_out_of_documented_range": None,
        "transition_adjacent": False,
        "source_snapshot_date": "2026-08-16",
        "geometry": box(0, 0, 30, 30).wkb,
    }
    base.update(over)
    return base


def test_validate_accepts_a_computable_geomedian_row():
    tj.validate_trajectories(pd.DataFrame([_row()]))


def test_validate_refuses_unknown_metric():
    with pytest.raises(tj.TrajectoryError, match="metric"):
        tj.validate_trajectories(pd.DataFrame([_row(metric="ndvi")]))


def test_validate_refuses_computable_row_without_value_and_vice_versa():
    with pytest.raises(tj.TrajectoryError, match="computable"):
        tj.validate_trajectories(pd.DataFrame([_row(value=None)]))
    with pytest.raises(tj.TrajectoryError, match="computable"):
        tj.validate_trajectories(
            pd.DataFrame([_row(computable=False, not_computable_reason="zero_valid_pixels")])
        )


def test_validate_refuses_not_computable_row_without_reason():
    with pytest.raises(tj.TrajectoryError, match="not_computable_reason"):
        tj.validate_trajectories(pd.DataFrame([_row(value=None, computable=False)]))


def test_validate_refuses_geomad_count_on_fc_rows_and_requires_null():
    tj.validate_trajectories(
        pd.DataFrame([_row(metric="bare_soil", collection_id="dea_fc_pc", geomad_count=None)])
    )
    with pytest.raises(tj.TrajectoryError, match="geomad_count"):
        tj.validate_trajectories(
            pd.DataFrame([_row(metric="bare_soil", collection_id="dea_fc_pc", geomad_count=0)])
        )


def test_validate_refuses_duplicate_site_year_metric_collection():
    with pytest.raises(tj.TrajectoryError, match="duplicate"):
        tj.validate_trajectories(pd.DataFrame([_row(), _row()]))


def test_overlapping_collections_are_distinct_rows_not_duplicates():
    df = pd.DataFrame([_row(), _row(collection_id="dea_gm_ls7e", item_id="item-2")])
    tj.validate_trajectories(df)
    assert len(df) == 2


def test_nullable_booleans_and_integers_survive_parquet_round_trip(tmp_path):
    df = pd.DataFrame(
        [
            _row(),
            _row(
                metric="ndmi",
                value=None,
                computable=False,
                not_computable_reason="zero_valid_pixels",
                n_valid_pixels=0,
                geomad_count=None,
            ),
        ]
    )
    path = tmp_path / "trajectories.parquet"
    tj.write_trajectories(df, path)
    back = tables.read_table(path)
    assert back["computable"].tolist() == [True, False]
    assert pd.isna(back.loc[1, "value"])
    assert pd.isna(back.loc[1, "geomad_count"])
    assert back.loc[0, "geomad_count"] == 12
    assert back.loc[1, "not_computable_reason"] == "zero_valid_pixels"


def test_rows_from_metrics_fans_metric_rows_into_schema_rows():
    from wa_mine_monitor.spectral_metrics import MetricRow

    metric_rows = [
        MetricRow("nbr", 0.2, 230, 228, True, None),
        MetricRow("ndmi", None, 230, 0, False, "zero_valid_pixels"),
    ]
    ctx = tj.RowContext(
        site_id="S1",
        maus_id="M1",
        year=2015,
        sensor="ls8",
        collection_id="dea_gm_ls8cls9c",
        item_id="item-1",
        product_version="3.1",
        geomad_count=12,
        effective_pixel_support_px=230,
        transition_adjacent=False,
        source_snapshot_date="2026-08-16",
        geometry_wkb=box(0, 0, 30, 30).wkb,
    )
    df = pd.DataFrame(tj.rows_from_metrics(metric_rows, ctx))
    tj.validate_trajectories(df)
    assert df["metric"].tolist() == ["nbr", "ndmi"]
    assert df.loc[1, "computable"] is False or df.loc[1, "computable"] == False
    assert df.loc[0, "geomad_count"] == 12


def test_rows_from_metrics_nulls_geomad_count_for_fc_context():
    from wa_mine_monitor.spectral_metrics import MetricRow

    ctx = tj.RowContext(
        site_id="S1",
        maus_id="M1",
        year=2015,
        sensor=None,
        collection_id="dea_fc_pc",
        item_id="item-9",
        product_version=None,
        geomad_count=None,
        effective_pixel_support_px=230,
        transition_adjacent=False,
        source_snapshot_date="2026-08-16",
        geometry_wkb=box(0, 0, 30, 30).wkb,
    )
    rows = tj.rows_from_metrics(
        [MetricRow("bare_soil", 40.0, 230, 230, True, None, value_out_of_documented_range=0)], ctx
    )
    assert rows[0]["geomad_count"] is None
    assert rows[0]["value_out_of_documented_range"] == 0
