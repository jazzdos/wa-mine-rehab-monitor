"""Tests for the Batch G per-site trajectory summary (design
2026-08-30). Claim-boundary tests carry the same discipline as
test_context_join.py: no column may imply causation, fire three-state
is never widened, sensor overlap is never resolved by priority."""

from __future__ import annotations

import pandas as pd
import pytest

from wa_mine_monitor import (
    climate_context,
    context_join,
    fire_context,
    trajectories,
    trajectory_summary,
)


def _register_df() -> pd.DataFrame:
    rows = [
        ("S1", "eligible", True),
        ("S2", "eligible", False),
        ("S9", "insufficient_pixel_support", False),
    ]
    return pd.DataFrame(
        {
            "site_id": [r[0] for r in rows],
            "trajectory_status": [r[1] for r in rows],
            "d3_forced_threshold": pd.array([r[2] for r in rows], dtype="boolean"),
            "lon": [116.0, 117.0, 118.0],
            "lat": [-32.0, -33.0, -31.0],
        }
    )


def _traj_row(site: str, year: int, metric: str, **over: object) -> dict:
    row: dict = {
        "site_id": site,
        "maus_id": "M1" if site == "S1" else "M2",
        "year": year,
        "metric": metric,
        "value": 0.5,
        "computable": True,
        "collection_id": "ga_ls8cls9c",
        "shared_footprint_site_count": 2 if site == "S1" else 1,
        "d3_forced_threshold": site == "S1",
    }
    row.update(over)
    return row


def _traj_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _context_row(site: str, year: int, **over: object) -> dict:
    row: dict = {
        "site_id": site,
        "maus_id": "M1" if site == "S1" else "M2",
        "year": year,
        "context_row_status": context_join.CONTEXT_ROW_JOINED,
        "context_complete": True,
        "fire_status": "not_recorded",
        "climate_status": climate_context.CLIMATE_STATUS_COMPUTED,
        "annual_rainfall_mm": 400.0,
    }
    row.update(over)
    return row


def _context_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _small_world() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    traj = _traj_df(
        [
            _traj_row("S1", 2000, "nbr"),
            _traj_row("S1", 2001, "nbr", value=0.7),
            _traj_row("S1", 2001, "ndmi", value=None, computable=False),
            _traj_row("S2", 2000, "nbr", value=0.2),
            _traj_row("S2", 2001, "nbr", value=0.3),
        ]
    )
    ctx = _context_df(
        [
            _context_row("S1", 2000),
            _context_row("S1", 2001, fire_status=fire_context.FIRE_STATUS_RECORDED),
            _context_row(
                "S2",
                2000,
                context_complete=False,
                climate_status="not_computable",
                annual_rainfall_mm=None,
            ),
            _context_row("S2", 2001),
        ]
    )
    return _register_df(), traj, ctx


def test_summary_columns_are_pinned_and_carry_no_causal_names() -> None:
    for name in trajectory_summary.SUMMARY_COLUMNS:
        assert not any(frag in name.lower() for frag in context_join.FORBIDDEN_NAME_FRAGMENTS), name
    for metric in trajectories.METRICS:
        assert f"{metric}_latest" in trajectory_summary.SUMMARY_COLUMNS
        assert f"{metric}_latest_year" in trajectory_summary.SUMMARY_COLUMNS
        assert f"{metric}_latest_collections" in trajectory_summary.SUMMARY_COLUMNS


def test_assemble_is_one_row_per_eligible_site_with_disclosures() -> None:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(register_df=register, traj_df=traj, context_df=ctx)
    assert list(df.columns) == list(trajectory_summary.SUMMARY_COLUMNS)
    assert sorted(df["site_id"]) == ["S1", "S2"]  # S9 is not eligible
    s1 = df.set_index("site_id").loc["S1"]
    assert s1["maus_id"] == "M1"
    assert s1["shared_footprint_site_count"] == 2
    assert bool(s1["d3_forced_threshold"]) is True
    assert s1["trajectory_status"] == "eligible"


def test_assemble_coverage_counts() -> None:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s1 = df.loc["S1"]
    assert (s1["year_min"], s1["year_max"]) == (2000, 2001)
    assert s1["years_observed"] == 2
    assert s1["years_computable"] == 2  # nbr computable in both years
    assert s1["years_not_computable"] == 0
    assert s1["context_complete_years"] == 2
    assert df.loc["S2"]["context_complete_years"] == 1


def test_assemble_refuses_site_set_mismatch() -> None:
    register, traj, ctx = _small_world()
    with pytest.raises(trajectory_summary.TrajectorySummaryError):
        trajectory_summary.assemble_summary(
            register_df=register, traj_df=traj[traj["site_id"] != "S2"], context_df=ctx
        )


def test_assemble_refuses_conflicting_per_site_disclosures() -> None:
    register, traj, ctx = _small_world()
    traj = traj.copy()
    traj.loc[traj.index[-1], "shared_footprint_site_count"] = 99
    traj.loc[traj.index[-1], "site_id"] = "S1"
    traj.loc[traj.index[-1], "maus_id"] = "M1"
    with pytest.raises(trajectory_summary.TrajectorySummaryError):
        trajectory_summary.assemble_summary(register_df=register, traj_df=traj, context_df=ctx)


def test_metric_latest_takes_the_latest_computable_year() -> None:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s1 = df.loc["S1"]
    assert s1["nbr_latest_year"] == 2001
    assert s1["nbr_latest"] == 0.7
    assert s1["nbr_latest_collections"] == 1
    # ndmi has no computable row for S1 -> all three NULL.
    assert pd.isna(s1["ndmi_latest"])
    assert pd.isna(s1["ndmi_latest_year"])
    assert pd.isna(s1["ndmi_latest_collections"])


def test_sensor_overlap_at_latest_year_is_disclosed_never_resolved() -> None:
    register, traj, ctx = _small_world()
    traj = pd.concat(
        [traj, _traj_df([_traj_row("S1", 2001, "nbr", value=0.9, collection_id="ga_ls7e")])],
        ignore_index=True,
    )
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s1 = df.loc["S1"]
    assert s1["nbr_latest_year"] == 2001
    assert s1["nbr_latest_collections"] == 2
    assert pd.isna(s1["nbr_latest"])  # neither 0.7 nor 0.9 wins


def test_fire_three_state_is_preserved_and_never_widened() -> None:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s1, s2 = df.loc["S1"], df.loc["S2"]
    assert s1["fire_status_latest"] == fire_context.FIRE_STATUS_RECORDED
    assert s1["fire_years_recorded"] == 1
    assert s1["last_recorded_fire_year"] == 2001
    # S2: no recorded fire. The count is a genuine 0 (the record was
    # consulted); the year is NULL, never a fabricated known-negative.
    assert s2["fire_status_latest"] == "not_recorded"
    assert s2["fire_years_recorded"] == 0
    assert pd.isna(s2["last_recorded_fire_year"])


def test_no_context_rows_leave_context_fields_null_not_zeroed() -> None:
    register, traj, ctx = _small_world()
    ctx = ctx.copy()
    ctx["context_row_status"] = context_join.CONTEXT_ROW_NO_CONTEXT
    ctx["context_complete"] = False
    for col in ("fire_status", "climate_status", "annual_rainfall_mm"):
        ctx[col] = None
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s1 = df.loc["S1"]
    assert pd.isna(s1["fire_status_latest"])
    assert s1["fire_years_recorded"] == 0
    assert pd.isna(s1["last_recorded_fire_year"])
    assert pd.isna(s1["rainfall_annual_mean"])
    assert pd.isna(s1["rainfall_latest"])
    assert s1["context_complete_years"] == 0


def test_climate_summary_uses_computed_rows_only() -> None:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s2 = df.loc["S2"]
    # S2's 2000 row is not_computable: the mean and latest come from
    # 2001 alone.
    assert s2["rainfall_annual_mean"] == 400.0
    assert s2["rainfall_latest"] == 400.0
    assert s2["rainfall_latest_year"] == 2001


def _valid_summary() -> tuple[pd.DataFrame, list[str]]:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(register_df=register, traj_df=traj, context_df=ctx)
    return df, ["S1", "S2"]


def test_validate_accepts_the_assembled_product() -> None:
    df, site_ids = _valid_summary()
    trajectory_summary.validate_summary(df, site_ids=site_ids)


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda df: df.drop(df.index[:1]),  # dropped site
        lambda df: df.drop(columns=["fire_status_latest"]),  # missing column
        lambda df: df.rename(columns={"nbr_latest": "nbr_cause"}),  # causal name
        lambda df: df.assign(trajectory_status="closed"),  # non-eligible status
        lambda df: df.assign(fire_status_latest="burned"),  # widened vocabulary
        lambda df: df.assign(shared_footprint_site_count=pd.NA),  # null disclosure
        lambda df: df.assign(  # resolved overlap
            nbr_latest_collections=pd.array([2, 1], dtype="Int64")
        ),
    ],
)
def test_validate_catches_each_violation(corrupt) -> None:
    df, site_ids = _valid_summary()
    with pytest.raises(trajectory_summary.TrajectorySummaryError):
        trajectory_summary.validate_summary(corrupt(df), site_ids=site_ids)


def test_gpkg_round_trip_two_layers(tmp_path) -> None:
    import pyogrio

    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(register_df=register, traj_df=traj, context_df=ctx)
    path = tmp_path / "trajectory_summary.gpkg"
    n_unlocated = trajectory_summary.write_summary_gpkg(
        summary_df=df, register_df=register, path=path
    )
    assert n_unlocated == 0
    layers = {name for name, _ in pyogrio.list_layers(path)}
    assert layers == {trajectory_summary.REGISTER_LAYER, trajectory_summary.SUMMARY_LAYER}
    import geopandas as gpd

    summary = gpd.read_file(path, layer=trajectory_summary.SUMMARY_LAYER)
    assert len(summary) == 2
    assert summary.crs is not None and summary.crs.to_epsg() == 4326
    # EXACT pinned column set (design section 3): nothing extra may ride
    # along, nothing pinned may be dropped by the gpkg round trip.
    assert set(summary.columns) == {*trajectory_summary.SUMMARY_COLUMNS, "geometry"}
    register_layer = gpd.read_file(path, layer=trajectory_summary.REGISTER_LAYER)
    assert len(register_layer) == 3  # S9 included: located, ineligible
    assert set(register_layer["trajectory_status"]) == {
        "eligible",
        "insufficient_pixel_support",
    }


def test_gpkg_refuses_an_unlocated_eligible_site(tmp_path) -> None:
    register, traj, ctx = _small_world()
    register = register.copy()
    register.loc[register["site_id"] == "S1", "lon"] = None
    df = trajectory_summary.assemble_summary(register_df=register, traj_df=traj, context_df=ctx)
    with pytest.raises(trajectory_summary.TrajectorySummaryError):
        trajectory_summary.write_summary_gpkg(
            summary_df=df, register_df=register, path=tmp_path / "x.gpkg"
        )


def test_gpkg_skips_unlocated_ineligible_sites_and_discloses_the_count(tmp_path) -> None:
    register, traj, ctx = _small_world()
    register = register.copy()
    register.loc[register["site_id"] == "S9", "lat"] = None
    df = trajectory_summary.assemble_summary(register_df=register, traj_df=traj, context_df=ctx)
    path = tmp_path / "trajectory_summary.gpkg"
    n_unlocated = trajectory_summary.write_summary_gpkg(
        summary_df=df, register_df=register, path=path
    )
    assert n_unlocated == 1


def test_qml_styles_reference_only_pinned_fields() -> None:
    """Every field a QML style references must exist in the layer it
    styles -- symbology silently degrades in QGIS when a field is
    renamed, so drift is caught here instead."""
    import re
    import xml.etree.ElementTree as ET
    from pathlib import Path

    styles_dir = Path(__file__).resolve().parents[1] / "qgis" / "styles"
    layer_fields = {
        "register_sites.qml": {"site_id", "trajectory_status", "d3_forced_threshold"},
        "site_summary.qml": set(trajectory_summary.SUMMARY_COLUMNS),
    }
    for name, allowed in layer_fields.items():
        path = styles_dir / name
        root = ET.parse(path).getroot()  # must parse as XML at all
        referenced: set[str] = set()
        for elem in root.iter():
            # A fieldName that is an expression (isExpression="1") is not a
            # bare field reference -- it embeds field names as quoted
            # identifiers inside an expression string, so it is pulled
            # through the regex path below alongside expression/filter/label,
            # never added to `referenced` verbatim.
            field_name = elem.get("fieldName")
            if field_name and elem.get("isExpression") != "1":
                referenced.add(field_name)
            attr = elem.get("attr")
            if attr:
                referenced.add(attr)
            keys = ["expression", "filter", "label"]
            if field_name and elem.get("isExpression") == "1":
                keys.append("fieldName")
            for key in keys:
                value = elem.get(key)
                if value:
                    referenced.update(re.findall(r'"([A-Za-z0-9_]+)"', value))
        unknown = referenced - allowed
        assert not unknown, f"{name} references unknown field(s): {sorted(unknown)}"
        assert referenced, f"{name} references no fields at all"


def test_register_qml_categorises_every_trajectory_status() -> None:
    import xml.etree.ElementTree as ET
    from pathlib import Path

    from wa_mine_monitor.register import _TRAJECTORY_STATUSES

    path = Path(__file__).resolve().parents[1] / "qgis" / "styles" / "register_sites.qml"
    root = ET.parse(path).getroot()
    values = {c.get("value") for c in root.iter("category")}
    assert set(_TRAJECTORY_STATUSES) <= values
