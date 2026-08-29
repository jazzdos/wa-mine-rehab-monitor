"""Tests for `sources/silo.py`: object URLs, NetCDF validation, grid
indexing, annual metric math, and the streamed download's request
shaping.

Every NetCDF input is a tiny synthetic fixture written with netCDF4 in
the test itself, and the download test drives a fake `requests.get`.
Nothing here touches the network -- the module's fixture-first rule,
and the owner's metered-connection constraint (design decision 5).
"""

from __future__ import annotations

import calendar
import json
import sys
from pathlib import Path
from typing import Self

import netCDF4
import numpy as np
import pytest
from typer.testing import CliRunner

from wa_mine_monitor import cli as cli_module
from wa_mine_monitor import snapshots
from wa_mine_monitor.cli import app
from wa_mine_monitor.sources import silo

runner = CliRunner()

# A tiny grid on the real 0.05-degree lattice, near Huntly.
LATS = [-32.75, -32.70, -32.65]
LONS = [115.60, 115.65, 115.70]


def write_daily_rain_nc(
    path: Path,
    year: int,
    lats: list[float],
    lons: list[float],
    rain: np.ndarray,
    *,
    fill_value: float = -32768.0,
) -> Path:
    """A minimal SILO-shaped `daily_rain` file: dimensions (time, lat,
    lon), coordinate variables `lat`/`lon` holding cell CENTRES, and a
    `daily_rain` variable with a `_FillValue`. Close enough to the real
    product for every code path under test, and deliberately tiny."""
    ds = netCDF4.Dataset(path, "w", format="NETCDF4")
    try:
        ds.createDimension("time", rain.shape[0])
        ds.createDimension("lat", len(lats))
        ds.createDimension("lon", len(lons))
        time_var = ds.createVariable("time", "f8", ("time",))
        time_var.units = f"days since {year}-01-01"
        time_var[:] = np.arange(rain.shape[0])
        ds.createVariable("lat", "f8", ("lat",))[:] = lats
        ds.createVariable("lon", "f8", ("lon",))[:] = lons
        rain_var = ds.createVariable(
            "daily_rain", "f4", ("time", "lat", "lon"), fill_value=fill_value
        )
        rain_var[:] = rain
    finally:
        ds.close()
    return path


def write_full_year_nc(
    path: Path, year: int, *, daily_mm: float = 1.0, lats: list[float] | None = None
) -> Path:
    """A complete year of uniform daily rainfall over the small grid --
    the shape every validation-passing fixture needs."""
    lats = LATS if lats is None else lats
    n_days = 366 if calendar.isleap(year) else 365
    rain = np.full((n_days, len(lats), len(LONS)), daily_mm, dtype="f4")
    return write_daily_rain_nc(path, year, lats, LONS, rain)


def test_annual_object_url_is_the_documented_bucket_layout() -> None:
    assert silo.annual_object_url("daily_rain", 2003) == (
        "https://silo-open-data.s3.ap-southeast-2.amazonaws.com/"
        "Official/annual/daily_rain/2003.daily_rain.nc"
    )


def test_annual_object_name_matches_the_bucket_basename() -> None:
    assert silo.annual_object_name("daily_rain", 2003) == "2003.daily_rain.nc"


def test_validate_accepts_a_complete_statewide_year(tmp_path: Path) -> None:
    path = write_full_year_nc(tmp_path / "2003.daily_rain.nc", 2003)
    silo.validate_daily_rain_file(path, year=2003, require_statewide=False)


def test_validate_accepts_a_leap_year_of_366_days(tmp_path: Path) -> None:
    path = write_full_year_nc(tmp_path / "2004.daily_rain.nc", 2004)
    assert calendar.isleap(2004)
    silo.validate_daily_rain_file(path, year=2004, require_statewide=False)


def test_validate_refuses_a_short_year(tmp_path: Path) -> None:
    """A truncated download opens cleanly and holds a real daily_rain
    variable; only the day count reveals it. Annual totals from a short
    year read as drought, so this refuses rather than warns."""
    rain = np.ones((364, len(LATS), len(LONS)), dtype="f4")
    path = write_daily_rain_nc(tmp_path / "2003.daily_rain.nc", 2003, LATS, LONS, rain)
    with pytest.raises(silo.SiloError, match="364 days"):
        silo.validate_daily_rain_file(path, year=2003, require_statewide=False)


def test_validate_refuses_a_leap_year_stored_as_365_days(tmp_path: Path) -> None:
    rain = np.ones((365, len(LATS), len(LONS)), dtype="f4")
    path = write_daily_rain_nc(tmp_path / "2004.daily_rain.nc", 2004, LATS, LONS, rain)
    with pytest.raises(silo.SiloError, match="366"):
        silo.validate_daily_rain_file(path, year=2004, require_statewide=False)


def test_validate_refuses_a_missing_variable(tmp_path: Path) -> None:
    ds = netCDF4.Dataset(tmp_path / "bad.nc", "w", format="NETCDF4")
    ds.createDimension("x", 1)
    ds.close()
    with pytest.raises(silo.SiloError, match="daily_rain"):
        silo.validate_daily_rain_file(tmp_path / "bad.nc", year=2003)


def test_validate_refuses_an_unreadable_file(tmp_path: Path) -> None:
    junk = tmp_path / "2003.daily_rain.nc"
    junk.write_bytes(b"not a netcdf file")
    with pytest.raises(silo.SiloError, match="not readable as NetCDF"):
        silo.validate_daily_rain_file(junk, year=2003)


def test_validate_refuses_a_non_silo_grid_spacing(tmp_path: Path) -> None:
    rain = np.ones((365, 3, 3), dtype="f4")
    path = write_daily_rain_nc(
        tmp_path / "2003.daily_rain.nc", 2003, [-32.7, -32.2, -31.7], LONS, rain
    )
    with pytest.raises(silo.SiloError, match="spacing"):
        silo.validate_daily_rain_file(path, year=2003, require_statewide=False)


def test_validate_refuses_a_grid_clipped_short_of_statewide_wa(tmp_path: Path) -> None:
    """D13 F5 acceptance: "Statewide region checks reject inherited
    SW-WA AOI clipping." A south-west-only grid is exactly the defect
    that disqualified ERA5-Land in env-health, and it would produce a
    silently site-incomplete climate table rather than an error."""
    path = write_full_year_nc(tmp_path / "2003.daily_rain.nc", 2003)
    with pytest.raises(silo.SiloError, match="does not cover statewide WA"):
        silo.validate_daily_rain_file(path, year=2003, require_statewide=True)


def test_cell_index_for_point_picks_the_containing_cell(tmp_path: Path) -> None:
    """Coordinate values are cell CENTRES; a point up to half a step
    away on each axis belongs to that cell."""
    path = write_full_year_nc(tmp_path / "2003.daily_rain.nc", 2003)
    grid = silo.read_grid(path)
    lat_i, lon_i = grid.cell_index_for_point(lat=-32.71, lon=115.67)
    assert (grid.lats[lat_i], grid.lons[lon_i]) == (-32.70, 115.65)


def test_cell_index_is_stable_on_a_cell_boundary(tmp_path: Path) -> None:
    """A point exactly on the boundary between two cells resolves
    deterministically (lowest index wins via argmin), so two runs over
    the same footprint never disagree about which cell it sits in."""
    path = write_full_year_nc(tmp_path / "2003.daily_rain.nc", 2003)
    grid = silo.read_grid(path)
    first = grid.cell_index_for_point(lat=-32.725, lon=115.625)
    second = grid.cell_index_for_point(lat=-32.725, lon=115.625)
    assert first == second


def test_cell_index_refuses_a_point_outside_the_grid(tmp_path: Path) -> None:
    """An out-of-extent footprint must surface as a refusal, never snap
    to an edge cell and report that cell's rainfall as the site's."""
    path = write_full_year_nc(tmp_path / "2003.daily_rain.nc", 2003)
    grid = silo.read_grid(path)
    with pytest.raises(silo.SiloError, match="outside the grid"):
        grid.cell_index_for_point(lat=-10.0, lon=100.0)


def test_cell_id_encodes_the_cell_centre() -> None:
    assert silo.cell_id(lat=-32.70, lon=115.65) == "-32.700_115.650"
    assert silo.cell_id(lat=-32.70, lon=115.675) == "-32.700_115.675"


def test_annual_metrics_sum_and_count_rain_days(tmp_path: Path) -> None:
    """Three days: 0.5 mm (below the 1.0 mm threshold), 1.0 mm (exactly
    at it -- counted, the threshold is >=), 10.0 mm. Total 11.5 mm,
    2 rain days. Validation is bypassed here deliberately: this test is
    about the arithmetic, not the day count."""
    rain = np.array([[[0.5]], [[1.0]], [[10.0]]], dtype="f4")
    path = write_daily_rain_nc(tmp_path / "x.nc", 2003, [-32.70], [115.65], rain)
    series = silo.cell_daily_series(path, lat_i=0, lon_i=0)
    assert silo.annual_metrics(series) == silo.AnnualMetrics(
        annual_rainfall_mm=11.5, rain_days_ge_1mm=2
    )


def test_annual_metrics_over_a_full_leap_year(tmp_path: Path) -> None:
    """366 days at 2.0 mm: 732.0 mm, 366 rain days -- the leap-year day
    is counted, not dropped by an off-by-one over a 365-day assumption."""
    path = write_full_year_nc(tmp_path / "2004.daily_rain.nc", 2004, daily_mm=2.0)
    series = silo.cell_daily_series(path, lat_i=0, lon_i=0)
    metrics = silo.annual_metrics(series)
    assert metrics.rain_days_ge_1mm == 366
    assert metrics.annual_rainfall_mm == pytest.approx(732.0)


def test_annual_metrics_refuse_missing_days_rather_than_zero_fill(tmp_path: Path) -> None:
    """D13 F5 acceptance: "No missing rainfall value becomes zero." A
    fill-valued day makes that cell-year not computable; the caller
    records the reason on the row."""
    fill = -32768.0
    rain = np.array([[[2.0]], [[fill]], [[3.0]]], dtype="f4")
    path = write_daily_rain_nc(tmp_path / "x.nc", 2003, [-32.70], [115.65], rain, fill_value=fill)
    series = silo.cell_daily_series(path, lat_i=0, lon_i=0)
    with pytest.raises(silo.SiloNotComputableError, match="1 missing daily value"):
        silo.annual_metrics(series)


def test_anomaly_is_annual_minus_baseline_mean() -> None:
    assert silo.rainfall_anomaly_mm(
        annual_rainfall_mm=500.0, baseline_annuals_mm=[400.0, 600.0]
    ) == pytest.approx(0.0)
    assert silo.rainfall_anomaly_mm(
        annual_rainfall_mm=650.0, baseline_annuals_mm=[400.0, 600.0]
    ) == pytest.approx(150.0)


def test_anomaly_refuses_an_empty_baseline() -> None:
    """A shorter mean is never silently substituted for the fixed
    1991-2020 baseline; the caller must refuse before reaching here."""
    with pytest.raises(silo.SiloError, match="empty baseline"):
        silo.rainfall_anomaly_mm(annual_rainfall_mm=500.0, baseline_annuals_mm=[])


class _FakeStreamedResponse:
    """A minimal stand-in for `requests.Response` used as a context manager."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):  # type: ignore[no-untyped-def]
        yield self._content

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_download_annual_file_streams_with_explicit_timeout_and_user_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> _FakeStreamedResponse:
        captured["url"] = url
        captured.update(kwargs)
        return _FakeStreamedResponse(b"fake-netcdf-bytes")

    monkeypatch.setattr(silo.requests, "get", fake_get)
    dest = tmp_path / "nested" / "2003.daily_rain.nc"
    result = silo.download_annual_file("https://example.test/2003.daily_rain.nc", dest)

    assert result == dest
    assert dest.read_bytes() == b"fake-netcdf-bytes"
    assert captured["stream"] is True
    assert isinstance(captured["timeout"], (int, float))
    assert captured["timeout"] > 0
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "wa-mine-rehab-monitor" in headers["User-Agent"]


def test_download_annual_file_raises_for_a_bad_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests

    def fake_get(url: str, **kwargs: object) -> _FakeStreamedResponse:
        class _Failing(_FakeStreamedResponse):
            def raise_for_status(self) -> None:
                raise requests.HTTPError("404 Not Found")

        return _Failing(b"")

    monkeypatch.setattr(silo.requests, "get", fake_get)
    with pytest.raises(requests.HTTPError):
        silo.download_annual_file("https://example.test/2003.daily_rain.nc", tmp_path / "out.nc")


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


def _refuse_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any download attempt is a TEST failure, not a download. The owner
    is on a metered connection (design decision 5): no test path may
    ever reach the bucket."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("network attempted")

    monkeypatch.setattr(cli_module, "download_annual_file", _boom)


def _write_statewide_year_nc(path: Path, year: int) -> Path:
    """A complete year on the small `LATS`/`LONS` grid, for CLI tests
    that run with `cli._SILO_REQUIRE_STATEWIDE` monkeypatched to False.

    A fixture that really spanned WA_BBOX at 0.05 degrees would be
    ~200 MB. The statewide coverage rule is exercised instead by
    `test_validate_refuses_a_grid_clipped_short_of_statewide_wa` above,
    at unit level.
    """
    return write_full_year_nc(path, year)


def test_fetch_silo_dry_run_prints_the_object_list_and_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--dry-run is the metered-connection guard: the full plan is
    disclosed with ZERO network and ZERO writes, so the owner can see
    the byte cost before committing to it."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _refuse_network(monkeypatch)

    result = runner.invoke(
        app,
        [
            "fetch-silo",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-30",
            "--start-year",
            "2001",
            "--end-year",
            "2003",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["objects"] == [
        silo.annual_object_url("daily_rain", year) for year in (2001, 2002, 2003)
    ]
    assert not (data_root / "raw" / "silo").exists()


def test_fetch_silo_refuses_an_inverted_year_range_before_any_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _refuse_network(monkeypatch)

    result = runner.invoke(
        app,
        [
            "fetch-silo",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-30",
            "--start-year",
            "2003",
            "--end-year",
            "2001",
        ],
    )
    assert result.exit_code == 1
    assert "refusal" in json.loads(result.output)
    assert not (data_root / "raw" / "silo").exists()


def test_fetch_silo_refuses_a_finalized_snapshot_before_any_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    _refuse_network(monkeypatch)
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "silo", "2026-08-30")
    (snapshot_dir / "SHA256SUMS.txt").write_text("")

    result = runner.invoke(
        app,
        [
            "fetch-silo",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-30",
            "--start-year",
            "2001",
            "--end-year",
            "2001",
        ],
    )
    assert result.exit_code == 1
    assert "refusal" in json.loads(result.output)


def test_fetch_silo_downloads_validates_and_finalizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path with `download_annual_file` faked to drop a real
    (synthetic, statewide) NetCDF at the destination: the file lands
    under raw/silo/<date>/, metadata and SHA256SUMS exist, verify is
    clean, and the manifest carries one SourceAsset per file."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    monkeypatch.setattr(cli_module, "_SILO_REQUIRE_STATEWIDE", False)

    def fake_download(url: str, dest_path: Path, **kwargs: object) -> Path:
        return _write_statewide_year_nc(Path(dest_path), 2001)

    monkeypatch.setattr(cli_module, "download_annual_file", fake_download)
    cli_args = [
        "fetch-silo",
        "--config",
        str(cfg_file),
        "--date",
        "2026-08-30",
        "--start-year",
        "2001",
        "--end-year",
        "2001",
    ]
    monkeypatch.setattr(sys, "argv", ["wa-mine-monitor", *cli_args], raising=False)

    result = runner.invoke(app, cli_args)
    assert result.exit_code == 0, result.output

    snapshot_dir = data_root / "raw" / "silo" / "2026-08-30"
    assert (snapshot_dir / "2001.daily_rain.nc").is_file()
    assert (snapshot_dir / "metadata.txt").is_file()
    assert (snapshot_dir / "SHA256SUMS.txt").is_file()
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)
    assert (n_bad, n_missing) == (0, 0)
    assert n_ok >= 1

    manifest = json.loads((snapshot_dir / "SHA256SUMS.txt.run_manifest.json").read_text())
    assert len(manifest["inputs"]) == 1
    assert manifest["inputs"][0]["licence"] == "CC-BY-4.0"
    assert manifest["inputs"][0]["uri"] == silo.annual_object_url("daily_rain", 2001)


def test_fetch_silo_refuses_an_invalid_download_before_finalize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A downloaded file that fails validation refuses BEFORE
    finalize_snapshot: junk bytes never enter SHA256SUMS, so a later
    build cannot digest-verify a corrupt snapshot clean."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    monkeypatch.setattr(cli_module, "_SILO_REQUIRE_STATEWIDE", False)

    def fake_download(url: str, dest_path: Path, **kwargs: object) -> Path:
        Path(dest_path).write_bytes(b"not a netcdf")
        return Path(dest_path)

    monkeypatch.setattr(cli_module, "download_annual_file", fake_download)
    result = runner.invoke(
        app,
        [
            "fetch-silo",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-30",
            "--start-year",
            "2001",
            "--end-year",
            "2001",
        ],
    )
    assert result.exit_code == 1
    assert "refusal" in json.loads(result.output)
    snapshot_dir = data_root / "raw" / "silo" / "2026-08-30"
    assert not (snapshot_dir / "SHA256SUMS.txt").exists()


def test_fetch_silo_resumes_by_skipping_a_valid_file_already_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted fetch leaves a dated directory with some years
    already down. A re-run must validate and SKIP those, not re-pull
    ~410 MB each over a metered connection."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    monkeypatch.setattr(cli_module, "_SILO_REQUIRE_STATEWIDE", False)
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "silo", "2026-08-30")
    _write_statewide_year_nc(snapshot_dir / "2001.daily_rain.nc", 2001)

    def fake_download(url: str, dest_path: Path, **kwargs: object) -> Path:
        return _write_statewide_year_nc(Path(dest_path), 2002)

    monkeypatch.setattr(cli_module, "download_annual_file", fake_download)
    result = runner.invoke(
        app,
        [
            "fetch-silo",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-30",
            "--start-year",
            "2001",
            "--end-year",
            "2002",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["resumed"] == 1
    assert payload["fetched"] == 1


def test_fetch_silo_refuses_a_stray_part_file_left_by_an_earlier_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`finalize_snapshot` hashes EVERY file under the snapshot
    directory (`snapshots.py:182-186`), and `verify_snapshot` afterwards
    checks integrity, not SILO validity. A `.part` left by an earlier
    failed download over a WIDER year range is never looked at by this
    run's per-year loop, so without the pre-finalize gate it would be
    checksummed into SHA256SUMS.txt and verify clean -- a finalized
    snapshot carrying a truncated file.

    The gate runs BEFORE the fetch loop, not just before finalize: each
    annual object is ~410 MB and this command exists to be run off a
    metered connection, so a stray file must cost nothing to discover.
    `n_downloads == 0` is the assertion that keeps it there.
    """
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _stub_git_state(monkeypatch)
    monkeypatch.setattr(cli_module, "_SILO_REQUIRE_STATEWIDE", False)
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "silo", "2026-08-30")
    (snapshot_dir / "2005.daily_rain.nc.part").write_bytes(b"truncated")

    n_downloads = 0

    def fake_download(url: str, dest_path: Path, **kwargs: object) -> Path:
        nonlocal n_downloads
        n_downloads += 1
        return _write_statewide_year_nc(Path(dest_path), 2001)

    monkeypatch.setattr(cli_module, "download_annual_file", fake_download)
    result = runner.invoke(
        app,
        [
            "fetch-silo",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-30",
            "--start-year",
            "2001",
            "--end-year",
            "2001",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "refusal" in payload
    assert "2005.daily_rain.nc.part" in json.dumps(payload)
    assert "before fetching" in payload["refusal"]
    assert n_downloads == 0
    assert not (snapshot_dir / "SHA256SUMS.txt").exists()
