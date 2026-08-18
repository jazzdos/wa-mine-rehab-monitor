"""Shared raster × polygon zonal statistics (T-075) — the reusable compute core
for every gridded/satellite adapter that reduces a pixel field onto region
polygons (SA2s today; any region set under the global rollout).

Extracted VERBATIM-in-arithmetic from adapters/modis_maiac_aod.py (the MAIAC
per-SA2 zonal reduce), so that the NOAA HMS smoke adapter (T-089 arm 4) and
future gridded adapters do not re-derive it. maiac's emitted output is proven
byte-identical across the extraction by tests/test_zonal.py (parity gate against
a frozen pre-refactor fixture).

MODEL — pixel-centre membership. A pixel belongs to a region iff its CENTRE
falls inside the region polygon (shapely.contains_xy), i.e. the stat is an
equal-weight mean over member pixels: the standard grid-resolution approximation
of an area-weighted mean (each member pixel contributes one pixel-area). No
partial-pixel weighting — at 1 km pixels vs SA2 polygons the boundary term is
noise, and exact sub-pixel intersection would cost far more than it buys.

INPUTS are adapter-neutral:
  * a rectilinear grid described by its outer corner bounds in the grid CRS
    (ulx, uly, lrx, lry — upper-left / lower-right corner, NOT pixel centres)
    plus (height, width); `bounds_from_affine()` converts a rasterio-style
    north-up affine transform to these bounds,
  * an optional pyproj Transformer (always_xy=True) mapping the grid CRS to
    EPSG:4326 — pass None when the grid is already lon/lat,
  * region polygons as {region_id: shapely geometry} in EPSG:4326
    (the lake-canonical CRS since T-029).
OUTPUT: {region_id: (stat, n_valid_pixels)} via `zonal_stats()`, or the raw
{region_id: flat-pixel-index array} via `build_assignment()` for adapters (like
maiac) that need custom accumulation (multi-orbit stacks, dual QA counts).

MEMOISATION — the expensive parts (the pixel-centre geolocation mesh and the
point-in-polygon assignment) depend ONLY on the grid geometry + the static
region set, NOT on the day's pixel values. `build_assignment()` therefore
memoises per (grid corners/dims × region-set signature) key: a multi-year
backfill over a fixed tile grid builds each tile's assignment ONCE. Callers may
pass their own `cache` dict (maiac does, so its tests can clear it) or share the
module default. `.clear()` is always safe — results are unchanged, only rebuilt.

Heavy deps (numpy, shapely) import lazily inside the functions, per the core/
convention — importing this module costs nothing.
"""
from __future__ import annotations

# Module-default assignment cache. Keyed by grid_key(); safe to .clear().
_ASSIGN_CACHE: dict = {}


def bounds_from_affine(transform, height: int, width: int):
    """Rasterio-style affine `(a, b, c, d, e, f)` (or an Affine object — anything
    that iterates to ≥6 coefficients) → the outer corner bounds
    (ulx, uly, lrx, lry) this module works in. North-up grids only: a rotated or
    sheared transform (b or d nonzero) raises ValueError — no adapter feeds one,
    and silently ignoring the rotation would misplace every pixel."""
    a, b, c, d, e, f = tuple(transform)[:6]
    if b != 0 or d != 0:
        raise ValueError(
            "rotated/sheared affine transforms are not supported "
            f"(b={b!r}, d={d!r} must be 0 for a north-up grid)"
        )
    return (c, f, c + a * width, f + e * height)


def pixel_centre_lonlat(ulx, uly, lrx, lry, height: int, width: int,
                        transformer=None):
    """Pixel-centre lon/lat (EPSG:4326), each shaped (height, width), for a
    rectilinear grid with outer corner bounds (ulx, uly, lrx, lry) in the grid
    CRS. Pixel-centre coordinates: x = ULx + (col+0.5)·(LRx−ULx)/W,
    y = ULy + (row+0.5)·(LRy−ULy)/H (LRy < ULy → the y step is negative).
    `transformer` is a pyproj Transformer (always_xy=True) from the grid CRS to
    EPSG:4326; None means the bounds are already lon/lat."""
    import numpy as np
    cols = np.arange(width)
    rows = np.arange(height)
    xs_1d = ulx + (cols + 0.5) * (lrx - ulx) / width
    ys_1d = uly + (rows + 0.5) * (lry - uly) / height
    x2d, y2d = np.meshgrid(xs_1d, ys_1d)             # (height, width)
    if transformer is None:
        return x2d, y2d
    lon2d, lat2d = transformer.transform(x2d, y2d)
    return np.asarray(lon2d), np.asarray(lat2d)


def region_signature(bounds_by_region: dict) -> tuple:
    """Hashable signature of a (static) region set for the assignment-cache key:
    sorted (region_id, rounded lon/lat bbox) tuples. Two runs over the same
    region tessellation share it; a different region set (global rollout) keys a
    distinct cache entry."""
    return tuple(sorted(
        (rid,) + tuple(round(float(c), 6) for c in bounds)
        for rid, bounds in bounds_by_region.items()
    ))


def grid_key(ulx, uly, lrx, lry, height: int, width: int,
             regions_sig: tuple) -> tuple:
    """Cache key for one physical grid × region set: the corner bounds (rounded)
    + grid dims + the region signature. Day-INVARIANT — the same grid on any day
    maps to the same key, so the mesh + assignment build once."""
    return (round(float(ulx), 3), round(float(uly), 3),
            round(float(lrx), 3), round(float(lry), 3),
            int(height), int(width), regions_sig)


def build_assignment(ulx, uly, lrx, lry, height: int, width: int,
                     polygons: dict, *, transformer=None,
                     bounds_by_region: dict | None = None,
                     regions_sig: tuple | None = None,
                     cache: dict | None = None,
                     lonlat_fn=None) -> dict:
    """Map (grid cell → region) for one grid: {region_id: flat-index array of
    the grid's in-polygon pixel centres} (indices into the raveled (H·W,) grid).
    Only regions with ≥1 member pixel appear.

    `polygons` = {region_id: shapely geometry} in EPSG:4326; iteration (and thus
    the returned dict's insertion order) follows `polygons` order — pass an
    ordered dict when downstream accumulation order matters (maiac does, for
    bit-reproducible float sums). `bounds_by_region` (lon/lat bboxes) and
    `regions_sig` are computed from `polygons` when omitted. `cache` defaults to
    the module-level `_ASSIGN_CACHE`; pass your own dict for isolated caching.
    `lonlat_fn` (default `pixel_centre_lonlat`, same signature) exists as a
    test/instrumentation seam.

    Depends ONLY on the grid's fixed bounds/dims and the static polygons, so it
    is memoised per `grid_key()` and reused across all days — the geolocation
    mesh + shapely.contains_xy run ONCE per unique grid × region set."""
    if bounds_by_region is None:
        bounds_by_region = {rid: p.bounds for rid, p in polygons.items()}
    if regions_sig is None:
        regions_sig = region_signature(bounds_by_region)
    if cache is None:
        cache = _ASSIGN_CACHE
    key = grid_key(ulx, uly, lrx, lry, height, width, regions_sig)
    cached = cache.get(key)
    if cached is not None:
        return cached

    import numpy as np
    import shapely

    if lonlat_fn is None:
        lonlat_fn = pixel_centre_lonlat
    assign: dict = {}
    lon2d, lat2d = lonlat_fn(ulx, uly, lrx, lry, height, width, transformer)
    lon_flat = lon2d.ravel()
    lat_flat = lat2d.ravel()
    finite = np.isfinite(lon_flat) & np.isfinite(lat_flat)
    if not finite.any():
        cache[key] = assign
        return assign
    gminlon = float(lon_flat[finite].min())
    gmaxlon = float(lon_flat[finite].max())
    gminlat = float(lat_flat[finite].min())
    gmaxlat = float(lat_flat[finite].max())

    for rid, poly in polygons.items():
        minlon, minlat, maxlon, maxlat = bounds_by_region[rid]
        if (maxlon < gminlon or minlon > gmaxlon
                or maxlat < gminlat or minlat > gmaxlat):
            continue  # region bbox does not touch this grid
        sel = (finite
               & (lon_flat >= minlon) & (lon_flat <= maxlon)
               & (lat_flat >= minlat) & (lat_flat <= maxlat))
        idx = np.where(sel)[0]
        if idx.size == 0:
            continue
        inpoly = shapely.contains_xy(poly, lon_flat[idx], lat_flat[idx])
        keep = idx[inpoly]
        if keep.size == 0:
            continue
        assign[rid] = keep

    cache[key] = assign
    return assign


def zonal_stats(values, assignment: dict, *, valid=None,
                stat: str = "mean") -> dict:
    """Reduce a pixel field onto regions: {region_id: (stat, n_valid_pixels)}.

    `values` is (height, width) or a stacked (n, height, width) — e.g. multiple
    orbits/timesteps pooled into one region stat — matching the grid
    `assignment` was built for. `valid` (same shape, boolean) masks which pixels
    count (QA-clean, non-fill, ...); None = all. Regions with zero valid member
    pixels are OMITTED — never fabricated. `stat` ∈ {mean, sum, min, max}; the
    mean is the equal-weight mean over valid member pixel centres (the
    grid-resolution area-weighted mean — see module docstring).

    Adapters needing more than one number per region (maiac's dual clean/total
    QA counts) should consume `build_assignment()` directly instead."""
    import numpy as np
    values = np.asarray(values)
    if values.ndim == 2:
        values = values[None, :, :]
    n_stack = values.shape[0]
    v_r = values.reshape(n_stack, -1)
    m_r = None
    if valid is not None:
        valid = np.asarray(valid, dtype=bool)
        if valid.ndim == 2:
            valid = valid[None, :, :]
        m_r = valid.reshape(n_stack, -1)

    out: dict = {}
    for rid, keep in assignment.items():
        sub = v_r[:, keep]
        mask = m_r[:, keep] if m_r is not None else None
        vals = sub[mask] if mask is not None else sub.ravel()
        n = int(vals.size)
        if n == 0:
            continue
        if stat == "mean":
            s = float(vals.sum()) / n
        elif stat == "sum":
            s = float(vals.sum())
        elif stat == "min":
            s = float(vals.min())
        elif stat == "max":
            s = float(vals.max())
        else:
            raise ValueError(f"unknown stat {stat!r} (mean/sum/min/max)")
        out[rid] = (s, n)
    return out
