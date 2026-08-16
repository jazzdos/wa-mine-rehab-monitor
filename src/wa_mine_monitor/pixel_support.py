"""Pixel-CENTRE support on the fixed 30 m EPSG:3577 grid (D13 task D2).

Membership is exact: a pixel belongs to a footprint when its CENTRE is
covered by the polygon (boundary inclusive). Partial-pixel weighting and
all_touched are prohibited by the D3 protocol. The assignment identity
binds grid CRS, affine transform, width, height and product tile identity,
so the same polygon on a different tile is a DIFFERENT assignment.

DEA collection-3 tiling convention (verified 2026-08-16 against live data):
grid origins follow the 96,000 m (3200 × 30 m) tile lattice with formula
`origin_x = x_index * 96_000` and `origin_y = (y_index + 1) * 96_000`,
where tile_id = f"x{x_index}y{y_index}" (indices can be negative). The
tile's upper-left corner (in north-up EPSG:3577) is at (origin_x, origin_y).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

import numpy as np
import shapely

from wa_mine_monitor import crosswalk

PIXEL_METRES = 30.0
#: DEA collection-3 tile size in metres (3200 pixels * 30 m/pixel).
TILE_SIZE_METRES = 96_000.0


class PixelSupportError(ValueError):
    """The grid cannot carry a D3 pixel-support assignment -- refused."""


@dataclass(frozen=True)
class GridSpec:
    """Grid identity: CRS, GDAL-order affine (a,b,c,d,e,f), size, tile."""

    crs: str
    transform: tuple[float, float, float, float, float, float]
    width: int
    height: int
    tile_id: str


@dataclass(frozen=True)
class PixelSupport:
    grid: GridSpec
    member_indices: tuple[tuple[int, int], ...]  # (row, col), sorted
    effective_pixel_support_px: int
    assignment_digest: str


def _validate_grid(grid: GridSpec) -> None:
    """Validate CRS, pixel size, lattice alignment, and DEA tile origin."""
    # Require EPSG:3577
    if grid.crs != crosswalk.TARGET_CRS:
        raise PixelSupportError(f"CRS {grid.crs} != required {crosswalk.TARGET_CRS}")

    a, b, c, d, e, f = grid.transform

    # Check rotation/shear: must be axis-aligned.
    if b != 0.0 or d != 0.0:
        raise PixelSupportError(f"rotated/sheared grid refused: transform {grid.transform}")

    # Check pixel size: must be 30 m.
    if a != PIXEL_METRES or e != -PIXEL_METRES:
        raise PixelSupportError(f"pixel size ({a}, {e}) != required (30.0, -30.0)")

    # Check grid origin is on the 30 m lattice.
    if c % PIXEL_METRES != 0.0 or f % PIXEL_METRES != 0.0:
        raise PixelSupportError(
            f"grid origin ({c}, {f}) is off the 30 m lattice -- shifted grid refused"
        )

    # If tile_id matches the DEA xNyN pattern, validate against collection-3
    # tile lattice: origin_x = x_index * 96_000, origin_y = (y_index + 1) * 96_000.
    tile_match = re.match(r"^x(-?\d+)y(-?\d+)$", grid.tile_id)
    if tile_match:
        x_index = int(tile_match.group(1))
        y_index = int(tile_match.group(2))

        expected_origin_x = x_index * TILE_SIZE_METRES
        expected_origin_y = (y_index + 1) * TILE_SIZE_METRES

        if c != expected_origin_x or f != expected_origin_y:
            raise PixelSupportError(
                f"grid origin ({c}, {f}) inconsistent with DEA collection-3 "
                f"tile lattice for tile_id {grid.tile_id}: "
                f"expected ({expected_origin_x}, {expected_origin_y})"
            )


def build_pixel_support(
    geometry: shapely.Geometry | None,
    geometry_crs: str,
    grid: GridSpec,
) -> PixelSupport | None:
    """Assign member pixel centres. Returns None (NOT computed) for a
    missing or invalid geometry; returns a computed 0-member assignment for
    a valid geometry covering no centre."""
    _validate_grid(grid)
    if geometry_crs != grid.crs:
        raise PixelSupportError(f"geometry CRS {geometry_crs} != grid CRS {grid.crs}")
    if geometry is None or geometry.is_empty or not geometry.is_valid:
        return None

    a, _, c, _, e, f = grid.transform
    minx, miny, maxx, maxy = geometry.bounds
    col_lo = max(0, int(np.floor((minx - c) / a - 0.5)))
    col_hi = min(grid.width - 1, int(np.ceil((maxx - c) / a - 0.5)))
    row_lo = max(0, int(np.floor((maxy - f) / e - 0.5)))
    row_hi = min(grid.height - 1, int(np.ceil((miny - f) / e - 0.5)))
    members: list[tuple[int, int]] = []
    if col_lo <= col_hi and row_lo <= row_hi:
        cols = np.arange(col_lo, col_hi + 1)
        rows = np.arange(row_lo, row_hi + 1)
        col_grid, row_grid = np.meshgrid(cols, rows)
        xs = c + (col_grid + 0.5) * a
        ys = f + (row_grid + 0.5) * e
        centres = shapely.points(xs.ravel(), ys.ravel())
        covered = shapely.covered_by(centres, geometry)
        members = sorted(
            (int(r), int(col))
            for r, col, hit in zip(row_grid.ravel(), col_grid.ravel(), covered, strict=True)
            if hit
        )
    digest_payload = json.dumps(
        {
            "crs": grid.crs,
            "transform": list(grid.transform),
            "width": grid.width,
            "height": grid.height,
            "tile_id": grid.tile_id,
            "members": members,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return PixelSupport(
        grid=grid,
        member_indices=tuple(members),
        effective_pixel_support_px=len(members),
        assignment_digest=hashlib.sha256(digest_payload.encode("utf-8")).hexdigest(),
    )
