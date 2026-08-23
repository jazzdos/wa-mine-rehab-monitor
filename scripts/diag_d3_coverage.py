"""Follow-up to diag_d3_computability.py: test whether the 2012-onward drop
in "not computable" footprint-years is driven by DEA STAC CATALOGUE ITEM
COVERAGE gaps (a (source, tile, year) simply missing from item_index for
one of a multi-tile footprint's touched tiles) rather than by per-pixel
invalidity (nodata / zero denominators).

diag_d3_computability.py's raster-read sample found pixel-level invalidity
is rare (~5% of dea_fc_pc reads failed, all <0.3% of members invalid,
scattered) -- far too small to explain a 176/180 -> 125-140/180 swing. This
script checks the OTHER candidate cause: whether item_index (the frozen
`select_catalogue_items` result, one item per (collection, tile, year))
actually covers every touched tile of a footprint for a given year and
source, for EVERY selected footprint (not just the raster-read sample),
across the full 1987-2025 span. No raster band reads at all -- only tile
grid discovery (one dataset open per DISTINCT tile, cached) plus item_index
membership checks, so it is cheap enough to run on the full population.

Read-only; writes nothing to curated outputs or src/.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("AWS_REGION", "ap-southeast-2")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "5")

import argparse

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from wa_mine_monitor import crosswalk, d3_inputs, pixel_support, register
from wa_mine_monitor.cli import _digest_verified_manifest, _load_dea_items
from wa_mine_monitor.config import load_config


def _log(obj: object) -> None:
    def _jsonable(o: object) -> object:
        if isinstance(o, dict):
            return {str(k): _jsonable(v) for k, v in o.items()}
        if isinstance(o, (list, tuple, set)):
            return [_jsonable(v) for v in o]
        if isinstance(o, np.generic):
            return o.item()
        return o

    print(json.dumps(_jsonable(obj), indent=2, sort_keys=True, default=str))
    sys.stdout.flush()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "luminosity.yaml")
    p.add_argument("--d3-inputs-date", required=True)
    p.add_argument("--year-start", type=int, default=1987)
    p.add_argument("--year-end", type=int, default=2025)
    p.add_argument("--selected-only", action="store_true", default=True)
    return p.parse_args()


_GEOMEDIAN_SOURCES = ("dea_gm_ls5t", "dea_gm_ls7e", "dea_gm_ls8cls9c")


def main() -> None:
    args = parse_args()
    resolved = load_config(args.config)
    data_root = resolved.run.data_root

    d3_inputs_manifest = _digest_verified_manifest(
        data_root / "curated" / "d3-inputs" / args.d3_inputs_date / "footprint_support.parquet"
    )
    register_input = next(
        i for i in d3_inputs_manifest["inputs"] if str(i["uri"]).startswith("curated/register/")
    )
    register_dir = data_root / "curated" / "register" / register_input["snapshot_date"]
    register_manifest = _digest_verified_manifest(register_dir / "register.parquet")
    catalogue_date = register_manifest["resolved_args"]["catalogue_date"]
    catalogue_dir = data_root / "raw" / "dea_stac" / catalogue_date
    items_by_source = _load_dea_items(catalogue_dir)
    _log(
        {
            "catalogue_dir": str(catalogue_dir),
            "n_items_by_source": {k: len(v) for k, v in items_by_source.items()},
        }
    )

    maus_snapshot_dir = register.latest_snapshot(data_root, "maus_v2")
    maus_path = maus_snapshot_dir / "wa_extract.gpkg"
    maus_source_gdf = gpd.read_file(maus_path)
    maus_gdf = maus_source_gdf[["maus_id", "geometry"]].to_crs(crosswalk.TARGET_CRS)
    maus_geom_by_id = dict(zip(maus_gdf["maus_id"].astype(str), maus_gdf.geometry, strict=True))

    fs_path = data_root / "curated" / "d3-inputs" / args.d3_inputs_date / "footprint_support.parquet"
    fs_df = pd.read_parquet(fs_path)
    pop = fs_df[fs_df["selected"] == True].copy() if args.selected_only else fs_df.copy()
    _log({"n_population_footprints": len(pop)})

    # --- tile grid discovery (one dataset open per distinct tile id) ---
    tile_grids: dict[str, pixel_support.GridSpec] = {}
    tile_bounds: dict[str, tuple[float, float, float, float]] = {}
    for source_id, items in items_by_source.items():
        kind = d3_inputs.D3_COLLECTION_KIND.get(source_id)
        if kind is None:
            continue
        for item in items:
            properties = item.get("properties") or {}
            tile_id = str(properties.get("odc:region_code") or "")
            if not tile_id or tile_id in tile_grids:
                continue
            try:
                hrefs = d3_inputs.resolve_band_hrefs(item, kind=kind)
            except d3_inputs.D3InputsError:
                continue
            href = next(iter(hrefs.values()))
            try:
                with rasterio.open(href) as dataset:
                    tile_grids[tile_id] = d3_inputs.grid_spec_from_dataset(dataset, tile_id=tile_id)
                    b = dataset.bounds
                    tile_bounds[tile_id] = (b.left, b.bottom, b.right, b.top)
            except (rasterio.errors.RasterioError, OSError):
                continue
    _log({"n_tile_grids_discovered": len(tile_grids)})

    footprint_tiles: dict[str, list[str]] = {}
    footprint_n_members: dict[str, int] = {}
    for maus_id in pop["maus_id"]:
        geometry = maus_geom_by_id.get(maus_id)
        if geometry is None:
            continue
        minx, miny, maxx, maxy = geometry.bounds
        touched_set: set[str] = set()
        n_members = 0
        for tile_id, grid in tile_grids.items():
            tminx, tminy, tmaxx, tmaxy = tile_bounds[tile_id]
            if maxx < tminx or minx > tmaxx or maxy < tminy or miny > tmaxy:
                continue
            support = pixel_support.build_pixel_support(geometry, crosswalk.TARGET_CRS, grid)
            if support is None or support.effective_pixel_support_px == 0:
                continue
            touched_set.add(tile_id)
            n_members += support.effective_pixel_support_px
        footprint_tiles[maus_id] = sorted(touched_set)
        footprint_n_members[maus_id] = n_members

    touched_tile_ids = sorted({t for tiles in footprint_tiles.values() for t in tiles})
    item_index = d3_inputs.select_catalogue_items(items_by_source, touched_tile_ids)
    years_by_source_tile: dict[tuple[str, str], set[int]] = {}
    for source_id, tile_id, year in item_index:
        years_by_source_tile.setdefault((source_id, tile_id), set()).add(year)

    def _epoch_covered_years(touched: list[str]) -> dict[str, set[int]]:
        """Per-source years where EVERY touched tile has an item (the exact
        gate build_d3_inputs_cmd's read-job construction applies)."""
        out: dict[str, set[int]] = {}
        for source_id in d3_inputs.D3_COLLECTION_KIND:
            common: set[int] | None = None
            for tile_id in touched:
                tile_years = years_by_source_tile.get((source_id, tile_id), set())
                common = tile_years if common is None else (common & tile_years)
            out[source_id] = common or set()
        return out

    # --- per footprint-year coverage verdict (proxy for computability,
    # under the "pixel validity is rare" finding from the raster sample) ---
    rows: list[dict[str, object]] = []
    for maus_id in pop["maus_id"]:
        touched = footprint_tiles.get(maus_id, [])
        if not touched:
            continue
        n_tiles = len(touched)
        per_source_years = _epoch_covered_years(touched)
        for year in range(args.year_start, args.year_end + 1):
            fc_covered = year in per_source_years.get("dea_fc_pc", set())
            gm_covered = any(year in per_source_years.get(s, set()) for s in _GEOMEDIAN_SOURCES)
            gm_covered_by = [s for s in _GEOMEDIAN_SOURCES if year in per_source_years.get(s, set())]
            rows.append(
                {
                    "maus_id": maus_id,
                    "year": year,
                    "n_touched_tiles": n_tiles,
                    "n_members": footprint_n_members.get(maus_id, 0),
                    "fc_item_covered": fc_covered,
                    "gm_item_covered": gm_covered,
                    "gm_covered_by": gm_covered_by,
                    "epoch_full_covered": fc_covered and gm_covered,
                }
            )

    df = pd.DataFrame(rows)
    print("--- COVERAGE SUMMARY (item-index availability only, no raster reads) ---")
    _log(
        {
            "epoch_full_covered_rate_by_year": (
                df.groupby("year")["epoch_full_covered"].mean().round(4).to_dict()
            )
        }
    )
    _log(
        {
            "fc_item_covered_rate_by_year": (
                df.groupby("year")["fc_item_covered"].mean().round(4).to_dict()
            )
        }
    )
    _log(
        {
            "gm_item_covered_rate_by_year": (
                df.groupby("year")["gm_item_covered"].mean().round(4).to_dict()
            )
        }
    )
    # multi-tile vs single-tile footprints: is the drop concentrated in
    # footprints that straddle more than one tile (more chances for one
    # source-tile-year item to be missing)?
    df["multi_tile"] = df["n_touched_tiles"] > 1
    _log(
        {
            "epoch_full_covered_rate_by_year_multi_tile": (
                df[df["multi_tile"]].groupby("year")["epoch_full_covered"].mean().round(4).to_dict()
            ),
            "epoch_full_covered_rate_by_year_single_tile": (
                df[~df["multi_tile"]].groupby("year")["epoch_full_covered"].mean().round(4).to_dict()
            ),
            "n_multi_tile_footprints": int(
                pop["maus_id"].isin([m for m, t in footprint_tiles.items() if len(t) > 1]).sum()
            ),
            "n_single_tile_footprints": int(
                pop["maus_id"].isin([m for m, t in footprint_tiles.items() if len(t) == 1]).sum()
            ),
        }
    )
    # which source is most often the missing one, in years the epoch failed
    failed = df[~df["epoch_full_covered"]]
    _log(
        {
            "n_footprint_years_epoch_not_covered": len(failed),
            "of_those_fc_missing": int((~failed["fc_item_covered"]).sum()),
            "of_those_no_gm_source_covered": int((~failed["gm_item_covered"]).sum()),
            "of_those_both_missing": int(
                ((~failed["fc_item_covered"]) & (~failed["gm_item_covered"])).sum()
            ),
        }
    )


if __name__ == "__main__":
    main()
