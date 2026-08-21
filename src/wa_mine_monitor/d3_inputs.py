"""Deterministic reduced-support simulation inputs (D13 Batch D task D3).

Sampling is WITHOUT replacement, deterministic, nested and input-order
free: members are ranked by sha256("{seed_material}|{replicate}|{member}")
and a sample of size n is the first n of that ranking, so the n=9 sample
is always a prefix-subset of the n=100 sample for the same replicate. The
seed material is pre-registered by the caller (protocol digest + maus_id +
collection + year) and never derived from a clock or process state.

The statistical unit throughout is the footprint (maus_id); register
sites never enter this module.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import pyarrow as pa

from wa_mine_monitor import d3_protocol, dea_raster, pixel_support

if TYPE_CHECKING:
    import rasterio  # type: ignore[import-untyped]

Member = tuple[str, int, int]  # (tile_id, row, col)

#: Geomedian metric -> (numerator-plus band, numerator-minus band).
GEOMEDIAN_METRIC_BANDS: dict[str, tuple[str, str]] = {
    "nbr": ("nbart_nir", "nbart_swir_2"),
    "ndmi": ("nbart_nir", "nbart_swir_1"),
}
FC_METRIC_ASSETS: dict[str, str] = {
    "bare_soil": "bs_pc_50",
    "photosynthetic_vegetation": "pv_pc_50",
    "non_photosynthetic_vegetation": "npv_pc_50",
}
MIN_SPEARMAN_YEARS = 2


class D3InputsError(ValueError):
    """Simulation-input construction violated the frozen protocol -- refused."""


def _rank_key(member: Member, replicate: int, seed_material: str) -> str:
    token = f"{seed_material}|{replicate}|{member[0]},{member[1]},{member[2]}"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _rank_all(
    members: Sequence[Member], *, replicate: int, seed_material: str
) -> tuple[Member, ...]:
    """Every distinct member ranked by sha256(seed_material|replicate|member),
    ascending -- the full ordering `sample_support` slices a prefix of.

    The ranking depends only on `(replicate, seed_material)`, never on a
    requested support size, so a caller needing several support levels for
    the SAME replicate (`simulate_footprint_year`, sweeping the frozen
    `supports` tuple) must compute this once and slice, rather than calling
    `sample_support` once per support -- that would re-sort (and re-hash
    every member) once per support for no behavioural difference, an O(len
    (supports)) multiplier this project's fixtures make expensive at real
    pixel counts.
    """
    distinct = sorted(set(members))
    return tuple(sorted(distinct, key=lambda m: _rank_key(m, replicate, seed_material)))


def sample_support(
    members: Sequence[Member], n: int, *, replicate: int, seed_material: str
) -> tuple[Member, ...]:
    ranked = _rank_all(members, replicate=replicate, seed_material=seed_material)
    if n > len(ranked):
        raise D3InputsError(f"requested support {n} exceeds available {len(ranked)} members")
    return ranked[:n]


def geomedian_valid_mask(bands: Mapping[str, np.ndarray]) -> np.ndarray:
    """Pixel validity for geomedian metrics: every band finite AND every
    metric denominator nonzero (design decision 11)."""
    stacked = np.vstack([bands[b] for b in sorted(bands)])
    valid = np.isfinite(stacked).all(axis=0)
    for plus, minus in GEOMEDIAN_METRIC_BANDS.values():
        valid &= (bands[plus] + bands[minus]) != 0
    return valid


def fc_valid_mask(values: Mapping[str, np.ndarray]) -> np.ndarray:
    stacked = np.vstack([values[a] for a in sorted(values)])
    return np.isfinite(stacked).all(axis=0)


def geomedian_metrics(bands: Mapping[str, np.ndarray]) -> dict[str, float]:
    """Spatial mean of the per-pixel index over the given pixel arrays.
    Caller guarantees validity (geomedian_valid_mask all-True)."""
    out: dict[str, float] = {}
    for metric, (plus, minus) in GEOMEDIAN_METRIC_BANDS.items():
        numerator = bands[plus] - bands[minus]
        denominator = bands[plus] + bands[minus]
        out[metric] = float(np.mean(numerator / denominator))
    return out


def fc_metrics(values: Mapping[str, np.ndarray]) -> dict[str, float]:
    return {metric: float(np.mean(values[asset])) for metric, asset in FC_METRIC_ASSETS.items()}


def spearman(full: pd.Series, reduced: pd.Series) -> float | None:
    if len(full) < MIN_SPEARMAN_YEARS or len(full) != len(reduced):
        raise D3InputsError(
            f"spearman needs >= {MIN_SPEARMAN_YEARS} paired years, got "
            f"{len(full)} vs {len(reduced)}"
        )
    if full.nunique() < 2 or reduced.nunique() < 2:
        return None  # undefined for a constant series -- caller discloses
    return float(full.rank().corr(reduced.rank()))


def grid_spec_from_dataset(
    dataset: rasterio.DatasetReader, *, tile_id: str
) -> pixel_support.GridSpec:
    """Grid identity read from the ACTUAL raster -- D2's tile binding."""
    t = dataset.transform
    return pixel_support.GridSpec(
        crs=str(dataset.crs),
        transform=(t.a, t.b, t.c, t.d, t.e, t.f),
        width=int(dataset.width),
        height=int(dataset.height),
        tile_id=tile_id,
    )


def read_member_values(
    datasets: Mapping[str, rasterio.DatasetReader],
    members: Sequence[Member],
) -> np.ndarray:
    """Member pixel values in CANONICAL order (sorted(set(members))),
    grouped per tile: one windowed read per tile covering that tile's
    member bounding box. Refuses a member whose tile has no dataset, and
    refuses a member whose row/col falls outside that tile's dataset
    bounds -- rasterio silently clips an out-of-bounds Window instead of
    failing, which would misalign the returned array against
    (row_lo, col_lo) and read the wrong pixel."""
    from rasterio.windows import Window  # type: ignore[import-untyped]

    canonical = sorted(set(members))
    missing = {m[0] for m in canonical} - set(datasets)
    if missing:
        raise D3InputsError(f"no dataset for tile(s): {sorted(missing)}")
    out = np.empty(len(canonical), dtype=np.float64)
    by_tile: dict[str, list[int]] = {}
    for i, m in enumerate(canonical):
        by_tile.setdefault(m[0], []).append(i)
    for tile_id, positions in by_tile.items():
        dataset = datasets[tile_id]
        rows = [canonical[i][1] for i in positions]
        cols = [canonical[i][2] for i in positions]
        for i in positions:
            _, r, c = canonical[i]
            if not (0 <= r < dataset.height and 0 <= c < dataset.width):
                raise D3InputsError(
                    f"member ({tile_id}, {r}, {c}) is out of bounds for tile "
                    f"'{tile_id}' with shape (height={dataset.height}, "
                    f"width={dataset.width}) -- refusing to build a Window "
                    "that rasterio would silently clip"
                )
        row_lo, col_lo = min(rows), min(cols)
        window = Window(
            col_off=col_lo,
            row_off=row_lo,
            width=max(cols) - col_lo + 1,
            height=max(rows) - row_lo + 1,
        )
        block = datasets[tile_id].read(1, window=window)
        for i in positions:
            _, r, c = canonical[i]
            out[i] = block[r - row_lo, c - col_lo]
    return out


def _require_canonical(members: Sequence[Member]) -> tuple[Member, ...]:
    canonical = tuple(members)
    if list(canonical) != sorted(set(canonical)):
        raise D3InputsError(
            "members must be sorted and duplicate-free (canonical order); "
            "band_values arrays are positionally aligned to that order"
        )
    return canonical


def year_computable(band_values: Mapping[str, np.ndarray], *, kind: str) -> bool:
    """Phase A computability: every member pixel valid (design decision 11)."""
    mask = geomedian_valid_mask(band_values) if kind == "geomedian" else fc_valid_mask(band_values)
    return bool(mask.all())


def simulate_footprint_year(
    *,
    maus_id: str,
    year: int,
    source_id: str,
    members: Sequence[Member],
    band_values: Mapping[str, np.ndarray],
    kind: str,  # "geomedian" | "fc"
    supports: Sequence[int],
    replicates: int,
    protocol_digest: str,
) -> tuple[list[dict[str, object]], dict[tuple[str, int], list[float]]] | None:
    """Full + reduced metrics for one footprint-year-collection (Phase B).

    `members` MUST be canonical (sorted, unique) and `band_values` arrays
    MUST be positionally aligned to it -- refused otherwise, because a
    silent misalignment assigns raster values to the wrong pixels.
    Support below 144 is a caller error (refused); an invalid pixel is a
    data property (year not computable -> None).
    """
    canonical = _require_canonical(members)
    if len(canonical) < d3_protocol.MIN_FULL_SUPPORT_PX:
        raise D3InputsError(
            f"full support {len(canonical)} is below the frozen minimum "
            f"{d3_protocol.MIN_FULL_SUPPORT_PX} -- caller must not submit"
        )
    for band, values in band_values.items():
        if len(values) != len(canonical):
            raise D3InputsError(
                f"band {band} has {len(values)} values for "
                f"{len(canonical)} members -- misaligned input"
            )
    if not year_computable(band_values, kind=kind):
        return None

    metric_fn = geomedian_metrics if kind == "geomedian" else fc_metrics
    full = metric_fn(band_values)
    member_index = {m: i for i, m in enumerate(canonical)}
    seed_material = f"{protocol_digest}|{maus_id}|{source_id}|{year}"

    # One ranking per replicate, computed ONCE and reused for every support
    # level below (the ranking is independent of `n` -- see `_rank_all`).
    # `supports` is frozen at 8 values and `replicates` at 100 (D13 C1/D1),
    # so this avoids re-sorting (and re-hashing every member) 8x over.
    replicate_rankings = {
        replicate: _rank_all(canonical, replicate=replicate, seed_material=seed_material)
        for replicate in range(replicates)
    }

    rows: list[dict[str, object]] = []
    reduced_series: dict[tuple[str, int], list[float]] = {}
    for support in supports:
        if support > len(canonical):
            raise D3InputsError(
                f"requested support {support} exceeds available {len(canonical)} members"
            )
        per_metric_errors: dict[str, list[float]] = {m: [] for m in full}
        per_metric_reduced: dict[str, list[float]] = {m: [] for m in full}
        for replicate in range(replicates):
            sample = replicate_rankings[replicate][:support]
            indices = [member_index[m] for m in sample]
            reduced = metric_fn({band: values[indices] for band, values in band_values.items()})
            for metric, value in reduced.items():
                per_metric_errors[metric].append(abs(value - full[metric]))
                per_metric_reduced[metric].append(value)
        for metric, errors in per_metric_errors.items():
            rows.append(
                {
                    "maus_id": maus_id,
                    "year": year,
                    "source_id": source_id,
                    "metric_id": metric,
                    "support_px": support,
                    "full_support_px": len(canonical),
                    "valid_support_px": len(canonical),
                    "full_value": full[metric],
                    "replicate_abs_errors": sorted(errors),
                    "n_replicates": replicates,
                    "protocol_digest": protocol_digest,
                }
            )
            reduced_series[(metric, support)] = per_metric_reduced[metric]
    return rows, reduced_series


# --- build-d3-inputs orchestration (D13 Batch D task D3, build-d3-inputs CLI) ---

#: Collection identity -> "geomedian" | "fc", the two extraction kinds
#: `year_computable`/`geomedian_metrics`/`fc_metrics` branch on. Frozen: the
#: four DEA_COLLECTIONS entries never grow or shrink independently of this.
D3_COLLECTION_KIND: dict[str, str] = {
    "dea_gm_ls5t": "geomedian",
    "dea_gm_ls7e": "geomedian",
    "dea_gm_ls8cls9c": "geomedian",
    "dea_fc_pc": "fc",
}

#: Shared by every `build-d3-inputs` output table -- the canonical-JSON
#: digest bundle of every source manifest a row was built from, IDENTICAL
#: on every row of every table (so a downstream reader can prove which run
#: of the seven upstream artefacts a row came from without re-reading five
#: separate manifests).
_INPUT_DIGEST_FIELD = pa.field("input_manifest_digests", pa.string(), nullable=False)

#: Per (footprint, year, collection, metric, support): the full-support
#: value, the reduced-support replicate errors (sorted, decision 6), and
#: enough stratum/protocol context to read the table standalone.
D3_SUPPORT_INPUTS_SCHEMA = pa.schema(
    [
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("region", pa.string(), nullable=False),
        pa.field("commodity_group", pa.string(), nullable=False),
        pa.field("shape_class", pa.string(), nullable=False),
        pa.field("year", pa.int64(), nullable=False),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("metric_id", pa.string(), nullable=False),
        pa.field("support_px", pa.int64(), nullable=False),
        pa.field("full_support_px", pa.int64(), nullable=False),
        pa.field("valid_support_px", pa.int64(), nullable=False),
        pa.field("full_value", pa.float64(), nullable=False),
        pa.field("replicate_abs_errors", pa.list_(pa.float64()), nullable=False),
        pa.field("n_replicates", pa.int64(), nullable=False),
        pa.field("protocol_digest", pa.string(), nullable=False),
        _INPUT_DIGEST_FIELD,
    ]
)

#: Per (footprint, collection, metric, support, replicate): one Spearman
#: rank correlation between the full-support and reduced-support metric
#: series across that footprint's own computable years.
D3_SPEARMAN_SCHEMA = pa.schema(
    [
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("metric_id", pa.string(), nullable=False),
        pa.field("support_px", pa.int64(), nullable=False),
        pa.field("replicate", pa.int64(), nullable=False),
        pa.field("spearman", pa.float64(), nullable=False),
        pa.field("n_years", pa.int64(), nullable=False),
        pa.field("protocol_digest", pa.string(), nullable=False),
        _INPUT_DIGEST_FIELD,
    ]
)

#: One row per `maus_id` in the Tier 1 population -- strata, computed pixel
#: support, epoch/full-support year counts, and candidate/selected flags.
D3_FOOTPRINT_SUPPORT_SCHEMA = pa.schema(
    [
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("region", pa.string(), nullable=True),
        pa.field("commodity_group", pa.string(), nullable=True),
        pa.field("shape_class", pa.string(), nullable=True),
        pa.field("effective_pixel_support_px", pa.int64(), nullable=True),
        pa.field("support_not_computed_reason", pa.string(), nullable=True),
        pa.field("n_epoch_covered_years", pa.int64(), nullable=False),
        pa.field("n_full_support_years", pa.int64(), nullable=False),
        pa.field("candidate", pa.bool_(), nullable=False),
        pa.field("selected", pa.bool_(), nullable=False),
        pa.field("protocol_digest", pa.string(), nullable=False),
        _INPUT_DIGEST_FIELD,
    ]
)

#: The full frozen 54-stratum space (3 regions x 6 commodity groups x 3
#: shape classes), zero-count strata included.
D3_STRATUM_SUMMARY_SCHEMA = pa.schema(
    [
        pa.field("region", pa.string(), nullable=False),
        pa.field("commodity_group", pa.string(), nullable=False),
        pa.field("shape_class", pa.string(), nullable=False),
        pa.field("n_footprints", pa.int64(), nullable=False),
        pa.field("n_adequate_footprints", pa.int64(), nullable=False),
        pa.field("adequate", pa.bool_(), nullable=False),
        pa.field("n_selected", pa.int64(), nullable=False),
        pa.field("protocol_digest", pa.string(), nullable=False),
        _INPUT_DIGEST_FIELD,
    ]
)

#: One row per asset actually opened (Phase A or Phase B), disclosing the
#: HTTP identity captured at read time -- null/null for a local-file href
#: (no HTTP layer touched it), never a fabricated "unchanged" sentinel.
D3_EXTRACTION_ASSETS_SCHEMA = pa.schema(
    [
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("tile_id", pa.string(), nullable=False),
        pa.field("year", pa.int64(), nullable=False),
        pa.field("asset_key", pa.string(), nullable=False),
        pa.field("href", pa.string(), nullable=False),
        pa.field("etag", pa.string(), nullable=True),
        pa.field("last_modified", pa.string(), nullable=True),
        pa.field("phase", pa.string(), nullable=False),
    ]
)


def canonical_input_digests(**digests: str) -> str:
    """Canonical-JSON form of the `input_manifest_digests` field: sorted
    keys, no whitespace, so byte-identical dicts always serialise
    byte-identically across every row of every table."""
    return json.dumps(digests, sort_keys=True, separators=(",", ":"))


def check_procedures_consistency(procedures: Mapping[str, str]) -> None:
    """Refuse (naming 'drift') when the frozen protocol's `procedures` text
    no longer names the asset keys, seed template or decode constants this
    module and `dea_raster` actually implement.

    The protocol digest alone only proves the YAML has not changed since
    freezing -- it says nothing about whether the CODE that consumes it
    still agrees with what the YAML documents. This is the second half of
    that guarantee: a code change that silently redefines
    `GEOMEDIAN_METRIC_BANDS`/`FC_METRIC_ASSETS`/`dea_raster`'s decode
    constants, or the seed-material template `simulate_footprint_year`
    builds, without updating the frozen procedures prose, is caught here
    rather than producing simulation rows that no longer match what the
    frozen protocol says they should.
    """
    item_selection = str(procedures.get("item_selection", ""))
    band_keys = sorted(
        {band for pair in GEOMEDIAN_METRIC_BANDS.values() for band in pair}
    ) + sorted(FC_METRIC_ASSETS.values())
    missing_bands = [key for key in band_keys if key not in item_selection]
    if missing_bands:
        raise D3InputsError(
            f"protocol drift: procedures.item_selection no longer names frozen "
            f"asset key(s) {missing_bands} -- GEOMEDIAN_METRIC_BANDS/"
            f"FC_METRIC_ASSETS and the frozen protocol text have diverged"
        )

    seed_template = str(procedures.get("seed_template", ""))
    expected_seed_pattern = "{protocol_digest}|{maus_id}|{source_id}|{year}"
    if expected_seed_pattern not in seed_template:
        raise D3InputsError(
            f"protocol drift: procedures.seed_template no longer names the "
            f"pattern {expected_seed_pattern!r} that simulate_footprint_year "
            f"actually builds its seed material from"
        )

    decode_rules = str(procedures.get("decode_rules", ""))
    decode_tokens = [
        str(dea_raster.GEOMEDIAN_NODATA),
        str(int(dea_raster.GEOMEDIAN_SCALE)),
        str(dea_raster.FC_NODATA),
        str(int(dea_raster.FC_DOCUMENTED_MAX)),
    ]
    missing_decode = [token for token in decode_tokens if token not in decode_rules]
    if missing_decode:
        raise D3InputsError(
            f"protocol drift: procedures.decode_rules no longer names decode "
            f"constant(s) {missing_decode} that dea_raster.py actually implements"
        )


def assign_footprint_commodities(
    maus_ids: Sequence[str],
    tier1_links: pd.DataFrame,
    register_df: pd.DataFrame,
    protocol: d3_protocol.D3Protocol,
) -> tuple[dict[str, str], dict[str, int]]:
    """Modal commodity group per footprint over its linked high-confidence
    sites' `d3_protocol.classify_commodity` results (design decision 9/10).

    `tier1_links` carries `site_id`/`maus_id` (a `tier1_population`
    crosswalk, or any frame with those two columns); `register_df` carries
    `site_id`/`commodity`. A tie resolves to the lexicographically smallest
    group name; the number of footprints where a tie occurred is disclosed
    under `n_footprints_with_ties` rather than left for a reader to
    re-derive from the per-footprint groups alone.
    """
    commodity_by_site = register_df.set_index("site_id")["commodity"]
    groups: dict[str, str] = {}
    n_ties = 0
    for maus_id in maus_ids:
        site_ids = sorted(set(tier1_links.loc[tier1_links["maus_id"] == maus_id, "site_id"]))
        classified = [
            d3_protocol.classify_commodity(commodity_by_site.get(site_id), protocol)
            for site_id in site_ids
        ]
        counts = Counter(classified)
        top = max(counts.values())
        tied = sorted(group for group, count in counts.items() if count == top)
        if len(tied) > 1:
            n_ties += 1
        groups[maus_id] = tied[0]
    return groups, {"n_footprints_with_ties": n_ties}


def footprint_compactness(geometry: Any) -> float:
    """Polsby-Popper compactness (4*pi*A/P^2) from a `TARGET_CRS` polygon."""
    perimeter = float(geometry.length)
    if perimeter <= 0.0:
        raise D3InputsError("footprint geometry has a zero perimeter -- compactness undefined")
    return float(4.0 * math.pi * float(geometry.area) / (perimeter**2))


def select_catalogue_items(
    items_by_source: Mapping[str, Sequence[Mapping[str, Any]]],
    tile_ids: Sequence[str],
) -> dict[tuple[str, str, int], Mapping[str, Any]]:
    """Item-selection rule (frozen in `procedures.item_selection`): exactly
    one item per (collection, tile, year), restricted to `tile_ids` (the
    tiles at least one footprint's pixel support actually intersects --
    an item on a tile no footprint touches is not this run's concern).

    Refuses (naming both item ids) the first duplicate found for the same
    key. A tile in `tile_ids` with zero items for some (collection, year)
    simply has no entry at that key -- "not epoch-covered", not an error.
    """
    selected: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    tile_id_set = set(tile_ids)
    for source_id, items in items_by_source.items():
        for item in items:
            properties = item.get("properties") or {}
            tile_id = str(properties.get("odc:region_code") or "")
            if tile_id not in tile_id_set:
                continue
            stamp = str(properties.get("datetime") or "")
            if len(stamp) < 4 or not stamp[:4].isdigit():
                raise D3InputsError(
                    f"{source_id}: item {item.get('id')!r} has no parseable "
                    f"properties.datetime year"
                )
            year = int(stamp[:4])
            key = (source_id, tile_id, year)
            if key in selected:
                raise D3InputsError(
                    f"duplicate item for (collection={source_id!r}, "
                    f"tile={tile_id!r}, year={year}): "
                    f"{selected[key].get('id')!r} and {item.get('id')!r}"
                )
            selected[key] = item
    return selected


def resolve_band_hrefs(item: Mapping[str, Any], *, kind: str) -> dict[str, str]:
    """The frozen band-asset hrefs an item must carry for `kind`, or refuse
    naming every missing key."""
    band_keys = (
        sorted({band for pair in GEOMEDIAN_METRIC_BANDS.values() for band in pair})
        if kind == "geomedian"
        else sorted(FC_METRIC_ASSETS.values())
    )
    assets = item.get("assets") or {}
    hrefs: dict[str, str] = {}
    missing: list[str] = []
    for key in band_keys:
        asset = assets.get(key)
        href = (asset or {}).get("href")
        if not href:
            missing.append(key)
        else:
            hrefs[key] = str(href)
    if missing:
        raise D3InputsError(f"item {item.get('id')!r} missing required band asset(s) {missing}")
    return hrefs
