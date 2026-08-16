# src/wa_mine_monitor/dea_volume.py
"""Tier 1 data-volume estimate from real populations (D13 Batch C task C5).

Replaces the provisional full-WA figures (367 tiles / 350 GB / 2.3 TB) with
an estimate derived from the ACTUAL high-confidence crosswalk population,
per-site Maus footprint SCALARS, the enriched register's per-collection
epoch counts, the captured STAC item index and the captured asset metadata.
The provisional figures are carried in the output as COMPARISON FIELDS ONLY
-- never computational constants (a figure inherited from an earlier
document and never re-derived is not established by being repeated).

Three disciplines shape the interface:

1. **Every constant is a declared INPUT.** `WindowPolicy`,
   `CollectionSelection` and `YearRange` arrive from the caller and are
   echoed verbatim into the output, so the estimate is recomputable from its
   own record. The old fixed 2,010 m window survives only as
   `WindowPolicy.minimum_side_px` -- a FLOOR, not the answer.
2. **Sizing comes from the footprint, not the point.** A site's window is
   sized from its Maus bounding-box span; `sqrt(area)` is not used, because
   it under-covers exactly the long, narrow footprints that matter most.
   The MINEDEX point remains a Tier 0 diagnostic only (D13 C3 acceptance).
3. **Nothing is silently assumed.** Bytes-per-pixel prefers OBSERVED
   `bytes_per_sample` from the asset index and falls back to the caller's
   declared assumption WITH a `bytes_per_pixel_source` label. Range requests
   are computed only from observed block sizes; where those are absent the
   figure is `None` and the absence is counted.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

#: The Tier 1 population rule lives in `crosswalk.tier1_population`
#: (`confidence == "high"`, design doc D1); it is imported rather than
#: restated so the two can never drift.
from wa_mine_monitor.crosswalk import tier1_population
from wa_mine_monitor.source_catalogue import spec_for_source


@dataclass(frozen=True)
class WindowPolicy:
    """How a footprint becomes a read window.

    ``minimum_side_px`` (67 px = 2,010 m at 30 m) is the FLOOR the old
    fixed-window constant became: below it, reads are dominated by
    per-request overhead rather than pixels. ``reference_buffer_metres``
    is added on EACH side so the window carries surrounding reference
    ground, not just the disturbance. ``alignment_pad_px`` covers arbitrary
    raster-grid alignment -- a window that fits exactly still straddles one
    extra pixel when it lands off-grid.
    """

    pixel_metres: int = 30
    minimum_side_px: int = 67
    reference_buffer_metres: int = 300
    alignment_pad_px: int = 1


@dataclass(frozen=True)
class CollectionSelection:
    """What this estimate intends to READ from one collection.

    ``asset_keys`` must be a non-empty, duplicate-free subset of the pinned
    `SourceSpec.asset_roles`: the spec says what every item must CARRY, this
    says what the run intends to FETCH, and conflating the two is how a band
    count becomes a hard-coded 10.
    """

    source_id: str
    metric_ids: tuple[str, ...]
    asset_keys: tuple[str, ...]
    assumed_bytes_per_pixel: int
    assumed_tile_pixels_per_side: int


@dataclass(frozen=True)
class YearRange:
    first_year: int
    last_year: int


#: Carried for comparison ONLY -- see the module docstring.
PROVISIONAL_FIGURES: dict[str, int] = {
    "provisional_n_tiles": 367,
    "provisional_bytes_estimate": 350 * 10**9,
    "provisional_bytes_upper_bound": int(2.3 * 10**12),
}

#: The one remaining declared scalar the inputs cannot supply.
VOLUME_ASSUMPTIONS: dict[str, Any] = {
    # Deflate on real-valued COGs. Declared, not measured -- and applied to
    # the windowed estimate only, never to the upper bound (whose whole
    # point is to be conservative).
    "compression_ratio": 0.6,
}

_FORMULAS: dict[str, str] = {
    "window_side_px": (
        "max(minimum_side_px, ceil((max(bbox_width_m, bbox_height_m) + "
        "2 * reference_buffer_metres) / pixel_metres) + alignment_pad_px)"
    ),
    "windowed_read_bytes_estimate": (
        "sum over collections of: sum over eligible sites of "
        "(window_side_px^2 * site_epochs * n_assets_selected * "
        "bytes_per_pixel), times compression_ratio"
    ),
    "upper_bound_bytes": (
        "sum over collections of: n_distinct_tile_years(collection) * "
        "tile_pixels_per_side^2 * n_assets_selected(collection) * "
        "bytes_per_pixel(collection) -- every distinct tile-year fetched "
        "WHOLE and uncompressed, priced at ITS OWN collection's rates; "
        "shared tiles counted once"
    ),
    "expected_range_requests": (
        "sum over collections of: sum over eligible sites of "
        "(ceil(window_side_px / block_width_px) * "
        "ceil(window_side_px / block_height_px) * site_epochs * "
        "n_assets_selected) -- null when block metadata is absent"
    ),
    "scratch_space_bytes": "upper_bound_bytes (worst case: whole tiles staged)",
}


class VolumePopulationError(ValueError):
    """The eligible population failed reconciliation against its inputs."""


def _epoch_column(source_id: str) -> str:
    from wa_mine_monitor.dea_coverage import DEA_EPOCH_COLUMN_BY_SOURCE

    return DEA_EPOCH_COLUMN_BY_SOURCE[source_id]


def _validate_selections(selections: Sequence[CollectionSelection]) -> None:
    for selection in selections:
        spec = spec_for_source(selection.source_id)
        if not selection.asset_keys:
            raise ValueError(f"{selection.source_id}: no assets selected")
        if len(set(selection.asset_keys)) != len(selection.asset_keys):
            raise ValueError(f"{selection.source_id}: duplicate selected asset key(s)")
        unknown = [key for key in selection.asset_keys if key not in spec.asset_roles]
        if unknown:
            raise ValueError(
                f"{selection.source_id}: selected asset(s) {unknown} are not in the "
                f"pinned spec's asset_roles -- the selection names bands the "
                f"catalogue never promised"
            )


def _window_for(span_metres: float, policy: WindowPolicy) -> tuple[int, str]:
    footprint_px = (
        math.ceil((span_metres + 2 * policy.reference_buffer_metres) / policy.pixel_metres)
        + policy.alignment_pad_px
    )
    if footprint_px <= policy.minimum_side_px:
        return policy.minimum_side_px, "floor"
    return footprint_px, "footprint"


def derive_volume_estimate(
    *,
    crosswalk_df: pd.DataFrame,
    register_df: pd.DataFrame,
    footprints_df: pd.DataFrame,
    item_index: pd.DataFrame,
    asset_index: pd.DataFrame,
    selections: Sequence[CollectionSelection],
    year_ranges: Mapping[str, YearRange],
    window_policy: WindowPolicy,
) -> dict[str, Any]:
    """Derive the Tier 1 volume estimate; every count reconciles or refuses.

    Eligible population: `crosswalk.tier1_population`'s distinct site_ids.
    Every eligible site must exist in the enriched register AND carry at
    least one footprint (refusal otherwise). Eligible sites whose coverage is
    NULL are excluded WITH A COUNT -- null never becomes zero.
    """
    _validate_selections(selections)

    high = tier1_population(crosswalk_df)
    eligible_sites = sorted(set(high["site_id"]))
    all_sites = set(register_df["site_id"])
    missing = [site for site in eligible_sites if site not in all_sites]
    if missing:
        raise VolumePopulationError(
            f"{len(missing)} eligible site(s) absent from the enriched register "
            f"(first: {missing[0]}) -- population fails reconciliation"
        )
    without_footprint = [
        site for site in eligible_sites if site not in set(footprints_df["site_id"])
    ]
    if without_footprint:
        raise VolumePopulationError(
            f"{len(without_footprint)} eligible site(s) carry no Maus footprint "
            f"(first: {without_footprint[0]}) -- refusing rather than sizing them "
            f"at the floor window, which would understate the read silently"
        )

    unmatched = sorted(all_sites - set(eligible_sites))
    eligible = register_df[register_df["site_id"].isin(eligible_sites)]
    epoch_columns = [_epoch_column(s.source_id) for s in selections]
    coverage_null = eligible[epoch_columns].isna().any(axis=1)
    computable = eligible[~coverage_null]

    # -- window per site: the LARGEST span across that site's footprints,
    #    so a site linked to two footprints is sized to cover both.
    windows: dict[str, dict[str, Any]] = {}
    for site_id in sorted(set(computable["site_id"])):
        links = footprints_df[footprints_df["site_id"] == site_id]
        span = float(links[["footprint_bbox_width_m", "footprint_bbox_height_m"]].to_numpy().max())
        side_px, sizing = _window_for(span, window_policy)
        windows[site_id] = {
            "window_side_px": side_px,
            "window_side_m": side_px * window_policy.pixel_metres,
            "window_sizing": sizing,
            "footprint_span_m": span,
        }

    # -- bytes-per-pixel and block sizes: OBSERVED where the captured items
    #    published them, the caller's declared assumption otherwise, always
    #    labelled.
    selection_records: dict[str, dict[str, Any]] = {}
    for selection in selections:
        observed = asset_index[
            (asset_index["source_id"] == selection.source_id)
            & (asset_index["asset_key"].isin(selection.asset_keys))
        ]
        observed_bpp = observed["bytes_per_sample"].dropna()
        observed_block_w = observed["block_width_px"].dropna()
        observed_block_h = observed["block_height_px"].dropna()
        observed_shape = observed["raster_width_px"].dropna()
        selection_records[selection.source_id] = {
            "collection_id": spec_for_source(selection.source_id).collection_id,
            "metric_ids": list(selection.metric_ids),
            "asset_keys": list(selection.asset_keys),
            "n_assets_selected": len(selection.asset_keys),
            "bytes_per_pixel": int(observed_bpp.max())
            if len(observed_bpp)
            else selection.assumed_bytes_per_pixel,
            "bytes_per_pixel_source": "observed" if len(observed_bpp) else "assumed",
            "tile_pixels_per_side": int(observed_shape.max())
            if len(observed_shape)
            else selection.assumed_tile_pixels_per_side,
            "tile_pixels_per_side_source": "observed" if len(observed_shape) else "assumed",
            "block_width_px": int(observed_block_w.max()) if len(observed_block_w) else None,
            "block_height_px": int(observed_block_h.max()) if len(observed_block_h) else None,
        }

    # -- windowed read bytes, per collection.
    per_collection_epochs: dict[str, int] = {}
    windowed_by_collection: dict[str, int] = {}
    raw_by_collection: dict[str, int] = {}
    range_requests: int | None = 0
    for selection in selections:
        record = selection_records[selection.source_id]
        column = _epoch_column(selection.source_id)
        raw = 0
        epochs_total = 0
        requests = 0
        for site_id, window in windows.items():
            site_epochs = int(computable.loc[computable["site_id"] == site_id, column].iloc[0])
            epochs_total += site_epochs
            pixels = window["window_side_px"] ** 2
            raw += pixels * site_epochs * record["n_assets_selected"] * record["bytes_per_pixel"]
            if record["block_width_px"] and record["block_height_px"]:
                requests += (
                    math.ceil(window["window_side_px"] / record["block_width_px"])
                    * math.ceil(window["window_side_px"] / record["block_height_px"])
                    * site_epochs
                    * record["n_assets_selected"]
                )
        per_collection_epochs[selection.source_id] = epochs_total
        raw_by_collection[selection.source_id] = raw
        windowed_by_collection[selection.source_id] = int(
            raw * VOLUME_ASSUMPTIONS["compression_ratio"]
        )
        if epochs_total > 0 and (
            record["block_width_px"] is None or record["block_height_px"] is None
        ):
            # Block metadata absent for a collection that actually has
            # site-epochs to read -- the TOTAL becomes null rather than a
            # partial sum wearing a complete label. A selected collection
            # with ZERO epochs contributes no requests either way, so its
            # absent block metadata must not null out other collections'
            # real counts.
            range_requests = None
        elif range_requests is not None:
            range_requests += requests

    # -- distinct tiles / tile-years intersecting eligible sites, within the
    #    declared year ranges, PER COLLECTION.
    located = computable[computable["lon"].notna() & computable["lat"].notna()]
    tiles: set[str] = set()
    tile_years_by_collection: dict[str, set[tuple[str, int]]] = {
        selection.source_id: set() for selection in selections
    }
    year_range_disclosure: dict[str, dict[str, int]] = {
        selection.source_id: {"n_item_years_outside_range": 0} for selection in selections
    }
    for _, item in item_index.iterrows():
        source_id = str(item["source_id"])
        if source_id not in tile_years_by_collection:
            continue
        hits = (
            (located["lon"] >= item["bbox_west"])
            & (located["lon"] <= item["bbox_east"])
            & (located["lat"] >= item["bbox_south"])
            & (located["lat"] <= item["bbox_north"])
        )
        if not bool(hits.any()):
            continue
        year = int(item["year"])
        year_window = year_ranges.get(source_id)
        if year_window is not None and not (
            year_window.first_year <= year <= year_window.last_year
        ):
            year_range_disclosure[source_id]["n_item_years_outside_range"] += 1
            continue
        tiles.add(str(item["tile_id"]))
        tile_years_by_collection[source_id].add((str(item["tile_id"]), year))

    upper_by_collection: dict[str, int] = {}
    for selection in selections:
        record = selection_records[selection.source_id]
        upper_by_collection[selection.source_id] = int(
            len(tile_years_by_collection[selection.source_id])
            * record["tile_pixels_per_side"] ** 2
            * record["n_assets_selected"]
            * record["bytes_per_pixel"]
        )

    asset_disclosure = {
        "n_assets": len(asset_index),
        "n_assets_block_size_missing": int(asset_index["block_width_px"].isna().sum()),
        "n_assets_file_size_missing": int(asset_index["file_size_bytes"].isna().sum()),
        "n_assets_data_type_missing": int(asset_index["bytes_per_sample"].isna().sum()),
    }
    upper_bound = sum(upper_by_collection.values())

    return {
        "population": {
            "n_sites_eligible": len(eligible_sites),
            "n_sites_unmatched": len(unmatched),
            "n_eligible_sites_coverage_not_computed": int(coverage_null.sum()),
            "n_distinct_footprints": int(
                footprints_df[footprints_df["site_id"].isin(eligible_sites)]["maus_id"].nunique()
            ),
        },
        "windows": {
            "by_site": windows,
            "n_sites_at_floor": sum(1 for w in windows.values() if w["window_sizing"] == "floor"),
        },
        "tiles": {
            "n_distinct_tiles": len(tiles),
            "n_distinct_tile_years_by_collection": {
                source_id: len(pairs) for source_id, pairs in tile_years_by_collection.items()
            },
        },
        "site_year_windows": {"per_collection": per_collection_epochs},
        "selections": selection_records,
        "year_ranges": {
            source_id: [window.first_year, window.last_year]
            for source_id, window in year_ranges.items()
        },
        "year_range_disclosure": year_range_disclosure,
        "window_policy": {
            "pixel_metres": window_policy.pixel_metres,
            "minimum_side_px": window_policy.minimum_side_px,
            "reference_buffer_metres": window_policy.reference_buffer_metres,
            "alignment_pad_px": window_policy.alignment_pad_px,
        },
        "bytes": {
            # Compression applied ONCE to the combined raw total, not summed
            # from per-collection truncated ints -- summing independently
            # rounded collection totals can differ from rounding the whole
            # by up to a few bytes, and the estimate must reproduce exactly
            # from its own formula (see `formulas`).
            "windowed_read_bytes_estimate": int(
                sum(raw_by_collection.values()) * VOLUME_ASSUMPTIONS["compression_ratio"]
            ),
            "windowed_read_bytes_by_collection": windowed_by_collection,
            "upper_bound_bytes": upper_bound,
            "upper_bound_bytes_by_collection": upper_by_collection,
            "scratch_space_bytes": upper_bound,
        },
        "expected_range_requests": range_requests,
        "asset_metadata_disclosure": asset_disclosure,
        "assumptions": dict(VOLUME_ASSUMPTIONS),
        "formulas": dict(_FORMULAS),
        "provisional_figures_comparison_only": dict(PROVISIONAL_FIGURES),
    }
