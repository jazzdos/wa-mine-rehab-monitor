"""CLI tests for `export-release` -- the first production caller of
`export_gate.export_public`.

Follows the fixture pattern of `tests/test_cli.py`'s
`build-maus-footprint-areas` tests: an in-`tmp_path` `data_root`, a git repo
initialised (but never committed to), a written project config, and a
prebuilt curated snapshot directory carrying its own run manifest -- built
here via a real `build-maus-footprint-areas` invocation, exactly the way
`tests/test_cli.py`'s Maus-footprints-to-crosswalk pipeline tests seed a
curated artefact for a downstream command to consume, so the digest-verified
manifest `export-release` reads is a real one, not a hand-built stand-in.
"""

import json
import subprocess
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon
from typer.testing import CliRunner

from wa_mine_monitor import release, snapshots
from wa_mine_monitor.cli import app

runner = CliRunner()


def _init_git_repo(repo_root: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True)


def _write_monitor_config(tmp_path: Path) -> Path:
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{tmp_path / "data"}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg_file


def _seed_maus_extract(data_root: Path, date_str: str) -> Path:
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "maus_v2", date_str)
    gdf = gpd.GeoDataFrame(
        {"maus_id": ["MAUS001", "MAUS002"]},
        geometry=[
            Polygon(
                [
                    (115.995, -32.005),
                    (116.005, -32.005),
                    (116.005, -31.995),
                    (115.995, -31.995),
                ]
            ),
            Polygon(
                [
                    (121.490, -30.705),
                    (121.510, -30.705),
                    (121.510, -30.695),
                    (121.490, -30.695),
                ]
            ),
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


def _seed_curated_footprint_areas(tmp_path: Path, cfg_file: Path, *, date: str) -> None:
    """Build a real `curated/maus_footprint_areas/<date>/footprint_areas.parquet`
    (plus its run manifest) via the real `build-maus-footprint-areas` command,
    so `export-release` reads a digest-verified artefact exactly like a real
    run would produce, not a hand-built stand-in.
    """
    _seed_maus_extract(tmp_path / "data", "2026-08-15")
    result = runner.invoke(
        app,
        ["build-maus-footprint-areas", "--config", str(cfg_file), "--date", date],
    )
    assert result.exit_code == 0, result.output


def test_export_release_writes_gated_package(tmp_path, monkeypatch) -> None:
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"
    _seed_curated_footprint_areas(tmp_path, cfg_file, date="2026-08-25")

    result = runner.invoke(
        app,
        [
            "export-release",
            "--package",
            "footprint-areas",
            "--date",
            "2026-08-25",
            "--config",
            str(cfg_file),
        ],
    )
    assert result.exit_code == 0, result.output

    out = data_root / "releases" / "2026-08-25" / "footprint-areas"
    published = pd.read_parquet(out / "footprint_areas.parquet")
    assert "redistribute_public" not in published.columns
    assert sorted(published["maus_id"]) == ["MAUS001", "MAUS002"]

    manifest = json.loads((out / "footprint_areas.parquet.run_manifest.json").read_text())
    assert manifest["resolved_args"]["output_licence"] == "CC-BY-SA-4.0"
    assert manifest["resolved_args"]["output_share_alike"] is True
    assert manifest["resolved_args"]["package"] == "footprint-areas"
    assert len(manifest["inputs"]) == 1

    # The CC-BY-SA obligations ship WITH the package, not only in the
    # manifest: attribution, source link, licence link, modification
    # statement (licence.py maus_v2 notes).
    attribution = (out / "ATTRIBUTION.txt").read_text()
    assert attribution == release.attribution_block(release.PACKAGES["footprint-areas"])


def test_export_release_refuses_unknown_package(tmp_path, monkeypatch) -> None:
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)

    result = runner.invoke(
        app,
        [
            "export-release",
            "--package",
            "not-a-real-package",
            "--date",
            "2026-08-25",
            "--config",
            str(cfg_file),
        ],
    )
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_export_release_refuses_existing_output(tmp_path, monkeypatch) -> None:
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    _seed_curated_footprint_areas(tmp_path, cfg_file, date="2026-08-25")

    first = runner.invoke(
        app,
        [
            "export-release",
            "--package",
            "footprint-areas",
            "--date",
            "2026-08-25",
            "--config",
            str(cfg_file),
        ],
    )
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        app,
        [
            "export-release",
            "--package",
            "footprint-areas",
            "--date",
            "2026-08-25",
            "--config",
            str(cfg_file),
        ],
    )
    assert second.exit_code == 1
    assert "refusal" in second.output


def test_export_release_refuses_restricted_rows(tmp_path, monkeypatch) -> None:
    """A frame carrying `redistribute_public=False` anywhere must refuse the
    WHOLE package (`PermissionError` surfaced as a JSON refusal, exit 1) --
    never filter. Pins D13 Batch G: "Row filtering is prohibited; a mixed
    package fails as a whole." Forced here by pointing `release.PACKAGES` at
    a package whose `source_id` resolves to a closed source
    (`dmirs_001_minedex`, `redistribute_public=False`).
    """
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    _seed_curated_footprint_areas(tmp_path, cfg_file, date="2026-08-25")

    closed = release.PackageSpec(
        curated_dir="maus_footprint_areas",
        filename="footprint_areas.parquet",
        source_id="dmirs_001_minedex",
        output_licence="CC-BY-4.0",
        share_alike=False,
        modification_statement="",
    )
    monkeypatch.setitem(release.PACKAGES, "footprint-areas", closed)

    result = runner.invoke(
        app,
        [
            "export-release",
            "--package",
            "footprint-areas",
            "--date",
            "2026-08-25",
            "--config",
            str(cfg_file),
        ],
    )
    assert result.exit_code == 1
    assert "refusal" in result.output
    out = tmp_path / "data" / "releases" / "2026-08-25" / "footprint-areas"
    assert not out.exists()


def test_export_release_never_leaves_parquet_without_attribution(tmp_path, monkeypatch) -> None:
    """CC-BY-SA obligation: a released parquet may never exist on disk
    without its `ATTRIBUTION.txt` beside it -- attribution without data is
    merely inert. Forces the parquet write (`_write_table_or_refuse`) to
    fail and asserts the command refuses (exit 1) with NO parquet file
    present; `ATTRIBUTION.txt` having already landed is fine, since it
    carries no licensed data on its own.
    """
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"
    _seed_curated_footprint_areas(tmp_path, cfg_file, date="2026-08-25")

    def _boom(*args, **kwargs):
        raise ValueError("simulated parquet write failure")

    monkeypatch.setattr("wa_mine_monitor.cli._write_table_or_refuse", _boom)

    result = runner.invoke(
        app,
        [
            "export-release",
            "--package",
            "footprint-areas",
            "--date",
            "2026-08-25",
            "--config",
            str(cfg_file),
        ],
    )
    assert result.exit_code != 0

    out = data_root / "releases" / "2026-08-25" / "footprint-areas"
    assert not (out / "footprint_areas.parquet").exists()
    assert (out / "ATTRIBUTION.txt").exists()
