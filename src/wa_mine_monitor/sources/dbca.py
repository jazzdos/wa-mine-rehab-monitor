"""DBCA-060 fire history: constants, validation, and attribute scan.

Claim boundary (see AGENTS.md and
`docs/decisions/2026-08-29-dbca-mirror-declined.md`): fire history is
context only, never a cause of rehabilitation status, and it is never a
compliance or performance finding. `not_recorded` is a statement about
the record -- DBCA-060's own scope is fires on DBCA-managed land or
where DBCA incurred costs, so spatial completeness is not modelled --
and it is NEVER treated as a known-negative fire label (limitation
L18, `docs/amendments-and-limitations.md`).

This module covers only the pieces the F3 acquisition step needs before
it stages the GeoPackage: the layer/field/vocabulary constants, and a
validation pass that scans attributes only (never geometry -- the real
GDA94 file is 2.1 GB) before the file is trusted downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely.geometry.base import BaseGeometry

LAYER_NAME = "DBCA_Fire_History_DBCA_060"
SOURCE_CRS = "EPSG:4283"
FIRE_TYPES = frozenset({"WF", "PB", "999"})
REQUIRED_FIELDS = ("fih_master_key", "fih_fire_type", "fih_year1")

#: Hard sanity bounds on `fih_year1` -- validation-time only. The frozen
#: F4 coverage window [COVERAGE_START_YEAR, snapshot_year - 1] is a
#: SEPARATE, narrower concept (decision record 2026-08-29).
YEAR_MIN = 1900
COVERAGE_START_YEAR = 1937


class DbcaError(ValueError):
    """DBCA-060 validation or read refused."""


@dataclass(frozen=True)
class FireHistorySummary:
    """Attribute-scan result for one validated DBCA-060 GeoPackage."""

    feature_count: int
    counts_by_type: dict[str, int]
    year_min: int
    year_max: int
    crs: str


def _normalised_code(value: object) -> str:
    """Normalise a raw `fih_fire_type` value for the vocabulary check.

    The real GDA94 file carries one raw lowercase `wf`; a null value
    normalises to the empty string, which also fails the vocabulary
    check rather than being silently accepted.
    """
    return "" if value is None else str(value).strip().upper()


def validate_fire_history_file(path: Path, *, snapshot_year: int) -> FireHistorySummary:
    """Validate a staged DBCA-060 GeoPackage and scan its attributes.

    Refuses (`DbcaError`) on: the fire-history layer being absent; the
    layer's CRS not being EPSG:4283; any required field missing; zero
    features; any normalised `fih_fire_type` outside `FIRE_TYPES`; or
    any `fih_year1` null, below `YEAR_MIN`, or above `snapshot_year`.
    Never loads geometry -- the real file is 2.1 GB.
    """
    layers = [name for name, *_ in pyogrio.list_layers(path)]
    if LAYER_NAME not in layers:
        raise DbcaError(f"layer {LAYER_NAME!r} not found in {path}; layers present: {layers}")

    info = pyogrio.read_info(path, layer=LAYER_NAME)
    crs = info["crs"]
    if crs != SOURCE_CRS:
        raise DbcaError(f"expected CRS {SOURCE_CRS}, got {crs!r} for {path}")

    fields = set(info["fields"])
    missing = [f for f in REQUIRED_FIELDS if f not in fields]
    if missing:
        raise DbcaError(f"required field(s) missing from {LAYER_NAME}: {missing}")

    feature_count = int(info["features"])
    if feature_count == 0:
        raise DbcaError(f"{LAYER_NAME} in {path} has 0 features")

    attrs = pyogrio.read_dataframe(
        path,
        layer=LAYER_NAME,
        read_geometry=False,
        columns=["fih_fire_type", "fih_year1"],
    )

    normalised = attrs["fih_fire_type"].map(_normalised_code)
    unexpected = sorted(set(normalised) - FIRE_TYPES)
    if unexpected:
        raise DbcaError(f"unexpected fih_fire_type code(s) in {path}: {unexpected}")

    years = attrs["fih_year1"]
    if years.isna().any():
        raise DbcaError(f"fih_year1 has null value(s) in {path}")

    year_min = int(years.min())
    year_max = int(years.max())
    if year_min < YEAR_MIN:
        raise DbcaError(f"fih_year1 {year_min} is below the minimum {YEAR_MIN}")
    if year_max > snapshot_year:
        raise DbcaError(f"fih_year1 {year_max} is after the snapshot year {snapshot_year}")

    counts_by_type = {code: int(count) for code, count in normalised.value_counts().items()}
    counts_by_type = dict(sorted(counts_by_type.items()))

    return FireHistorySummary(
        feature_count=feature_count,
        counts_by_type=counts_by_type,
        year_min=year_min,
        year_max=year_max,
        crs=crs,
    )


def fire_year_counts_for_footprint(
    gpkg_path: Path,
    footprint_4283: BaseGeometry,
) -> dict[int, int]:
    """Count intersecting fire polygons per `fih_year1` for one footprint.

    Reads ONLY the footprint's bbox window from the GeoPackage
    (`pyogrio` bbox pushdown onto the layer's r-tree) -- the statewide
    file is 2.1 GB and must never be loaded whole. bbox prefilter, then
    an exact `.intersects` test (touching-only bboxes are not
    intersections). All fire types count (WF, PB and 999 are all
    recorded fires).
    """
    frame = gpd.read_file(
        gpkg_path,
        layer=LAYER_NAME,
        bbox=tuple(footprint_4283.bounds),
        columns=["fih_year1"],
        engine="pyogrio",
    )
    if frame.empty:
        return {}

    intersecting = frame[frame.geometry.intersects(footprint_4283)]
    if intersecting.empty:
        return {}

    return {
        int(year): int(count) for year, count in intersecting["fih_year1"].value_counts().items()
    }
