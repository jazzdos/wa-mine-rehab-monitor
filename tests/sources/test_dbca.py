"""Tests for `sources/dbca.py`: DBCA-060 fire history validation.

Every GeoPackage input is a tiny synthetic fixture written with
geopandas in the test itself. Nothing here touches the network or the
real 2.1 GB GeoPackage.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon
from typer.testing import CliRunner

from wa_mine_monitor import cli as cli_module
from wa_mine_monitor import snapshots
from wa_mine_monitor.cli import app
from wa_mine_monitor.sources import dbca

LAYER = dbca.LAYER_NAME
runner = CliRunner()


def _square(x: float, y: float, side: float = 0.01) -> Polygon:
    return Polygon([(x, y), (x + side, y), (x + side, y + side), (x, y + side)])


def write_fire_gpkg(
    path: Path,
    rows: list[dict],
    *,
    layer: str = LAYER,
    crs: str = "EPSG:4283",
) -> Path:
    frame = gpd.GeoDataFrame(
        {
            "fih_master_key": [r.get("key", f"K{i}") for i, r in enumerate(rows)],
            "fih_fire_type": [r.get("fire_type", "WF") for r in rows],
            "fih_year1": pd.array([r.get("year", 2000) for r in rows], dtype="int64"),
            "geometry": [r.get("geom", _square(116.0, -32.0)) for r in rows],
        },
        crs=crs,
    )
    frame.to_file(path, driver="GPKG", layer=layer)
    return path


def test_validate_accepts_a_conformant_file_and_reports_counts(tmp_path: Path) -> None:
    path = write_fire_gpkg(
        tmp_path / "fire.gpkg",
        [
            {"fire_type": "WF", "year": 1990},
            {"fire_type": "PB", "year": 1990},
            {"fire_type": "999", "year": 2001},
        ],
    )
    summary = dbca.validate_fire_history_file(path, snapshot_year=2026)
    assert summary.feature_count == 3
    assert summary.counts_by_type == {"999": 1, "PB": 1, "WF": 1}
    assert summary.year_min == 1990
    assert summary.year_max == 2001
    assert summary.crs == "EPSG:4283"


def test_validate_refuses_missing_layer(tmp_path: Path) -> None:
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [{}], layer="WRONG_LAYER")
    with pytest.raises(dbca.DbcaError, match="layer"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


def test_validate_refuses_wrong_crs(tmp_path: Path) -> None:
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [{}], crs="EPSG:4326")
    with pytest.raises(dbca.DbcaError, match="4283"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


def test_validate_tripwires_on_an_unexpected_fire_type_code(tmp_path: Path) -> None:
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [{"fire_type": "MR"}])
    with pytest.raises(dbca.DbcaError, match="MR"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


def test_validate_normalises_case_and_whitespace_variants(tmp_path: Path) -> None:
    # The real GDA94 file carries one raw lowercase `wf` (jarrah census).
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [{"fire_type": " wf "}])
    summary = dbca.validate_fire_history_file(path, snapshot_year=2026)
    assert summary.counts_by_type == {"WF": 1}


def test_validate_refuses_a_year_outside_bounds(tmp_path: Path) -> None:
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [{"year": 1850}])
    with pytest.raises(dbca.DbcaError, match="1850"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


def test_validate_refuses_a_null_year(tmp_path: Path) -> None:
    path = tmp_path / "fire.gpkg"
    frame = gpd.GeoDataFrame(
        {
            "fih_master_key": ["K0"],
            "fih_fire_type": ["WF"],
            "fih_year1": pd.array([None], dtype="float64"),
            "geometry": [_square(116.0, -32.0)],
        },
        crs="EPSG:4283",
    )
    frame.to_file(path, driver="GPKG", layer=LAYER)
    with pytest.raises(dbca.DbcaError, match="null"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


def test_validate_refuses_a_year_after_the_snapshot(tmp_path: Path) -> None:
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [{"year": 2027}])
    with pytest.raises(dbca.DbcaError, match="2027"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


def test_validate_refuses_an_empty_layer(tmp_path: Path) -> None:
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [])
    with pytest.raises(dbca.DbcaError, match="0 features"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


def test_validate_refuses_a_missing_required_field(tmp_path: Path) -> None:
    path = tmp_path / "fire.gpkg"
    frame = gpd.GeoDataFrame(
        {
            "fih_master_key": ["K0"],
            "fih_fire_type": ["WF"],
            "geometry": [_square(116.0, -32.0)],
        },
        crs="EPSG:4283",
    )
    frame.to_file(path, driver="GPKG", layer=LAYER)
    with pytest.raises(dbca.DbcaError, match="fih_year1"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


# ---------------------------------------------------------------------------
# CLI: `fetch-dbca-fire`
# ---------------------------------------------------------------------------


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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _seed_source_dir(
    tmp_path: Path,
    *,
    fire_rows: list[dict] | None = None,
    write_sums: bool = True,
    bad_digest: bool = False,
) -> Path:
    """A minimal authoritative DBCA-060 package directory: one fixture
    GeoPackage, a small `extras.zip`, `metadata.txt`, and (unless
    disabled) a `SHA256SUMS.txt` covering the zip's real digest."""
    source_dir = tmp_path / "source-package"
    source_dir.mkdir()
    write_fire_gpkg(
        source_dir / "fire.gpkg",
        fire_rows if fire_rows is not None else [{"fire_type": "WF", "year": 1990}],
    )
    extras_zip = source_dir / "extras.zip"
    with zipfile.ZipFile(extras_zip, "w") as zf:
        zf.writestr("readme.txt", "extra material")
    (source_dir / "metadata.txt").write_text("source metadata\n")
    if write_sums:
        digest = "0" * 64 if bad_digest else _sha256_bytes(extras_zip.read_bytes())
        (source_dir / "SHA256SUMS.txt").write_text(f"{digest}  extras.zip\n")
    return source_dir


def test_fetch_dbca_fire_refuses_mirror_mode_before_any_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    source_dir = _seed_source_dir(tmp_path)

    result = runner.invoke(
        app,
        [
            "fetch-dbca-fire",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-29",
            "--mode",
            "mirror",
            "--source-dir",
            str(source_dir),
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "mirror route is declined" in payload["refusal"]
    assert not data_root.exists()


def test_fetch_dbca_fire_stages_validates_and_finalizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    source_dir = _seed_source_dir(
        tmp_path,
        fire_rows=[
            {"fire_type": "WF", "year": 1990},
            {"fire_type": "PB", "year": 2001},
        ],
    )
    monkeypatch.setattr(cli_module, "_fetch_catalogue_page", lambda url: b"<html>CC BY 4.0</html>")

    result = runner.invoke(
        app,
        [
            "fetch-dbca-fire",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-29",
            "--mode",
            "authoritative",
            "--source-dir",
            str(source_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["feature_count"] == 2
    assert payload["counts_by_type"] == {"PB": 1, "WF": 1}

    snapshot_dir = data_root / "raw" / "dbca_060_fire" / "2026-08-29"
    assert (snapshot_dir / "fire.gpkg").is_file()
    assert (snapshot_dir / "catalogue-page.html").is_file()
    assert (snapshot_dir / "metadata.txt").is_file()
    assert (snapshot_dir / "SHA256SUMS.txt").is_file()
    assert (snapshot_dir / "source-SHA256SUMS.txt").is_file()
    assert (snapshot_dir / "source-metadata.txt").is_file()

    _n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)
    assert (n_bad, n_missing) == (0, 0)

    manifest = json.loads((snapshot_dir / "SHA256SUMS.txt.run_manifest.json").read_text())
    assert len(manifest["inputs"]) == 2
    assert manifest["resolved_args"]["counts_by_type"] == {"PB": 1, "WF": 1}


def test_fetch_dbca_fire_refuses_source_dir_without_sums(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    source_dir = _seed_source_dir(tmp_path, write_sums=False)

    result = runner.invoke(
        app,
        [
            "fetch-dbca-fire",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-29",
            "--source-dir",
            str(source_dir),
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["stage"] == "source_package"
    assert not data_root.exists()


def test_fetch_dbca_fire_refuses_source_digest_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    source_dir = _seed_source_dir(tmp_path, bad_digest=True)

    result = runner.invoke(
        app,
        [
            "fetch-dbca-fire",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-29",
            "--source-dir",
            str(source_dir),
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["stage"] == "source_digests"
    assert not data_root.exists()


def test_fetch_dbca_fire_refuses_invalid_gpkg_before_finalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    source_dir = _seed_source_dir(tmp_path, fire_rows=[{"fire_type": "MR"}])

    result = runner.invoke(
        app,
        [
            "fetch-dbca-fire",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-29",
            "--source-dir",
            str(source_dir),
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["stage"] == "validation"

    snapshot_dir = data_root / "raw" / "dbca_060_fire" / "2026-08-29"
    assert not (snapshot_dir / "SHA256SUMS.txt").exists()


def test_fetch_dbca_fire_refuses_when_evidence_fetch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    source_dir = _seed_source_dir(tmp_path)

    def _boom(url: str) -> bytes:
        raise RuntimeError("network refused")

    monkeypatch.setattr(cli_module, "_fetch_catalogue_page", _boom)

    result = runner.invoke(
        app,
        [
            "fetch-dbca-fire",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-29",
            "--source-dir",
            str(source_dir),
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["stage"] == "licence_evidence"

    snapshot_dir = data_root / "raw" / "dbca_060_fire" / "2026-08-29"
    assert not (snapshot_dir / "SHA256SUMS.txt").exists()


def test_fetch_dbca_fire_refuses_a_finalized_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    source_dir = _seed_source_dir(tmp_path)
    monkeypatch.setattr(cli_module, "_fetch_catalogue_page", lambda url: b"<html>CC BY 4.0</html>")

    cli_args = [
        "fetch-dbca-fire",
        "--config",
        str(cfg_file),
        "--date",
        "2026-08-29",
        "--source-dir",
        str(source_dir),
    ]
    first = runner.invoke(app, cli_args)
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, cli_args)
    assert second.exit_code == 1
    payload = json.loads(second.output)
    snapshot_dir = data_root / "raw" / "dbca_060_fire" / "2026-08-29"
    assert str(snapshot_dir) in payload["refusal"]


# ---------------------------------------------------------------------------
# `fire_year_counts_for_footprint`
# ---------------------------------------------------------------------------


def test_fire_year_counts_counts_intersections_per_year(tmp_path: Path) -> None:
    footprint = _square(116.0, -32.0, side=0.02)
    path = write_fire_gpkg(
        tmp_path / "fire.gpkg",
        [
            {"fire_type": "WF", "year": 1990, "geom": _square(116.0, -32.0)},
            {"fire_type": "PB", "year": 1990, "geom": _square(116.005, -32.005)},
            {"fire_type": "999", "year": 2001, "geom": _square(116.001, -32.001)},
            {"fire_type": "WF", "year": 2015, "geom": _square(120.0, -35.0)},
        ],
    )
    counts = dbca.fire_year_counts_for_footprint(path, footprint)
    assert counts == {1990: 2, 2001: 1}


def test_fire_year_counts_excludes_bbox_overlap_without_intersection(tmp_path: Path) -> None:
    # Footprint is a small square at (116.0, -32.0). This fire polygon's
    # bbox overlaps the footprint's bbox, but the polygon itself (an
    # L-shape carved away from the footprint's corner) does not
    # intersect the footprint geometry.
    footprint = _square(116.0, -32.0, side=0.01)
    from shapely.geometry import Polygon as _Polygon

    non_intersecting = _Polygon(
        [
            (116.01, -32.01),
            (116.02, -32.01),
            (116.02, -32.0),
            (116.015, -32.0),
            (116.015, -32.005),
            (116.01, -32.005),
        ]
    )
    path = write_fire_gpkg(
        tmp_path / "fire.gpkg",
        [{"fire_type": "WF", "year": 1990, "geom": non_intersecting}],
    )
    counts = dbca.fire_year_counts_for_footprint(path, footprint)
    assert counts == {}


def test_fire_year_counts_returns_empty_for_no_intersections(tmp_path: Path) -> None:
    footprint = _square(116.0, -32.0)
    path = write_fire_gpkg(
        tmp_path / "fire.gpkg",
        [{"fire_type": "WF", "year": 1990, "geom": _square(120.0, -35.0)}],
    )
    counts = dbca.fire_year_counts_for_footprint(path, footprint)
    assert counts == {}


def test_fire_year_counts_counts_all_fire_types(tmp_path: Path) -> None:
    footprint = _square(116.0, -32.0)
    path = write_fire_gpkg(
        tmp_path / "fire.gpkg",
        [
            {"fire_type": "WF", "year": 1990, "geom": _square(116.0, -32.0)},
            {"fire_type": "PB", "year": 1991, "geom": _square(116.0, -32.0)},
            {"fire_type": "999", "year": 1992, "geom": _square(116.0, -32.0)},
        ],
    )
    counts = dbca.fire_year_counts_for_footprint(path, footprint)
    assert counts == {1990: 1, 1991: 1, 1992: 1}


def test_fetch_dbca_fire_refuses_stray_files_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    source_dir = _seed_source_dir(tmp_path)

    snapshot_dir = snapshots.create_snapshot_dir(data_root, "dbca_060_fire", "2026-08-29")
    (snapshot_dir / "stray.part").write_text("junk")

    result = runner.invoke(
        app,
        [
            "fetch-dbca-fire",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-29",
            "--source-dir",
            str(source_dir),
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "before staging" in payload["refusal"]
