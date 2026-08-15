"""Tests for `wa_mine_monitor.crosswalk` and the `build-crosswalk` CLI command.

The MINEDEX-Maus crosswalk (design doc §3). Fixture-first, same discipline
as `tests/test_register.py`: toy GeoDataFrames built in-test with
geopandas/shapely, in `crosswalk.TARGET_CRS` (`EPSG:3577`) directly, since
`build_crosswalk` itself never reprojects -- reprojection is exercised only
by the CLI test at the bottom of this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
import shapely.errors
from shapely.geometry import Point, Polygon
from typer.testing import CliRunner

from wa_mine_monitor import cli as cli_module
from wa_mine_monitor import crosswalk as crosswalk_module
from wa_mine_monitor import export_gate, register, snapshots
from wa_mine_monitor.cli import app
from wa_mine_monitor.crosswalk import (
    CONFIDENCE_LEVELS,
    CROSSWALK_SCHEMA,
    MATCH_NEAREST_WITHIN_2000M,
    MATCH_POINT_IN_POLYGON,
    MATCH_UNMATCHED,
    TARGET_CRS,
    build_crosswalk,
    crosswalk_counts,
    tier1_population,
)
from wa_mine_monitor.tables import write_table

runner = CliRunner()

_EXPECTED_COLUMNS = [
    "site_id",
    "maus_id",
    "match_method",
    "distance_m",
    "confidence",
    "ambiguity_n",
    "shared_by_n",
    "manual_review_status",
]


# --- fixture builders --------------------------------------------------------


def _sites_gdf(rows: list[tuple[str, float, float]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"site_id": [row[0] for row in rows]},
        geometry=[Point(row[1], row[2]) for row in rows],
        crs=TARGET_CRS,
    )


def _square(cx: float, cy: float, half: float) -> Polygon:
    return Polygon(
        [
            (cx - half, cy - half),
            (cx + half, cy - half),
            (cx + half, cy + half),
            (cx - half, cy + half),
        ]
    )


def _polygons_gdf(rows: list[tuple[str, Polygon]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"maus_id": [row[0] for row in rows]},
        geometry=[row[1] for row in rows],
        crs=TARGET_CRS,
    )


# --- CRS assertion ------------------------------------------------------------


def test_build_crosswalk_raises_when_minedex_gdf_crs_is_wrong() -> None:
    minedex_gdf = _sites_gdf([("S1", 0.0, 0.0)])
    minedex_gdf = minedex_gdf.set_crs("EPSG:4326", allow_override=True)
    maus_gdf = _polygons_gdf([("M1", _square(0.0, 0.0, 100.0))])

    with pytest.raises(ValueError, match="minedex_gdf.*EPSG:3577"):
        build_crosswalk(minedex_gdf, maus_gdf)


def test_build_crosswalk_raises_when_maus_gdf_crs_is_wrong() -> None:
    minedex_gdf = _sites_gdf([("S1", 0.0, 0.0)])
    maus_gdf = _polygons_gdf([("M1", _square(0.0, 0.0, 100.0))])
    maus_gdf = maus_gdf.set_crs("EPSG:4326", allow_override=True)

    with pytest.raises(ValueError, match="maus_gdf.*EPSG:3577"):
        build_crosswalk(minedex_gdf, maus_gdf)


# --- site_id uniqueness / non-nullity enforcement -------------------------------
#
# Regression for the finding that `build_crosswalk` assumed `site_id` was
# unique and non-null, and neither the register nor `validate_minedex_gpkg`
# enforces either. (a) A duplicate `site_id` let the containment pass's
# `already_matched` exclusion drop an UNRELATED site sharing the same id
# from the nearest-match pass entirely -- one of two rows vanished, and
# `crosswalk_counts` still reconciled, because the dropped site was never
# counted anywhere. (b) A null `site_id` inside a polygon fell through
# `_containment_rows`' default `groupby(dropna=True)` and was picked up by
# `_nearest_rows`' `dropna=False` instead, silently downgrading a
# `point_in_polygon`/`high` match to `nearest_within_2000m`/`medium`.


def test_build_crosswalk_raises_on_duplicate_site_id() -> None:
    # One 'DUP' site sits inside the polygon; a SECOND, unrelated site
    # sharing the same site_id sits ~50 km away -- the far site is the one
    # that silently vanished before this guard existed.
    minedex_gdf = _sites_gdf([("DUP", 0.0, 0.0), ("DUP", 50_000.0, 50_000.0)])
    maus_gdf = _polygons_gdf([("M1", _square(0.0, 0.0, 100.0))])

    with pytest.raises(ValueError, match="duplicate.*DUP"):
        build_crosswalk(minedex_gdf, maus_gdf)


def test_build_crosswalk_raises_on_null_site_id() -> None:
    minedex_gdf = _sites_gdf([("S1", 0.0, 0.0)])
    minedex_gdf.loc[0, "site_id"] = None
    maus_gdf = _polygons_gdf([("M1", _square(0.0, 0.0, 100.0))])

    with pytest.raises(ValueError, match="null"):
        build_crosswalk(minedex_gdf, maus_gdf)


def test_build_crosswalk_allows_an_empty_minedex_gdf_with_no_site_ids_to_collide() -> None:
    minedex_gdf = _sites_gdf([])
    maus_gdf = _polygons_gdf([("M1", _square(0.0, 0.0, 100.0))])

    df = build_crosswalk(minedex_gdf, maus_gdf)

    assert len(df) == 0


# --- maus_id uniqueness / non-nullity enforcement -------------------------------
#
# Regression for the finding that `build_crosswalk` guarded `site_id` but
# had no symmetric guard on `maus_id`, while `_nearest_rows`' merge on
# `maus_id` is a cartesian join whenever that id is non-unique. `maus_id`
# is `sha256(WKB)[:12]`, so identical clipped geometries collide, and
# `validate_maus_extract` checks no uniqueness.


def test_build_crosswalk_raises_on_duplicate_maus_id() -> None:
    # The measured scenario: one site with a single real candidate 500 m
    # away, and that candidate polygon duplicated in `maus_gdf`. Before this
    # guard the merge fanned the site out to FOUR rows (2 sjoin hits x 2
    # merge matches), read `ambiguity_n = 4`, and downgraded `confidence`
    # medium -> low on a site whose real candidate set never changed.
    minedex_gdf = _sites_gdf([("S1", 0.0, 0.0)])
    polygon = _square(1000.0, 0.0, 500.0)
    maus_gdf = _polygons_gdf([("DUPM", polygon), ("DUPM", polygon)])

    with pytest.raises(ValueError, match="duplicate.*DUPM"):
        build_crosswalk(minedex_gdf, maus_gdf)


def test_build_crosswalk_raises_on_duplicate_maus_id_for_a_contained_site() -> None:
    # The containment pass fans out the same way: 2 identical
    # `point_in_polygon` rows with `ambiguity_n = 2` for one real polygon.
    minedex_gdf = _sites_gdf([("S1", 0.0, 0.0)])
    polygon = _square(0.0, 0.0, 100.0)
    maus_gdf = _polygons_gdf([("DUPM", polygon), ("DUPM", polygon)])

    with pytest.raises(ValueError, match="duplicate.*DUPM"):
        build_crosswalk(minedex_gdf, maus_gdf)


def test_build_crosswalk_raises_on_null_maus_id() -> None:
    minedex_gdf = _sites_gdf([("S1", 0.0, 0.0)])
    maus_gdf = _polygons_gdf([("M1", _square(0.0, 0.0, 100.0))])
    maus_gdf.loc[0, "maus_id"] = None

    with pytest.raises(ValueError, match="null"):
        build_crosswalk(minedex_gdf, maus_gdf)


def test_the_duplicate_maus_id_fan_out_the_guard_prevents_is_real() -> None:
    """The counterfactual: `_nearest_rows` called directly, past the guard,
    on the same duplicated-polygon input.

    Without this, `test_build_crosswalk_raises_on_duplicate_maus_id` only
    shows that a `ValueError` is raised, never that the condition it refuses
    would have corrupted anything -- the guard could be over-broad and no
    test would say so. Four rows for one site with one real candidate is
    what the merge actually produces.
    """
    minedex_gdf = _sites_gdf([("S1", 0.0, 0.0)])
    polygon = _square(1000.0, 0.0, 500.0)
    maus_gdf = _polygons_gdf([("DUPM", polygon), ("DUPM", polygon)])

    rows = crosswalk_module._nearest_rows(minedex_gdf, maus_gdf, already_matched=set())

    assert len(rows) == 4
    assert {row["ambiguity_n"] for row in rows} == {4}
    assert {row["confidence"] for row in rows} == {"low"}


# --- sites with no usable location ----------------------------------------------
#
# Regression for the finding that a MINEDEX record with a NULL geometry
# reaches the crosswalk as `POINT (NaN NaN)` (the CLI rebuilds point
# geometry from the register's `lon`/`lat`, which are NaN for such a
# record) and killed the `dwithin` pass with a bare
# `shapely.errors.GEOSException` -- an uncaught traceback with empty stdout
# at the CLI. A NULL geometry does not raise at all: `sjoin` simply matches
# nothing, so the site would have been reported `unmatched`, claiming a
# distance test that was never computed.


def _sites_gdf_with_geometries(rows: list[tuple[str, object]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"site_id": [row[0] for row in rows]},
        geometry=[row[1] for row in rows],
        crs=TARGET_CRS,
    )


def test_build_crosswalk_raises_naming_a_site_whose_geometry_is_null() -> None:
    minedex_gdf = _sites_gdf_with_geometries([("S1", Point(0.0, 0.0)), ("NOGEOM", None)])
    maus_gdf = _polygons_gdf([("M1", _square(0.0, 0.0, 100.0))])

    with pytest.raises(ValueError, match="no usable point geometry.*NOGEOM"):
        build_crosswalk(minedex_gdf, maus_gdf)


def test_build_crosswalk_raises_naming_a_site_whose_coordinates_are_nan() -> None:
    """A register row with null `lon`/`lat` arrives here as `POINT (NaN
    NaN)` -- what `gpd.points_from_xy` builds from NaN, and what the CLI
    hands this function."""
    nan_point = Point(float("nan"), float("nan"))
    minedex_gdf = _sites_gdf_with_geometries([("S1", Point(0.0, 0.0)), ("NANPT", nan_point)])
    maus_gdf = _polygons_gdf([("M1", _square(0.0, 0.0, 100.0))])

    with pytest.raises(ValueError, match="no usable point geometry.*NANPT"):
        build_crosswalk(minedex_gdf, maus_gdf)


def test_build_crosswalk_raises_naming_a_site_whose_coordinates_are_infinite() -> None:
    """Regression: an inf `lon`/`lat` in the register survives `to_crs` as
    inf (unlike NaN, which stays NaN), and an `isna()`-only guard passed it
    through to be emitted as `unmatched` -- reporting a distance test that
    was never computed as one that fired. The guard now tests FINITENESS."""
    inf_point = Point(float("inf"), float("inf"))
    minedex_gdf = _sites_gdf_with_geometries([("S1", Point(0.0, 0.0)), ("INFPT", inf_point)])
    maus_gdf = _polygons_gdf([("M1", _square(0.0, 0.0, 100.0))])

    with pytest.raises(ValueError, match="no usable point geometry.*INFPT"):
        build_crosswalk(minedex_gdf, maus_gdf)


def test_a_nan_point_really_does_break_the_dwithin_pass_the_guard_fronts() -> None:
    """The counterfactual for the NaN-point refusal: the raw `dwithin`
    `sjoin` this guard runs in front of raises `GEOSException` on the same
    input, measured at geopandas 1.1.4 / shapely 2.1.2. If a future version
    stops raising, this test goes red and the guard's stated reason gets
    re-read rather than silently outliving its cause.
    """
    nan_point = Point(float("nan"), float("nan"))
    minedex_gdf = _sites_gdf_with_geometries([("NANPT", nan_point)])
    maus_gdf = _polygons_gdf([("M1", _square(0.0, 0.0, 100.0))])

    with pytest.raises(shapely.errors.GEOSException):
        gpd.sjoin(
            minedex_gdf[["site_id", "geometry"]],
            maus_gdf[["maus_id", "geometry"]],
            how="left",
            predicate="dwithin",
            distance=2000.0,
        )


# --- point_in_polygon ----------------------------------------------------------


def test_site_inside_polygon_is_point_in_polygon_high_confidence() -> None:
    minedex_gdf = _sites_gdf([("S1", 0.0, 0.0)])
    maus_gdf = _polygons_gdf([("M1", _square(0.0, 0.0, 100.0))])

    df = build_crosswalk(minedex_gdf, maus_gdf)

    assert list(df.columns) == _EXPECTED_COLUMNS
    assert len(df) == 1
    row = df.iloc[0]
    assert row["site_id"] == "S1"
    assert row["maus_id"] == "M1"
    assert row["match_method"] == MATCH_POINT_IN_POLYGON
    assert row["distance_m"] == 0.0
    assert row["confidence"] == "high"
    assert row["ambiguity_n"] == 1
    assert row["shared_by_n"] == 1
    assert row["manual_review_status"] == "unreviewed"


# --- nearest-within-2000m, single candidate ------------------------------------


def test_site_within_2000m_of_exactly_one_polygon_is_medium_confidence() -> None:
    # Site at (5000, 0); polygon's nearest edge sits at x=6000 -> 1000 m away,
    # well inside the 2000 m bound, and the site is NOT inside the polygon.
    minedex_gdf = _sites_gdf([("S2", 5000.0, 0.0)])
    maus_gdf = _polygons_gdf([("M2", _square(6050.0, 0.0, 50.0))])

    df = build_crosswalk(minedex_gdf, maus_gdf)

    assert len(df) == 1
    row = df.iloc[0]
    assert row["site_id"] == "S2"
    assert row["maus_id"] == "M2"
    assert row["match_method"] == MATCH_NEAREST_WITHIN_2000M
    assert row["distance_m"] == pytest.approx(1000.0)
    assert row["distance_m"] > 0.0
    assert row["confidence"] == "medium"
    assert row["ambiguity_n"] == 1
    assert row["shared_by_n"] == 1


# --- nearest-within-2000m, two tied candidates ---------------------------------


def test_site_within_2000m_of_two_polygons_is_low_confidence_one_row_each() -> None:
    # Site at (0, 0); polygon A's nearest edge sits 500 m away, polygon B's
    # 1,500 m away -- distinct, non-tied distances, BOTH within the 2000 m
    # bound. This is the real-geometry case design doc §3/T10 Step 1
    # requires ("a site within 2,000 m of TWO polygons"): the population is
    # every candidate within the bound, not merely a nearest-distance tie.
    minedex_gdf = _sites_gdf([("S3", 0.0, 0.0)])
    maus_gdf = _polygons_gdf(
        [
            ("M3A", _square(550.0, 0.0, 50.0)),  # nearest edge at x=500
            ("M3B", _square(1550.0, 0.0, 50.0)),  # nearest edge at x=1500
        ]
    )

    df = build_crosswalk(minedex_gdf, maus_gdf)

    assert len(df) == 2
    assert set(df["site_id"]) == {"S3"}
    assert set(df["maus_id"]) == {"M3A", "M3B"}
    assert (df["match_method"] == MATCH_NEAREST_WITHIN_2000M).all()
    assert (df["confidence"] == "low").all()
    assert (df["ambiguity_n"] == 2).all()
    distance_by_maus_id = dict(zip(df["maus_id"], df["distance_m"], strict=True))
    assert distance_by_maus_id["M3A"] == pytest.approx(500.0)
    assert distance_by_maus_id["M3B"] == pytest.approx(1500.0)
    # Neither polygon is shared with any other site.
    assert (df["shared_by_n"] == 1).all()


def test_site_equidistant_from_two_polygons_is_low_confidence_one_row_each() -> None:
    # The former tie case, kept as its own pin now that the general case
    # above no longer depends on it: an EXACT 950.0/950.0 tie is still just
    # two candidates within the bound, and must still surface as two rows.
    minedex_gdf = _sites_gdf([("S3T", 0.0, 10000.0)])
    maus_gdf = _polygons_gdf(
        [
            ("M3TA", _square(1000.0, 10000.0, 50.0)),
            ("M3TB", _square(-1000.0, 10000.0, 50.0)),
        ]
    )

    df = build_crosswalk(minedex_gdf, maus_gdf)

    assert len(df) == 2
    assert set(df["site_id"]) == {"S3T"}
    assert set(df["maus_id"]) == {"M3TA", "M3TB"}
    assert (df["match_method"] == MATCH_NEAREST_WITHIN_2000M).all()
    assert (df["confidence"] == "low").all()
    assert (df["ambiguity_n"] == 2).all()
    assert df["distance_m"].tolist() == pytest.approx([950.0, 950.0])
    assert (df["shared_by_n"] == 1).all()


# --- many-to-one: two sites inside one polygon ---------------------------------


def test_two_sites_inside_one_polygon_both_kept_shared_by_n_two() -> None:
    minedex_gdf = _sites_gdf([("S4A", 20000.0, 20000.0), ("S4B", 20050.0, 20030.0)])
    maus_gdf = _polygons_gdf([("M4", _square(20000.0, 20000.0, 500.0))])

    df = build_crosswalk(minedex_gdf, maus_gdf)

    assert len(df) == 2
    assert sorted(df["site_id"]) == ["S4A", "S4B"]
    assert (df["maus_id"] == "M4").all()
    assert (df["match_method"] == MATCH_POINT_IN_POLYGON).all()
    assert (df["confidence"] == "high").all()
    assert (df["ambiguity_n"] == 1).all()
    assert (df["shared_by_n"] == 2).all()


# --- unmatched ------------------------------------------------------------------


def test_site_farther_than_2000m_from_every_polygon_is_unmatched() -> None:
    minedex_gdf = _sites_gdf([("S5", 0.0, 0.0)])
    maus_gdf = _polygons_gdf([("M5", _square(5050.0, 5050.0, 50.0))])

    df = build_crosswalk(minedex_gdf, maus_gdf)

    assert len(df) == 1
    row = df.iloc[0]
    assert row["site_id"] == "S5"
    assert pd.isna(row["maus_id"])
    assert row["match_method"] == MATCH_UNMATCHED
    assert pd.isna(row["distance_m"])
    assert row["confidence"] == "none"
    assert row["ambiguity_n"] == 0
    assert row["shared_by_n"] == 1


def test_site_farther_than_2000m_stays_unmatched_when_no_polygons_exist() -> None:
    """The zero-Maus-polygons edge case: every site must still be classified,
    never dropped or raise, when `maus_gdf` carries no features at all."""
    minedex_gdf = _sites_gdf([("S6", 0.0, 0.0)])
    maus_gdf = _polygons_gdf([])

    df = build_crosswalk(minedex_gdf, maus_gdf)

    assert len(df) == 1
    assert df.iloc[0]["match_method"] == MATCH_UNMATCHED
    assert df.iloc[0]["confidence"] == "none"


# --- every row is unreviewed ----------------------------------------------------


def _combined_fixture() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    minedex_gdf = _sites_gdf(
        [
            ("S1", 0.0, 0.0),
            ("S2", 5000.0, 0.0),
            ("S3", 0.0, 10000.0),
            ("S5", 0.0, 0.0),  # deliberately placed far from every polygon below
        ]
    )
    minedex_gdf.loc[minedex_gdf["site_id"] == "S5", "geometry"] = [Point(-50000.0, -50000.0)]
    maus_gdf = _polygons_gdf(
        [
            ("M1", _square(0.0, 0.0, 100.0)),
            ("M2", _square(6050.0, 0.0, 50.0)),
            ("M3A", _square(1000.0, 10000.0, 50.0)),
            ("M3B", _square(-1000.0, 10000.0, 50.0)),
        ]
    )
    return minedex_gdf, maus_gdf


def test_every_row_is_unreviewed() -> None:
    minedex_gdf, maus_gdf = _combined_fixture()
    df = build_crosswalk(minedex_gdf, maus_gdf)
    assert (df["manual_review_status"] == "unreviewed").all()


def test_crosswalk_declared_schema_matches_output_columns() -> None:
    assert list(CROSSWALK_SCHEMA.names) == _EXPECTED_COLUMNS
    assert CROSSWALK_SCHEMA.field("maus_id").nullable
    assert CROSSWALK_SCHEMA.field("distance_m").nullable


def test_crosswalk_frame_carries_no_geometry_and_passes_the_export_gate() -> None:
    minedex_gdf, maus_gdf = _combined_fixture()
    df = build_crosswalk(minedex_gdf, maus_gdf)
    assert export_gate.has_geometry(df) is False


# --- deterministic ordering -----------------------------------------------------


def test_build_crosswalk_output_is_sorted_by_site_id_then_maus_id_nulls_last() -> None:
    minedex_gdf, maus_gdf = _combined_fixture()
    df = build_crosswalk(minedex_gdf, maus_gdf)

    sort_key = list(zip(df["site_id"], df["maus_id"].isna(), df["maus_id"].fillna(""), strict=True))
    assert sort_key == sorted(sort_key)


def test_build_crosswalk_is_deterministic_across_shuffled_input() -> None:
    minedex_gdf, maus_gdf = _combined_fixture()

    first = build_crosswalk(
        minedex_gdf.iloc[::-1].reset_index(drop=True),
        maus_gdf.iloc[::-1].reset_index(drop=True),
    )
    second = build_crosswalk(
        minedex_gdf.sample(frac=1, random_state=7).reset_index(drop=True),
        maus_gdf.sample(frac=1, random_state=11).reset_index(drop=True),
    )

    pd.testing.assert_frame_equal(first, second)


def test_build_crosswalk_called_twice_over_the_same_input_is_byte_identical() -> None:
    minedex_gdf, maus_gdf = _combined_fixture()
    first = build_crosswalk(minedex_gdf, maus_gdf)
    second = build_crosswalk(minedex_gdf, maus_gdf)
    pd.testing.assert_frame_equal(first, second)


# --- crosswalk_counts -----------------------------------------------------------


def test_crosswalk_counts_reconciles_distinct_sites_and_reports_row_total() -> None:
    minedex_gdf, maus_gdf = _combined_fixture()
    df = build_crosswalk(minedex_gdf, maus_gdf)

    counts = crosswalk_counts(df)

    assert list(CONFIDENCE_LEVELS) == ["high", "medium", "low", "none"]
    assert counts["high"] == 1  # S1
    assert counts["medium"] == 1  # S2
    assert counts["low"] == 1  # S3 (2 rows, 1 distinct site)
    assert counts["none"] == 1  # S5
    assert counts[register.TOTAL_KEY] == 4
    assert counts["row_total"] == 5  # S3 contributes 2 rows

    reconcilable = {key: value for key, value in counts.items() if key != "row_total"}
    assert register.reconcile_counts(reconcilable) == reconcilable


# --- tier1_population -------------------------------------------------------------


def test_tier1_population_returns_only_high_confidence_rows() -> None:
    minedex_gdf, maus_gdf = _combined_fixture()
    df = build_crosswalk(minedex_gdf, maus_gdf)
    counts = crosswalk_counts(df)

    population = tier1_population(df)

    assert set(population["site_id"]) == {"S1"}
    assert (population["confidence"] == "high").all()
    # medium/low/none are excluded, and their counts are independently
    # reported by crosswalk_counts rather than silently discarded.
    assert counts["medium"] == 1
    assert counts["low"] == 1
    assert counts["none"] == 1
    assert "S2" not in set(population["site_id"])
    assert "S3" not in set(population["site_id"])
    assert "S5" not in set(population["site_id"])


# --- build-crosswalk CLI command -----------------------------------------------


def _write_config(tmp_path: Path, data_root: Path) -> Path:
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{data_root}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg_file


def _seed_register(data_root: Path, date_str: str) -> Path:
    """A minimal curated register directly written via `tables.write_table`
    -- this test exercises `build-crosswalk`, not `build-register` (covered
    in its own test file)."""
    output_dir = data_root / "curated" / "register" / date_str
    output_dir.mkdir(parents=True)
    df = pd.DataFrame(
        {
            "site_id": ["R1", "R2"],
            "site_name": ["Site One", "Site Two"],
            "commodity": ["Bauxite", "Gold"],
            "stage": ["Operating", "Operating"],
            "owners_at_snapshot": ["Acme", "Acme"],
            "snapshot_date": [date_str, date_str],
            # R1 sits inside the Maus polygon below; R2 is far away (Kalgoorlie-ish).
            "lon": [116.0, 121.5],
            "lat": [-32.0, -30.7],
            "n_tenements_intersecting": [0, 0],
            "inclusion_status": ["operating", "operating"],
        }
    )
    write_table(df, output_dir / "register.parquet", register.REGISTER_SCHEMA)
    return output_dir


def _seed_maus_extract(data_root: Path, date_str: str, *, finalize: bool = True) -> Path:
    """`finalize=False` leaves the snapshot WITHOUT a `SHA256SUMS.txt` --
    the state an interrupted `fetch-maus-extract` leaves behind, which
    `build-crosswalk`'s integrity gate must refuse to read."""
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "maus_v2", date_str)
    gdf = gpd.GeoDataFrame(
        {"maus_id": ["MAUS001"]},
        # A small square in degrees around R1's exact lon/lat -- reprojection
        # to EPSG:3577 preserves containment.
        geometry=[
            Polygon(
                [
                    (115.995, -32.005),
                    (116.005, -32.005),
                    (116.005, -31.995),
                    (115.995, -31.995),
                ]
            )
        ],
        crs="EPSG:4326",
    )
    gdf.to_file(snapshot_dir / "wa_extract.gpkg", driver="GPKG", layer="wa_extract")
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="Maus et al. v2 WA extract",
        endpoint="https://example.test/maus",
        licence_note="CC-BY-SA-4.0",
        purpose="test fixture",
    )
    if finalize:
        snapshots.finalize_snapshot(snapshot_dir)
    return snapshot_dir


def test_build_crosswalk_cli_writes_parquet_counts_and_manifest_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_register(data_root, "2026-08-14")
    _seed_maus_extract(data_root, "2026-08-15")

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )
    assert result.exit_code == 0, result.output

    output_dir = data_root / "curated" / "crosswalk" / "2026-08-16"
    crosswalk_path = output_dir / "crosswalk.parquet"
    assert crosswalk_path.is_file()

    written = pd.read_parquet(crosswalk_path)
    assert list(written.columns) == _EXPECTED_COLUMNS
    assert export_gate.has_geometry(written) is False

    r1_row = written.loc[written["site_id"] == "R1"].iloc[0]
    assert r1_row["maus_id"] == "MAUS001"
    assert r1_row["match_method"] == MATCH_POINT_IN_POLYGON
    assert r1_row["confidence"] == "high"

    r2_row = written.loc[written["site_id"] == "R2"].iloc[0]
    assert pd.isna(r2_row["maus_id"])
    assert r2_row["match_method"] == MATCH_UNMATCHED
    assert r2_row["confidence"] == "none"

    counts_path = output_dir / "crosswalk_counts.json"
    assert counts_path.is_file()
    counts = json.loads(counts_path.read_text())
    assert counts["high"] == 1
    assert counts["none"] == 1
    assert counts[register.TOTAL_KEY] == 2

    manifest_path = Path(str(crosswalk_path) + ".run_manifest.json")
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["resolved_args"]["counts"]["total"] == 2
    assert len(manifest["inputs"]) == 2
    # The Maus snapshot's (ok, bad, missing) verification triple is recorded
    # -- the fixture snapshot holds exactly two hashed files (the GeoPackage
    # and metadata.txt).
    assert manifest["resolved_args"]["maus_snapshot_verification"] == {
        "n_ok": 2,
        "n_bad": 0,
        "n_missing": 0,
    }
    # Regression: `register_dir`/`maus_snapshot_dir` used to be written into
    # `resolved_args` as absolute account paths -- `write_run_manifest`
    # scrubs `resolved_args` for secrets but does not root-relativise it.
    # Both are now reduced via `manifests.root_relative_path`, the same
    # treatment `fetch-maus-extract` gives `resolved_args.source_local_path`.
    assert manifest["resolved_args"]["register_dir"] == "curated/register/2026-08-14"
    assert manifest["resolved_args"]["register_dir_root"] == "data_root"
    assert manifest["resolved_args"]["maus_snapshot_dir"] == "raw/maus_v2/2026-08-15"
    assert manifest["resolved_args"]["maus_snapshot_dir_root"] == "data_root"
    for key, value in manifest["resolved_args"].items():
        if isinstance(value, str):
            assert not value.startswith("/"), f"resolved_args[{key!r}] is an absolute path"


def test_build_crosswalk_cli_refuses_when_no_register_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_maus_extract(data_root, "2026-08-15")

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )
    assert result.exit_code == 1
    assert "register" in result.output


def test_build_crosswalk_cli_refuses_when_no_maus_extract_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_register(data_root, "2026-08-14")

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )
    assert result.exit_code == 1
    assert "maus_v2" in result.output


# --- build-crosswalk CLI snapshot-integrity gate --------------------------------
#
# Same regression `tests/test_register.py` records for `build-register`:
# `register.latest_snapshot` selects the Maus snapshot by DATE alone, so
# under the old wiring an unfinalized or tampered `raw/maus_v2/<date>/`
# directory silently became the crosswalk's source (reproduced during review
# with a drop-in `maus_id = "UNVALIDATED"` matched at confidence high).
# `cli._verify_snapshot_or_refuse` now gates the read.


def test_build_crosswalk_cli_refuses_an_unfinalized_maus_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_register(data_root, "2026-08-14")
    # An older, finalized snapshot exists; the LATEST one was never
    # finalized -- date-only selection walks straight into it.
    _seed_maus_extract(data_root, "2026-08-01")
    _seed_maus_extract(data_root, "2026-08-15", finalize=False)

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert "never finalized" in payload["refusal"]
    assert payload["source_id"] == "maus_v2"
    assert payload["snapshot_dir"].endswith("2026-08-15")
    assert not (data_root / "curated" / "crosswalk" / "2026-08-16").exists()


def test_build_crosswalk_cli_refuses_a_tampered_maus_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewed reproduction: a file altered after finalize (standing in
    for a dropped-in unvalidated extract) refuses the build with the three
    verification counts named, before any matching happens."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_register(data_root, "2026-08-14")
    snapshot_dir = _seed_maus_extract(data_root, "2026-08-15")
    (snapshot_dir / "metadata.txt").write_text("tampered after finalize\n")

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "integrity verification" in payload["refusal"]
    assert payload["n_ok"] == 1
    assert payload["n_bad"] == 1
    assert payload["n_missing"] == 0
    assert not (data_root / "curated" / "crosswalk" / "2026-08-16").exists()


def test_build_crosswalk_cli_refuses_a_maus_gpkg_dropped_in_after_finalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewed reproduction this batch's finding closes: finalize
    `maus_v2` WITHOUT `wa_extract.gpkg`, then drop one in afterwards.
    `SHA256SUMS.txt` has no opinion on a file it never hashed, so
    `verify_snapshot` alone reported `{n_ok: 1, n_bad: 0, n_missing: 0}`
    (metadata.txt only) and `build-crosswalk` read the never-hashed
    GeoPackage, matching a `maus_id = "UNVALIDATED"` at confidence high with
    a manifest asserting a verification that never touched the file the
    crosswalk was actually built from.
    `required_files=("wa_extract.gpkg",)` now refuses this before any read."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_register(data_root, "2026-08-14")

    snapshot_dir = snapshots.create_snapshot_dir(data_root, "maus_v2", "2026-08-15")
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="Maus et al. v2 WA extract",
        endpoint="https://example.test/maus",
        licence_note="CC-BY-SA-4.0",
        purpose="test fixture",
    )
    snapshots.finalize_snapshot(snapshot_dir)  # no wa_extract.gpkg present yet

    # Drop the GeoPackage in AFTER finalize -- SHA256SUMS.txt was already
    # written and has no opinion on this file.
    gdf = gpd.GeoDataFrame(
        {"maus_id": ["UNVALIDATED"]},
        geometry=[
            Polygon(
                [
                    (115.995, -32.005),
                    (116.005, -32.005),
                    (116.005, -31.995),
                    (115.995, -31.995),
                ]
            )
        ],
        crs="EPSG:4326",
    )
    gdf.to_file(snapshot_dir / "wa_extract.gpkg", driver="GPKG", layer="wa_extract")

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert "wa_extract.gpkg" in payload["refusal"]
    assert payload["missing_required_files"] == ["wa_extract.gpkg"]
    assert payload["source_id"] == "maus_v2"
    assert not (data_root / "curated" / "crosswalk" / "2026-08-16").exists()


def _seed_register_with_an_unlocatable_row(data_root: Path, date_str: str) -> Path:
    """A curated register carrying one row whose `lon`/`lat` are null -- what
    `build-register` writes for a MINEDEX record with no geometry, and what
    it discloses in its own `reconciliation.md`."""
    output_dir = data_root / "curated" / "register" / date_str
    output_dir.mkdir(parents=True)
    df = pd.DataFrame(
        {
            "site_id": ["R1", "NOLOC"],
            "site_name": ["Site One", "Unlocatable Site"],
            "commodity": ["Bauxite", "Gold"],
            "stage": ["Operating", "Operating"],
            "owners_at_snapshot": ["Acme", "Acme"],
            "snapshot_date": [date_str, date_str],
            "lon": [116.0, float("nan")],
            "lat": [-32.0, float("nan")],
            "n_tenements_intersecting": [0, 0],
            "inclusion_status": ["operating", "operating"],
        }
    )
    write_table(df, output_dir / "register.parquet", register.REGISTER_SCHEMA)
    return output_dir


def test_build_crosswalk_cli_excludes_a_register_row_with_no_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A null-`lon`/`lat` register row used to reach the CLI as
    `POINT (NaN NaN)` and kill the `dwithin` pass with a bare
    `GEOSException` -- an uncaught traceback with empty stdout, then later a
    structured refusal naming the site and writing nothing at all.
    `crosswalk.filter_register_for_crosswalk` now excludes it from the
    crosswalk's input population BEFORE geometry is built (this command's
    own docstring), so the run succeeds, the located sibling site is scored
    normally, and the exclusion is disclosed as a count rather than either
    crashing or refusing the whole run on a measured property of a healthy
    source."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_register_with_an_unlocatable_row(data_root, "2026-08-14")
    _seed_maus_extract(data_root, "2026-08-15")

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert payload["crosswalk_population"] == {
        "n_excluded_null_site_id": 0,
        "n_excluded_duplicate_site_id": 0,
        "n_excluded_no_usable_location": 1,
        "n_excluded_total": 1,
        "n_crosswalk_input_rows": 1,
    }

    output_dir = data_root / "curated" / "crosswalk" / "2026-08-16"
    written = pd.read_parquet(output_dir / "crosswalk.parquet")
    # Only R1 (located) is scored -- NOLOC never reaches build_crosswalk.
    assert list(written["site_id"]) == ["R1"]

    manifest = json.loads((output_dir / "crosswalk.parquet.run_manifest.json").read_text())
    assert manifest["resolved_args"]["crosswalk_population"] == payload["crosswalk_population"]


def _seed_register_with_a_duplicate_site_id(data_root: Path, date_str: str) -> Path:
    """A curated register carrying two rows sharing the same `site_id` --
    the measured `Sites.csv` `SiteCode` duplication property
    (`register.site_id_duplication_counts`'s docstring), reproduced at the
    curated-register layer `build-crosswalk` reads."""
    output_dir = data_root / "curated" / "register" / date_str
    output_dir.mkdir(parents=True)
    df = pd.DataFrame(
        {
            "site_id": ["R1", "R1", "R2"],
            "site_name": ["Site One A", "Site One B", "Site Two"],
            "commodity": ["Bauxite", "Bauxite", "Gold"],
            "stage": ["Operating", "Operating", "Operating"],
            "owners_at_snapshot": ["Acme", "Acme", "Acme"],
            "snapshot_date": [date_str, date_str, date_str],
            "lon": [116.0, 116.0, 121.5],
            "lat": [-32.0, -32.0, -30.7],
            "n_tenements_intersecting": [0, 0, 0],
            "inclusion_status": ["operating", "operating", "operating"],
        }
    )
    write_table(df, output_dir / "register.parquet", register.REGISTER_SCHEMA)
    return output_dir


def test_build_crosswalk_cli_excludes_a_duplicate_site_id_rather_than_refusing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicated `site_id` used to hard-refuse `build-crosswalk`'s whole
    run (`crosswalk._assert_no_null_or_duplicate_site_ids`) on a measured
    property of a healthy real MINEDEX extract. `filter_register_for_
    crosswalk` now excludes both R1 rows and scores R2 normally."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_register_with_a_duplicate_site_id(data_root, "2026-08-14")
    _seed_maus_extract(data_root, "2026-08-15")

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["crosswalk_population"] == {
        "n_excluded_null_site_id": 0,
        "n_excluded_duplicate_site_id": 2,
        "n_excluded_no_usable_location": 0,
        "n_excluded_total": 2,
        "n_crosswalk_input_rows": 1,
    }

    output_dir = data_root / "curated" / "crosswalk" / "2026-08-16"
    written = pd.read_parquet(output_dir / "crosswalk.parquet")
    assert list(written["site_id"]) == ["R2"]


# --- build-crosswalk CLI re-run guard -------------------------------------------
#
# Regression for the finding that `build-crosswalk` had no re-run guard --
# the same shape as `build-register`'s (see `tests/test_register.py`): a
# second run against an already-built `--date` overwrote `crosswalk.parquet`
# and `crosswalk_counts.json`, then raised an uncaught `FileExistsError` out
# of `write_run_manifest`, leaving an artefact on disk that no longer
# matched its own manifest.


def _seed_maus_extract_moved(data_root: Path, date_str: str) -> Path:
    """A second, later Maus extract snapshot whose polygon sits somewhere
    ELSE -- so a re-run picking it up (via `latest_snapshot`) would produce a
    materially different `crosswalk.parquet` than the first run's, exactly
    the drift the guard exists to catch."""
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "maus_v2", date_str)
    gdf = gpd.GeoDataFrame(
        {"maus_id": ["MAUS999"]},
        # Nowhere near R1 or R2 -- both sites would come back `unmatched`.
        geometry=[
            Polygon(
                [
                    (100.0, -20.0),
                    (100.01, -20.0),
                    (100.01, -19.99),
                    (100.0, -19.99),
                ]
            )
        ],
        crs="EPSG:4326",
    )
    gdf.to_file(snapshot_dir / "wa_extract.gpkg", driver="GPKG", layer="wa_extract")
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="Maus et al. v2 WA extract",
        endpoint="https://example.test/maus",
        licence_note="CC-BY-SA-4.0",
        purpose="test fixture",
    )
    snapshots.finalize_snapshot(snapshot_dir)
    return snapshot_dir


def test_build_crosswalk_cli_refuses_a_rerun_against_an_already_built_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_register(data_root, "2026-08-14")
    _seed_maus_extract(data_root, "2026-08-15")

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    first = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )
    assert first.exit_code == 0, first.output

    crosswalk_path = data_root / "curated" / "crosswalk" / "2026-08-16" / "crosswalk.parquet"
    manifest_path = Path(str(crosswalk_path) + ".run_manifest.json")
    original_bytes = crosswalk_path.read_bytes()
    original_manifest_bytes = manifest_path.read_bytes()

    # A newer Maus extract snapshot lands after the first build.
    _seed_maus_extract_moved(data_root, "2026-08-16")

    second = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )

    assert second.exit_code == 1
    assert "Traceback" not in second.output
    payload = json.loads(second.output)
    assert "refusal" in payload
    assert str(crosswalk_path) in second.output

    assert crosswalk_path.read_bytes() == original_bytes
    assert manifest_path.read_bytes() == original_manifest_bytes


# --- build-crosswalk CLI structured refusals, never tracebacks -----------------
#
# Regression for the finding that `build_crosswalk_cmd` read `register.
# parquet` (`pd.read_parquet`) and the Maus WA extract (`gpd.read_file`) with
# no exception wrapping at all -- unlike `build_register_cmd`, whose reads of
# `minedex.gpkg`/`tenements.gpkg` are wrapped in
# `except (pyogrio.errors.DataSourceError, OSError)` rendering a structured
# JSON refusal. A found `curated/register/<date>/` directory missing
# `register.parquet`, a verified `maus_v2` snapshot missing `wa_extract.gpkg`
# by NAME, and a `register.parquet` missing `lon`/`lat` all died as uncaught
# tracebacks with empty stdout before this wrapper existed.


def test_build_crosswalk_cli_refuses_a_register_dir_missing_the_parquet_as_structured_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `curated/register/<date>/` directory exists (so `_latest_curated_
    dated_dir` finds it) but `register.parquet` itself is absent -- the
    state a failed `build-register` write can leave, since it `mkdir`s the
    output directory before `write_table` runs."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    (data_root / "curated" / "register" / "2026-08-14").mkdir(parents=True)
    _seed_maus_extract(data_root, "2026-08-15")

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert "refusal" in payload
    assert payload["register_parquet"].endswith("register.parquet")
    assert not (data_root / "curated" / "crosswalk" / "2026-08-16").exists()


def test_build_crosswalk_cli_refuses_a_maus_gpkg_named_something_else_as_structured_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finalized `maus_v2` snapshot whose GeoPackage is not literally named
    `wa_extract.gpkg` is now refused at the integrity gate itself
    (`required_files=("wa_extract.gpkg",)`), before any read is attempted --
    not at the later `gpd.read_file` call. Superseded name/assertions: this
    used to document the OLD behaviour ("verifies clean under
    `verify_snapshot` ... refuse cleanly at the read"), which is exactly the
    reviewed reproduction this batch's finding closes."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_register(data_root, "2026-08-14")
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "maus_v2", "2026-08-15")
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="Maus et al. v2 WA extract",
        endpoint="https://example.test/maus",
        licence_note="CC-BY-SA-4.0",
        purpose="test fixture -- gpkg deliberately absent by name",
    )
    snapshots.finalize_snapshot(snapshot_dir)

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert "wa_extract.gpkg" in payload["refusal"]
    assert payload["missing_required_files"] == ["wa_extract.gpkg"]
    assert payload["source_id"] == "maus_v2"
    assert not (data_root / "curated" / "crosswalk" / "2026-08-16").exists()


def test_build_crosswalk_cli_refuses_a_register_missing_lon_lat_as_structured_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `register.parquet` written outside `register.build_register`'s own
    path (hand-built, or from an older schema) that lacks `lon`/`lat` --
    `register_df["lon"]` raises `KeyError`, previously uncaught."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    output_dir = data_root / "curated" / "register" / "2026-08-14"
    output_dir.mkdir(parents=True)
    pd.DataFrame({"site_id": ["R1"], "site_name": ["Site One"]}).to_parquet(
        output_dir / "register.parquet"
    )
    _seed_maus_extract(data_root, "2026-08-15")

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert "refusal" in payload
    assert "lon" in payload["refusal"]
    assert payload["register_parquet"].endswith("register.parquet")
    assert not (data_root / "curated" / "crosswalk" / "2026-08-16").exists()


def test_build_crosswalk_cli_refuses_a_non_string_site_id_as_structured_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The symmetric case to `build-register`'s: `write_table(df,
    crosswalk_path, CROSSWALK_SCHEMA)` sat outside the structured-refusal
    wrapper too. A `register.parquet` written outside `write_table`'s
    declared-schema path (hand-built, or from an older schema) can carry an
    integer `site_id`, which `build_crosswalk` copies through verbatim and
    `CROSSWALK_SCHEMA`'s `pa.string()` then refuses at the write."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    output_dir = data_root / "curated" / "register" / "2026-08-14"
    output_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "site_id": [1, 2],
            "lon": [116.0, 121.5],
            "lat": [-32.0, -30.7],
        }
    ).to_parquet(output_dir / "register.parquet")
    _seed_maus_extract(data_root, "2026-08-15")

    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-16"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert "site_id" in payload["refusal"]
    assert "int64" in payload["refusal"]
    assert payload["output"].endswith("crosswalk.parquet")
    assert payload["register_parquet"].endswith("register.parquet")
    assert payload["partial_output_removed"] is False
    crosswalk_path = data_root / "curated" / "crosswalk" / "2026-08-16" / "crosswalk.parquet"
    assert not crosswalk_path.exists()
