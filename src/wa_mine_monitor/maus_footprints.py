"""Derived footprint SCALARS from the Maus snapshot -- never geometry.

D13 C5 names Maus footprints among the volume estimator's inputs. The
estimator needs a SIZE per site, not a shape, so this module reduces each
matched polygon to three scalars (area, and bounding-box width/height in
metres) and nothing else. The CC-BY-SA geometry stays in the raw snapshot;
the scalars carry the Maus lineage forward
(`licence.SOURCES["maus_v2"]`, ShareAlike), which is why the artefact is
written under its own manifest rather than folded into an unrelated one.

Area alone cannot size a window: a long, narrow strip and a square of equal
area need very different reads. Bounding-box width and height are therefore
derived here rather than reconstructed from `sqrt(area)` downstream, where
the reconstruction would be wrong precisely for the elongated footprints
that matter most.

Every input failure REFUSES. A dropped polygon is a site that silently gets
the floor window -- an under-estimate wearing the same field names as a
measurement.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pyarrow as pa

from wa_mine_monitor import crosswalk

#: Declared output schema -- `maus_id` plus derived scalars, no geometry.
MAUS_FOOTPRINT_STATS_SCHEMA = pa.schema(
    [
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("footprint_area_m2", pa.float64(), nullable=False),
        pa.field("footprint_bbox_width_m", pa.float64(), nullable=False),
        pa.field("footprint_bbox_height_m", pa.float64(), nullable=False),
    ]
)

_JOIN_COLUMNS: tuple[str, ...] = (
    "site_id",
    "maus_id",
    "footprint_area_m2",
    "footprint_bbox_width_m",
    "footprint_bbox_height_m",
)


class FootprintStatsError(ValueError):
    """A footprint could not be reduced to trustworthy scalars -- refused."""


def derive_footprint_stats(maus_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Reduce Maus polygons to per-`maus_id` scalars, sorted by `maus_id`.

    `maus_gdf` must ALREADY be in `crosswalk.TARGET_CRS` (EPSG:3577, the
    equal-area metric CRS the crosswalk measures in) -- reprojecting here
    would silently accept a caller that never declared a CRS at all.
    """
    if maus_gdf.crs is None or maus_gdf.crs != crosswalk.TARGET_CRS:
        raise FootprintStatsError(
            f"maus_gdf must be projected to {crosswalk.TARGET_CRS} (equal-area, "
            f"metres) before areas are read; got {maus_gdf.crs!r}"
        )
    ids = maus_gdf["maus_id"].astype(str)
    duplicated = sorted(set(ids[ids.duplicated()]))
    if duplicated:
        raise FootprintStatsError(
            f"{len(duplicated)} duplicate maus_id(s) (first: {duplicated[0]}) -- "
            f"a duplicated footprint would double-count in the volume estimate"
        )
    rows: list[dict[str, object]] = []
    for maus_id, geometry in zip(ids, maus_gdf.geometry, strict=True):
        if geometry is None or geometry.is_empty:
            raise FootprintStatsError(
                f"maus_id {maus_id}: null or empty geometry -- refused rather "
                f"than dropped (a dropped footprint silently becomes the floor window)"
            )
        area = float(geometry.area)
        if area <= 0.0:
            raise FootprintStatsError(f"maus_id {maus_id}: non-positive area {area}")
        min_x, min_y, max_x, max_y = geometry.bounds
        rows.append(
            {
                "maus_id": maus_id,
                "footprint_area_m2": area,
                "footprint_bbox_width_m": float(max_x - min_x),
                "footprint_bbox_height_m": float(max_y - min_y),
            }
        )
    stats = pd.DataFrame(rows, columns=list(MAUS_FOOTPRINT_STATS_SCHEMA.names))
    return stats.sort_values("maus_id").reset_index(drop=True)


def join_site_footprints(
    high_confidence_crosswalk: pd.DataFrame, footprint_stats: pd.DataFrame
) -> pd.DataFrame:
    """One row per site-footprint LINK, carrying `maus_id`.

    Not reduced to `(site_id, area)`: several sites can share one footprint,
    and a site can hold more than one high-confidence link. Keeping
    `maus_id` is what lets the estimator tell a shared footprint from
    distinct ones instead of counting the same ground twice.
    """
    missing = sorted(
        set(high_confidence_crosswalk["maus_id"].dropna().astype(str))
        - set(footprint_stats["maus_id"].astype(str))
    )
    if missing:
        raise FootprintStatsError(
            f"{len(missing)} crosswalk maus_id(s) absent from the footprint "
            f"stats (first: {missing[0]}) -- the crosswalk and the footprint "
            f"artefact were built from different Maus snapshots"
        )
    joined = high_confidence_crosswalk.merge(
        footprint_stats, on="maus_id", how="left", validate="many_to_one"
    )
    return joined.loc[:, list(_JOIN_COLUMNS)].reset_index(drop=True)
