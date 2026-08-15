"""Maus et al. v2 global mining polygons: WA bounding-box extract.

CC-BY-SA-4.0 (`wa_mine_monitor.licence.SOURCES["maus_v2"]`). Per the design
doc's D1 decision, the Maus v2 polygons are the SOLE Tier 1 measurement
footprint for v1, and the ShareAlike-derived package (the WA extract, the
crosswalk, trajectories over the Maus mask) publishes SEPARATELY under
CC-BY-SA-4.0 with attribution, source link and modification statement --
ShareAlike is applied conservatively to the whole package, no scalar-field
carve-outs asserted (see `licence.py`'s `maus_v2` entry).

This is a LOCAL-source module, unlike `sources/tenements.py` and
`sources/minedex.py`: the jarrah data root already holds the global v2
GeoPackage (`~/data/jarrah-rehab/raw/maus-v2/2026-07-20/`), and the
`fetch-maus-extract` CLI command takes `--source-gpkg` pointing at it,
read-only, rather than re-downloading anything. There is therefore no
network call in this module at all, and no `requests`-faking fixture-first
rule to follow here -- every test in `tests/sources/test_maus.py` builds a
toy GeoPackage in-test with geopandas, into `tmp_path`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.geometry import box

#: WA bounding box `(minx, miny, maxx, maxy)`, EPSG:4326 degrees -- the
#: extract footprint `clip_to_wa` clips every Maus v2 feature to. Pinned as
#: a named constant (not a scattered literal) so a silent change to it is a
#: loud, reviewable diff -- same convention as `sources/tenements.py`'s
#: `slip_download_url`. `test_wa_bbox_is_pinned` asserts the exact tuple.
WA_BBOX: tuple[float, float, float, float] = (112.5, -35.5, 129.1, -13.5)

#: Number of leading hex characters of a geometry's WKB sha256 kept as
#: `maus_id`. 12 hex chars (48 bits) is ample to distinguish the WA
#: extract's feature count while staying short enough to read in a table.
_MAUS_ID_HEX_LENGTH = 12


class SnapshotValidationError(Exception):
    """A written WA Maus v2 extract GeoPackage failed validation.

    Always names the offending file path in its message, so a caller does
    not have to reconstruct which snapshot failed from a bare traceback.
    """


def read_source_gpkg(path: Path) -> gpd.GeoDataFrame:
    """Read the local global Maus v2 GeoPackage at `path`, read-only.

    A thin, named wrapper -- not a bare `gpd.read_file` call at the CLI call
    site -- so "the one function that reads the pinned local source" has a
    single indirection point, the same convention `slip_download_url` gives
    the pinned download endpoints in `sources/tenements.py` /
    `sources/minedex.py`. This module never downloads or writes the source
    file; `path` is always caller-supplied (the `--source-gpkg` CLI option),
    never hardcoded here.
    """
    return gpd.read_file(path)


def _geometry_id(geometry: object) -> str:
    """The first `_MAUS_ID_HEX_LENGTH` hex characters of the sha256 of
    `geometry`'s WKB.

    Deterministic: the same geometry bytes in always produce the same
    digest out, so `maus_id` is a stable per-feature key across independent
    calls over the same source data -- `test_clip_to_wa_is_deterministic_
    across_calls` checks this by calling `clip_to_wa` twice over freshly
    built, identical input.
    """
    wkb: bytes = geometry.wkb  # type: ignore[attr-defined]
    return hashlib.sha256(wkb).hexdigest()[:_MAUS_ID_HEX_LENGTH]


def clip_to_wa(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Clip `gdf` to `WA_BBOX` and add a deterministic `maus_id` column.

    Per the design's D1 decision, Maus v2 polygons are the SOLE Tier 1
    measurement footprint; every downstream stage reads WA features off
    this clip, never off the unfiltered global dataset. Uses `gpd.clip`
    (an actual geometric clip, not a bare bounding-box filter), so a
    feature straddling the WA boundary is truncated to the part actually
    inside it rather than kept or dropped whole.

    `maus_id` is computed per feature off the CLIPPED geometry (via
    `_geometry_id`) -- deterministic by construction: the same input
    GeoDataFrame always produces the same ids.

    Returns a new GeoDataFrame with a reset integer index (0..n-1) -- the
    row positions of the clipped-away features are never meaningful to a
    caller.

    `maus_id` is built as an explicitly-dtyped `pd.Series`, never as
    `clipped.geometry.apply(_geometry_id)`. `GeoSeries.apply` preserves the
    caller's geometry dtype when the result is EMPTY, so on a zero-feature
    clip (no source feature fell inside `WA_BBOX`) the `apply` form produced
    a SECOND geometry column: the frame then had geometry columns
    `['geometry', 'maus_id']`, and `GeoDataFrame.to_file` -- which supports
    exactly one -- died with an unhandled `ValueError` at the CLI, BEFORE
    `validate_maus_extract` was ever reached, so the empty-extract refusal
    below was unreachable through `fetch-maus-extract` and the command exited
    on a raw traceback with empty stdout. Declaring the dtype makes the
    column's type a property of the CONTRACT rather than of the rows that
    happen to be present -- the same discipline the project applies to every
    persisted table's Arrow schema.
    """
    clipped = gpd.clip(gdf, box(*WA_BBOX)).reset_index(drop=True)
    clipped["maus_id"] = pd.Series(
        [_geometry_id(geometry) for geometry in clipped.geometry],
        index=clipped.index,
        dtype="string",
    )
    return clipped


def validate_maus_extract(path: Path) -> dict[str, object]:
    """Validate a written WA Maus v2 extract GeoPackage and summarise it.

    Returns `{"feature_count": int, "crs": str, "columns": list[str]}` on a
    readable GeoPackage carrying at least one feature.

    Raises `SnapshotValidationError`, always naming `path` in its message,
    when: the file does not exist or cannot be read as a GeoPackage at all;
    the GeoPackage carries a layer count other than exactly 1 (naming every
    layer found -- reading only the first of several would silently validate
    an arbitrary fraction of the download); or its one layer is empty (0
    features) -- which for this source means no feature in the global Maus
    v2 dataset fell inside `WA_BBOX`. A silently empty, ambiguous or
    unreadable extract must never look like a valid one to anything
    downstream -- it fails loudly here instead, before `fetch-maus-extract`
    finalizes the snapshot.
    """
    path = Path(path)
    try:
        layers = pyogrio.list_layers(path)
    except pyogrio.errors.DataSourceError as exc:
        raise SnapshotValidationError(f"{path}: not a readable GeoPackage ({exc})") from exc

    if len(layers) == 0:
        raise SnapshotValidationError(f"{path}: GeoPackage contains no layers")
    if len(layers) != 1:
        layer_names = [str(layer[0]) for layer in layers]
        raise SnapshotValidationError(
            f"{path}: GeoPackage carries {len(layers)} layers {layer_names}, expected "
            "exactly 1 -- reading only the first would silently validate an "
            "arbitrary fraction of the download"
        )

    layer_name = str(layers[0][0])
    gdf = gpd.read_file(path, layer=layer_name)
    if len(gdf) == 0:
        raise SnapshotValidationError(
            f"{path}: layer is empty (0 features) -- no source feature fell inside WA_BBOX"
        )

    return {
        "feature_count": len(gdf),
        "crs": str(gdf.crs),
        "columns": list(gdf.columns),
        "layer": layer_name,
    }
