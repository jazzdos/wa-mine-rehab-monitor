"""Standalone diagnostic: WHY are footprint-years from ~2012 onward often
"not computable" in the D3 build_d3_inputs Phase A validity pass, while
1987-1999 is ~176/180 computable?

This script reuses the repo's own setup/read/decode code (imported directly
from wa_mine_monitor.cli and wa_mine_monitor.d3_inputs -- not reimplemented)
to reproduce, for a SAMPLE of footprints, exactly what Phase A does:

  1. Load the same gate-verified curated inputs build_d3_inputs_cmd uses
     (register -> catalogue_date, maus geometry snapshot, DEA STAC catalogue
     snapshot, footprint_support.parquet from the target curated d3-inputs
     run to pick footprints and their region/selected flags).
  2. For each sampled footprint: rebuild its tile grid, pixel-support
     members, and touched tiles exactly as build_d3_inputs_cmd's Phase A
     block does (pixel_support.build_pixel_support against each touched
     tile's ACTUAL raster grid).
  3. select_catalogue_items over the touched tiles, for each (footprint,
     year in the requested range, source) read the raw member-pixel bands
     via cli._read_footprint_year_bands (same function, same code path),
     decode via cli._decode_d3_bands, and compute:
       - n_members
       - n_invalid pixels (year_computable's own valid-mask, inverted)
       - per-band non-finite pixel count
       - per-metric zero-denominator pixel count (geomedian only)
       - contiguity check: invalid members' row/col min/max vs. all
         members' row/col min/max, plus whether invalid members form a
         single small contiguous block per tile or are scattered singleton
         pixels across the tile.

Never modifies curated outputs or any src/ file. Read-only against S3 DEA
assets (AWS_NO_SIGN_REQUEST=YES) and the local curated parquet lake.

Run on luminosity (data lives there; do not rsync to a metered Mac):

    cd ~/wa-mine-rehab-monitor
    AWS_NO_SIGN_REQUEST=YES AWS_REGION=ap-southeast-2 \
    GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR GDAL_HTTP_MAX_RETRY=5 \
    GDAL_HTTP_RETRY_DELAY=5 \
    ~/.local/bin/uv run python scripts/diag_d3_computability.py \
      --config config/luminosity.yaml \
      --d3-inputs-date 2026-08-21 \
      --year-start 2008 --year-end 2020 \
      --n-per-region 4 \
      > /mnt/data/wa-mine-monitor/reports/diag-computability.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("AWS_REGION", "ap-southeast-2")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "5")

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from wa_mine_monitor import crosswalk, d3_inputs, pixel_support, register
from wa_mine_monitor.cli import (
    _decode_d3_bands,
    _digest_verified_manifest,
    _load_dea_items,
    _read_footprint_year_bands,
)
from wa_mine_monitor.config import load_config

_GEOMEDIAN_SOURCES = ("dea_gm_ls5t", "dea_gm_ls7e", "dea_gm_ls8cls9c")


def _jsonable(obj: object) -> object:
    """Recursively coerce dict keys/values (numpy scalars, tuple keys,
    pandas Timestamps, etc.) into plain JSON-safe Python types."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


def _log(obj: object) -> None:
    print(json.dumps(_jsonable(obj), indent=2, sort_keys=True, default=str))
    sys.stdout.flush()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "luminosity.yaml")
    p.add_argument(
        "--d3-inputs-date",
        required=True,
        help="dated curated/d3-inputs/<date>/ run to pick sample footprints (region, selected) from",
    )
    p.add_argument("--year-start", type=int, default=2008)
    p.add_argument("--year-end", type=int, default=2020)
    p.add_argument("--n-per-region", type=int, default=4)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--strategy",
        choices=("random", "worst"),
        default="random",
        help=(
            "'random' (default): seeded random draw of selected footprints per region. "
            "'worst': the N lowest (n_full_support_years / n_epoch_covered_years) selected "
            "footprints per region -- targets the actual low-computability tail instead of "
            "the (mostly-high-computability) bulk a random draw tends to land on."
        ),
    )
    return p.parse_args()


def _row_col_extent(members: list[tuple[str, int, int]]) -> dict[str, dict[str, int]]:
    by_tile: dict[str, list[tuple[int, int]]] = {}
    for tile_id, r, c in members:
        by_tile.setdefault(tile_id, []).append((r, c))
    out: dict[str, dict[str, int]] = {}
    for tile_id, rcs in by_tile.items():
        rows = [r for r, _ in rcs]
        cols = [c for _, c in rcs]
        out[tile_id] = {
            "n": len(rcs),
            "row_min": min(rows),
            "row_max": max(rows),
            "col_min": min(cols),
            "col_max": max(cols),
        }
    return out


def _contiguity_report(
    all_members: list[tuple[str, int, int]], invalid_idx: np.ndarray
) -> dict[str, object]:
    """Compare invalid members' per-tile row/col bounding box against ALL
    members' per-tile bounding box, plus how many distinct tiles carry
    invalid members and the largest per-tile invalid run (row-adjacent
    pixel count) as a cheap scatter-vs-block signal."""
    invalid_members = [all_members[i] for i in invalid_idx]
    all_extent = _row_col_extent(all_members)
    invalid_extent = _row_col_extent(invalid_members)
    per_tile: dict[str, object] = {}
    for tile_id, inv in invalid_extent.items():
        allb = all_extent[tile_id]
        row_span_frac = (inv["row_max"] - inv["row_min"] + 1) / max(
            1, allb["row_max"] - allb["row_min"] + 1
        )
        col_span_frac = (inv["col_max"] - inv["col_min"] + 1) / max(
            1, allb["col_max"] - allb["col_min"] + 1
        )
        # a "block" if invalid pixels are dense within their own bbox
        bbox_cells = (inv["row_max"] - inv["row_min"] + 1) * (inv["col_max"] - inv["col_min"] + 1)
        density = inv["n"] / bbox_cells if bbox_cells else 0.0
        per_tile[tile_id] = {
            "n_invalid_this_tile": inv["n"],
            "n_all_this_tile": allb["n"],
            "invalid_bbox_row_span_frac_of_all": round(row_span_frac, 4),
            "invalid_bbox_col_span_frac_of_all": round(col_span_frac, 4),
            "invalid_density_within_own_bbox": round(density, 4),
            "touches_edge_of_all_bbox": bool(
                inv["row_min"] == allb["row_min"]
                or inv["row_max"] == allb["row_max"]
                or inv["col_min"] == allb["col_min"]
                or inv["col_max"] == allb["col_max"]
            ),
        }
    return {
        "n_tiles_with_invalid": len(invalid_extent),
        "n_tiles_total": len(all_extent),
        "per_tile": per_tile,
    }


def main() -> None:
    args = parse_args()
    resolved = load_config(args.config)
    data_root = resolved.run.data_root

    # --- register -> catalogue_date, pinned to the EXACT register snapshot
    # the target d3-inputs run itself used (its own run manifest names it
    # under "inputs"; `_latest_curated_dated_dir` picks whatever the LATEST
    # curated/register/ directory happens to be right now, which can be a
    # newer, unrelated register build -- e.g. one lacking catalogue_date
    # entirely -- so it is deliberately not used here). ---
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
    _log({"catalogue_dir": str(catalogue_dir)})
    items_by_source = _load_dea_items(catalogue_dir)
    _log({"n_items_by_source": {k: len(v) for k, v in items_by_source.items()}})

    # --- maus geometry snapshot (mirrors GATE 6) ---
    maus_snapshot_dir = register.latest_snapshot(data_root, "maus_v2")
    maus_path = maus_snapshot_dir / "wa_extract.gpkg"
    maus_source_gdf = gpd.read_file(maus_path)
    maus_gdf = maus_source_gdf[["maus_id", "geometry"]].to_crs(crosswalk.TARGET_CRS)
    maus_geom_by_id = dict(zip(maus_gdf["maus_id"].astype(str), maus_gdf.geometry, strict=True))

    # --- sample footprints from the target d3-inputs run ---
    fs_path = data_root / "curated" / "d3-inputs" / args.d3_inputs_date / "footprint_support.parquet"
    fs_df = pd.read_parquet(fs_path)
    selected = fs_df[fs_df["selected"] == True].copy()
    sample_rows = []
    if args.strategy == "worst":
        selected["computable_frac"] = (
            selected["n_full_support_years"] / selected["n_epoch_covered_years"]
        )
        for region, group in selected.groupby("region"):
            take = min(args.n_per_region, len(group))
            sample_rows.append(group.sort_values("computable_frac").head(take))
    else:
        rng = np.random.default_rng(args.seed)
        for region, group in selected.groupby("region"):
            ids = sorted(group["maus_id"].tolist())
            take = min(args.n_per_region, len(ids))
            chosen = rng.choice(ids, size=take, replace=False)
            sample_rows.append(group[group["maus_id"].isin(chosen)])
    sample_df = pd.concat(sample_rows, ignore_index=True)
    _log(
        {
            "n_sample_footprints": len(sample_df),
            "sample_maus_ids_by_region": sample_df.groupby("region")["maus_id"].apply(list).to_dict(),
        }
    )

    # --- per-footprint tile grids, members, touched tiles (mirrors the
    # support block in build_d3_inputs_cmd, restricted to the sampled
    # footprints' geometries only -- tile discovery still scans ALL tiles
    # in items_by_source since a footprint's touched tiles are not known
    # ahead of time). ---
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
            except (rasterio.errors.RasterioError, OSError) as exc:
                _log({"tile_grid_open_failed": tile_id, "error": str(exc)})
    _log({"n_tile_grids_discovered": len(tile_grids)})

    footprint_members: dict[str, list[tuple[str, int, int]]] = {}
    footprint_tiles: dict[str, list[str]] = {}
    for maus_id in sample_df["maus_id"]:
        geometry = maus_geom_by_id[maus_id]
        minx, miny, maxx, maxy = geometry.bounds
        member_set: set[tuple[str, int, int]] = set()
        touched_set: set[str] = set()
        for tile_id, grid in tile_grids.items():
            tminx, tminy, tmaxx, tmaxy = tile_bounds[tile_id]
            if maxx < tminx or minx > tmaxx or maxy < tminy or miny > tmaxy:
                continue
            support = pixel_support.build_pixel_support(geometry, crosswalk.TARGET_CRS, grid)
            if support is None or support.effective_pixel_support_px == 0:
                continue
            touched_set.add(tile_id)
            member_set.update((tile_id, r, c) for r, c in support.member_indices)
        footprint_members[maus_id] = sorted(member_set)
        footprint_tiles[maus_id] = sorted(touched_set)

    touched_tile_ids = sorted({t for tiles in footprint_tiles.values() for t in tiles})
    item_index = d3_inputs.select_catalogue_items(items_by_source, touched_tile_ids)
    years_by_source_tile: dict[tuple[str, str], set[int]] = {}
    for source_id, tile_id, year in item_index:
        years_by_source_tile.setdefault((source_id, tile_id), set()).add(year)

    # --- diagnostic reads: every (footprint, year in range, source) whose
    # item_index covers ALL of that footprint's touched tiles. ---
    detail_rows: list[dict[str, Any]] = []
    for _, row in sample_df.iterrows():
        maus_id = row["maus_id"]
        region = row["region"]
        touched = footprint_tiles[maus_id]
        members = footprint_members[maus_id]
        n_members = len(members)
        if not touched or n_members == 0:
            _log({"maus_id": maus_id, "skip": "no touched tiles / no members"})
            continue
        for year in range(args.year_start, args.year_end + 1):
            for source_id, kind in d3_inputs.D3_COLLECTION_KIND.items():
                if not all((source_id, tile_id, year) in item_index for tile_id in touched):
                    continue
                try:
                    raw_bands, _extraction_rows = _read_footprint_year_bands(
                        source_id=source_id,
                        kind=kind,
                        year=year,
                        touched_tiles=touched,
                        members=members,
                        item_index=item_index,
                        phase="diag",
                    )
                except (d3_inputs.D3InputsError, rasterio.errors.RasterioError, OSError) as exc:
                    _log(
                        {
                            "maus_id": maus_id,
                            "region": region,
                            "year": year,
                            "source_id": source_id,
                            "read_error": str(exc),
                        }
                    )
                    continue
                decoded = _decode_d3_bands(raw_bands, kind=kind)
                if kind == "geomedian":
                    mask = d3_inputs.geomedian_valid_mask(decoded)
                else:
                    mask = d3_inputs.fc_valid_mask(decoded)
                n_invalid = int((~mask).sum())
                computable = bool(mask.all())

                per_band_nonfinite = {
                    band: int((~np.isfinite(values)).sum()) for band, values in decoded.items()
                }
                per_metric_zero_denom: dict[str, int] = {}
                if kind == "geomedian":
                    for metric, (plus, minus) in d3_inputs.GEOMEDIAN_METRIC_BANDS.items():
                        denom = decoded[plus] + decoded[minus]
                        # zero-but-finite denominators only (NaN already
                        # counted under non-finite bands above)
                        per_metric_zero_denom[metric] = int(
                            ((denom == 0) & np.isfinite(denom)).sum()
                        )

                contiguity = None
                if n_invalid > 0:
                    invalid_idx = np.flatnonzero(~mask)
                    contiguity = _contiguity_report(members, invalid_idx)

                detail_rows.append(
                    {
                        "maus_id": maus_id,
                        "region": region,
                        "year": year,
                        "source_id": source_id,
                        "kind": kind,
                        "n_members": n_members,
                        "n_invalid": n_invalid,
                        "invalid_frac": round(n_invalid / n_members, 6),
                        "computable": computable,
                        "per_band_nonfinite": per_band_nonfinite,
                        "per_metric_zero_denominator": per_metric_zero_denom,
                        "contiguity": contiguity,
                    }
                )

    _log({"n_footprint_year_source_reads": len(detail_rows)})

    # --- full detail (JSON lines, one per read) ---
    print("--- DETAIL ROWS (one JSON object per line) ---")
    for row in detail_rows:
        print(json.dumps(row, sort_keys=True, default=str))
    sys.stdout.flush()

    # --- summary ---
    df = pd.DataFrame(
        [
            {
                "maus_id": r["maus_id"],
                "region": r["region"],
                "year": r["year"],
                "source_id": r["source_id"],
                "kind": r["kind"],
                "n_members": r["n_members"],
                "n_invalid": r["n_invalid"],
                "invalid_frac": r["invalid_frac"],
                "computable": r["computable"],
            }
            for r in detail_rows
        ]
    )
    if df.empty:
        _log({"summary": "no reads collected -- check year range / item_index coverage"})
        return

    print("--- SUMMARY ---")
    _log(
        {
            "reads_by_source_computable": {
                f"{k[0]}|computable={k[1]}": v
                for k, v in df.groupby(["source_id", "computable"]).size().to_dict().items()
            }
        }
    )
    _log(
        {
            "reads_by_source_computable_rate": (
                df.groupby("source_id")["computable"].mean().round(4).to_dict()
            )
        }
    )
    _log(
        {
            "reads_by_year_computable_rate": (
                df.groupby("year")["computable"].mean().round(4).to_dict()
            )
        }
    )

    failing = df[~df["computable"]]
    if not failing.empty:
        _log(
            {
                "failing_reads": len(failing),
                "failing_by_source": failing["source_id"].value_counts().to_dict(),
                "invalid_frac_distribution_when_failing": {
                    "median": round(float(failing["invalid_frac"].median()), 6),
                    "p90": round(float(failing["invalid_frac"].quantile(0.9)), 6),
                    "max": round(float(failing["invalid_frac"].max()), 6),
                    "min": round(float(failing["invalid_frac"].min()), 6),
                },
                "n_failing_le_2pct_invalid": int((failing["invalid_frac"] <= 0.02).sum()),
                "n_failing_gt_2pct_invalid": int((failing["invalid_frac"] > 0.02).sum()),
            }
        )

        # band-level attribution: which band/metric drives failures, per source
        band_fail_counts: Counter[str] = Counter()
        metric_zero_denom_counts: Counter[str] = Counter()
        for r in detail_rows:
            if r["computable"]:
                continue
            for band, n in r["per_band_nonfinite"].items():
                if n > 0:
                    band_fail_counts[f"{r['source_id']}:{band}"] += 1
            for metric, n in r["per_metric_zero_denominator"].items():
                if n > 0:
                    metric_zero_denom_counts[f"{r['source_id']}:{metric}"] += 1
        _log({"failing_reads_with_nonfinite_by_source_band": dict(band_fail_counts)})
        _log({"failing_reads_with_zero_denominator_by_source_metric": dict(metric_zero_denom_counts)})
    else:
        _log({"failing_reads": 0})


if __name__ == "__main__":
    main()
