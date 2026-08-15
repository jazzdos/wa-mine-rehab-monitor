"""DMIRS-003 Mining Tenements: pinned DASC bundle endpoint, fetch and validation.

CC-BY-4.0 (`wa_mine_monitor.licence.SOURCES["dmirs_003_tenements"]`) -- the
one clean tenements source in this project's registry, unlike MINEDEX
(DMIRS-001), which is fail-closed pending resolution of its own licence
conflict (see `licence.py`'s module docstring).

**D6 acquisition route (2026-08-16).** The SLIP direct-download GeoPackage
endpoint this module used to pin --
`https://data-downloads.slip.wa.gov.au/DMIRS-003/Geopackage` -- was measured
auth-gated on 2026-08-16 (Task 11 live acceptance run): it returns a Landgate
SSO login page (`sso.slip.wa.gov.au`), not data. Per the delegated director's
D6 ruling (`docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md`),
this module now downloads the DMIRS DASC (`dasc.dmirs.wa.gov.au`) statewide
GDA2020 ESRI Shapefile bundle instead -- an unauthenticated zip, preserved
byte-for-byte, validated by reading its members directly (no capture-time
conversion). `DASC_TENEMENTS_SHP_URL` pins the exact endpoint as a bare
module-level constant, so `test_dasc_tenements_shp_url_is_pinned` makes a
silent change to it a loud, reviewable diff.

Fixture-first rule: nothing in this module's unit tests touches the network.
`download_tenements_zip`'s request-shaping is checked against a fake
`requests.get`; a real download only ever happens when an operator runs the
`fetch-tenements` CLI command, which monkeypatches this function out entirely
in its own tests.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

#: Pinned DMIRS-003 DASC bundle download endpoint -- DASC file id 2056,
#: "Tenements - Current (live and pending) - [GDA2020]", measured by direct
#: download 2026-08-16 (D6). A bare constant, not a function wrapping it:
#: the task spec that introduced this route calls these "pinned constants"
#: and `test_dasc_tenements_shp_url_is_pinned` asserts the exact string, so
#: this is the one indirection point every call site goes through.
DASC_TENEMENTS_SHP_URL = "https://dasc.dmirs.wa.gov.au/Download/File/2056"

#: Filename the downloaded DASC zip is written to inside a snapshot
#: directory.
TENEMENTS_ZIP_FILENAME = "tenements_current_gda2020_shp.zip"

#: The exact set of members the DASC tenements zip is expected to contain,
#: measured by direct download 2026-08-16. Naming every required member (not
#: just checking the shapefile's own four sidecar files) is what lets
#: `validate_tenements_zip` name EVERY missing member on a truncated or
#: corrupted download, rather than failing opaquely on whichever one
#: `gpd.read_file` happens to touch first.
TENEMENTS_REQUIRED_MEMBERS: tuple[str, ...] = (
    "CurrentTenements.cpg",
    "CurrentTenements.dbf",
    "CurrentTenements.prj",
    "CurrentTenements.shp",
    "CurrentTenements.shx",
    "CurrentTenements_DataDictionary.pdf",
    "CurrentTenements_Metadata_GDA2020.pdf",
    "Licence_CCBY4.pdf",
)

#: The shapefile's basename inside the zip, without extension -- used to
#: build the `/vsizip/` GDAL virtual-filesystem path `validate_tenements_zip`
#: reads through, without ever extracting the zip to a temporary directory.
TENEMENTS_SHAPEFILE_BASENAME = "CurrentTenements"

#: Expected CRS of the DASC tenements shapefile, measured by direct download
#: 2026-08-16 -- GDA2020, the datum DASC's own product identity names as its
#: continuing output. Compared by exact string equality against
#: `str(gdf.crs)`, confirmed empirically to render as exactly this string
#: for an EPSG:7844-tagged GeoDataFrame.
TENEMENTS_EXPECTED_CRS = "EPSG:7844"

#: Columns `validate_tenements_zip` requires present, measured by direct
#: download 2026-08-16. `EXTRACT_DA` is required here (not merely reported)
#: because the extract-date consistency check below reads it.
TENEMENTS_REQUIRED_COLUMNS: tuple[str, ...] = (
    "FMT_TENID",
    "TENSTATUS",
    "HOLDER1",
    "EXTRACT_DA",
)

#: User-Agent sent with every download request, identifying this project
#: rather than falling back to `requests`' default -- some endpoints treat an
#: unidentified client as a bot and block it.
_USER_AGENT = "wa-mine-rehab-monitor/0.1 (github.com/jazzdos/wa-mine-rehab-monitor)"

#: Streaming download timeout in seconds. Explicit because `requests` has no
#: default timeout at all: an unresponsive endpoint would otherwise hang a
#: fetch indefinitely.
_DOWNLOAD_TIMEOUT_SECONDS = 60.0

#: Chunk size for the streamed download. A tenement extract can run to tens
#: of megabytes; this never buffers the whole response in memory.
_DOWNLOAD_CHUNK_SIZE = 1_048_576  # 1 MiB


class SnapshotValidationError(Exception):
    """A downloaded DMIRS-003 DASC tenements zip failed validation.

    Always names the offending zip path in its message, so a caller does
    not have to reconstruct which snapshot failed from a bare traceback.
    """


def download_tenements_zip(
    url: str,
    dest_path: Path,
    *,
    timeout: float = _DOWNLOAD_TIMEOUT_SECONDS,
    user_agent: str = _USER_AGENT,
) -> Path:
    """Stream-download the DASC zip at `url` to `dest_path`.

    Streams in fixed-size chunks rather than buffering the whole response,
    with an explicit timeout (`requests` has none by default) and an
    explicit User-Agent identifying this project. Raises `requests.HTTPError`
    on a non-2xx response (`raise_for_status`) before anything is written to
    `dest_path` beyond the (empty) file `open(..., "wb")` creates.

    Never exercised against a real endpoint by a unit test -- see this
    module's fixture-first rule. `tests/sources/test_tenements.py` checks
    this function's request-shaping against a fake `requests.get`; the
    `fetch-tenements` CLI command's own tests monkeypatch this function
    outright.
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


def _extract_dates(values: pd.Series) -> set[str]:
    """Normalise an EXTRACT_DA/EXTRACT_DATE series to a set of ISO date strings.

    A shapefile DBF date field and a CSV text date field can render the same
    calendar date in different literal forms (a `datetime.date` object vs.
    `"14/08/2026"`), so a raw string-equality comparison across sources would
    spuriously disagree on genuinely identical extracts. Every value is
    parsed with `pandas.to_datetime(dayfirst=True)` (the measured DASC CSV
    convention) and reduced to its ISO `date().isoformat()`; a value that
    fails to parse falls back to its raw `str()` so it still participates in
    the distinct-value count rather than vanishing silently.
    """
    parsed = pd.to_datetime(values, errors="coerce", dayfirst=True)
    normalized: set[str] = set()
    for raw_value, parsed_value in zip(values, parsed, strict=True):
        if pd.isna(parsed_value):
            normalized.add(str(raw_value))
        else:
            normalized.add(parsed_value.date().isoformat())
    return normalized


def validate_tenements_zip(zip_path: Path) -> dict[str, object]:
    """Validate a downloaded DASC tenements zip and summarise it.

    Returns `{"feature_count": int, "crs": str, "columns": list[str],
    "members": list[str], "extract_date": str}` on a zip that passes every
    check below.

    Raises `SnapshotValidationError`, always naming `zip_path` in its
    message, when:

    - the file is not a readable zip at all;
    - any member of `TENEMENTS_REQUIRED_MEMBERS` is absent -- EVERY missing
      member is named, not just the first;
    - the shapefile (read via the `/vsizip/` GDAL virtual filesystem, so the
      zip is never extracted to a temporary directory) is unreadable or
      empty (0 features);
    - the shapefile's CRS is not exactly `TENEMENTS_EXPECTED_CRS`;
    - any column in `TENEMENTS_REQUIRED_COLUMNS` is absent;
    - `EXTRACT_DA` carries more than one distinct normalised date (see
      `_extract_dates`) -- a tenements bundle spanning more than one extract
      run is not a single, coherent snapshot.

    A silently empty, ambiguous, malformed or multi-extract-date bundle must
    never look like a valid one to anything downstream -- it fails loudly
    here instead, before `fetch-tenements` finalizes the snapshot.
    """
    zip_path = Path(zip_path)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = set(zf.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise SnapshotValidationError(f"{zip_path}: not a readable zip ({exc})") from exc

    missing_members = [name for name in TENEMENTS_REQUIRED_MEMBERS if name not in members]
    if missing_members:
        raise SnapshotValidationError(
            f"{zip_path}: missing required member(s) {missing_members} "
            f"(zip contains {sorted(members)})"
        )

    try:
        gdf = gpd.read_file(f"/vsizip/{zip_path}/{TENEMENTS_SHAPEFILE_BASENAME}.shp")
    except Exception as exc:
        raise SnapshotValidationError(f"{zip_path}: shapefile not readable ({exc})") from exc

    if len(gdf) == 0:
        raise SnapshotValidationError(f"{zip_path}: shapefile is empty (0 features)")

    crs_str = str(gdf.crs)
    if crs_str != TENEMENTS_EXPECTED_CRS:
        raise SnapshotValidationError(
            f"{zip_path}: shapefile CRS is {crs_str!r}, expected {TENEMENTS_EXPECTED_CRS!r}"
        )

    missing_columns = [c for c in TENEMENTS_REQUIRED_COLUMNS if c not in gdf.columns]
    if missing_columns:
        raise SnapshotValidationError(
            f"{zip_path}: missing required column(s) {missing_columns} "
            f"(columns present: {sorted(gdf.columns)})"
        )

    distinct_extract_dates = sorted(_extract_dates(gdf["EXTRACT_DA"]))
    if len(distinct_extract_dates) != 1:
        raise SnapshotValidationError(
            f"{zip_path}: EXTRACT_DA carries {len(distinct_extract_dates)} distinct "
            f"value(s) {distinct_extract_dates}, expected exactly 1"
        )

    return {
        "feature_count": len(gdf),
        "crs": crs_str,
        "columns": list(gdf.columns),
        "members": sorted(members),
        "extract_date": distinct_extract_dates[0],
    }
