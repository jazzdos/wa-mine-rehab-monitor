"""Tests for `wa_mine_monitor.fire_context` -- pure row assembly -- and for
the `build-fire-context` CLI command that reads real DBCA-060/register/
crosswalk/Maus inputs and calls it.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pytest
from shapely.geometry import Polygon
from typer.testing import CliRunner

from tests.sources.test_dbca import write_fire_gpkg
from wa_mine_monitor import cli as cli_module
from wa_mine_monitor import crosswalk, fire_context, licence, manifests, register, snapshots
from wa_mine_monitor.cli import app
from wa_mine_monitor.provenance import SourceAsset, sha256_file
from wa_mine_monitor.tables import write_table


def test_coverage_window_is_frozen_at_1937_to_snapshot_minus_one() -> None:
    assert fire_context.coverage_window(2026) == (1937, 2025)


def test_one_row_per_site_year_pair_always() -> None:
    df = fire_context.assemble_rows(
        site_maus_pairs=[("S1", "M1"), ("S2", "M2")],
        counts_by_maus_year={},
        no_footprint_by_maus={},
        years=[2020, 2021, 2022],
        snapshot_year=2026,
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    assert len(df) == 6
    assert set(zip(df["site_id"], df["year"])) == {
        ("S1", 2020),
        ("S1", 2021),
        ("S1", 2022),
        ("S2", 2020),
        ("S2", 2021),
        ("S2", 2022),
    }


def test_rule_1_no_footprint_wins_for_every_year_of_that_site() -> None:
    df = fire_context.assemble_rows(
        site_maus_pairs=[("S1", "M1")],
        counts_by_maus_year={("M1", 2020): 3},
        no_footprint_by_maus={"M1": "empty geometry"},
        years=[2020, 2021],
        snapshot_year=2026,
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    assert len(df) == 2
    for _, row in df.iterrows():
        assert row["fire_status"] == fire_context.FIRE_STATUS_UNKNOWN
        assert row["fire_coverage_status"] == fire_context.COVERAGE_NO_FOOTPRINT
        assert row["not_computable_reason"] == "empty geometry"
        assert pd.isna(row["fire_record_count"])


def test_rule_2_recorded_inside_window_is_covered() -> None:
    df = fire_context.assemble_rows(
        site_maus_pairs=[("S1", "M1")],
        counts_by_maus_year={("M1", 2020): 2},
        no_footprint_by_maus={},
        years=[2020],
        snapshot_year=2026,
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    row = df.iloc[0]
    assert row["fire_status"] == fire_context.FIRE_STATUS_RECORDED
    assert row["fire_coverage_status"] == fire_context.COVERAGE_COVERED
    assert row["fire_record_count"] == 2
    assert pd.isna(row["not_computable_reason"])


def test_rule_2_recorded_outside_window_is_still_recorded() -> None:
    df = fire_context.assemble_rows(
        site_maus_pairs=[("S1", "M1")],
        counts_by_maus_year={("M1", 2030): 1},
        no_footprint_by_maus={},
        years=[2030],
        snapshot_year=2026,
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    row = df.iloc[0]
    assert row["fire_status"] == fire_context.FIRE_STATUS_RECORDED
    assert row["fire_coverage_status"] == fire_context.COVERAGE_OUTSIDE_WINDOW
    assert row["fire_record_count"] == 1
    assert pd.isna(row["not_computable_reason"])


def test_rule_3_zero_count_inside_window_is_not_recorded() -> None:
    df = fire_context.assemble_rows(
        site_maus_pairs=[("S1", "M1")],
        counts_by_maus_year={},
        no_footprint_by_maus={},
        years=[2020],
        snapshot_year=2026,
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    row = df.iloc[0]
    assert row["fire_status"] == fire_context.FIRE_STATUS_NOT_RECORDED
    assert row["fire_coverage_status"] == fire_context.COVERAGE_COVERED
    assert row["fire_record_count"] == 0
    assert pd.isna(row["not_computable_reason"])


def test_rule_4_zero_count_outside_window_is_unknown() -> None:
    df = fire_context.assemble_rows(
        site_maus_pairs=[("S1", "M1")],
        counts_by_maus_year={},
        no_footprint_by_maus={},
        years=[2030],
        snapshot_year=2026,
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    row = df.iloc[0]
    assert row["fire_status"] == fire_context.FIRE_STATUS_UNKNOWN
    assert row["fire_coverage_status"] == fire_context.COVERAGE_OUTSIDE_WINDOW
    assert pd.isna(row["fire_record_count"])
    assert row["not_computable_reason"] == "year outside the declared coverage window [1937, 2025]"


def test_empty_years_raises() -> None:
    with pytest.raises(fire_context.FireContextError):
        fire_context.assemble_rows(
            site_maus_pairs=[("S1", "M1")],
            counts_by_maus_year={},
            no_footprint_by_maus={},
            years=[],
            snapshot_year=2026,
            snapshot_date="2026-08-26",
            source_version="v1",
        )


def test_empty_site_maus_pairs_raises() -> None:
    with pytest.raises(fire_context.FireContextError):
        fire_context.assemble_rows(
            site_maus_pairs=[],
            counts_by_maus_year={},
            no_footprint_by_maus={},
            years=[2020],
            snapshot_year=2026,
            snapshot_date="2026-08-26",
            source_version="v1",
        )


def test_negative_count_raises() -> None:
    with pytest.raises(fire_context.FireContextError):
        fire_context.assemble_rows(
            site_maus_pairs=[("S1", "M1")],
            counts_by_maus_year={("M1", 2020): -1},
            no_footprint_by_maus={},
            years=[2020],
            snapshot_year=2026,
            snapshot_date="2026-08-26",
            source_version="v1",
        )


def test_column_order_matches_schema() -> None:
    df = fire_context.assemble_rows(
        site_maus_pairs=[("S1", "M1")],
        counts_by_maus_year={},
        no_footprint_by_maus={},
        years=[2020],
        snapshot_year=2026,
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    assert list(df.columns) == list(fire_context.FIRE_CONTEXT_SCHEMA.names)


def test_reconciliation_passes_on_assembled_output() -> None:
    df = fire_context.assemble_rows(
        site_maus_pairs=[("S1", "M1"), ("S2", "M2")],
        counts_by_maus_year={("M1", 2020): 2},
        no_footprint_by_maus={"M2": "no footprint"},
        years=[2020, 2021],
        snapshot_year=2026,
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    fire_context.validate_row_counts(df, n_pairs=2, n_years=2)


def test_reconciliation_fails_on_row_dropped_frame() -> None:
    df = fire_context.assemble_rows(
        site_maus_pairs=[("S1", "M1"), ("S2", "M2")],
        counts_by_maus_year={("M1", 2020): 2},
        no_footprint_by_maus={},
        years=[2020, 2021],
        snapshot_year=2026,
        snapshot_date="2026-08-26",
        source_version="v1",
    )
    mutated = df.iloc[:-1]
    with pytest.raises(fire_context.FireContextError):
        fire_context.validate_row_counts(mutated, n_pairs=2, n_years=2)


# ---------------------------------------------------------------------------
# `build-fire-context` CLI
# ---------------------------------------------------------------------------

runner = CliRunner()


def _write_config(tmp_path: Path, data_root: Path) -> Path:
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{data_root}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg_file


def _stub_git_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )


def _footprint_box(lon: float = 116.005, lat: float = -31.995, delta: float = 0.02) -> Polygon:
    """A footprint box that fully contains `write_fire_gpkg`'s default
    fixture fire square (`_square(116.0, -32.0, side=0.01)`)."""
    return Polygon(
        [
            (lon - delta, lat - delta),
            (lon + delta, lat - delta),
            (lon + delta, lat + delta),
            (lon - delta, lat + delta),
        ]
    )


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


def _seed_dbca_snapshot(
    data_root: Path,
    date_str: str,
    fire_rows: list[dict],
    *,
    finalize: bool = True,
    gpkg_name: str = "fire.gpkg",
    write_gpkg_before_finalize: bool = True,
) -> Path:
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "dbca_060_fire", date_str)
    if write_gpkg_before_finalize:
        write_fire_gpkg(snapshot_dir / gpkg_name, fire_rows)
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="DBCA-060 Fire History",
        endpoint="https://catalogue.data.wa.gov.au/dataset/fire-history-dbca-060",
        licence_note="CC-BY-4.0",
        purpose="test fixture",
    )
    if finalize:
        snapshots.finalize_snapshot(snapshot_dir)
    if not write_gpkg_before_finalize:
        write_fire_gpkg(snapshot_dir / gpkg_name, fire_rows)
    return snapshot_dir


def _seed_fire_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    register_rows: list[dict],
    crosswalk_rows: list[dict],
    maus_geoms: dict[str, Polygon],
    fire_rows: list[dict],
    dbca_date: str = "2026-08-29",
    finalize_dbca: bool = True,
    dbca_gpkg_before_finalize: bool = True,
) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    _seed_dbca_snapshot(
        data_root,
        dbca_date,
        fire_rows,
        finalize=finalize_dbca,
        write_gpkg_before_finalize=dbca_gpkg_before_finalize,
    )
    _, maus_sha256 = _seed_maus_extract(data_root, "2026-08-14", maus_geoms)
    _seed_eligible_register(data_root, "2026-08-15", register_rows)
    _seed_crosswalk(data_root, "2026-08-16", crosswalk_rows, maus_sha256=maus_sha256)
    return cfg_file, data_root


_STANDARD_REGISTER_ROWS = [_eligible_register_row("S1", lon=116.005, lat=-31.995)]
_STANDARD_CROSSWALK_ROWS = [_crosswalk_row("S1", "MAUS001")]
_STANDARD_MAUS_GEOMS = {"MAUS001": _footprint_box()}


def _invoke_build(cfg_file: Path, *, date: str, start_year: int, end_year: int):
    return runner.invoke(
        app,
        [
            "build-fire-context",
            "--config",
            str(cfg_file),
            "--date",
            date,
            "--start-year",
            str(start_year),
            "--end-year",
            str(end_year),
        ],
    )


def test_build_fire_context_cli_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file, data_root = _seed_fire_world(
        tmp_path,
        monkeypatch,
        register_rows=_STANDARD_REGISTER_ROWS,
        crosswalk_rows=_STANDARD_CROSSWALK_ROWS,
        maus_geoms=_STANDARD_MAUS_GEOMS,
        fire_rows=[
            {"year": 1990},
            {"year": 1990},
            {"year": 2001},
        ],
    )
    result = _invoke_build(cfg_file, date="2026-08-30", start_year=1989, end_year=2002)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    output_path = data_root / "curated" / "fire-context" / "2026-08-30" / "fire_context.parquet"
    assert output_path.is_file()
    assert payload["output_path"] == str(output_path)
    assert payload["rows"] == 14

    written = pd.read_parquet(output_path)
    assert len(written) == 14
    row_1990 = written[written["year"] == 1990].iloc[0]
    assert row_1990["fire_status"] == fire_context.FIRE_STATUS_RECORDED
    assert row_1990["fire_record_count"] == 2
    assert row_1990["fire_coverage_status"] == fire_context.COVERAGE_COVERED

    row_2001 = written[written["year"] == 2001].iloc[0]
    assert row_2001["fire_status"] == fire_context.FIRE_STATUS_RECORDED
    assert row_2001["fire_record_count"] == 1

    other_rows = written[~written["year"].isin([1990, 2001])]
    assert (other_rows["fire_status"] == fire_context.FIRE_STATUS_NOT_RECORDED).all()
    assert (other_rows["fire_record_count"] == 0).all()
    assert (other_rows["fire_coverage_status"] == fire_context.COVERAGE_COVERED).all()

    status_sum = sum(payload["status_counts"].values())
    assert status_sum == 14

    manifest_path = Path(str(output_path) + manifests.MANIFEST_SUFFIX)
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    dbca_asset_uris = [asset for asset in manifest["inputs"] if asset.get("licence") == "CC-BY-4.0"]
    assert len(dbca_asset_uris) == 1


def test_build_fire_context_marks_prewindow_years_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, _data_root = _seed_fire_world(
        tmp_path,
        monkeypatch,
        register_rows=_STANDARD_REGISTER_ROWS,
        crosswalk_rows=_STANDARD_CROSSWALK_ROWS,
        maus_geoms=_STANDARD_MAUS_GEOMS,
        fire_rows=[{"year": 1990}],
    )
    result = _invoke_build(cfg_file, date="2026-08-30", start_year=1936, end_year=1937)
    assert result.exit_code == 0, result.output
    written = pd.read_parquet(json.loads(result.output)["output_path"])

    row_1936 = written[written["year"] == 1936].iloc[0]
    assert row_1936["fire_status"] == fire_context.FIRE_STATUS_UNKNOWN
    assert row_1936["fire_coverage_status"] == fire_context.COVERAGE_OUTSIDE_WINDOW

    row_1937 = written[written["year"] == 1937].iloc[0]
    assert row_1937["fire_status"] == fire_context.FIRE_STATUS_NOT_RECORDED
    assert row_1937["fire_coverage_status"] == fire_context.COVERAGE_COVERED


def test_build_fire_context_snapshot_year_excluded_from_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, _data_root = _seed_fire_world(
        tmp_path,
        monkeypatch,
        register_rows=_STANDARD_REGISTER_ROWS,
        crosswalk_rows=_STANDARD_CROSSWALK_ROWS,
        maus_geoms=_STANDARD_MAUS_GEOMS,
        fire_rows=[{"year": 1990}],
        dbca_date="2026-08-29",
    )
    result = _invoke_build(cfg_file, date="2026-08-30", start_year=2026, end_year=2026)
    assert result.exit_code == 0, result.output
    written = pd.read_parquet(json.loads(result.output)["output_path"])
    row_2026 = written.iloc[0]
    assert row_2026["fire_status"] == fire_context.FIRE_STATUS_UNKNOWN
    assert row_2026["fire_coverage_status"] == fire_context.COVERAGE_OUTSIDE_WINDOW


def test_build_fire_context_recorded_wins_outside_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, _data_root = _seed_fire_world(
        tmp_path,
        monkeypatch,
        register_rows=_STANDARD_REGISTER_ROWS,
        crosswalk_rows=_STANDARD_CROSSWALK_ROWS,
        maus_geoms=_STANDARD_MAUS_GEOMS,
        fire_rows=[{"year": 2026}],
        dbca_date="2026-08-29",
    )
    result = _invoke_build(cfg_file, date="2026-08-30", start_year=2026, end_year=2026)
    assert result.exit_code == 0, result.output
    written = pd.read_parquet(json.loads(result.output)["output_path"])
    row_2026 = written.iloc[0]
    assert row_2026["fire_status"] == fire_context.FIRE_STATUS_RECORDED
    assert row_2026["fire_coverage_status"] == fire_context.COVERAGE_OUTSIDE_WINDOW
    assert row_2026["fire_record_count"] == 1


def test_build_fire_context_invalid_footprint_is_unknown_not_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid_geom = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    assert not invalid_geom.is_valid
    cfg_file, _data_root = _seed_fire_world(
        tmp_path,
        monkeypatch,
        register_rows=_STANDARD_REGISTER_ROWS,
        crosswalk_rows=_STANDARD_CROSSWALK_ROWS,
        maus_geoms={"MAUS001": invalid_geom},
        fire_rows=[{"year": 1990}],
    )
    result = _invoke_build(cfg_file, date="2026-08-30", start_year=1989, end_year=1990)
    assert result.exit_code == 0, result.output
    written = pd.read_parquet(json.loads(result.output)["output_path"])
    assert (written["fire_status"] == fire_context.FIRE_STATUS_UNKNOWN).all()
    assert (written["fire_coverage_status"] == fire_context.COVERAGE_NO_FOOTPRINT).all()
    assert written["fire_record_count"].isna().all()


def test_build_fire_context_refuses_inverted_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, data_root = _seed_fire_world(
        tmp_path,
        monkeypatch,
        register_rows=_STANDARD_REGISTER_ROWS,
        crosswalk_rows=_STANDARD_CROSSWALK_ROWS,
        maus_geoms=_STANDARD_MAUS_GEOMS,
        fire_rows=[{"year": 1990}],
    )
    result = _invoke_build(cfg_file, date="2026-08-30", start_year=2002, end_year=1989)
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "inverted year range" in payload["refusal"]
    assert not (data_root / "curated" / "fire-context").exists()


def test_build_fire_context_refuses_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, _data_root = _seed_fire_world(
        tmp_path,
        monkeypatch,
        register_rows=_STANDARD_REGISTER_ROWS,
        crosswalk_rows=_STANDARD_CROSSWALK_ROWS,
        maus_geoms=_STANDARD_MAUS_GEOMS,
        fire_rows=[{"year": 1990}],
    )
    first = _invoke_build(cfg_file, date="2026-08-30", start_year=1989, end_year=1990)
    assert first.exit_code == 0, first.output
    second = _invoke_build(cfg_file, date="2026-08-30", start_year=1989, end_year=1990)
    assert second.exit_code == 1
    payload = json.loads(second.output)
    assert "refusal" in payload


def test_build_fire_context_refuses_unverified_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, _data_root = _seed_fire_world(
        tmp_path,
        monkeypatch,
        register_rows=_STANDARD_REGISTER_ROWS,
        crosswalk_rows=_STANDARD_CROSSWALK_ROWS,
        maus_geoms=_STANDARD_MAUS_GEOMS,
        fire_rows=[{"year": 1990}],
        finalize_dbca=False,
    )
    result = _invoke_build(cfg_file, date="2026-08-30", start_year=1989, end_year=1990)
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "refusal" in payload


def test_build_fire_context_refuses_gpkg_dropped_in_after_finalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, _data_root = _seed_fire_world(
        tmp_path,
        monkeypatch,
        register_rows=_STANDARD_REGISTER_ROWS,
        crosswalk_rows=_STANDARD_CROSSWALK_ROWS,
        maus_geoms=_STANDARD_MAUS_GEOMS,
        fire_rows=[{"year": 1990}],
        dbca_gpkg_before_finalize=False,
    )
    result = _invoke_build(cfg_file, date="2026-08-30", start_year=1989, end_year=1990)
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "refusal" in payload
    assert "fire.gpkg" in payload["refusal"] or "never hashed" in payload["refusal"]


def test_build_fire_context_refuses_non_d3_register(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    _seed_dbca_snapshot(data_root, "2026-08-29", [{"year": 1990}])
    _, maus_sha256 = _seed_maus_extract(data_root, "2026-08-14", _STANDARD_MAUS_GEOMS)

    # A register lacking `d3_forced_threshold` -- seeded by hand rather than
    # via `_eligible_register_row`/`_seed_eligible_register`, which always
    # write that column.
    row = _eligible_register_row("S1", lon=116.005, lat=-31.995)
    del row["d3_forced_threshold"]
    output_dir = data_root / "curated" / "register" / "2026-08-15"
    output_dir.mkdir(parents=True)
    schema_names = [
        n for n in register.ELIGIBLE_REGISTER_SCHEMA.names if n != "d3_forced_threshold"
    ]
    df = pd.DataFrame([row])[schema_names]
    path = output_dir / "register.parquet"
    schema = pa.schema(
        [f for f in register.ELIGIBLE_REGISTER_SCHEMA if f.name != "d3_forced_threshold"]
    )
    write_table(df, path, schema)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    _seed_crosswalk(data_root, "2026-08-16", _STANDARD_CROSSWALK_ROWS, maus_sha256=maus_sha256)

    result = _invoke_build(cfg_file, date="2026-08-30", start_year=1989, end_year=1990)
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "d3_forced_threshold" in payload["refusal"]
