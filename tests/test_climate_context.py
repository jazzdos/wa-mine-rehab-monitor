"""Tests for `wa_mine_monitor.climate_context` -- pure row assembly -- and
for the `build-climate-context` CLI command that reads real SILO/register/
crosswalk/Maus inputs and calls it.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import pytest
from shapely.geometry import Polygon
from typer.testing import CliRunner

from tests.sources.test_silo import write_full_year_nc
from wa_mine_monitor import cli as cli_module
from wa_mine_monitor import (
    climate_context,
    crosswalk,
    export_gate,
    licence,
    manifests,
    register,
    snapshots,
    tables,
)
from wa_mine_monitor.cli import app
from wa_mine_monitor.provenance import SourceAsset, sha256_file
from wa_mine_monitor.sources.silo import AnnualMetrics, annual_object_name
from wa_mine_monitor.tables import write_table

runner = CliRunner()


def test_computable_cell_year_yields_computed_status_and_metrics() -> None:
    df = climate_context.assemble_rows(
        site_maus_pairs=[("S1", "M1")],
        cell_id_by_maus={"M1": "-32.000_116.000"},
        metrics_by_cell_year={
            ("-32.000_116.000", 2020): AnnualMetrics(annual_rainfall_mm=650.0, rain_days_ge_1mm=80)
        },
        not_computable_by_cell_year={},
        baseline_annuals_by_cell={"-32.000_116.000": [500.0, 600.0]},
        baseline_gap_by_cell={},
        years=[2020],
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    assert len(df) == 1
    row = df.iloc[0]
    assert row["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED
    assert row["annual_rainfall_mm"] == 650.0
    assert row["rain_days_ge_1mm"] == 80
    assert row["rainfall_anomaly_mm"] == pytest.approx(650.0 - 550.0)
    assert pd.isna(row["not_computable_reason"])
    assert row["rainfall_baseline_start_year"] == 1991
    assert row["rainfall_baseline_end_year"] == 2020


def test_not_computable_cell_year_yields_nulls_and_is_kept() -> None:
    df = climate_context.assemble_rows(
        site_maus_pairs=[("S1", "M1")],
        cell_id_by_maus={"M1": "-32.000_116.000"},
        metrics_by_cell_year={},
        not_computable_by_cell_year={("-32.000_116.000", 2020): "3 missing daily values"},
        baseline_annuals_by_cell={},
        baseline_gap_by_cell={},
        years=[2020],
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    assert len(df) == 1
    row = df.iloc[0]
    assert row["climate_status"] == climate_context.CLIMATE_STATUS_NOT_COMPUTABLE
    assert pd.isna(row["annual_rainfall_mm"])
    assert pd.isna(row["rain_days_ge_1mm"])
    assert pd.isna(row["rainfall_anomaly_mm"])
    assert row["not_computable_reason"] == "3 missing daily values"
    # Row is KEPT, never dropped (D13 F6).
    assert set(df["site_id"]) == {"S1"}


def test_incomplete_baseline_makes_every_year_of_that_cell_not_computable() -> None:
    df = climate_context.assemble_rows(
        site_maus_pairs=[("S1", "M1")],
        cell_id_by_maus={"M1": "-32.000_116.000"},
        metrics_by_cell_year={
            ("-32.000_116.000", 2020): AnnualMetrics(annual_rainfall_mm=650.0, rain_days_ge_1mm=80),
            ("-32.000_116.000", 2021): AnnualMetrics(annual_rainfall_mm=700.0, rain_days_ge_1mm=90),
        },
        not_computable_by_cell_year={},
        baseline_annuals_by_cell={},
        baseline_gap_by_cell={
            "-32.000_116.000": "baseline missing years 1991-2005 (only 1 of 30 years present)"
        },
        years=[2020, 2021],
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    assert len(df) == 2
    assert (df["climate_status"] == climate_context.CLIMATE_STATUS_NOT_COMPUTABLE).all()
    assert df["annual_rainfall_mm"].isna().all()
    for reason in df["not_computable_reason"]:
        assert "baseline missing years 1991-2005" in reason


def test_two_sites_sharing_one_maus_id_get_same_cell_and_metrics() -> None:
    df = climate_context.assemble_rows(
        site_maus_pairs=[("S1", "M1"), ("S2", "M1")],
        cell_id_by_maus={"M1": "-32.000_116.000"},
        metrics_by_cell_year={
            ("-32.000_116.000", 2020): AnnualMetrics(annual_rainfall_mm=650.0, rain_days_ge_1mm=80)
        },
        not_computable_by_cell_year={},
        baseline_annuals_by_cell={"-32.000_116.000": [500.0, 600.0]},
        baseline_gap_by_cell={},
        years=[2020],
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    assert len(df) == 2
    assert set(df["silo_cell_id"]) == {"-32.000_116.000"}
    assert df["annual_rainfall_mm"].nunique() == 1
    assert df["rainfall_anomaly_mm"].nunique() == 1


def test_frame_conforms_to_schema_round_trip(tmp_path: Path) -> None:
    df = climate_context.assemble_rows(
        site_maus_pairs=[("S1", "M1")],
        cell_id_by_maus={"M1": "-32.000_116.000"},
        metrics_by_cell_year={
            ("-32.000_116.000", 2020): AnnualMetrics(annual_rainfall_mm=650.0, rain_days_ge_1mm=80)
        },
        not_computable_by_cell_year={},
        baseline_annuals_by_cell={"-32.000_116.000": [500.0, 600.0]},
        baseline_gap_by_cell={},
        years=[2020],
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    out_path = tmp_path / "x.parquet"
    tables.write_table(df, out_path, climate_context.CLIMATE_CONTEXT_SCHEMA)
    assert pq.read_schema(out_path).equals(climate_context.CLIMATE_CONTEXT_SCHEMA)


def test_has_no_geometry() -> None:
    df = climate_context.assemble_rows(
        site_maus_pairs=[("S1", "M1")],
        cell_id_by_maus={"M1": "-32.000_116.000"},
        metrics_by_cell_year={
            ("-32.000_116.000", 2020): AnnualMetrics(annual_rainfall_mm=650.0, rain_days_ge_1mm=80)
        },
        not_computable_by_cell_year={},
        baseline_annuals_by_cell={"-32.000_116.000": [500.0, 600.0]},
        baseline_gap_by_cell={},
        years=[2020],
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    assert export_gate.has_geometry(df) is False


def test_missing_cell_mapping_raises_climate_context_error() -> None:
    with pytest.raises(climate_context.ClimateContextError):
        climate_context.assemble_rows(
            site_maus_pairs=[("S1", "M1")],
            cell_id_by_maus={},
            metrics_by_cell_year={},
            not_computable_by_cell_year={},
            baseline_annuals_by_cell={},
            baseline_gap_by_cell={},
            years=[2020],
            snapshot_date="2026-08-26",
            source_version="v1",
        )


# --- build-climate-context CLI command ---------------------------------------
#
# A miniature world under tmp_path: a raw `silo` snapshot (one tiny NetCDF
# per year), a curated D3-eligibility-annotated register, a curated Tier 1
# crosswalk, and a raw `maus_v2` snapshot -- reusing the `tests/test_
# crosswalk.py` seeding idiom (write the curated artefacts directly via
# `tables.write_table` + `manifests.write_run_manifest`, not through the
# CLI commands that build them).
#
# Grid: `write_full_year_nc`'s default LATS/LONS (`tests/sources/test_
# silo.py`) is a 3x3 lattice on 0.05-degree centres, centre cell
# (lat=-32.70, lon=115.65). Every register site sits exactly on that centre
# unless a test needs it OUTSIDE the grid.

_GRID_LAT = -32.70
_GRID_LON = 115.65
_OUTSIDE_LAT = -30.00
_OUTSIDE_LON = 140.00


def _write_config(tmp_path: Path, data_root: Path) -> Path:
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{data_root}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg_file


def _box(lon: float, lat: float, *, delta: float = 0.005) -> Polygon:
    return Polygon(
        [
            (lon - delta, lat - delta),
            (lon + delta, lat - delta),
            (lon + delta, lat + delta),
            (lon - delta, lat + delta),
        ]
    )


def _seed_silo_snapshot(
    data_root: Path, date_str: str, years: list[int], *, finalize: bool = True
) -> Path:
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "silo", date_str)
    for year in years:
        write_full_year_nc(snapshot_dir / annual_object_name("daily_rain", year), year)
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="SILO Climate Database (gridded daily_rain, annual NetCDF)",
        endpoint="https://example.test/silo",
        licence_note="CC-BY-4.0",
        purpose="test fixture",
    )
    if finalize:
        snapshots.finalize_snapshot(snapshot_dir)
    return snapshot_dir


def _eligible_register_row(site_id: str, *, lon: float, lat: float, **overrides: object) -> dict:
    row: dict[str, object] = {
        "site_id": site_id,
        "site_name": f"Site {site_id}",
        "commodity": "Bauxite",
        "stage": "Operating",
        "owners_at_snapshot": "Acme",
        "snapshot_date": "2026-08-10",
        "lon": lon,
        "lat": lat,
        "n_tenements_intersecting": 0,
        "inclusion_status": "operating",
        "n_dea_gm_ls5t_epochs": None,
        "n_dea_gm_ls7e_epochs": None,
        "n_dea_gm_ls8cls9c_epochs": None,
        "n_dea_fc_pc_epochs": None,
        "effective_pixel_support_px": 200,
        "d3_threshold_px": 144,
        "d3_eligible": True,
        "trajectory_status": "eligible",
        "d3_forced_threshold": True,
    }
    row.update(overrides)
    return row


def _seed_eligible_register(data_root: Path, date_str: str, rows: list[dict]) -> Path:
    output_dir = data_root / "curated" / "register" / date_str
    output_dir.mkdir(parents=True)
    df = pd.DataFrame(rows)[list(register.ELIGIBLE_REGISTER_SCHEMA.names)]
    path = output_dir / "register.parquet"
    write_table(df, path, register.ELIGIBLE_REGISTER_SCHEMA)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    return output_dir


def _crosswalk_row(site_id: str, maus_id: str, *, confidence: str = "high") -> dict:
    return {
        "site_id": site_id,
        "maus_id": maus_id,
        "match_method": crosswalk.MATCH_POINT_IN_POLYGON,
        "distance_m": 0.0,
        "confidence": confidence,
        "ambiguity_n": 1,
        "shared_by_n": 1,
        "manual_review_status": "unreviewed",
    }


def _seed_crosswalk(data_root: Path, date_str: str, rows: list[dict], *, maus_sha256: str) -> Path:
    output_dir = data_root / "curated" / "crosswalk" / date_str
    output_dir.mkdir(parents=True)
    df = pd.DataFrame(rows)[list(crosswalk.CROSSWALK_SCHEMA.names)]
    path = output_dir / "crosswalk.parquet"
    write_table(df, path, crosswalk.CROSSWALK_SCHEMA)
    manifests.write_run_manifest(
        output=path,
        inputs=[
            SourceAsset(
                uri="test://maus",
                sha256=maus_sha256,
                licence=licence.SOURCES["maus_v2"].licence_id,
                redistribute_public=True,
            )
        ],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    return output_dir


def _seed_maus_extract(
    data_root: Path, date_str: str, geoms: dict[str, Polygon], *, finalize: bool = True
) -> tuple[Path, str]:
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "maus_v2", date_str)
    gdf = gpd.GeoDataFrame(
        {"maus_id": list(geoms.keys())}, geometry=list(geoms.values()), crs="EPSG:4326"
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
    gpkg_sha256 = sha256_file(snapshot_dir / "wa_extract.gpkg")
    return snapshot_dir, gpkg_sha256


def _seed_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    register_rows: list[dict],
    crosswalk_rows: list[dict],
    maus_geoms: dict[str, Polygon],
    silo_years: list[int],
    finalize_silo: bool = True,
) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )
    _seed_silo_snapshot(data_root, "2026-08-10", silo_years, finalize=finalize_silo)
    _, maus_sha256 = _seed_maus_extract(data_root, "2026-08-14", maus_geoms)
    _seed_eligible_register(data_root, "2026-08-15", register_rows)
    _seed_crosswalk(data_root, "2026-08-16", crosswalk_rows, maus_sha256=maus_sha256)
    return cfg_file, data_root


_BASELINE_YEARS = list(
    range(climate_context.BASELINE_START_YEAR, climate_context.BASELINE_END_YEAR + 1)
)


def test_build_climate_context_cli_writes_parquet_and_manifest_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, data_root = _seed_world(
        tmp_path,
        monkeypatch,
        register_rows=[_eligible_register_row("S1", lon=_GRID_LON, lat=_GRID_LAT)],
        crosswalk_rows=[_crosswalk_row("S1", "MAUS001")],
        maus_geoms={"MAUS001": _box(_GRID_LON, _GRID_LAT)},
        silo_years=_BASELINE_YEARS,
    )
    result = runner.invoke(
        app,
        [
            "build-climate-context",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-20",
            "--start-year",
            "2019",
            "--end-year",
            "2020",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    output_path = (
        data_root / "curated" / "climate-context" / "2026-08-20" / "climate_context.parquet"
    )
    assert output_path.is_file()
    assert payload["output_path"] == str(output_path)
    assert payload["rows"] == 2

    written = pd.read_parquet(output_path)
    assert list(written.columns) == list(climate_context.CLIMATE_CONTEXT_SCHEMA.names)
    assert set(written["year"]) == {2019, 2020}
    assert (written["site_id"] == "S1").all()
    assert (written["maus_id"] == "MAUS001").all()
    assert (written["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED).all()
    assert written["not_computable_reason"].isna().all()
    assert (written["rainfall_baseline_start_year"] == climate_context.BASELINE_START_YEAR).all()
    assert (written["rainfall_baseline_end_year"] == climate_context.BASELINE_END_YEAR).all()

    manifest_path = Path(str(output_path) + manifests.MANIFEST_SUFFIX)
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    silo_asset_uris = {
        Path(asset["uri"]).name
        for asset in manifest["inputs"]
        if asset.get("licence") == licence.SOURCES["silo"].licence_id
    }
    assert silo_asset_uris == {annual_object_name("daily_rain", year) for year in _BASELINE_YEARS}


def test_build_climate_context_refuses_when_output_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )
    output_dir = data_root / "curated" / "climate-context" / "2026-08-20"
    output_dir.mkdir(parents=True)
    (output_dir / "climate_context.parquet").write_bytes(b"not a real parquet file")

    result = runner.invoke(
        app,
        [
            "build-climate-context",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-20",
            "--start-year",
            "2019",
            "--end-year",
            "2020",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "already exists" in payload["refusal"]


def test_build_climate_context_refuses_a_missing_baseline_year(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    years_without_1995 = [y for y in _BASELINE_YEARS if y != 1995]
    cfg_file, data_root = _seed_world(
        tmp_path,
        monkeypatch,
        register_rows=[_eligible_register_row("S1", lon=_GRID_LON, lat=_GRID_LAT)],
        crosswalk_rows=[_crosswalk_row("S1", "MAUS001")],
        maus_geoms={"MAUS001": _box(_GRID_LON, _GRID_LAT)},
        silo_years=years_without_1995,
    )
    result = runner.invoke(
        app,
        [
            "build-climate-context",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-20",
            "--start-year",
            "2019",
            "--end-year",
            "2020",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "1995.daily_rain.nc" in payload["refusal"]
    assert not (data_root / "curated" / "climate-context").exists()


def test_build_climate_context_refuses_a_missing_requested_year(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, data_root = _seed_world(
        tmp_path,
        monkeypatch,
        register_rows=[_eligible_register_row("S1", lon=_GRID_LON, lat=_GRID_LAT)],
        crosswalk_rows=[_crosswalk_row("S1", "MAUS001")],
        maus_geoms={"MAUS001": _box(_GRID_LON, _GRID_LAT)},
        silo_years=_BASELINE_YEARS,  # no 2021
    )
    result = runner.invoke(
        app,
        [
            "build-climate-context",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-20",
            "--start-year",
            "2021",
            "--end-year",
            "2021",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "2021.daily_rain.nc" in payload["refusal"]
    assert not (data_root / "curated" / "climate-context").exists()


def test_build_climate_context_refuses_an_unverified_silo_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, _data_root = _seed_world(
        tmp_path,
        monkeypatch,
        register_rows=[_eligible_register_row("S1", lon=_GRID_LON, lat=_GRID_LAT)],
        crosswalk_rows=[_crosswalk_row("S1", "MAUS001")],
        maus_geoms={"MAUS001": _box(_GRID_LON, _GRID_LAT)},
        silo_years=_BASELINE_YEARS,
        finalize_silo=False,
    )
    result = runner.invoke(
        app,
        [
            "build-climate-context",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-20",
            "--start-year",
            "2019",
            "--end-year",
            "2020",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "never finalized" in payload["refusal"]


def test_build_climate_context_site_maus_tie_break_picks_lexicographically_smallest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, data_root = _seed_world(
        tmp_path,
        monkeypatch,
        register_rows=[_eligible_register_row("S1", lon=_GRID_LON, lat=_GRID_LAT)],
        crosswalk_rows=[
            _crosswalk_row("S1", "ZZZ9"),
            _crosswalk_row("S1", "AAA1"),
        ],
        maus_geoms={
            "AAA1": _box(_GRID_LON, _GRID_LAT),
            "ZZZ9": _box(_GRID_LON, _GRID_LAT),
        },
        silo_years=_BASELINE_YEARS,
    )
    result = runner.invoke(
        app,
        [
            "build-climate-context",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-20",
            "--start-year",
            "2019",
            "--end-year",
            "2020",
        ],
    )
    assert result.exit_code == 0, result.output
    output_path = (
        data_root / "curated" / "climate-context" / "2026-08-20" / "climate_context.parquet"
    )
    written = pd.read_parquet(output_path)
    assert set(written["maus_id"]) == {"AAA1"}


def test_build_climate_context_footprint_outside_grid_is_not_computable_not_aborted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, data_root = _seed_world(
        tmp_path,
        monkeypatch,
        register_rows=[
            _eligible_register_row("S1", lon=_GRID_LON, lat=_GRID_LAT),
            _eligible_register_row("S_OUT", lon=_OUTSIDE_LON, lat=_OUTSIDE_LAT),
        ],
        crosswalk_rows=[
            _crosswalk_row("S1", "MAUS001"),
            _crosswalk_row("S_OUT", "MAUS_OUT"),
        ],
        maus_geoms={
            "MAUS001": _box(_GRID_LON, _GRID_LAT),
            "MAUS_OUT": _box(_OUTSIDE_LON, _OUTSIDE_LAT),
        },
        silo_years=_BASELINE_YEARS,
    )
    result = runner.invoke(
        app,
        [
            "build-climate-context",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-20",
            "--start-year",
            "2019",
            "--end-year",
            "2020",
        ],
    )
    assert result.exit_code == 0, result.output
    output_path = (
        data_root / "curated" / "climate-context" / "2026-08-20" / "climate_context.parquet"
    )
    written = pd.read_parquet(output_path)
    assert len(written) == 4

    out_rows = written.loc[written["site_id"] == "S_OUT"]
    assert len(out_rows) == 2
    assert (out_rows["climate_status"] == climate_context.CLIMATE_STATUS_NOT_COMPUTABLE).all()
    assert out_rows["annual_rainfall_mm"].isna().all()
    assert out_rows["not_computable_reason"].str.contains("outside the SILO grid").all()

    in_rows = written.loc[written["site_id"] == "S1"]
    assert (in_rows["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED).all()


def test_build_climate_context_refuses_an_inverted_year_range_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )
    result = runner.invoke(
        app,
        [
            "build-climate-context",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-20",
            "--start-year",
            "2003",
            "--end-year",
            "2001",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "inverted year range" in payload["refusal"]
    assert not (data_root / "curated" / "climate-context").exists()


def test_build_climate_context_refuses_an_eligible_site_missing_from_the_crosswalk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, data_root = _seed_world(
        tmp_path,
        monkeypatch,
        register_rows=[
            _eligible_register_row("S1", lon=_GRID_LON, lat=_GRID_LAT),
            _eligible_register_row("S_MISSING", lon=_GRID_LON, lat=_GRID_LAT),
        ],
        # S_MISSING has no crosswalk row at all -- a stale register/crosswalk
        # pair that must be refused by name, not silently dropped.
        crosswalk_rows=[_crosswalk_row("S1", "MAUS001")],
        maus_geoms={"MAUS001": _box(_GRID_LON, _GRID_LAT)},
        silo_years=_BASELINE_YEARS,
    )
    result = runner.invoke(
        app,
        [
            "build-climate-context",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-20",
            "--start-year",
            "2019",
            "--end-year",
            "2020",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "S_MISSING" in payload["refusal"]
    assert not (data_root / "curated" / "climate-context").exists()
