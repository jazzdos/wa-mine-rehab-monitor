"""Tests for Maus footprint SCALARS (D13 Batch C task C5's footprint input).

Toy geopandas frames built in-test, the discipline `tests/sources/test_maus.py`
already uses; no committed geometry fixture and no geometry in any output.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from wa_mine_monitor import crosswalk
from wa_mine_monitor.maus_footprints import (
    MAUS_FOOTPRINT_STATS_SCHEMA,
    FootprintStatsError,
    derive_footprint_stats,
    join_site_footprints,
)


def _rect(x0, y0, width_m, height_m):
    return Polygon(
        [
            (x0, y0),
            (x0 + width_m, y0),
            (x0 + width_m, y0 + height_m),
            (x0, y0 + height_m),
        ]
    )


def _maus_gdf(rows, *, crs=crosswalk.TARGET_CRS):
    return gpd.GeoDataFrame(
        {"maus_id": [maus_id for maus_id, _ in rows]},
        geometry=[geometry for _, geometry in rows],
        crs=crs,
    )


def test_area_and_bounds_are_metres_in_the_equal_area_crs():
    gdf = _maus_gdf([("M1", _rect(0.0, 0.0, 900.0, 300.0))])
    stats = derive_footprint_stats(gdf)
    row = stats.iloc[0]
    assert row["maus_id"] == "M1"
    assert row["footprint_area_m2"] == pytest.approx(900.0 * 300.0)
    assert row["footprint_bbox_width_m"] == pytest.approx(900.0)
    assert row["footprint_bbox_height_m"] == pytest.approx(300.0)


def test_output_schema_carries_no_geometry_column():
    stats = derive_footprint_stats(_maus_gdf([("M1", _rect(0.0, 0.0, 100.0, 100.0))]))
    assert list(stats.columns) == MAUS_FOOTPRINT_STATS_SCHEMA.names
    assert "geometry" not in stats.columns


def test_row_order_is_deterministic_by_maus_id():
    gdf = _maus_gdf(
        [
            ("M2", _rect(0.0, 0.0, 100.0, 100.0)),
            ("M1", _rect(500.0, 0.0, 100.0, 100.0)),
        ]
    )
    assert list(derive_footprint_stats(gdf)["maus_id"]) == ["M1", "M2"]


def test_wrong_crs_is_refused_not_silently_reprojected():
    gdf = _maus_gdf([("M1", _rect(0.0, 0.0, 100.0, 100.0))], crs="EPSG:4326")
    with pytest.raises(FootprintStatsError, match="3577"):
        derive_footprint_stats(gdf)


def test_duplicate_maus_id_is_refused():
    gdf = _maus_gdf(
        [("M1", _rect(0.0, 0.0, 100.0, 100.0)), ("M1", _rect(500.0, 0.0, 100.0, 100.0))]
    )
    with pytest.raises(FootprintStatsError, match="duplicate"):
        derive_footprint_stats(gdf)


@pytest.mark.parametrize(
    "geometry", [None, Polygon(), _rect(0.0, 0.0, 0.0, 0.0)], ids=["null", "empty", "zero-area"]
)
def test_unusable_geometry_is_refused_not_dropped(geometry):
    gdf = _maus_gdf([("M1", geometry)])
    with pytest.raises(FootprintStatsError):
        derive_footprint_stats(gdf)


def _high_confidence_crosswalk(rows):
    return pd.DataFrame(rows, columns=["site_id", "maus_id", "confidence"])


def test_join_preserves_every_site_footprint_link():
    """Two sites sharing one footprint is a real shape (`shared_by_n` in
    CROSSWALK_SCHEMA exists for it): both links survive, and `maus_id` stays
    in the join output so shared and distinct footprints can be told apart."""
    stats = derive_footprint_stats(
        _maus_gdf(
            [
                ("M1", _rect(0.0, 0.0, 900.0, 300.0)),
                ("M2", _rect(5000.0, 0.0, 300.0, 300.0)),
            ]
        )
    )
    joined = join_site_footprints(
        _high_confidence_crosswalk(
            [
                {"site_id": "S1", "maus_id": "M1", "confidence": "high"},
                {"site_id": "S2", "maus_id": "M1", "confidence": "high"},
                {"site_id": "S3", "maus_id": "M2", "confidence": "high"},
            ]
        ),
        stats,
    )
    assert len(joined) == 3
    assert set(joined.columns) == {
        "site_id",
        "maus_id",
        "footprint_area_m2",
        "footprint_bbox_width_m",
        "footprint_bbox_height_m",
    }
    assert joined.loc[joined["site_id"] == "S2", "footprint_area_m2"].iloc[0] == pytest.approx(
        900.0 * 300.0
    )


def test_join_refuses_a_maus_id_absent_from_the_stats():
    stats = derive_footprint_stats(_maus_gdf([("M1", _rect(0.0, 0.0, 100.0, 100.0))]))
    with pytest.raises(FootprintStatsError, match="M9"):
        join_site_footprints(
            _high_confidence_crosswalk([{"site_id": "S1", "maus_id": "M9", "confidence": "high"}]),
            stats,
        )
