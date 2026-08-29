"""SILO gridded daily rainfall: object URLs and NetCDF validation.

This project consumes SILO's GRIDDED product from the anonymous AWS
open-data bucket (`s3://silo-open-data`, ap-southeast-2, CC BY 4.0;
see `licence.SOURCES["silo"]` and
`docs/decisions/2026-08-26-silo-gridded-feed.md`). One object per year
per variable holds that year's DAILY 0.05-degree grids. Only
`daily_rain` is fetched: `rain_days_ge_1mm` cannot be derived from
annual totals, and the D13 F5 schema names rainfall fields only.

The account-gated point/Data Drill API is NOT used, so no credential
exists on this route -- the D13 F5 `secrets.py` work and its
credential-redaction tests are objectless here and are dropped by the
recorded deviation, not silently skipped.

Streamed download and CLI wiring land in a later task; this module
covers only the pieces a downloader needs before it touches the
network -- the object naming, and the validation that must run on
whatever it fetches before that file is trusted downstream.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import netCDF4
import numpy as np
import requests

from wa_mine_monitor.sources.maus import WA_BBOX

_BUCKET_BASE = "https://silo-open-data.s3.ap-southeast-2.amazonaws.com/Official/annual"

#: SILO's published grid step. Asserted at validation time against each
#: file's own coordinate variables, so a future product change fails
#: loudly here rather than silently mis-indexing cells downstream.
GRID_STEP_DEGREES = 0.05

#: User-Agent sent with every download request, identifying this project
#: rather than falling back to `requests`' default -- some endpoints treat an
#: unidentified client as a bot and block it. Mirrors `sources/tenements.py`.
_USER_AGENT = "wa-mine-rehab-monitor/0.1 (github.com/jazzdos/wa-mine-rehab-monitor)"

#: Streaming download timeout in seconds. Explicit because `requests` has no
#: default timeout at all: an unresponsive endpoint would otherwise hang a
#: fetch indefinitely. Longer than `tenements.py`'s 60 s because these
#: annual objects run to ~410 MB rather than a few MB.
_DOWNLOAD_TIMEOUT_SECONDS = 300.0

#: Chunk size for the streamed download. Never buffers a ~410 MB annual
#: object in memory.
_DOWNLOAD_CHUNK_SIZE = 1_048_576  # 1 MiB


class SiloError(ValueError):
    """A SILO artefact violated an expectation this module refuses on."""


def annual_object_url(variable: str, year: int) -> str:
    """The bucket URL for one year of daily grids for `variable`."""
    return f"{_BUCKET_BASE}/{variable}/{year}.{variable}.nc"


def annual_object_name(variable: str, year: int) -> str:
    """The local filename for that object -- identical to the bucket
    basename, so a snapshot directory listing reads as the bucket did."""
    return f"{year}.{variable}.nc"


def expected_day_count(year: int) -> int:
    """Days SILO stores for `year`: 366 in a leap year, else 365."""
    return 366 if calendar.isleap(year) else 365


def validate_daily_rain_file(path: Path, *, year: int, require_statewide: bool = True) -> None:
    """Refuse `path` unless it is a readable NetCDF holding a complete
    year of `daily_rain` on the published 0.05-degree grid.

    Four separate failures, each of which would otherwise survive into a
    finalized snapshot and corrupt every downstream metric:

    1. unreadable/truncated bytes -- a partial download;
    2. no `daily_rain` variable -- the wrong object was fetched;
    3. wrong coordinate spacing -- not the SILO grid this code indexes;
    4. a short year -- an annual total over 364 days reads as drought.

    `require_statewide` additionally asserts the grid spans `WA_BBOX`.
    It is on by default (real SILO grids cover the continent) and turned
    off only by tests using deliberately tiny fixture grids.

    Runs BEFORE `finalize_snapshot`, so nothing that fails here ever
    enters `SHA256SUMS.txt`.
    """
    try:
        ds = netCDF4.Dataset(path, "r")
    except OSError as exc:
        raise SiloError(f"{path} is not readable as NetCDF: {exc}") from exc
    try:
        if "daily_rain" not in ds.variables:
            raise SiloError(
                f"{path} has no daily_rain variable -- refusing to treat it as a "
                "SILO daily rainfall file"
            )
        coords: dict[str, np.ndarray] = {}
        for axis in ("lat", "lon"):
            if axis not in ds.variables:
                raise SiloError(f"{path} has no {axis} coordinate variable")
            values = np.asarray(ds.variables[axis][:], dtype="f8")
            if values.size >= 2:
                steps = np.abs(np.diff(values))
                if not np.allclose(steps, GRID_STEP_DEGREES, atol=1e-6):
                    raise SiloError(
                        f"{path} {axis} spacing is not the expected "
                        f"{GRID_STEP_DEGREES} degree SILO grid"
                    )
            coords[axis] = values

        n_days = int(ds.variables["daily_rain"].shape[0])
        expected = expected_day_count(year)
        if n_days != expected:
            raise SiloError(
                f"{path} holds {n_days} days but {year} has {expected} -- refusing an "
                "incomplete year (a short year understates every annual total)"
            )

        if require_statewide:
            lon_min, lat_min, lon_max, lat_max = WA_BBOX
            half = GRID_STEP_DEGREES / 2 + 1e-6
            covers = (
                coords["lat"].min() - half <= lat_min
                and coords["lat"].max() + half >= lat_max
                and coords["lon"].min() - half <= lon_min
                and coords["lon"].max() + half >= lon_max
            )
            if not covers:
                raise SiloError(
                    f"{path} does not cover statewide WA (needs {WA_BBOX}, has "
                    f"lat {coords['lat'].min()}..{coords['lat'].max()}, "
                    f"lon {coords['lon'].min()}..{coords['lon'].max()}) -- an "
                    "AOI-clipped grid would silently drop sites outside it"
                )
    finally:
        ds.close()


def cell_id(*, lat: float, lon: float) -> str:
    """`silo_cell_id` for a cell CENTRE: fixed three-decimal
    `lat_lon`. Self-describing -- a reader recovers the cell centre from
    the id alone, with no lookup table -- and stable across runs, which
    is what the D13 F5 schema keys climate context on."""
    return f"{lat:.3f}_{lon:.3f}"


@dataclass(frozen=True)
class SiloGrid:
    """One file's coordinate arrays. `lats`/`lons` are cell CENTRES in
    the file's own storage order; indexing works whether that order is
    ascending or descending."""

    lats: tuple[float, ...]
    lons: tuple[float, ...]

    def cell_index_for_point(self, *, lat: float, lon: float) -> tuple[int, int]:
        """Indices of the cell whose centre is nearest `(lat, lon)`.

        Refuses a point more than half a grid step from every centre --
        i.e. outside the grid -- rather than snapping to an edge cell.
        Snapping would attribute a neighbouring cell's rainfall to the
        site with nothing on the row disclosing it.

        `np.argmin` breaks an exact tie (a point on a cell boundary) by
        lowest index, which is deterministic across runs.
        """
        lat_arr = np.asarray(self.lats)
        lon_arr = np.asarray(self.lons)
        lat_i = int(np.argmin(np.abs(lat_arr - lat)))
        lon_i = int(np.argmin(np.abs(lon_arr - lon)))
        half = GRID_STEP_DEGREES / 2 + 1e-9
        if abs(lat_arr[lat_i] - lat) > half or abs(lon_arr[lon_i] - lon) > half:
            raise SiloError(
                f"point (lat={lat}, lon={lon}) is outside the grid -- refusing to "
                "snap to an edge cell"
            )
        return lat_i, lon_i


def read_grid(path: Path) -> SiloGrid:
    """The coordinate arrays of an already-validated `daily_rain` file."""
    ds = netCDF4.Dataset(path, "r")
    try:
        lats = tuple(float(v) for v in np.asarray(ds.variables["lat"][:], dtype="f8"))
        lons = tuple(float(v) for v in np.asarray(ds.variables["lon"][:], dtype="f8"))
    finally:
        ds.close()
    return SiloGrid(lats=lats, lons=lons)


#: D13 F5 names ">=1 mm rain-day counts": the threshold is inclusive.
RAIN_DAY_THRESHOLD_MM = 1.0


class SiloNotComputableError(SiloError):
    """A cell-year could not be computed from the data present.

    Distinct from `SiloError` because the CALLER handles it differently:
    a build records `climate_status="not_computable"` plus a reason on
    the row and carries on, rather than aborting the run. The value is
    never defaulted (D13 F5: no missing rainfall value becomes zero).
    """


@dataclass(frozen=True)
class AnnualMetrics:
    """One cell-year's computable rainfall metrics."""

    annual_rainfall_mm: float
    rain_days_ge_1mm: int


def cell_daily_series(path: Path, *, lat_i: int, lon_i: int) -> np.ma.MaskedArray:
    """One cell's daily values for the file's whole year, masked where
    the file marks fill.

    Reads a single `(time,)` slice rather than the whole grid, so a
    statewide build touches only the cells Tier 1 footprints actually
    occupy -- a few hundred cells out of ~700k per annual file.
    """
    ds = netCDF4.Dataset(path, "r")
    try:
        values = ds.variables["daily_rain"][:, lat_i, lon_i]
    finally:
        ds.close()
    return np.ma.masked_invalid(values)


def annual_metrics(series: np.ma.MaskedArray) -> AnnualMetrics:
    """Annual total and >=1.0 mm rain-day count for one cell-year.

    Any masked (fill or NaN) day refuses the whole year: a partial
    year's total understates rainfall in a way that reads downstream as
    drought, which is exactly the misreading this project's claim
    boundary exists to prevent.
    """
    n_missing = int(np.ma.count_masked(series))
    if n_missing:
        raise SiloNotComputableError(
            f"{n_missing} missing daily value(s) -- refusing to compute annual "
            "metrics from a partial year"
        )
    values = np.asarray(series, dtype="f8")
    return AnnualMetrics(
        annual_rainfall_mm=float(values.sum()),
        rain_days_ge_1mm=int((values >= RAIN_DAY_THRESHOLD_MM).sum()),
    )


def rainfall_anomaly_mm(
    *, annual_rainfall_mm: float, baseline_annuals_mm: Sequence[float]
) -> float:
    """Annual total minus the mean of that cell's baseline-period annual
    totals.

    The baseline is fixed 1991-2020 by the D13 F5 schema. The CALLER
    guarantees the sequence covers the full baseline -- an incomplete
    baseline is a refusal upstream, never a quietly shorter mean here,
    because a 12-year "1991-2020 anomaly" is a mislabelled number.
    """
    if not baseline_annuals_mm:
        raise SiloError("empty baseline -- the caller must refuse before this point")
    return annual_rainfall_mm - (sum(baseline_annuals_mm) / len(baseline_annuals_mm))


def download_annual_file(
    url: str,
    dest_path: Path,
    *,
    timeout: float = _DOWNLOAD_TIMEOUT_SECONDS,
    user_agent: str = _USER_AGENT,
) -> Path:
    """Stream-download the annual NetCDF object at `url` to `dest_path`.

    Streams in fixed-size chunks rather than buffering, with an explicit
    timeout (`requests` has none by default) and an explicit User-Agent
    identifying this project. Raises `requests.HTTPError` on a non-2xx
    response before anything beyond the empty file is written.

    The timeout is long (300 s) because these objects are ~410 MB;
    `fetch-silo` writes to a `.part` name and renames on success, so an
    interrupted transfer never leaves a short file at the real path.

    Never exercised against the real bucket by any test in this repo.
    `tests/sources/test_silo.py` checks its request-shaping against a
    fake `requests.get`; `fetch-silo`'s own tests monkeypatch this
    function outright.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        url, stream=True, timeout=timeout, headers={"User-Agent": user_agent}
    ) as response:
        response.raise_for_status()
        with open(dest_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    handle.write(chunk)
    return dest_path
