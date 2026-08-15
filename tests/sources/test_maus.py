"""Tests for `wa_mine_monitor.sources.maus` and the `fetch-maus-extract` CLI command.

Maus et al. v2 (CC-BY-SA-4.0) is a LOCAL-source case, unlike
`sources/tenements.py`/`sources/minedex.py`: the jarrah data root already
holds the global v2 GeoPackage
(`~/data/jarrah-rehab/raw/maus-v2/2026-07-20/`), and `fetch-maus-extract`
takes `--source-gpkg` pointing at it (read-only) rather than downloading
anything -- so there is no network call to fake in this file at all. Every
test builds a toy GeoPackage in-test with geopandas, into `tmp_path`, rather
than reading a committed binary fixture or the real global dataset.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon
from typer.testing import CliRunner

from wa_mine_monitor import licence, snapshots
from wa_mine_monitor.cli import app
from wa_mine_monitor.provenance import sha256_file
from wa_mine_monitor.sources import maus

runner = CliRunner()

# --- fixture builders -------------------------------------------------------


def _toy_global_gdf() -> gpd.GeoDataFrame:
    """One polygon inside WA_BBOX (near lon 120, lat -30), one in Brazil
    (near lon -50, lat -10), EPSG:4326 -- exactly the toy global GeoDataFrame
    the task brief specifies for `clip_to_wa`."""
    wa_polygon = Polygon([(119.9, -30.1), (120.1, -30.1), (120.1, -29.9), (119.9, -29.9)])
    brazil_polygon = Polygon([(-50.1, -10.1), (-49.9, -10.1), (-49.9, -9.9), (-50.1, -9.9)])
    return gpd.GeoDataFrame(
        {"name": ["wa_site", "brazil_site"]},
        geometry=[wa_polygon, brazil_polygon],
        crs="EPSG:4326",
    )


def _write_global_fixture_gpkg(path: Path, *, wa_feature_count: int = 2) -> Path:
    """Build a toy global Maus-v2-shaped GeoPackage fixture, in-test.

    `wa_feature_count` toy points inside `WA_BBOX`, plus one point in Brazil
    (well outside it) -- so a `fetch-maus-extract` run over this fixture has
    something real to clip away.
    """
    wa_points = [Point(115.0 + i * 0.1, -32.0 - i * 0.1) for i in range(wa_feature_count)]
    brazil_point = Point(-50.0, -10.0)
    gdf = gpd.GeoDataFrame(
        {
            "mining_area_name": [f"WA site {i}" for i in range(wa_feature_count)] + ["Brazil site"],
            "geometry": [*wa_points, brazil_point],
        },
        crs="EPSG:4326",
    )
    gdf.to_file(path, driver="GPKG", layer="mining_areas")
    return path


def _write_no_wa_features_fixture_gpkg(path: Path) -> Path:
    """A valid GeoPackage with features, none of which fall inside WA_BBOX."""
    gdf = gpd.GeoDataFrame(
        {"mining_area_name": ["Brazil site"]},
        geometry=[Point(-50.0, -10.0)],
        crs="EPSG:4326",
    )
    gdf.to_file(path, driver="GPKG", layer="mining_areas")
    return path


def _write_two_layer_fixture_gpkg(path: Path) -> Path:
    """A valid, WA-extract-shaped GeoPackage carrying TWO layers -- reading
    only the first (as `gpd.read_file(path)` does with no `layer=`
    argument) would silently validate an arbitrary fraction of the extract."""
    clipped = maus.clip_to_wa(_toy_global_gdf())
    clipped.to_file(path, driver="GPKG", layer="wa_extract")
    extra_gdf = gpd.GeoDataFrame(
        {"name": ["extra_site"]},
        geometry=[Point(120.0, -30.0)],
        crs="EPSG:4326",
    )
    extra_gdf.to_file(path, driver="GPKG", layer="extra_layer")
    return path


# --- WA_BBOX ------------------------------------------------------------------


def test_wa_bbox_is_pinned() -> None:
    """A silent change to this bbox must be a loud, reviewable diff."""
    assert maus.WA_BBOX == (112.5, -35.5, 129.1, -13.5)


# --- clip_to_wa -----------------------------------------------------------


def test_clip_to_wa_keeps_only_the_wa_polygon_and_adds_a_deterministic_maus_id() -> None:
    clipped = maus.clip_to_wa(_toy_global_gdf())

    assert len(clipped) == 1
    assert clipped.iloc[0]["name"] == "wa_site"
    assert str(clipped.crs) == "EPSG:4326"

    assert "maus_id" in clipped.columns
    maus_id = clipped.iloc[0]["maus_id"]
    assert isinstance(maus_id, str)
    assert len(maus_id) == 12
    assert all(c in "0123456789abcdef" for c in maus_id)


def test_clip_to_wa_is_deterministic_across_calls() -> None:
    """Same input -> same id, checked by calling `clip_to_wa` twice over
    freshly built, identical input -- never over the same in-memory frame,
    which would prove nothing about determinism."""
    first = maus.clip_to_wa(_toy_global_gdf())
    second = maus.clip_to_wa(_toy_global_gdf())

    assert first.iloc[0]["maus_id"] == second.iloc[0]["maus_id"]


# --- validate_maus_extract ---------------------------------------------------


def test_validate_maus_extract_returns_summary_for_valid_fixture(tmp_path: Path) -> None:
    gpkg_path = tmp_path / "wa_extract.gpkg"
    clipped = maus.clip_to_wa(_toy_global_gdf())
    clipped.to_file(gpkg_path, driver="GPKG", layer="wa_extract")

    summary = maus.validate_maus_extract(gpkg_path)

    assert summary["feature_count"] == 1
    assert "4326" in str(summary["crs"])
    columns = summary["columns"]
    assert isinstance(columns, list)
    assert "maus_id" in columns


def test_validate_maus_extract_raises_on_empty_layer(tmp_path: Path) -> None:
    gpkg_path = tmp_path / "empty.gpkg"
    empty_gdf = gpd.GeoDataFrame({"name": []}, geometry=[], crs="EPSG:4326")
    empty_gdf.to_file(gpkg_path, driver="GPKG", layer="wa_extract")

    with pytest.raises(maus.SnapshotValidationError) as excinfo:
        maus.validate_maus_extract(gpkg_path)
    assert str(gpkg_path) in str(excinfo.value)


def test_validate_maus_extract_raises_on_missing_file(tmp_path: Path) -> None:
    gpkg_path = tmp_path / "does-not-exist.gpkg"

    with pytest.raises(maus.SnapshotValidationError) as excinfo:
        maus.validate_maus_extract(gpkg_path)
    assert str(gpkg_path) in str(excinfo.value)


def test_validate_maus_extract_raises_on_unreadable_file(tmp_path: Path) -> None:
    gpkg_path = tmp_path / "garbage.gpkg"
    gpkg_path.write_bytes(b"not a geopackage")

    with pytest.raises(maus.SnapshotValidationError) as excinfo:
        maus.validate_maus_extract(gpkg_path)
    assert str(gpkg_path) in str(excinfo.value)


def test_validate_maus_extract_raises_on_multiple_layers(tmp_path: Path) -> None:
    """Regression: `pyogrio.list_layers` was checked only for `len == 0`,
    then `gpd.read_file(path)` read ONLY THE FIRST layer -- so a 2-layer
    GeoPackage validated (and would have finalized/manifested) a feature
    count describing an arbitrary fraction of the extract."""
    gpkg_path = tmp_path / "multi.gpkg"
    _write_two_layer_fixture_gpkg(gpkg_path)

    with pytest.raises(maus.SnapshotValidationError) as excinfo:
        maus.validate_maus_extract(gpkg_path)
    message = str(excinfo.value)
    assert str(gpkg_path) in message
    assert "wa_extract" in message
    assert "extra_layer" in message


# --- read_source_gpkg ---------------------------------------------------------


def test_read_source_gpkg_reads_the_local_file(tmp_path: Path) -> None:
    gpkg_path = tmp_path / "global.gpkg"
    _write_global_fixture_gpkg(gpkg_path, wa_feature_count=2)

    gdf = maus.read_source_gpkg(gpkg_path)

    assert len(gdf) == 3  # 2 WA points + 1 Brazil point, unclipped


# --- fetch-maus-extract CLI command -------------------------------------------


def _write_config(tmp_path: Path, data_root: Path) -> Path:
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{data_root}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg_file


def test_fetch_maus_extract_cli_writes_a_verified_snapshot_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    source_gpkg = tmp_path / "global-maus-v2.gpkg"
    _write_global_fixture_gpkg(source_gpkg, wa_feature_count=2)

    # `collect_git_state` shells out to the real checkout; this project's own
    # build phase runs on a repo with zero commits (see CLAUDE.md's cross-task
    # ledger), and `tests/test_provenance.py` establishes the convention that
    # a git-state test never depends on the live project repo either way.
    monkeypatch.setattr(
        "wa_mine_monitor.cli.collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    cli_args = [
        "fetch-maus-extract",
        "--config",
        str(cfg_file),
        "--date",
        "2026-08-15",
        "--source-gpkg",
        str(source_gpkg),
    ]
    # `write_run_manifest` is never given an explicit `argv` by `cli.py`, so
    # it falls back to the REAL `sys.argv` -- which `CliRunner.invoke` does
    # NOT set to `cli_args` above; left alone, `sys.argv` stays pytest's own
    # invocation (e.g. `["__main__.py", ".../test_maus.py", "-q"]`), so the
    # absolute `source_gpkg` path is never in it regardless of whether the
    # manifest writer scrubs it -- making the `str(source_gpkg) not in
    # json.dumps(manifest)` assertion below pass for the wrong reason. Stand
    # in a realistic operator argv, carrying the same absolute path a live
    # invocation would, so that assertion (and the positive one beside it)
    # actually exercise the reduction.
    monkeypatch.setattr(sys, "argv", ["wa-mine-monitor", *cli_args], raising=False)

    result = runner.invoke(app, cli_args)
    assert result.exit_code == 0, result.output

    snapshot_dir = data_root / "raw" / "maus_v2" / "2026-08-15"
    assert snapshot_dir.is_dir()

    extract_path = snapshot_dir / "wa_extract.gpkg"
    assert extract_path.is_file()

    extract_gdf = gpd.read_file(extract_path)
    assert len(extract_gdf) == 2  # only the 2 WA points, the Brazil point dropped
    for _, row in extract_gdf.iterrows():
        assert 112.5 <= row.geometry.x <= 129.1
        assert -35.5 <= row.geometry.y <= -13.5
    assert "maus_id" in extract_gdf.columns

    metadata_text = (snapshot_dir / "metadata.txt").read_text()
    assert "CC-BY-SA-4.0" in metadata_text
    assert "clipped to the WA bounding box from the global v2 dataset" in metadata_text

    assert (snapshot_dir / "SHA256SUMS.txt").is_file()
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)
    assert n_bad == 0
    assert n_missing == 0
    assert n_ok >= 1

    manifest_path = snapshot_dir / "SHA256SUMS.txt.run_manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())

    # Input asset: the LOCAL source gpkg, with its own sha256 and CC-BY-SA-4.0.
    assert manifest["inputs"][0]["licence"] == "CC-BY-SA-4.0"
    assert manifest["inputs"][0]["redistribute_public"] is True
    assert manifest["inputs"][0]["sha256"] == sha256_file(source_gpkg)

    # Provenance records BOTH the PANGAEA DOI and the local source path,
    # root-relativised the same way `write_run_manifest` reduces every other
    # local path it records: `source_gpkg` sits outside `data_root` in this
    # fixture, so it is reduced to its bare filename with `"unrooted"` named
    # explicitly, never the account-identifying absolute path.
    source_record = licence.SOURCES["maus_v2"]
    assert "10.1594/PANGAEA.942325" in manifest["resolved_args"]["source_doi"]
    assert manifest["resolved_args"]["source_doi"] == source_record.source_url
    assert manifest["resolved_args"]["source_local_path"] == source_gpkg.name
    assert manifest["resolved_args"]["source_local_path_root"] == "unrooted"
    assert str(source_gpkg) not in json.dumps(manifest)

    # `argv` carries the SAME absolute path a fourth time (as the
    # `--source-gpkg` flag's value in `sys.argv`, stood in above) and it
    # must be reduced too, not merely absent from the other three fields --
    # a positive assertion, not just an absence one, so a future regression
    # that stopped reducing `argv` specifically would be caught here even if
    # it left the other three fields alone.
    assert str(source_gpkg) not in manifest["argv"]
    assert source_gpkg.name in manifest["argv"]
    source_gpkg_index = manifest["argv"].index(source_gpkg.name)
    assert manifest["argv_paths_root"][source_gpkg_index] == "unrooted"

    # Output asset terms: CC-BY-SA-4.0, redistribute_public True, ShareAlike.
    assert manifest["resolved_args"]["output_licence"] == "CC-BY-SA-4.0"
    assert manifest["resolved_args"]["output_redistribute_public"] is True
    assert manifest["resolved_args"]["output_share_alike"] is True
    assert isinstance(manifest["resolved_args"]["output_share_alike_note"], str)
    assert len(manifest["resolved_args"]["output_share_alike_note"]) > 0


def test_fetch_maus_extract_cli_rejects_a_malformed_date(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    source_gpkg = tmp_path / "global-maus-v2.gpkg"
    _write_global_fixture_gpkg(source_gpkg, wa_feature_count=2)

    result = runner.invoke(
        app,
        [
            "fetch-maus-extract",
            "--config",
            str(cfg_file),
            "--date",
            "15-08-2026",
            "--source-gpkg",
            str(source_gpkg),
        ],
    )
    assert result.exit_code != 0
    assert not (data_root / "raw").exists()


def test_fetch_maus_extract_cli_rejects_a_missing_source_gpkg(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    result = runner.invoke(
        app,
        [
            "fetch-maus-extract",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-15",
            "--source-gpkg",
            str(tmp_path / "does-not-exist.gpkg"),
        ],
    )
    assert result.exit_code != 0
    assert not (data_root / "raw").exists()


def test_fetch_maus_extract_cli_reports_a_validation_failure_without_finalizing(
    tmp_path: Path,
) -> None:
    """A source gpkg with no features inside WA_BBOX must be reported, not
    silently finalized and manifested as though it were a good snapshot.

    Asserts the GRACEFUL report, not merely a non-zero exit: an earlier
    version of this test checked `exit_code != 0` alone, which an unhandled
    traceback satisfies just as well as a structured refusal -- and that is
    exactly what the command did. `clip_to_wa` built `maus_id` with
    `GeoSeries.apply`, which preserves the geometry dtype on an EMPTY
    result, so the zero-feature frame carried geometry columns
    `['geometry', 'maus_id']` and `GeoDataFrame.to_file` (one geometry
    column only) raised `ValueError` before `validate_maus_extract` was ever
    called. The command exited 1 with EMPTY stdout and a raw traceback, and
    `validate_maus_extract`'s carefully worded "no source feature fell
    inside WA_BBOX" message was unreachable through the CLI. So this now
    matches the tenements/minedex refusal tests: no traceback, and stdout
    parses as JSON carrying `validation_error`."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    source_gpkg = tmp_path / "global-maus-v2-no-wa.gpkg"
    _write_no_wa_features_fixture_gpkg(source_gpkg)

    result = runner.invoke(
        app,
        [
            "fetch-maus-extract",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-15",
            "--source-gpkg",
            str(source_gpkg),
        ],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output

    payload = json.loads(result.output)
    assert "validation_error" in payload
    assert "no source feature fell inside WA_BBOX" in payload["validation_error"]

    snapshot_dir = data_root / "raw" / "maus_v2" / "2026-08-15"
    assert not (snapshot_dir / "SHA256SUMS.txt").exists()


def test_clip_to_wa_on_an_empty_clip_keeps_exactly_one_geometry_column(tmp_path: Path) -> None:
    """Regression at the unit boundary for the same defect: on a
    zero-feature clip `maus_id` must be a declared string column, never a
    second geometry column, and the frame must still be writable.

    `GeoSeries.apply` returns a geometry-dtype Series when the result is
    empty, which made `maus_id` a SECOND geometry column and broke
    `to_file`. Asserted here on the frame `clip_to_wa` returns AND through a
    real `to_file` write, because the dtype alone is not what the CLI
    depends on."""
    source_gpkg = tmp_path / "global-maus-v2-no-wa.gpkg"
    _write_no_wa_features_fixture_gpkg(source_gpkg)

    clipped = maus.clip_to_wa(maus.read_source_gpkg(source_gpkg))

    assert len(clipped) == 0
    geometry_columns = [
        column for column in clipped.columns if clipped[column].dtype.name == "geometry"
    ]
    assert geometry_columns == ["geometry"]
    assert clipped["maus_id"].dtype.name == "string"

    written = tmp_path / "wa_extract.gpkg"
    clipped.to_file(written, driver="GPKG", layer="wa_extract")
    assert written.exists()


def test_fetch_maus_extract_cli_refuses_a_second_invocation_for_an_already_finalized_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a re-run for a date that already succeeded used to
    re-read the source gpkg, re-clip and overwrite `wa_extract.gpkg`, only
    then hitting an uncaught `FileExistsError` from `finalize_snapshot`.
    This asserts the re-run is refused BEFORE the source gpkg is even read,
    and that the extract bytes, `SHA256SUMS.txt` and `verify_snapshot`'s
    verdict are all untouched by the second invocation."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    source_gpkg = tmp_path / "global-maus-v2.gpkg"
    _write_global_fixture_gpkg(source_gpkg, wa_feature_count=2)

    monkeypatch.setattr(
        "wa_mine_monitor.cli.collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    invoke_args = [
        "fetch-maus-extract",
        "--config",
        str(cfg_file),
        "--date",
        "2026-08-15",
        "--source-gpkg",
        str(source_gpkg),
    ]

    first = runner.invoke(app, invoke_args)
    assert first.exit_code == 0, first.output

    snapshot_dir = data_root / "raw" / "maus_v2" / "2026-08-15"
    extract_bytes_before = (snapshot_dir / "wa_extract.gpkg").read_bytes()
    sums_before = (snapshot_dir / "SHA256SUMS.txt").read_text()
    verify_before = snapshots.verify_snapshot(snapshot_dir)

    second = runner.invoke(app, invoke_args)
    assert second.exit_code != 0
    assert "Traceback" not in second.output

    assert (snapshot_dir / "wa_extract.gpkg").read_bytes() == extract_bytes_before
    assert (snapshot_dir / "SHA256SUMS.txt").read_text() == sums_before
    assert snapshots.verify_snapshot(snapshot_dir) == verify_before
