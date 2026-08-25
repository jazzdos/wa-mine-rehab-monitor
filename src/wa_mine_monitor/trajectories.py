"""Tier 1 trajectory table (D13 E3).

One row per (site, year, metric, collection) -- sensor overlaps are
preserved as separate rows, never collapsed. Geometry is Maus-derived and
carried as WKB in EPSG:3577; the whole table is package-bound to
CC-BY-SA-4.0 and private pending Batch G export adjudication.

`n_valid_pixels` IS the quantity the 2026-08-23 decision
(`docs/decisions/2026-08-23-d3-commodity-codes-and-valid-fraction.md`)
names `valid_support_px`: the mask-based valid member count for one
footprint-year-collection, from `geomedian_valid_mask`/`fc_valid_mask`.
It is not duplicated under a second column name.

`shared_footprint_site_count` and `d3_forced_threshold` implement the
2026-08-25 Tier 1 product framing decision
(`docs/decisions/2026-08-25-tier1-product-framing.md`): every row
carries the number of eligible sites sharing its `maus_id` footprint
(>= 1, since the row's own site is always one of them) and whether the
site's D3 eligibility came from the forced threshold, so that any
downstream presentation can disclose sharing without a second lookup.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa

from wa_mine_monitor import d3_inputs, spectral_metrics, tables

GEOMETRY_CRS = "EPSG:3577"

#: Closed metric vocabulary: D3 geomedian metrics then FC metrics, in the
#: order the frozen protocol declares them.
METRICS: tuple[str, ...] = tuple(d3_inputs.GEOMEDIAN_METRIC_BANDS) + tuple(
    d3_inputs.FC_METRIC_ASSETS
)

TRAJECTORY_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string(), nullable=False),
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("year", pa.int32(), nullable=False),
        pa.field("metric", pa.string(), nullable=False),
        pa.field("value", pa.float64(), nullable=True),
        pa.field("sensor", pa.string(), nullable=True),
        pa.field("collection_id", pa.string(), nullable=False),
        pa.field("item_id", pa.string(), nullable=False),
        pa.field("product_version", pa.string(), nullable=True),
        pa.field("geomad_count", pa.int64(), nullable=True),
        pa.field("n_member_pixels", pa.int64(), nullable=False),
        pa.field("n_valid_pixels", pa.int64(), nullable=True),
        pa.field("effective_pixel_support_px", pa.int64(), nullable=True),
        pa.field("computable", pa.bool_(), nullable=False),
        pa.field("not_computable_reason", pa.string(), nullable=True),
        pa.field("value_out_of_documented_range", pa.int64(), nullable=True),
        pa.field("transition_adjacent", pa.bool_(), nullable=False),
        pa.field("shared_footprint_site_count", pa.int64(), nullable=False),
        pa.field("d3_forced_threshold", pa.bool_(), nullable=False),
        pa.field("source_snapshot_date", pa.string(), nullable=False),
        pa.field("geometry", pa.binary(), nullable=False),
    ]
)

FC_METRICS: frozenset[str] = frozenset(d3_inputs.FC_METRIC_ASSETS)
_KEY = ("site_id", "year", "metric", "collection_id")


class TrajectoryError(ValueError):
    """A trajectory frame that violates the E3 row contract."""


def validate_trajectories(df: pd.DataFrame) -> None:
    """Refuse any frame that breaks the E3 contract. Checks are ordered
    cheapest-first; the first violation is reported."""
    missing = [c for c in TRAJECTORY_SCHEMA.names if c not in df.columns]
    if missing:
        raise TrajectoryError(f"missing column(s): {missing}")
    non_nullable_cols = [f.name for f in TRAJECTORY_SCHEMA if not f.nullable]
    null_counts = df[non_nullable_cols].isna().any()
    has_nulls = sorted(null_counts[null_counts].index)
    if has_nulls:
        raise TrajectoryError(f"non-nullable column(s) contain null values: {has_nulls}")
    bad_metric = sorted(set(df["metric"]) - set(METRICS))
    if bad_metric:
        raise TrajectoryError(f"unknown metric value(s): {bad_metric}")
    bad_reason = sorted(
        set(df["not_computable_reason"].dropna()) - set(spectral_metrics.NOT_COMPUTABLE_REASONS)
    )
    if bad_reason:
        raise TrajectoryError(f"unknown not_computable_reason value(s): {bad_reason}")
    computable = df["computable"].astype(bool)
    has_value = df["value"].notna()
    if (computable != has_value).any():
        raise TrajectoryError("computable must be True iff value is non-null")
    if (~computable & df["not_computable_reason"].isna()).any():
        raise TrajectoryError("not_computable_reason is required when computable is False")
    if (computable & df["not_computable_reason"].notna()).any():
        raise TrajectoryError("not_computable_reason must be null when computable is True")
    fc = df["metric"].isin(FC_METRICS)
    if (fc & df["geomad_count"].notna()).any():
        raise TrajectoryError("geomad_count must be null for FC metrics (never fabricated)")
    if df.duplicated(list(_KEY)).any():
        raise TrajectoryError(f"duplicate rows on {_KEY}")
    if (df["shared_footprint_site_count"] < 1).any():
        raise TrajectoryError("shared_footprint_site_count must be >= 1 on every row")


def write_trajectories(df: pd.DataFrame, path: Path) -> None:
    """Validate, then write under `TRAJECTORY_SCHEMA` via `tables.write_table`
    so nullable ints/bools are preserved (no float64 coercion)."""
    validate_trajectories(df)
    out = df.copy()
    for col in (
        "geomad_count",
        "n_valid_pixels",
        "effective_pixel_support_px",
        "value_out_of_documented_range",
    ):
        out[col] = out[col].astype("Int64")
    out["computable"] = out["computable"].astype("boolean")
    out["transition_adjacent"] = out["transition_adjacent"].astype("boolean")
    out["year"] = out["year"].astype("int32")
    tables.write_table(out[TRAJECTORY_SCHEMA.names], path, TRAJECTORY_SCHEMA)


@dataclass(frozen=True)
class RowContext:
    """Everything about one (site, year, collection) that is not a metric."""

    site_id: str
    maus_id: str
    year: int
    sensor: str | None
    collection_id: str
    item_id: str
    product_version: str | None
    geomad_count: int | None
    effective_pixel_support_px: int | None
    transition_adjacent: bool
    shared_footprint_site_count: int
    d3_forced_threshold: bool
    source_snapshot_date: str
    geometry_wkb: bytes


def rows_from_metrics(
    metric_rows: Sequence[spectral_metrics.MetricRow], ctx: RowContext
) -> list[dict[str, object]]:
    return [
        {
            "site_id": ctx.site_id,
            "maus_id": ctx.maus_id,
            "year": ctx.year,
            "metric": m.metric,
            "value": m.value,
            "sensor": ctx.sensor,
            "collection_id": ctx.collection_id,
            "item_id": ctx.item_id,
            "product_version": ctx.product_version,
            "geomad_count": ctx.geomad_count,
            "n_member_pixels": m.n_member_pixels,
            "n_valid_pixels": m.n_valid_pixels,
            "effective_pixel_support_px": ctx.effective_pixel_support_px,
            "computable": m.computable,
            "not_computable_reason": m.not_computable_reason,
            "value_out_of_documented_range": m.value_out_of_documented_range,
            "transition_adjacent": ctx.transition_adjacent,
            "shared_footprint_site_count": ctx.shared_footprint_site_count,
            "d3_forced_threshold": ctx.d3_forced_threshold,
            "source_snapshot_date": ctx.source_snapshot_date,
            "geometry": ctx.geometry_wkb,
        }
        for m in metric_rows
    ]
