"""DPIRD-020 Regional Development Commission boundary extract.

Validates and normalises the downloaded GeoPackage: exactly one usable
region-name column, non-null unique names, both protocol-required regions
present, reprojected to `crosswalk.TARGET_CRS` so downstream point-in-
polygon runs in the same equal-area CRS as every other spatial join in
this project. The D3 strata (`pilbara`/`goldfields_esperance`/`other_wa`)
are assigned by `d3_protocol`, not here.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon

from wa_mine_monitor import crosswalk

#: Region-name column candidates, in preference order. DPIRD-020's SLIP
#: exports have carried both spellings; the loader requires exactly one of
#: them to be present.
_NAME_COLUMNS: tuple[str, ...] = ("dpird_region_name", "region_name", "name")

#: The two regions the D3 protocol names as their own strata. Their absence
#: means the wrong dataset (or a truncated download) -- refuse, never guess.
REQUIRED_REGIONS: tuple[str, ...] = ("Pilbara", "Goldfields-Esperance")


class RegionExtractError(ValueError):
    """The boundary extract cannot support the D3 protocol -- refused."""


def load_regions(path: Path) -> gpd.GeoDataFrame:
    """Read, validate and normalise the DPIRD-020 GeoPackage."""
    gdf = gpd.read_file(path)

    # Check CRS is present
    if gdf.crs is None:
        raise RegionExtractError("boundary extract has no CRS")

    # Check exactly one name column is present (not multiple)
    name_columns_present = [c for c in _NAME_COLUMNS if c in gdf.columns]
    if len(name_columns_present) > 1:
        raise RegionExtractError(
            f"multiple name column candidates present: {name_columns_present}; "
            f"expected exactly one of {_NAME_COLUMNS}"
        )

    # Find the name column
    name_column = next((c for c in _NAME_COLUMNS if c in gdf.columns), None)
    if name_column is None:
        raise RegionExtractError(
            f"no usable region name column in {sorted(gdf.columns)}; "
            f"expected one of {_NAME_COLUMNS}"
        )

    # Check for null region names
    names = gdf[name_column]
    if names.isna().any():
        raise RegionExtractError("null region name in boundary extract")

    # Check for duplicate region names
    if names.duplicated().any():
        duplicated = sorted(names[names.duplicated()].unique())
        raise RegionExtractError(f"duplicate region names: {duplicated}")

    # Check for required regions
    missing = [r for r in REQUIRED_REGIONS if r not in set(names)]
    if missing:
        raise RegionExtractError(f"required regions absent from extract: {missing}")

    # Validate geometries
    for idx, geom in enumerate(gdf.geometry):
        if geom is None or geom.is_empty:
            raise RegionExtractError(f"row {idx} has null or empty geometry")
        if not geom.is_valid:
            raise RegionExtractError(f"row {idx} has invalid geometry")
        # Check that geometry is a Polygon or MultiPolygon
        if not isinstance(geom, (Polygon, MultiPolygon)):
            geom_type = geom.geom_type
            raise RegionExtractError(
                f"row {idx} has {geom_type} geometry; expected Polygon or MultiPolygon"
            )

    # Validate that region polygon interiors do not overlap
    # (pairwise check: interior intersection area > 0 → refusal)
    for i in range(len(gdf)):
        for j in range(i + 1, len(gdf)):
            geom_i = gdf.geometry.iloc[i]
            geom_j = gdf.geometry.iloc[j]
            # Check if interiors overlap (interior_intersection area > 0)
            # For simple polygons, if they overlap in their interior, the intersection
            # will be a polygon with non-zero area
            intersection = geom_i.intersection(geom_j)
            if intersection.geom_type == "Polygon" and not intersection.is_empty:
                # Interior overlap detected
                name_i = gdf[name_column].iloc[i]
                name_j = gdf[name_column].iloc[j]
                raise RegionExtractError(
                    f"regions '{name_i}' and '{name_j}' have overlapping interiors"
                )

    # Select the name column and geometry, rename to region_name
    out = gdf[[name_column, "geometry"]].rename(columns={name_column: "region_name"})
    return out.to_crs(crosswalk.TARGET_CRS).reset_index(drop=True)
