"""Shared fixture builders for `tests/sources/*.py`.

Both `test_tenements.py` and `test_minedex.py` need the same two low-level
things: a shapefile's sidecar files written into a plain zip under the exact
DASC member names, and a placeholder licence PDF whose bytes can be compared
byte-for-byte. This module is the one place that assembly logic lives, per
the task note that a genuinely shared fixture-zip helper belongs here (so a
follow-on task reworking the register against the new DASC snapshot layout
can reuse it too) -- everything SOURCE-SHAPED (which columns, which stages,
which extract date) stays in each test file's own fixture builder.

Fixture-first rule, same as every `sources/*.py` module: nothing in this
project's unit tests touches the network, and no test reads a committed
binary fixture -- every zip is built in-test, into `tmp_path`.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd

#: Minimal placeholder PDF bytes -- not a real PDF, just non-empty, stable
#: content a test can extract and compare byte-for-byte. Every fixture zip
#: in this project's tests that needs a "licence PDF" member uses this exact
#: constant, so a byte-identical-extraction assertion always compares
#: against a known value rather than a freshly random one.
PLACEHOLDER_LICENCE_PDF_BYTES = b"%PDF-1.4 placeholder CC-BY-4.0 licence text\n%%EOF\n"


def write_zip(zip_path: Path, members: dict[str, bytes]) -> Path:
    """Write a zip at `zip_path` containing exactly `members` (name -> bytes).

    Creates `zip_path`'s parent directory if needed. Overwrites any existing
    file at `zip_path`.
    """
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return zip_path


def shapefile_members(gdf: gpd.GeoDataFrame, tmp_dir: Path, basename: str) -> dict[str, bytes]:
    """Write `gdf` as an ESRI Shapefile into `tmp_dir` and return its sidecar
    files as `{filename: bytes}`.

    Keyed by the exact filenames a real DASC shapefile zip carries
    (`<basename>.shp`, `.shx`, `.dbf`, `.prj`, and `.cpg` when the writer
    emits one) -- never extracted from a committed zip, always freshly
    written by geopandas into `tmp_dir` for this one call.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    shp_path = tmp_dir / f"{basename}.shp"
    gdf.to_file(shp_path, driver="ESRI Shapefile")
    members: dict[str, bytes] = {}
    for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        candidate = tmp_dir / f"{basename}{suffix}"
        if candidate.exists():
            members[candidate.name] = candidate.read_bytes()
    return members
