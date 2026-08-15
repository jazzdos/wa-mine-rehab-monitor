"""Tests for `wa_mine_monitor.sources.tenements` and the `fetch-tenements` CLI command.

D6 acquisition route (2026-08-16): DMIRS-003 tenements are acquired as an
unauthenticated DASC bundle zip (`DASC_TENEMENTS_SHP_URL`, file id 2056), not
the auth-gated SLIP direct-download GeoPackage this file used to test against
-- see `sources/tenements.py`'s module docstring for the ruling.

Fixture-first rule: no test in this file touches the network. The pure
validation tests build a toy DASC-shaped zip in-test with geopandas (via
`tests.sources._fixtures`), never a committed binary fixture. The download
function's request-shaping is checked against a fake `requests.get`, never a
real endpoint. The CLI test monkeypatches `wa_mine_monitor.cli.download_tenements_zip`
outright and copies a fixture zip in its place -- a live download only ever
happens when an operator runs the CLI for real.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Self

import geopandas as gpd
import pytest
from shapely.geometry import Polygon
from typer.testing import CliRunner

from tests.sources._fixtures import (
    PLACEHOLDER_LICENCE_PDF_BYTES,
    shapefile_members,
    write_zip,
)
from wa_mine_monitor import cli as cli_module
from wa_mine_monitor import snapshots
from wa_mine_monitor.cli import app
from wa_mine_monitor.sources import tenements

runner = CliRunner()

# --- fixture builders -------------------------------------------------------


def _build_tenements_gdf(
    *, feature_count: int = 3, crs: str = "EPSG:7844", extract_date: str = "14/08/2026"
) -> gpd.GeoDataFrame:
    """A toy DMIRS-003-DASC-shaped GeoDataFrame: `feature_count` polygons
    carrying the columns `validate_tenements_zip` requires
    (`FMT_TENID`/`TENSTATUS`/`HOLDER1`/`EXTRACT_DA`), all sharing one
    `extract_date` -- callers that need a multi-extract-date fixture pass a
    per-feature list instead."""
    polygons = [
        Polygon(
            [
                (115.0 + i, -32.0 - i),
                (115.1 + i, -32.0 - i),
                (115.1 + i, -31.9 - i),
                (115.0 + i, -31.9 - i),
            ]
        )
        for i in range(feature_count)
    ]
    extract_dates = (
        extract_date if isinstance(extract_date, list) else [extract_date] * feature_count
    )
    return gpd.GeoDataFrame(
        {
            "FMT_TENID": [f"E70/{1000 + i}" for i in range(feature_count)],
            "TENSTATUS": ["Live"] * feature_count,
            "HOLDER1": [f"Holder {i}" for i in range(feature_count)],
            "EXTRACT_DA": extract_dates,
            "geometry": polygons,
        },
        crs=crs,
    )


def _write_tenements_zip(
    zip_path: Path,
    tmp_dir: Path,
    *,
    feature_count: int = 3,
    crs: str = "EPSG:7844",
    extract_date: str | list[str] = "14/08/2026",
    omit_members: tuple[str, ...] = (),
    drop_columns: tuple[str, ...] = (),
) -> Path:
    """Build a full DASC tenements SHP zip fixture: a shapefile (via
    `_build_tenements_gdf`) zipped alongside placeholder `*.pdf` members
    under every name `TENEMENTS_REQUIRED_MEMBERS` expects.

    `omit_members` drops named members entirely (a truncated download);
    `drop_columns` drops named columns from the GeoDataFrame BEFORE writing
    the shapefile (a schema regression) -- these are two different defects
    with two different refusal messages, so a caller exercises exactly one
    at a time.
    """
    gdf = _build_tenements_gdf(feature_count=feature_count, crs=crs, extract_date=extract_date)
    if drop_columns:
        gdf = gdf.drop(columns=list(drop_columns))
    members = shapefile_members(gdf, tmp_dir, tenements.TENEMENTS_SHAPEFILE_BASENAME)
    members["CurrentTenements_DataDictionary.pdf"] = b"placeholder data dictionary"
    members["CurrentTenements_Metadata_GDA2020.pdf"] = b"placeholder metadata"
    members["Licence_CCBY4.pdf"] = PLACEHOLDER_LICENCE_PDF_BYTES
    for name in omit_members:
        members.pop(name, None)
    return write_zip(zip_path, members)


# --- pinned constants ---------------------------------------------------------


def test_dasc_tenements_shp_url_is_pinned() -> None:
    """A silent change to this URL must be a loud, reviewable diff -- not a
    quiet redirection of what `fetch-tenements` downloads."""
    assert tenements.DASC_TENEMENTS_SHP_URL == "https://dasc.dmirs.wa.gov.au/Download/File/2056"


def test_tenements_zip_filename_is_pinned() -> None:
    assert tenements.TENEMENTS_ZIP_FILENAME == "tenements_current_gda2020_shp.zip"


def test_tenements_required_members_are_pinned() -> None:
    assert tenements.TENEMENTS_REQUIRED_MEMBERS == (
        "CurrentTenements.cpg",
        "CurrentTenements.dbf",
        "CurrentTenements.prj",
        "CurrentTenements.shp",
        "CurrentTenements.shx",
        "CurrentTenements_DataDictionary.pdf",
        "CurrentTenements_Metadata_GDA2020.pdf",
        "Licence_CCBY4.pdf",
    )


def test_tenements_expected_crs_is_pinned() -> None:
    assert tenements.TENEMENTS_EXPECTED_CRS == "EPSG:7844"


def test_tenements_required_columns_are_pinned() -> None:
    assert tenements.TENEMENTS_REQUIRED_COLUMNS == (
        "FMT_TENID",
        "TENSTATUS",
        "HOLDER1",
        "EXTRACT_DA",
    )


# --- validate_tenements_zip ---------------------------------------------------


def test_validate_tenements_zip_returns_summary_for_valid_fixture(tmp_path: Path) -> None:
    zip_path = tmp_path / "tenements.zip"
    _write_tenements_zip(zip_path, tmp_path / "build", feature_count=3)

    summary = tenements.validate_tenements_zip(zip_path)

    assert summary["feature_count"] == 3
    assert summary["crs"] == "EPSG:7844"
    columns = summary["columns"]
    assert isinstance(columns, list)
    for column in tenements.TENEMENTS_REQUIRED_COLUMNS:
        assert column in columns
    members = summary["members"]
    assert isinstance(members, list)
    for member in tenements.TENEMENTS_REQUIRED_MEMBERS:
        assert member in members
    assert summary["extract_date"] == "2026-08-14"


def test_validate_tenements_zip_raises_on_missing_member(tmp_path: Path) -> None:
    zip_path = tmp_path / "tenements.zip"
    _write_tenements_zip(
        zip_path, tmp_path / "build", omit_members=("Licence_CCBY4.pdf", "CurrentTenements.prj")
    )

    with pytest.raises(tenements.SnapshotValidationError) as excinfo:
        tenements.validate_tenements_zip(zip_path)
    message = str(excinfo.value)
    assert str(zip_path) in message
    assert "Licence_CCBY4.pdf" in message
    assert "CurrentTenements.prj" in message


def test_validate_tenements_zip_raises_on_missing_file(tmp_path: Path) -> None:
    zip_path = tmp_path / "does-not-exist.zip"

    with pytest.raises(tenements.SnapshotValidationError) as excinfo:
        tenements.validate_tenements_zip(zip_path)
    assert str(zip_path) in str(excinfo.value)


def test_validate_tenements_zip_raises_on_unreadable_file(tmp_path: Path) -> None:
    zip_path = tmp_path / "garbage.zip"
    zip_path.write_bytes(b"not a zip")

    with pytest.raises(tenements.SnapshotValidationError) as excinfo:
        tenements.validate_tenements_zip(zip_path)
    assert str(zip_path) in str(excinfo.value)


def test_validate_tenements_zip_raises_on_empty_shapefile(tmp_path: Path) -> None:
    zip_path = tmp_path / "empty.zip"
    _write_tenements_zip(zip_path, tmp_path / "build", feature_count=0)

    with pytest.raises(tenements.SnapshotValidationError) as excinfo:
        tenements.validate_tenements_zip(zip_path)
    assert "empty" in str(excinfo.value)


def test_validate_tenements_zip_raises_on_wrong_crs(tmp_path: Path) -> None:
    zip_path = tmp_path / "wrong_crs.zip"
    _write_tenements_zip(zip_path, tmp_path / "build", crs="EPSG:4326")

    with pytest.raises(tenements.SnapshotValidationError) as excinfo:
        tenements.validate_tenements_zip(zip_path)
    message = str(excinfo.value)
    assert "4326" in message
    assert "7844" in message


def test_validate_tenements_zip_raises_on_missing_required_column(tmp_path: Path) -> None:
    zip_path = tmp_path / "missing_column.zip"
    _write_tenements_zip(zip_path, tmp_path / "build", drop_columns=("HOLDER1",))

    with pytest.raises(tenements.SnapshotValidationError) as excinfo:
        tenements.validate_tenements_zip(zip_path)
    assert "HOLDER1" in str(excinfo.value)


def test_validate_tenements_zip_raises_on_multiple_extract_dates(tmp_path: Path) -> None:
    zip_path = tmp_path / "multi_extract.zip"
    _write_tenements_zip(
        zip_path,
        tmp_path / "build",
        feature_count=3,
        extract_date=["14/08/2026", "14/08/2026", "13/08/2026"],
    )

    with pytest.raises(tenements.SnapshotValidationError) as excinfo:
        tenements.validate_tenements_zip(zip_path)
    message = str(excinfo.value)
    assert "2026-08-14" in message
    assert "2026-08-13" in message


# --- download_tenements_zip --------------------------------------------------


class _FakeStreamedResponse:
    """A minimal stand-in for `requests.Response` used as a context manager."""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True

    def iter_content(self, chunk_size: int):  # type: ignore[no-untyped-def]
        yield self._content

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_download_tenements_zip_streams_with_explicit_timeout_and_user_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the "requests, streamed chunks, explicit timeout,
    explicit User-Agent" contract -- checked against a fake `requests.get`,
    never a real network call (fixture-first rule)."""
    captured: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> _FakeStreamedResponse:
        captured["url"] = url
        captured.update(kwargs)
        return _FakeStreamedResponse(b"fake-zip-bytes")

    monkeypatch.setattr(tenements.requests, "get", fake_get)

    dest_path = tmp_path / "nested" / "tenements.zip"
    result = tenements.download_tenements_zip("https://example.test/download", dest_path)

    assert result == dest_path
    assert dest_path.read_bytes() == b"fake-zip-bytes"
    assert captured["url"] == "https://example.test/download"
    assert captured["stream"] is True
    assert isinstance(captured["timeout"], (int, float))
    assert captured["timeout"] > 0
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "wa-mine-rehab-monitor" in headers["User-Agent"]


def test_download_tenements_zip_raises_for_a_bad_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests

    def fake_get(url: str, **kwargs: object) -> _FakeStreamedResponse:
        class _Failing(_FakeStreamedResponse):
            def raise_for_status(self) -> None:
                raise requests.HTTPError("500 Server Error")

        return _Failing(b"")

    monkeypatch.setattr(tenements.requests, "get", fake_get)

    with pytest.raises(requests.HTTPError):
        tenements.download_tenements_zip("https://example.test/download", tmp_path / "out.zip")


# --- fetch-tenements CLI command ---------------------------------------------


def _write_config(tmp_path: Path, data_root: Path) -> Path:
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{data_root}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg_file


def _monkeypatch_download_from_fixture(
    monkeypatch: pytest.MonkeyPatch, fixture_zip: Path, *, call_count: dict[str, int] | None = None
) -> None:
    def fake_download(url: str, dest_path: Path, **kwargs: object) -> Path:
        if call_count is not None:
            call_count["n"] += 1
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture_zip, dest_path)
        return dest_path

    monkeypatch.setattr(cli_module, "download_tenements_zip", fake_download)


def test_fetch_tenements_cli_writes_a_verified_snapshot_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    fixture_zip = tmp_path / "fixture.zip"
    _write_tenements_zip(fixture_zip, tmp_path / "build", feature_count=3)

    _monkeypatch_download_from_fixture(monkeypatch, fixture_zip)
    # `collect_git_state` shells out to the real checkout; this project's own
    # build phase runs on a repo with zero commits (see CLAUDE.md's cross-task
    # ledger), and `tests/test_provenance.py` establishes the convention that
    # a git-state test never depends on the live project repo either way.
    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    result = runner.invoke(
        app,
        ["fetch-tenements", "--config", str(cfg_file), "--date", "2026-08-15"],
    )
    assert result.exit_code == 0, result.output

    snapshot_dir = data_root / "raw" / "dmirs_003_tenements" / "2026-08-15"
    assert snapshot_dir.is_dir()
    assert (snapshot_dir / tenements.TENEMENTS_ZIP_FILENAME).is_file()
    metadata_text = (snapshot_dir / "metadata.txt").read_text()
    assert "CC-BY-4.0" in metadata_text
    assert "catalogue.data.wa.gov.au" in metadata_text
    assert "2056" in metadata_text
    assert (snapshot_dir / "SHA256SUMS.txt").is_file()

    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)
    assert n_bad == 0
    assert n_missing == 0
    assert n_ok >= 1

    manifest_path = snapshot_dir / "SHA256SUMS.txt.run_manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["inputs"][0]["licence"] == "CC-BY-4.0"
    assert manifest["inputs"][0]["redistribute_public"] is True
    assert manifest["resolved_args"]["validation_summary"]["feature_count"] == 3


def test_fetch_tenements_cli_survives_a_zero_commit_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE DEFECT this closes, driven end to end: `collect_git_state` shells
    out to `git rev-parse HEAD` with `check=True`, which raises on a repo
    with no commits -- exactly this project's own current build-phase repo
    state (`CLAUDE.md`'s cross-task ledger: "zero commits exist"). Every
    OTHER happy-path test in this file monkeypatches `collect_git_state`
    itself, so none of them can see this; this one patches `_REPO_ROOT`
    instead, to a real git repo built in-test with `git init` and no
    commit, and leaves `collect_git_state` UNPATCHED. Before the fix:
    `CalledProcessError`, uncaught, exit 1, after an empty snapshot
    directory had already been created."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    fixture_zip = tmp_path / "fixture.zip"
    _write_tenements_zip(fixture_zip, tmp_path / "build", feature_count=3)
    _monkeypatch_download_from_fixture(monkeypatch, fixture_zip)

    zero_commit_repo = tmp_path / "zero-commit-repo"
    zero_commit_repo.mkdir()
    subprocess.run(["git", "init"], cwd=zero_commit_repo, check=True, capture_output=True)
    monkeypatch.setattr(cli_module, "_REPO_ROOT", zero_commit_repo)

    result = runner.invoke(
        app,
        ["fetch-tenements", "--config", str(cfg_file), "--date", "2026-08-15"],
    )
    assert result.exit_code == 0, result.output

    manifest_path = (
        data_root
        / "raw"
        / "dmirs_003_tenements"
        / "2026-08-15"
        / "SHA256SUMS.txt.run_manifest.json"
    )
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["git"]["sha"] is None
    assert manifest["git"]["unborn_head"] is True


def test_fetch_tenements_cli_rejects_a_malformed_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    def fake_download(url: str, dest_path: Path, **kwargs: object) -> Path:
        raise AssertionError("download must not be reached for a rejected --date")

    monkeypatch.setattr(cli_module, "download_tenements_zip", fake_download)

    result = runner.invoke(
        app,
        ["fetch-tenements", "--config", str(cfg_file), "--date", "15-08-2026"],
    )
    assert result.exit_code != 0
    assert not (data_root / "raw").exists()


def test_fetch_tenements_cli_reports_a_validation_failure_without_finalizing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty/unreadable download must be reported, not silently finalized
    and manifested as though it were a good snapshot."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    empty_zip = tmp_path / "empty.zip"
    _write_tenements_zip(empty_zip, tmp_path / "build", feature_count=0)

    _monkeypatch_download_from_fixture(monkeypatch, empty_zip)

    result = runner.invoke(
        app,
        ["fetch-tenements", "--config", str(cfg_file), "--date", "2026-08-15"],
    )
    assert result.exit_code != 0

    snapshot_dir = data_root / "raw" / "dmirs_003_tenements" / "2026-08-15"
    assert not (snapshot_dir / "SHA256SUMS.txt").exists()


def test_fetch_tenements_cli_refuses_a_second_invocation_for_an_already_finalized_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `create_snapshot_dir` is idempotent by design, so a
    re-run for a date that already succeeded used to proceed all the way
    through a second download and overwrite the zip, only THEN hitting an
    uncaught `FileExistsError` from `finalize_snapshot`. This asserts the
    re-run is refused BEFORE the download is even reached, and that the zip
    bytes, `SHA256SUMS.txt` and `verify_snapshot`'s verdict are all
    untouched by the second invocation."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    fixture_zip = tmp_path / "fixture.zip"
    _write_tenements_zip(fixture_zip, tmp_path / "build", feature_count=3)

    call_count = {"n": 0}
    _monkeypatch_download_from_fixture(monkeypatch, fixture_zip, call_count=call_count)
    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )

    first = runner.invoke(
        app, ["fetch-tenements", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert first.exit_code == 0, first.output
    assert call_count["n"] == 1

    snapshot_dir = data_root / "raw" / "dmirs_003_tenements" / "2026-08-15"
    zip_bytes_before = (snapshot_dir / tenements.TENEMENTS_ZIP_FILENAME).read_bytes()
    sums_before = (snapshot_dir / "SHA256SUMS.txt").read_text()
    verify_before = snapshots.verify_snapshot(snapshot_dir)

    second = runner.invoke(
        app, ["fetch-tenements", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert second.exit_code != 0
    assert "Traceback" not in second.output
    assert call_count["n"] == 1  # download never reached on the re-run

    assert (snapshot_dir / tenements.TENEMENTS_ZIP_FILENAME).read_bytes() == zip_bytes_before
    assert (snapshot_dir / "SHA256SUMS.txt").read_text() == sums_before
    assert snapshots.verify_snapshot(snapshot_dir) == verify_before
