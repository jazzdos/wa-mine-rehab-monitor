# SILO Gridded Rainfall Feed Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Land SILO gridded daily rainfall as a dated raw snapshot and
derive the Batch F climate-context table (annual rainfall, ≥1 mm rain
days, fixed-baseline anomaly per Tier 1 site-year), per the approved
design `docs/plans/2026-08-26-silo-climate-feed-design.md`.

**Architecture:** Two CLI commands mirroring the repo's existing
acquisition and curated-build shapes. `fetch-silo` downloads whole
annual `daily_rain` NetCDF objects from the anonymous `silo-open-data`
S3 bucket into `<data_root>/raw/silo/<date>/` (refusal gates before any
I/O, per-file validation before finalize, `--dry-run` that touches
neither network nor disk). `build-climate-context` verifies that
snapshot, maps each Tier 1 Maus footprint to the 0.05° cell containing
its equal-area centroid, and writes
`curated/climate-context/<date>/climate_context.parquet` with a run
manifest. New modules `src/wa_mine_monitor/sources/silo.py` (URLs,
download, NetCDF validation, grid indexing, metric math) and
`src/wa_mine_monitor/climate_context.py` (schema + pure row assembly),
matching the D13 §5 F5 file plan.

**Tech Stack:** Python 3.12, uv, typer, netCDF4 (new dependency),
requests, pyarrow, pandas, geopandas, pytest. Zero network in tests —
synthetic NetCDF fixtures throughout. **Nothing in this plan downloads
real data**; `fetch-silo` is owner-run later, off the metered
connection.

---

## House rules the implementer must know

Read `docs/plans/2026-08-26-silo-climate-feed-design.md` first. On
schema and claim boundary it defers to
`docs/decisions/2026-08-16-d13-batches-c-g-detailing.md` (F5 at lines
878–930).

**Verified anchors** (checked against the tree at commit `355b3be`;
if a line number has drifted, the symbol name is authoritative):

| What | Where |
|---|---|
| `_collect_git_state_disclosing_gaps` | `src/wa_mine_monitor/cli.py:143` |
| `_load_config_or_exit` | `cli.py:208` |
| `_validate_snapshot_date` | `cli.py:252` |
| `DateOption` / `ConfigOption` | `cli.py:267` / `cli.py:124` |
| `_refuse_if_snapshot_already_finalized(dir, *, config, git_state)` | `cli.py:421` |
| `_refuse_if_curated_output_already_exists(path, *, config, git_state)` | `cli.py:485` |
| `_latest_curated_dated_dir(base_dir, *, label)` | `cli.py:551` |
| `_verify_snapshot_or_refuse(dir, *, source_id, required_files=())` | `cli.py:580` |
| `_digest_verified_manifest(artefact_path)` | `cli.py:787` |
| `fetch-tenements` (acquisition template) | `cli.py:901–1002` |
| `fetch-maus-extract` | `cli.py:1505` |
| `build-crosswalk` (curated-build template) | `cli.py:2080` |
| `extract-trajectories` | `cli.py:5573`, gates at 5615 / 5650 / 5693 / 5728 |
| Maus read + reproject to `crosswalk.TARGET_CRS` | `cli.py:5889–5891` |
| Site→Maus eligibility tie-break | `cli.py:5941–5945` |
| `download_tenements_zip` (download template) | `sources/tenements.py:111–142` |
| `WA_BBOX = (112.5, -35.5, 129.1, -13.5)` | `sources/maus.py:36` |
| `TARGET_CRS = "EPSG:3577"` | `crosswalk.py:46` |
| SILO licence entry | `licence.py:283–300` |
| Licensing matrix SILO row / prose / pin sentence | `docs/licensing-matrix.md:47` / `:170` / `:59` |
| Open item O7 | `docs/amendments-and-limitations.md:293` |

**Conventions this repo actually uses** — several differ from what a
newcomer would guess, so do not improvise:

- **Downloads live in `sources/<name>.py`, never in `cli.py`.**
  `requests` is *not* imported in `cli.py`. `cli.py` does
  `from wa_mine_monitor.sources.tenements import download_tenements_zip`
  and tests monkeypatch `cli_module.download_tenements_zip` outright
  (`tests/sources/test_tenements.py:341`). Follow this exactly.
- **Acquisition-command tests live beside their source module**, in
  `tests/sources/test_<source>.py` — both the module unit tests *and*
  the CLI-command tests (see `tests/sources/test_maus.py:200` onward).
  Curated-build CLI tests live beside their module's test file
  (`tests/test_crosswalk.py:680` onward holds `build-crosswalk`'s).
  This is also what D13 F5's file plan names.
- Each test module defines its own local
  `def _write_config(tmp_path: Path, data_root: Path) -> Path` helper
  (`tests/sources/test_maus.py:203`, `tests/test_crosswalk.py:683`).
  There is no shared fixture; copy the four-line helper.
- Every CLI test that reaches `write_run_manifest` must monkeypatch
  git state:
  `monkeypatch.setattr(cli_module, "collect_git_state", lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""})`.
- Errors are `ValueError` subclasses named `<Thing>Error`. CLI refusals
  are `typer.echo(json.dumps({"refusal": ...}, indent=2, sort_keys=True))`
  then `raise typer.Exit(1) from None`.
- Tests are plain `def test_*(tmp_path, monkeypatch)` functions — no
  classes, no `conftest.py` additions. Module-level pyarrow schemas.
  Ruff runs on defaults with `line-length = 100`; mypy is
  `strict = false, check_untyped_defs = true`. Docstrings carry the
  *reasoning*, at the density of `src/wa_mine_monitor/trajectory_extract.py`.
- `snapshot_date` is always the caller-supplied `--date`
  (`_validate_snapshot_date`), never `date.today()`.
- **The full suite takes ~9 minutes (855 tests at baseline).** Scope
  pytest per task as each task states; the full battery is Task 12.

---

### Task 1: netCDF4 dependency

**Files:**
- Modify: `pyproject.toml` (dependencies list lines 7–19; first
  `[[tool.mypy.overrides]]` block, lines 41–54)

netCDF4 is a new dependency. rasterio (already present) can open NetCDF
through GDAL, but it exposes bands, not named coordinate variables or a
`_FillValue`-masked time series per cell — the two things every metric
here needs. netCDF4 is the smaller, more direct tool; record that
reasoning in the mypy-override comment so the choice is legible later.

**Step 1: Add the dependency**

Run: `uv add "netCDF4>=1.7"`
Expected: `pyproject.toml` and `uv.lock` updated, resolve succeeds.

**Step 2: Add the mypy override**

netCDF4 ships no `py.typed` marker. Extend the FIRST
`[[tool.mypy.overrides]]` block's `module` list with `"netCDF4.*"`, and
extend that block's comment with one sentence: netCDF4 carries the
identical gap and is imported by `sources/silo.py`.

**Step 3: Verify**

Run: `uv run python -c "import netCDF4; print(netCDF4.__version__)"`
Expected: a version string, exit 0.

Run: `uv run mypy src scripts`
Expected: `Success` (nothing imports it yet).

---

### Task 2: licence entry correction (gridded, anonymous, CC BY 4.0)

**Files:**
- Modify: `src/wa_mine_monitor/licence.py:283–300` (the `"silo"` entry)
- Modify: `src/wa_mine_monitor/http.py` (module docstring, line ~17)
- Modify: `docs/licensing-matrix.md` (line 47, line 59–60, line 170–172)
- Test: `tests/test_licence.py`

**Step 1: Write the failing test**

Append to `tests/test_licence.py`:

```python
def test_silo_licence_records_the_anonymous_gridded_route() -> None:
    """This project consumes SILO's GRIDDED product from the anonymous
    AWS open-data bucket (CC BY 4.0), not the account-gated point/Data
    Drill API. The licence entry must say so: a reader deciding whether
    an export is redistributable reasons from these fields, and
    "open-with-account" would send them chasing a credential that does
    not exist on this route. O7 closes on this fact rather than on a
    registration -- see docs/decisions/2026-08-26-silo-gridded-feed.md.
    """
    entry = licence.SOURCES["silo"]
    assert entry.licence_id == "CC-BY-4.0"
    assert entry.licence_url == "https://creativecommons.org/licenses/by/4.0/"
    assert "silo-open-data" in entry.notes
    assert "anonymous" in entry.notes.lower()
    assert entry.redistribute_public is True
    assert "SILO" in entry.attribution_text
```

**Step 2: Run it to make sure it fails**

Run: `uv run pytest tests/test_licence.py -q`
Expected: 1 failed (`licence_id` is `"open-with-account"`), rest pass.

**Step 3: Amend the entry**

In `src/wa_mine_monitor/licence.py:283–300` keep `source_id`, `title`,
`source_url`, `attribution_text` and `redistribute_public=True`
unchanged. Replace `licence_id`, `licence_url` and `notes` with:

```python
        licence_id="CC-BY-4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        notes=(
            "Consumed as the GRIDDED product from the anonymous AWS "
            "open-data bucket (s3://silo-open-data, ap-southeast-2): "
            "CC BY 4.0, no account and no credential on this route. The "
            "account-gated point/Data Drill API is NOT used by this "
            "project -- the earlier 'open-with-account' record described "
            "that route. Derived rainfall context is redistributable "
            "with attribution."
        ),
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_licence.py -q`
Expected: all pass. (No other test currently asserts SILO's licence
values — `grep -rn silo tests/` returns nothing today. If build-flow's
own edits create one, update it in the same change.)

**Step 5: Correct the stale credential claim in `http.py`**

`src/wa_mine_monitor/http.py`'s module docstring justifies URL
redaction with "SILO's API key travels as a query param". That claim is
now wrong for this project's route. Keep the redaction behaviour and
the justification, but replace the SILO example with a generic one
("query values can carry credentials") plus a clause noting SILO is
consumed anonymously here. Do not change any code in that module.

**Step 6: Update the licensing matrix**

- Line 47: change the licence column from `open, account-gated` to
  `CC BY 4.0 (gridded, anonymous)`; leave `**True**` and the
  attribution string untouched.
- Lines 59–60: replace "SILO is fetched per date range under the
  account-gated API." with "SILO is fetched as whole annual gridded
  NetCDF objects (`<year>.daily_rain.nc`) from the anonymous open-data
  bucket."
- Lines 170–172: the "**Open, account-gated (`redistribute_public=True`):**
  SILO." bullet no longer describes this project. Move SILO into the
  CC-BY bullet group and state the gridded anonymous route and that the
  account-gated point API is unused. If that leaves the
  "Open, account-gated" category with no members, delete the category
  rather than leaving an empty heading.

**Step 7: Verify**

Run: `uv run pytest tests/test_licence.py tests/test_http.py -q`
Expected: all pass.

---

### Task 3: `sources/silo.py` — object URLs and NetCDF validation

**Files:**
- Create: `src/wa_mine_monitor/sources/silo.py`
- Test: `tests/sources/test_silo.py` (new)

Validation must cover three D13 F5 acceptance items that a naive
"does it open?" check misses: the grid is the published 0.05° grid, it
covers **all of WA** (D13: "Statewide region checks reject inherited
SW-WA AOI clipping" — the failure mode that killed ERA5-Land in
env-health), and the file holds a **complete** year (365 days, 366 in a
leap year — D13's "monthly-window completeness" and "leap-year"
items). A short year silently understates every annual total.

**Step 1: Write the failing tests**

Create `tests/sources/test_silo.py`:

```python
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
from pathlib import Path
from typing import Self

import netCDF4
import numpy as np
import pytest

from wa_mine_monitor.sources import silo

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
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/sources/test_silo.py -q`
Expected: collection error — `ModuleNotFoundError: wa_mine_monitor.sources.silo`.

**Step 3: Implement**

Create `src/wa_mine_monitor/sources/silo.py`:

```python
"""SILO gridded daily rainfall: object URLs, streamed download, NetCDF
validation, grid indexing and annual metric math.

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

`download_annual_file` is the one function that performs network I/O,
and only the owner-run `fetch-silo` command calls it. No test in this
repo exercises it against a real endpoint.
"""

from __future__ import annotations

import calendar
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

_USER_AGENT = "wa-mine-rehab-monitor/0.1 (github.com/jazzdos/wa-mine-rehab-monitor)"
_DOWNLOAD_TIMEOUT_SECONDS = 300.0
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


def validate_daily_rain_file(
    path: Path, *, year: int, require_statewide: bool = True
) -> None:
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
```

**Step 4: Run to verify pass**

Run: `uv run pytest tests/sources/test_silo.py -q`
Expected: 10 passed.

---

### Task 4: `sources/silo.py` — grid indexing and cell ids

**Files:**
- Modify: `src/wa_mine_monitor/sources/silo.py`
- Test: `tests/sources/test_silo.py`

**Step 1: Write the failing tests**

Append to `tests/sources/test_silo.py`:

```python
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
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/sources/test_silo.py -q`
Expected: 4 new failures (`read_grid` / `cell_id` absent), 10 pass.

**Step 3: Implement**

Append to `src/wa_mine_monitor/sources/silo.py`:

```python
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
```

**Step 4: Run to verify pass**

Run: `uv run pytest tests/sources/test_silo.py -q`
Expected: 14 passed.

---

### Task 5: `sources/silo.py` — annual metric math

**Files:**
- Modify: `src/wa_mine_monitor/sources/silo.py`
- Test: `tests/sources/test_silo.py`

**Step 1: Write the failing tests**

Append to `tests/sources/test_silo.py`:

```python
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
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/sources/test_silo.py -q`
Expected: 5 new failures, 14 pass.

**Step 3: Implement**

Append to `src/wa_mine_monitor/sources/silo.py`:

```python
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
```

Add `from collections.abc import Sequence` to the module's imports.

**Step 4: Run to verify pass**

Run: `uv run pytest tests/sources/test_silo.py -q`
Expected: 19 passed.

**Step 5: Interim battery**

Run: `uv run ruff check src tests && uv run ruff format src tests && uv run mypy src scripts`
Expected: clean.

---

### Task 6: `sources/silo.py` — streamed download

**Files:**
- Modify: `src/wa_mine_monitor/sources/silo.py`
- Test: `tests/sources/test_silo.py`

Mirrors `download_tenements_zip` (`sources/tenements.py:111–142`)
exactly: `requests`, streamed chunks, explicit timeout, explicit
User-Agent. The timeout is longer (300 s) because these objects are
~410 MB rather than a few MB.

**Step 1: Write the failing tests**

Append to `tests/sources/test_silo.py` (mirrors
`tests/sources/test_tenements.py:248–316`):

```python
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
        silo.download_annual_file(
            "https://example.test/2003.daily_rain.nc", tmp_path / "out.nc"
        )
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/sources/test_silo.py -k download -q`
Expected: 2 failures (`download_annual_file` absent).

**Step 3: Implement**

Append to `src/wa_mine_monitor/sources/silo.py`:

```python
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
```

**Step 4: Run to verify pass**

Run: `uv run pytest tests/sources/test_silo.py -q`
Expected: 21 passed.

---

### Task 7: `fetch-silo` CLI command

**Files:**
- Modify: `src/wa_mine_monitor/cli.py` (import block ~line 68; new
  command after `fetch_maus_extract` ends)
- Test: `tests/sources/test_silo.py`

**Step 1: Write the failing tests**

Append to `tests/sources/test_silo.py`. Add these imports at the top of
the file: `import json`, `import sys`, `from typer.testing import
CliRunner`, `from wa_mine_monitor import cli as cli_module`,
`from wa_mine_monitor import snapshots`, `from wa_mine_monitor.cli import app`,
and `runner = CliRunner()` at module level.

```python
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
            "fetch-silo", "--config", str(cfg_file), "--date", "2026-08-30",
            "--start-year", "2001", "--end-year", "2003", "--dry-run",
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
            "fetch-silo", "--config", str(cfg_file), "--date", "2026-08-30",
            "--start-year", "2003", "--end-year", "2001",
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
            "fetch-silo", "--config", str(cfg_file), "--date", "2026-08-30",
            "--start-year", "2001", "--end-year", "2001",
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

    def fake_download(url: str, dest_path: Path, **kwargs: object) -> Path:
        return _write_statewide_year_nc(Path(dest_path), 2001)

    monkeypatch.setattr(cli_module, "download_annual_file", fake_download)
    cli_args = [
        "fetch-silo", "--config", str(cfg_file), "--date", "2026-08-30",
        "--start-year", "2001", "--end-year", "2001",
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

    def fake_download(url: str, dest_path: Path, **kwargs: object) -> Path:
        Path(dest_path).write_bytes(b"not a netcdf")
        return Path(dest_path)

    monkeypatch.setattr(cli_module, "download_annual_file", fake_download)
    result = runner.invoke(
        app,
        [
            "fetch-silo", "--config", str(cfg_file), "--date", "2026-08-30",
            "--start-year", "2001", "--end-year", "2001",
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
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "silo", "2026-08-30")
    _write_statewide_year_nc(snapshot_dir / "2001.daily_rain.nc", 2001)

    def fake_download(url: str, dest_path: Path, **kwargs: object) -> Path:
        return _write_statewide_year_nc(Path(dest_path), 2002)

    monkeypatch.setattr(cli_module, "download_annual_file", fake_download)
    result = runner.invoke(
        app,
        [
            "fetch-silo", "--config", str(cfg_file), "--date", "2026-08-30",
            "--start-year", "2001", "--end-year", "2002",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["resumed"] == 1
    assert payload["fetched"] == 1
```

**Why `_write_statewide_year_nc` is not a real statewide grid.** A grid
that genuinely spans `WA_BBOX` at 0.05° is ~332 × ~440 cells; with 365
float32 days that is ~200 MB per fixture file, which is unusable in a
test suite. So the CLI reads its statewide flag from a module-level
constant that the CLI tests flip, and the coverage rule itself is tested
once at unit level in Task 3
(`test_validate_refuses_a_grid_clipped_short_of_statewide_wa`), where no
large array is needed. Define the helper as a thin alias over the small
grid:

```python
def _write_statewide_year_nc(path: Path, year: int) -> Path:
    """A complete year on the small `LATS`/`LONS` grid, for CLI tests
    that run with `cli._SILO_REQUIRE_STATEWIDE` monkeypatched to False.

    A fixture that really spanned WA_BBOX at 0.05 degrees would be
    ~200 MB. The statewide coverage rule is exercised instead by
    `test_validate_refuses_a_grid_clipped_short_of_statewide_wa` above,
    at unit level.
    """
    return write_full_year_nc(path, year)
```

and add this line to `_stub_git_state`'s callers — or to a shared
`_small_grid_validation` helper used by every `fetch_silo` CLI test that
reaches validation:

```python
    monkeypatch.setattr(cli_module, "_SILO_REQUIRE_STATEWIDE", False)
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/sources/test_silo.py -k fetch_silo -q`
Expected: 6 failures — `No such command 'fetch-silo'`.

**Step 3: Add the import**

In `src/wa_mine_monitor/cli.py`, beside the other `sources.*` imports
(~line 68), add:

```python
from wa_mine_monitor.sources.silo import (
    SiloError,
    annual_object_name,
    annual_object_url,
    download_annual_file,
    validate_daily_rain_file,
)
```

and near the other module-level constants add:

```python
#: Production `fetch-silo` asserts the downloaded grid spans WA (see
#: `silo.validate_daily_rain_file`). CLI tests monkeypatch this to False
#: so their fixtures can be a few cells rather than a continental grid;
#: the coverage rule itself is tested at unit level in
#: `tests/sources/test_silo.py`.
_SILO_REQUIRE_STATEWIDE = True
```

**Step 4: Implement the command**

Add `fetch_silo_cmd` after `fetch_maus_extract` ends, following
`fetch-tenements` (`cli.py:901–1002`) gate for gate:

```python
@app.command("fetch-silo")
def fetch_silo_cmd(
    config: Path = ConfigOption,
    date: str = DateOption,
    start_year: int = typer.Option(
        1987, "--start-year", help="First year to fetch (inclusive)."
    ),
    end_year: int = typer.Option(
        ..., "--end-year", help="Last year to fetch (inclusive). Required, never defaulted."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the planned objects and destination, then exit without network or disk I/O.",
    ),
) -> None:
    """Fetch a dated SILO gridded daily-rainfall snapshot (CC BY 4.0)."""
```

Docstring must record: the anonymous bucket route; that `--end-year` is
required rather than defaulting to the current year (the house rule
forbids `date.today()` in artefact-shaping arguments, and a rolling
default would make two runs of "the same" command fetch different data);
and that the objects are ~410 MB each.

Behaviour, in order:

1. `resolved = _load_config_or_exit(config)`;
   `resolved_config = resolved.model_dump(mode="json")`.
2. Refuse `start_year > end_year`, and `start_year < 1889` (SILO
   rainfall begins 1889), with JSON refusals — **before** any snapshot
   directory is created.
3. `urls = [annual_object_url("daily_rain", y) for y in range(start_year, end_year + 1)]`.
4. If `dry_run`: echo sorted-keys JSON
   `{"dry_run": true, "objects": urls, "destination": str(resolved.run.data_root / "raw" / "silo" / date), "note": "each annual object is ~410 MB; run off a metered connection"}`
   and `return` — **before** `create_snapshot_dir`, so nothing is
   written.
5. `source = licence.SOURCES["silo"]`;
   `snapshot_dir = snapshots.create_snapshot_dir(resolved.run.data_root, "silo", date)`;
   `git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)`;
   `_refuse_if_snapshot_already_finalized(snapshot_dir, config=resolved_config, git_state=git_state)`.
6. Per-year loop, destination
   `snapshot_dir / annual_object_name("daily_rain", year)`:
   - If the destination already exists, validate it
     (`validate_daily_rain_file(dest, year=year, require_statewide=_SILO_REQUIRE_STATEWIDE)`)
     and skip the download, counting it as `resumed`. On validation
     failure, refuse naming the file and the remedy ("delete the partial
     file and re-run").
   - Otherwise download to a sibling `.part` path via
     `download_annual_file(url, part_path)`, `part_path.replace(dest)`
     on success, then validate. Wrap the download in
     `except Exception as exc:  # noqa: BLE001` and emit
     `{"refusal": ..., "stage": "download", "url": url}`; wrap
     validation separately with `except SiloError as exc:` and emit
     `{"refusal": ..., "stage": "validation", "file": dest.name}`.
     Both exit 1 **before** finalize.
7. `snapshots.write_snapshot_metadata(snapshot_dir, source=f"{source.title} (gridded daily_rain, annual NetCDF)", endpoint=annual_object_url("daily_rain", start_year), licence_note=f"{source.licence_id} -- {source.licence_url}", purpose="SILO gridded daily rainfall for Batch F climate context.")`
   — mirror the call shape at `cli.py:948–962`.
8. `sums_path = snapshots.finalize_snapshot(snapshot_dir)`;
   `n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)`.
9. One `SourceAsset` per file, fetched or resumed:
   `SourceAsset(uri=url, sha256=sha256_file(dest), collection=None, snapshot_date=dt_date.fromisoformat(date), licence=source.licence_id, redistribute_public=source.redistribute_public)`.
10. `manifests.write_run_manifest(output=sums_path, inputs=assets, config=resolved_config, git_state=git_state, resolved_args={"date": date, "start_year": start_year, "end_year": end_year, "variable": "daily_rain", "fetched": n_fetched, "resumed": n_resumed})`.
11. JSON summary (sorted keys, `default=str`): `snapshot_dir`,
    `fetched`, `resumed`, `verify`, `manifest_path`.

**Step 5: Run to verify pass**

Run: `uv run pytest tests/sources/test_silo.py -q`
Expected: 27 passed.

**Step 6: Check for regressions in the CLI surface**

Run: `uv run pytest tests/test_cli.py -q`
Expected: no failures.

---

### Task 8: `climate_context.py` — schema and pure row assembly

**Files:**
- Create: `src/wa_mine_monitor/climate_context.py`
- Test: `tests/test_climate_context.py` (new)

Pure functions only — no I/O, no CLI. The CLI in Task 9 does every read
and every refusal; this module only turns already-computed inputs into
schema-conformant rows.

**Step 1: Write the failing tests**

Create `tests/test_climate_context.py` covering, one plain function
each:

(a) **a computable cell-year** yields `climate_status == "computed"`,
all three metrics non-null, `not_computable_reason` null, and
`rainfall_baseline_start_year`/`_end_year` == 1991/2020;

(b) **a not-computable cell-year** yields nulls for all three metrics,
`climate_status == "not_computable"` and a non-empty reason — and the
row is **KEPT**, never dropped (D13 F6: "Trajectory rows are not
dropped because context is unknown");

(c) **an incomplete baseline for a cell** makes every year of that cell
not computable, with a reason naming the missing baseline years — never
a mean over a shorter period;

(d) **two sites sharing one `maus_id`** get the same `silo_cell_id` and
identical metric values (the shared-footprint case the Tier 1 product
framing already discloses);

(e) **the assembled frame conforms to `CLIMATE_CONTEXT_SCHEMA`
exactly** — round-trip it through
`tables.write_table(df, tmp_path / "x.parquet", climate_context.CLIMATE_CONTEXT_SCHEMA)`
and assert
`pq.read_schema(tmp_path / "x.parquet").equals(climate_context.CLIMATE_CONTEXT_SCHEMA)`,
so a column-set, dtype or nullability drift fails here rather than at
the export boundary. (`write_table` already refuses a column-set
mismatch in either direction — `tables.py:36–42`.);

(f) **`export_gate.has_geometry(df) is False`** — climate context
carries a cell id, never a geometry (mirrors the assertion at
`tests/test_crosswalk.py:770`).

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_climate_context.py -q`
Expected: collection error — module absent.

**Step 3: Implement**

Create `src/wa_mine_monitor/climate_context.py` with:

```python
CLIMATE_CONTEXT_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string(), nullable=False),
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("year", pa.int32(), nullable=False),
        pa.field("silo_cell_id", pa.string(), nullable=False),
        pa.field("annual_rainfall_mm", pa.float64(), nullable=True),
        pa.field("rain_days_ge_1mm", pa.int32(), nullable=True),
        pa.field("rainfall_anomaly_mm", pa.float64(), nullable=True),
        pa.field("rainfall_baseline_start_year", pa.int32(), nullable=False),
        pa.field("rainfall_baseline_end_year", pa.int32(), nullable=False),
        pa.field("climate_status", pa.string(), nullable=False),
        pa.field("not_computable_reason", pa.string(), nullable=True),
        pa.field("silo_source_version", pa.string(), nullable=False),
        pa.field("silo_snapshot_date", pa.string(), nullable=False),
    ]
)

BASELINE_START_YEAR = 1991
BASELINE_END_YEAR = 2020

CLIMATE_STATUS_COMPUTED = "computed"
CLIMATE_STATUS_NOT_COMPUTABLE = "not_computable"


class ClimateContextError(ValueError):
    """Climate-context assembly refused a structurally invalid input."""
```

and one pure function:

```python
def assemble_rows(
    *,
    site_maus_pairs: Sequence[tuple[str, str]],
    cell_id_by_maus: Mapping[str, str],
    metrics_by_cell_year: Mapping[tuple[str, int], AnnualMetrics],
    not_computable_by_cell_year: Mapping[tuple[str, int], str],
    baseline_annuals_by_cell: Mapping[str, Sequence[float]],
    baseline_gap_by_cell: Mapping[str, str],
    years: Sequence[int],
    snapshot_date: str,
    source_version: str,
) -> pd.DataFrame:
```

Return a `pd.DataFrame`, not a `pa.Table`: `tables.write_table` takes
`(df: pd.DataFrame, path: Path, schema: pa.Schema)` (`tables.py:36`)
and every other build in this repo hands it a DataFrame.

Rules the implementation must follow, each stated in the docstring:

- Exactly one row per `(site_id, year)` for every pair and year given —
  the row count is `len(site_maus_pairs) * len(years)`, unconditionally.
- `climate_status` is only ever `"computed"` or `"not_computable"`;
  the two constants above are the only permitted values.
- A cell with an entry in `baseline_gap_by_cell` is
  `"not_computable"` for **every** year, reason = that entry.
- A `(cell, year)` in `not_computable_by_cell_year` is
  `"not_computable"` for that year, reason = that entry.
- `not_computable` rows carry `None` for all three metric columns.
  Never `0`, never `NaN` (D13 F5 acceptance).
- `computed` rows carry `not_computable_reason = None`.
- Baseline year columns are the module constants on every row,
  computed or not, so a reader never has to infer which baseline a
  null anomaly would have used.
- Raise `ClimateContextError` if a `maus_id` has no entry in
  `cell_id_by_maus` — a missing cell mapping is a caller bug, not a
  row-level unknown.

Module docstring carries the claim boundary verbatim: these are context
rows displayed beside trajectories; no causal attribution is generated
here or anywhere; "cause not determined" belongs to F6's join.

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_climate_context.py -q`
Expected: all pass.

---

### Task 9: `build-climate-context` CLI command

**Files:**
- Modify: `src/wa_mine_monitor/cli.py` (new command after
  `build_crosswalk_cmd`)
- Test: `tests/test_climate_context.py`

**Step 1: Read the templates first**

Read, in this order, before writing anything:
- `cli.py:2080` onward (`build-crosswalk`) — the curated-build shape.
- `cli.py:5650–5760` (`extract-trajectories` GATEs 2–4) — how the
  eligible register, crosswalk and Maus snapshot are resolved and
  digest-verified.
- `cli.py:5941–5945` — the site→Maus tie-break, to be reproduced
  **exactly**, comment included.
- `tests/test_crosswalk.py:683–770` — `_write_config`, `_seed_register`,
  `_seed_maus_extract` fixture idiom to copy.

**Step 2: Write the failing CLI tests**

Append to `tests/test_climate_context.py`. Build a miniature world under
`tmp_path` reusing the `tests/test_crosswalk.py` seeding idiom, plus:

- a finalized `raw/silo/<date>/` snapshot holding one small NetCDF per
  year for the **full 1991–2020 baseline** plus the requested years
  (import `write_full_year_nc` from `tests.sources.test_silo`; 30-odd
  tiny files are fast to write);
- a curated register carrying `trajectory_status` and
  `d3_forced_threshold` (what `apply-d3-threshold` writes) with at least
  one `"eligible"` site;
- a curated crosswalk mapping those sites to `maus_id`s;
- a finalized `maus_v2` snapshot whose `wa_extract.gpkg` polygons sit
  inside the fixture grid.

Cases:

(a) **happy path** writes
`curated/climate-context/<date>/climate_context.parquet` matching
`CLIMATE_CONTEXT_SCHEMA`, one row per eligible site-year, with a run
manifest beside it whose `inputs` include one `SourceAsset` per SILO
file actually read;

(b) **refuses when the output already exists**
(`_refuse_if_curated_output_already_exists`);

(c) **refuses when the SILO snapshot is missing a baseline year** —
message names the missing year(s), and no partial output is written.
This is the "never a silently narrower baseline" rule;

(d) **refuses when the snapshot is missing a requested trajectory year**;

(e) **refuses when the snapshot is unverified** (no `SHA256SUMS.txt` —
`_verify_snapshot_or_refuse`);

(f) **the site→Maus tie-break matches `cli.py:5941–5945`**: a site with
two high-confidence crosswalk rows resolves to the lexicographically
smallest `maus_id`;

(g) **a footprint outside the grid** yields `not_computable` rows for
that site rather than aborting the run or snapping to an edge cell.

**Step 3: Run to verify failure**

Run: `uv run pytest tests/test_climate_context.py -k build_climate_context -q`
Expected: `No such command 'build-climate-context'`.

**Step 4: Implement the command**

```python
@app.command("build-climate-context")
def build_climate_context_cmd(
    config: Path = ConfigOption,
    date: str = DateOption,
    start_year: int = typer.Option(..., "--start-year"),
    end_year: int = typer.Option(..., "--end-year"),
) -> None:
```

Order of operations:

1. `_load_config_or_exit`; `git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)`.
2. `_refuse_if_curated_output_already_exists(data_root / "curated" / "climate-context" / date / "climate_context.parquet", config=..., git_state=...)`.
3. Resolve the latest `raw/silo` snapshot via
   `register.latest_snapshot(data_root, "silo")`, then
   `_verify_snapshot_or_refuse(snapshot_dir, source_id="silo", required_files=tuple(annual_object_name("daily_rain", y) for y in needed_years))`
   where
   `needed_years = sorted(set(range(BASELINE_START_YEAR, BASELINE_END_YEAR + 1)) | set(range(start_year, end_year + 1)))`.
   A missing baseline year and a missing requested year are both this
   one refusal; the message must name the years, so cases (c) and (d)
   are distinguishable from the output.
4. Eligible register: GATE 2 + GATE 3 of `extract-trajectories`
   (`_latest_curated_dated_dir`, `_digest_verified_manifest`,
   `read_table`, the `trajectory_status` / `d3_forced_threshold` column
   checks, then `trajectory_extract.select_eligible_sites`).
5. Crosswalk + Maus snapshot exactly as `extract-trajectories` GATE 4
   resolves them, including the crosswalk-vs-Maus sha256 agreement check
   at `cli.py:5875–5888`.
6. Reproduce the tie-break at `cli.py:5941–5945` **verbatim**, comment
   included, pointing at `register.py:~1373`.
7. **Centroids in equal-area, then to geographic.** Read the Maus gpkg
   and reproject to `crosswalk.TARGET_CRS` (EPSG:3577) as every other
   command does, take `.centroid`, then reproject the centroid points to
   `EPSG:4326` for cell lookup. A centroid taken in degrees is not the
   footprint's areal centre; taking it in the project's own equal-area
   CRS keeps this consistent with the footprint areas the rest of the
   pipeline reports. State that reasoning in a comment.
8. `silo.read_grid` on one snapshot file; `cell_index_for_point` per
   footprint centroid; `silo.cell_id` per cell. A `SiloError` here
   (footprint outside the grid) becomes a per-cell
   `not_computable_by_cell_year` entry for every year, not a run abort.
9. Per **occupied cell** (not per site — sites sharing a footprint share
   a cell, and rainfall is read once): `cell_daily_series` +
   `annual_metrics` for each needed year, catching
   `SiloNotComputableError` into `not_computable_by_cell_year`. Build
   `baseline_annuals_by_cell` from the 1991–2020 results; if any
   baseline year for a cell is not computable, put an entry in
   `baseline_gap_by_cell` naming those years instead.
10. `rainfall_anomaly_mm` per requested year for cells with a complete
    baseline; `climate_context.assemble_rows(...)`.
11. `write_table(rows_df, output_path, climate_context.CLIMATE_CONTEXT_SCHEMA)`
    then `manifests.write_run_manifest` with one `SourceAsset` per SILO
    file read plus the Maus/register/crosswalk assets in the same shape
    `extract_trajectories_cmd` records them.
12. JSON summary: row count, `computed` / `not_computable` counts, cells
    touched, sites, year range, output and manifest paths.

**Step 5: Run to verify pass**

Run: `uv run pytest tests/test_climate_context.py -q`
Expected: all pass.

**Step 6: Interim battery**

Run: `uv run ruff check src tests && uv run ruff format src tests && uv run mypy src scripts`
Expected: clean.

---

### Task 10: decision record and O7 close-out

**Files:**
- Create: `docs/decisions/2026-08-26-silo-gridded-feed.md`
- Modify: `docs/amendments-and-limitations.md:293` (the O7 row)

**Step 1: Write the decision record**

Follow the house style — read
`docs/decisions/2026-08-25-e5-engine-parity-rescope.md` for shape
first. Title: "SILO gridded feed: anonymous route, in-repo ingestion".
It records:

1. The five owner decisions from
   `docs/plans/2026-08-26-silo-climate-feed-design.md` §"Owner decisions
   taken in this session": in-repo ingestion (with the env-health
   investigation summary — SILO holds no data, no adapter, no credential
   there, and was killed as REDUNDANT-KILL in favour of ERA5-Land, whose
   AOI is not statewide); the anonymous gridded product, which closes O7
   by fact rather than by registration; storage at
   `<data_root>/raw/silo/<date>/`; whole-annual-file fetch; and no
   download without explicit owner approval on a metered connection.
2. The **centroid-cell method** and its justification: Maus footprints
   are nearly all far smaller than a 5 km cell; the centroid is taken in
   EPSG:3577 and reprojected for lookup; the chosen cell is recorded
   on every row as `silo_cell_id`.
3. The **D13 F5 deviation**: `secrets.py` changes and credential-
   redaction tests are dropped as objectless — the gridded route has no
   credential. The point/Data Drill API remains unused. If a future need
   for it arises, the credential machinery returns with it. Note also
   that `http.py`'s docstring reference to a SILO API key was corrected
   for the same reason.
4. The fail-closed data semantics: `rain_days_ge_1mm` threshold is
   inclusive at 1.0 mm; a missing daily value makes the cell-year
   `not_computable` and never zero; an incomplete 1991–2020 baseline
   refuses rather than producing a shorter mean; a short year is refused
   at validation; a grid not covering WA is refused at validation.

**Step 2: Update the open-items register**

`docs/amendments-and-limitations.md:293`. Match the existing closed-item
style exactly (see O2, O5, O6, O8 on lines 287–292):

```markdown
| ~~O7~~ | ~~No SILO account or snapshot exists on either data root~~ | **Closed 2026-08-26: the gridded product is anonymous (no account exists on this route); `decisions/2026-08-26-silo-gridded-feed.md`** |
```

**Step 3: Verify the cross-references resolve**

Run: `uv run pytest tests/test_licence.py -q`
Expected: pass (the Task 2 test's docstring cites this record).

Confirm by eye that every path named in the new decision record exists.

---

### Task 11: ROADMAP pointer

**Files:**
- Modify: `docs/ROADMAP.md`

`AGENTS.md` directs every reader to `docs/ROADMAP.md` first. Add the two
new commands to whatever command inventory / build-sequence section it
carries, and note that `fetch-silo` is owner-run and has not yet been
run against the real bucket. Read the file and match its existing
structure; do not restructure it.

Run: `grep -n -i "silo" docs/ROADMAP.md`
Expected: the new entries, consistent with surrounding rows.

---

### Task 12: full verification battery

**Step 1:** `uv run ruff check src tests` — expected: clean.

**Step 2:** `uv run ruff format --check src tests` — expected: clean
(run `uv run ruff format src tests` first if it reports files).

**Step 3:** `uv run mypy src scripts` — expected: `Success`.

**Step 4:** `uv run pytest -q -rs` — expected: all pass. Baseline was
**855 passed in ~9 minutes**; this plan adds roughly 40 tests, so
expect ~895 and no failures. Any drop below 855 is a regression, not a
rounding difference.

---

## Explicitly out of scope

- **Running `fetch-silo` against the real bucket.** Owner-run only,
  after explicit approval, off the metered connection (design decision
  5). No task above may issue a real download.
- Fire context (D13 F4), the trajectory context join (D13 F6), the
  Batch F acceptance checkpoint, and any change to
  `extract-trajectories` or the export gate.
- Any SILO variable other than `daily_rain`; the point/Data Drill API;
  any credential machinery (`secrets.py` is untouched).
- Wiring climate context into a release package — that is Batch G.
