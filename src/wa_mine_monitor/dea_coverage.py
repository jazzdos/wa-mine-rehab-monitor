"""Per-site DEA epoch coverage from a captured STAC catalogue snapshot.

An EPOCH is a distinct calendar year with at least one intersecting item in
a collection: multiple tiles covering one site in one year are ONE epoch
(the site sits on a tile boundary, not in two years). Coverage counts use
the register's internal MINEDEX point for the Tier 0 coverage DIAGNOSTIC
only -- a point-in-bbox test never defines or substitutes a Tier 1
footprint (D13 C3 acceptance).

Null vs zero follows the register's ``n_tenements_intersecting`` semantic
(D12.2): a coordinate-less site gets NULL (not computable), a located site
with no intersecting item gets a GENUINE ZERO.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from wa_mine_monitor.source_catalogue import spec_for_source

#: Column order of the item index frame. D13 C3 requires collection, item
#: ID, year, geometry, ASSET IDENTITY, product version and tile identity:
#: `source_id` is the licence table's key for a collection, not the
#: collection's own id, so both are carried.
ITEM_INDEX_COLUMNS: tuple[str, ...] = (
    "source_id",
    "collection_id",
    "item_id",
    "year",
    "bbox_west",
    "bbox_south",
    "bbox_east",
    "bbox_north",
    "tile_id",
    "product_version",
    "asset_identity",
)

#: source_id -> enriched-register column (D13 C3 field names, exact).
DEA_EPOCH_COLUMN_BY_SOURCE: dict[str, str] = {
    "dea_gm_ls5t": "n_dea_gm_ls5t_epochs",
    "dea_gm_ls7e": "n_dea_gm_ls7e_epochs",
    "dea_gm_ls8cls9c": "n_dea_gm_ls8cls9c_epochs",
    "dea_fc_pc": "n_dea_fc_pc_epochs",
}

#: Fixed keys of the per-collection coverage disclosure -- three counts plus
#: the item-reconciliation pair, never a boolean (the
#: `tenement_count_disclosure` discipline).
COVERAGE_DISCLOSURE_KEYS: tuple[str, ...] = (
    "n_sites_coverage_computed",
    "n_sites_coverage_zero",
    "n_sites_coverage_not_computed",
    "n_distinct_items",
    "n_duplicate_items_refused",
)

#: Column order of the asset index. Every metadata field is NULLABLE: DEA
#: STAC items do not uniformly carry `file:size`, `proj:shape` or
#: `raster:bands`, and a field this module invented would be indistinguishable
#: from one the source published.
ASSET_INDEX_COLUMNS: tuple[str, ...] = (
    "source_id",
    "collection_id",
    "item_id",
    "asset_key",
    "file_size_bytes",
    "raster_width_px",
    "raster_height_px",
    "block_width_px",
    "block_height_px",
    "data_type",
    "bytes_per_sample",
    "metadata_source",
)

#: Fixed keys of the per-collection asset-metadata disclosure.
ASSET_METADATA_DISCLOSURE_KEYS: tuple[str, ...] = (
    "n_assets",
    "n_assets_file_size_missing",
    "n_assets_block_size_missing",
    "n_assets_raster_shape_missing",
    "n_assets_data_type_missing",
)

#: DECLARED dtype widths. A dtype outside this table leaves
#: `bytes_per_sample` NULL rather than guessing a width.
_BYTES_PER_SAMPLE: dict[str, int] = {
    "uint8": 1,
    "int8": 1,
    "uint16": 2,
    "int16": 2,
    "uint32": 4,
    "int32": 4,
    "float32": 4,
    "uint64": 8,
    "int64": 8,
    "float64": 8,
}


def build_item_index(
    items_by_source: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Flatten captured STAC items into one indexable frame.

    Returns ``(index, duplicates_refused)`` where ``duplicates_refused``
    counts, per source, items dropped because their id was already seen --
    reported (never silent) and carried into the coverage disclosure. C2's
    fetch already refuses duplicates at capture; this second count exists
    so an index built from any OTHER item source inherits the guard.
    """
    rows: list[dict[str, Any]] = []
    duplicates_refused: dict[str, int] = {}
    for source_id, items in items_by_source.items():
        # KeyError on an unpinned source: an index row whose collection
        # cannot be named is a coverage claim with no provenance.
        collection_id = spec_for_source(source_id).collection_id
        seen: set[str] = set()
        duplicates_refused[source_id] = 0
        for item in items:
            item_id = str(item.get("id"))
            if item_id in seen:
                duplicates_refused[source_id] += 1
                continue
            seen.add(item_id)
            bbox = item.get("bbox")
            if not bbox or len(bbox) != 4:
                raise ValueError(
                    f"{source_id}: item {item_id} has no usable bbox -- a "
                    f"skipped item is invisible coverage loss, so this refuses"
                )
            properties = item.get("properties") or {}
            stamp = str(properties.get("datetime") or "")
            if len(stamp) < 4 or not stamp[:4].isdigit():
                raise ValueError(f"{source_id}: item {item_id} has no parseable datetime year")
            assets = item.get("assets") or {}
            if not assets:
                raise ValueError(
                    f"{source_id}: item {item_id} carries no assets -- asset "
                    f"identity is a declared index field (D13 C3), so an "
                    f"assetless item is refused rather than indexed blank"
                )
            rows.append(
                {
                    "source_id": source_id,
                    "collection_id": collection_id,
                    "item_id": item_id,
                    "year": int(stamp[:4]),
                    "bbox_west": float(bbox[0]),
                    "bbox_south": float(bbox[1]),
                    "bbox_east": float(bbox[2]),
                    "bbox_north": float(bbox[3]),
                    "tile_id": str(properties.get("odc:region_code") or ""),
                    "product_version": str(properties.get("odc:dataset_version") or ""),
                    # Asset identity: the item's sorted asset keys, joined.
                    # Readable and directly comparable to a spec's
                    # `asset_roles`, so a mid-series asset-set change shows up
                    # as a value difference rather than an opaque digest.
                    "asset_identity": "|".join(sorted(assets)),
                }
            )
    index = pd.DataFrame(rows, columns=list(ITEM_INDEX_COLUMNS))
    return index, duplicates_refused


def build_asset_index(
    items_by_source: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    """Normalise captured item ASSET metadata into one nullable-typed frame.

    Returns ``(asset_index, disclosures)``. Nothing is defaulted: a missing
    block size stays null and is counted, so a downstream range-request
    figure is either derived from published metadata or absent -- never a
    plausible-looking constant (the amendment's 1c requirement).
    """
    rows: list[dict[str, Any]] = []
    disclosures: dict[str, dict[str, int]] = {}
    for source_id, items in items_by_source.items():
        collection_id = spec_for_source(source_id).collection_id
        counts = dict.fromkeys(ASSET_METADATA_DISCLOSURE_KEYS, 0)
        for item in items:
            item_id = str(item.get("id"))
            for asset_key, asset in (item.get("assets") or {}).items():
                asset = asset or {}
                file_size = asset.get("file:size")
                shape = asset.get("proj:shape") or [None, None]
                bands = asset.get("raster:bands") or [{}]
                band = bands[0] if bands else {}
                block = band.get("block_size") or [None, None]
                data_type = band.get("data_type")
                counts["n_assets"] += 1
                counts["n_assets_file_size_missing"] += int(file_size is None)
                counts["n_assets_block_size_missing"] += int(block[0] is None)
                counts["n_assets_raster_shape_missing"] += int(shape[0] is None)
                counts["n_assets_data_type_missing"] += int(data_type is None)
                observed = any(
                    value is not None for value in (file_size, shape[0], block[0], data_type)
                )
                rows.append(
                    {
                        "source_id": source_id,
                        "collection_id": collection_id,
                        "item_id": item_id,
                        "asset_key": str(asset_key),
                        "file_size_bytes": file_size,
                        # `proj:shape` is [height, width].
                        "raster_height_px": shape[0],
                        "raster_width_px": shape[1] if len(shape) > 1 else None,
                        "block_height_px": block[0],
                        "block_width_px": block[1] if len(block) > 1 else None,
                        "data_type": data_type,
                        "bytes_per_sample": _BYTES_PER_SAMPLE.get(str(data_type)),
                        "metadata_source": "stac-item-asset" if observed else "absent",
                    }
                )
        disclosures[source_id] = counts
    index = pd.DataFrame(rows, columns=list(ASSET_INDEX_COLUMNS))
    for column in (
        "file_size_bytes",
        "raster_width_px",
        "raster_height_px",
        "block_width_px",
        "block_height_px",
        "bytes_per_sample",
    ):
        index[column] = pd.array(index[column], dtype="Int64")
    return index, disclosures


def count_site_epochs(
    register: pd.DataFrame,
    item_index: pd.DataFrame,
    *,
    duplicates_refused: Mapping[str, int],
) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    """Count distinct intersecting item-years per site per collection.

    Returns ``(coverage, disclosures)``: coverage has ``site_id`` plus the
    four nullable Int64 epoch columns in `DEA_EPOCH_COLUMN_BY_SOURCE` order;
    disclosures maps source_id -> the fixed-key count dict. A source with
    no rows in ``item_index`` still gets a column (zeros for located sites)
    and a disclosure -- an unfetched collection must read as zero-item, not
    silently absent, so the reconciliation check can see it.
    """
    located = register["lon"].notna() & register["lat"].notna()
    coverage = pd.DataFrame({"site_id": register["site_id"].to_numpy()})
    disclosures: dict[str, dict[str, int]] = {}

    for source_id, column in DEA_EPOCH_COLUMN_BY_SOURCE.items():
        subset = item_index[item_index["source_id"] == source_id]
        counts = pd.array([pd.NA] * len(register), dtype="Int64")
        n_zero = 0
        for position in range(len(register)):
            if not bool(located.iloc[position]):
                continue
            lon = float(register["lon"].iloc[position])
            lat = float(register["lat"].iloc[position])
            if len(subset):
                hits = (
                    (subset["bbox_west"] <= lon)
                    & (lon <= subset["bbox_east"])
                    & (subset["bbox_south"] <= lat)
                    & (lat <= subset["bbox_north"])
                )
                n_epochs = int(subset.loc[hits, "year"].nunique())
            else:
                n_epochs = 0
            counts[position] = n_epochs
            if n_epochs == 0:
                n_zero += 1
        coverage[column] = counts
        disclosures[source_id] = {
            "n_sites_coverage_computed": int(located.sum()),
            "n_sites_coverage_zero": n_zero,
            "n_sites_coverage_not_computed": int((~located).sum()),
            "n_distinct_items": len(subset),
            "n_duplicate_items_refused": int(duplicates_refused.get(source_id, 0)),
        }
    return coverage, disclosures
