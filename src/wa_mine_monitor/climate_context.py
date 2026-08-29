"""Climate-context row assembly: SILO rainfall metrics displayed beside
trajectory rows.

**Claim boundary (verbatim, repeated at every layer that touches climate
context).** These are context rows displayed beside trajectories; no
causal attribution is generated here or anywhere in this project.
"Cause not determined" belongs to the later F6 join, which is the only
place this project ever puts a trajectory and a climate row side by
side for a reader to draw an inference from -- and even there it states
explicitly that it has not determined one. This module never joins
climate to trajectory rows and never says why a trajectory moved.

**Pure, and why that split exists.** Every read (the SILO grid files,
the crosswalk, the site register) and every refusal that follows from a
read (a missing snapshot, an unreadable file) belongs to the CLI command
that will call this module in a later task. This module receives
already-computed inputs -- metrics, baselines, gap reasons -- as plain
mappings and turns them into schema-conformant rows. That split makes
`assemble_rows` testable with dicts and no filesystem, and it keeps the
one rule this module exists to enforce -- a trajectory row is never
dropped for missing climate context (D13 F6) -- in one place a test can
hit directly, rather than smeared across I/O-heavy CLI code.

**Row-count invariant.** `assemble_rows` returns exactly one row per
`(site_id, year)` pair for every entry in `site_maus_pairs` crossed with
every year in `years` -- unconditionally, whether or not that cell-year
is computable. A row is the caller's record that context was CONSIDERED
for that site-year, not that it succeeded; dropping unknown-context rows
would make "no row" ambiguous between "not evaluated" and "evaluated,
unknown" and silently shrink every downstream frame that joins on
`(site_id, year)`.

**Baseline gap vs. year gap, and why they're different maps.**
`rainfall_anomaly_mm` (silo.py) refuses on an incomplete baseline rather
than average over a shorter period, because a 12-year mean mislabelled
as "1991-2020" is a wrong number, not a missing one. That refusal is
cell-wide, not year-wide: an incomplete baseline for a cell makes EVERY
year for that cell not-computable, via `baseline_gap_by_cell`, checked
first and independently of any particular year's own data. A given
year's own missing/partial daily data is a separate, per-cell-year
failure, via `not_computable_by_cell_year` (populated by the caller from
`SiloNotComputableError`s raised out of `annual_metrics`/
`cell_daily_series`). Both land on the same `climate_status`/
`not_computable_reason` columns because a reader of the row has no
downstream use for which refusal produced it -- only that the metrics
are unknown and why.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd
import pyarrow as pa

from wa_mine_monitor.sources.silo import AnnualMetrics

CLIMATE_CONTEXT_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string(), nullable=False),
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("year", pa.int32(), nullable=False),
        pa.field("silo_cell_id", pa.string(), nullable=False),
        pa.field("annual_rainfall_mm", pa.float64(), nullable=True),
        pa.field("rain_days_ge_1mm", pa.int32(), nullable=True),
        pa.field("rainfall_anomaly_mm", pa.float64(), nullable=True),
        pa.field("rainfall_baseline_start_year", pa.int32(), nullable=False),
        pa.field("rainfall_baseline_end_year", pa.int32(), nullable=False),
        pa.field("climate_status", pa.string(), nullable=False),
        pa.field("not_computable_reason", pa.string(), nullable=True),
        pa.field("silo_source_version", pa.string(), nullable=False),
        pa.field("silo_snapshot_date", pa.string(), nullable=False),
    ]
)

#: Fixed SILO climate-normal baseline period (D13 F5 schema). Written onto
#: every row -- computed or not -- so a reader never has to infer which
#: baseline a null anomaly would have used.
BASELINE_START_YEAR = 1991
BASELINE_END_YEAR = 2020

#: The only two permitted `climate_status` values.
CLIMATE_STATUS_COMPUTED = "computed"
CLIMATE_STATUS_NOT_COMPUTABLE = "not_computable"


class ClimateContextError(ValueError):
    """Climate-context assembly refused a structurally invalid input."""


def assemble_rows(
    *,
    site_maus_pairs: Sequence[tuple[str, str]],
    cell_id_by_maus: Mapping[str, str],
    metrics_by_cell_year: Mapping[tuple[str, int], AnnualMetrics],
    not_computable_by_cell_year: Mapping[tuple[str, int], str],
    baseline_annuals_by_cell: Mapping[str, Sequence[float]],
    baseline_gap_by_cell: Mapping[str, str],
    years: Sequence[int],
    snapshot_date: str,
    source_version: str,
) -> pd.DataFrame:
    """Assemble `CLIMATE_CONTEXT_SCHEMA`-conformant rows, one per
    `(site_id, year)` for every pair in `site_maus_pairs` crossed with
    every year in `years`.

    A `maus_id` absent from `cell_id_by_maus` raises `ClimateContextError`
    immediately: the crosswalk step upstream guarantees every Tier 1
    `maus_id` has a cell, so a missing entry here is a caller bug (a stale
    or mismatched crosswalk), not a per-row unknown to encode on the row
    the way a missing daily value is.

    Per cell-year, in order:

    1. `baseline_gap_by_cell` is checked first, independent of `years` --
       a cell with an entry there is `not_computable` for EVERY year,
       carrying that entry as the reason. This is deliberately checked
       before anything year-specific: an incomplete baseline invalidates
       every year's anomaly, not just the years the gap happened to touch.
    2. otherwise, `not_computable_by_cell_year` is checked for this exact
       `(cell, year)`; a hit is `not_computable` with that entry as reason.
    3. otherwise the cell-year is `computed`, its metrics come from
       `metrics_by_cell_year[(cell, year)]`, and its anomaly is the metric
       total minus the mean of `baseline_annuals_by_cell[cell]`.

    A `not_computable` row carries `None` (never `0`, never `NaN` -- D13
    F5) for `annual_rainfall_mm`, `rain_days_ge_1mm` and
    `rainfall_anomaly_mm`. A `computed` row carries `not_computable_reason
    = None`. Two sites sharing one `maus_id` land on the same
    `silo_cell_id` and therefore identical metric values for a given year
    -- the shared-footprint case the Tier 1 product framing already
    discloses, not a bug to special-case here.
    """
    for _site_id, maus_id in site_maus_pairs:
        if maus_id not in cell_id_by_maus:
            raise ClimateContextError(
                f"maus_id {maus_id!r} has no entry in cell_id_by_maus -- a missing "
                "cell mapping is a caller bug, not a row-level unknown"
            )

    rows: list[dict[str, object]] = []
    for site_id, maus_id in site_maus_pairs:
        cell = cell_id_by_maus[maus_id]
        for year in years:
            base_row = {
                "site_id": site_id,
                "maus_id": maus_id,
                "year": year,
                "silo_cell_id": cell,
                "rainfall_baseline_start_year": BASELINE_START_YEAR,
                "rainfall_baseline_end_year": BASELINE_END_YEAR,
                "silo_source_version": source_version,
                "silo_snapshot_date": snapshot_date,
            }

            baseline_gap_reason = baseline_gap_by_cell.get(cell)
            year_reason = not_computable_by_cell_year.get((cell, year))
            reason = baseline_gap_reason if baseline_gap_reason is not None else year_reason

            if reason is not None:
                rows.append(
                    {
                        **base_row,
                        "annual_rainfall_mm": None,
                        "rain_days_ge_1mm": None,
                        "rainfall_anomaly_mm": None,
                        "climate_status": CLIMATE_STATUS_NOT_COMPUTABLE,
                        "not_computable_reason": reason,
                    }
                )
                continue

            metrics = metrics_by_cell_year[(cell, year)]
            baseline_annuals = baseline_annuals_by_cell[cell]
            anomaly = metrics.annual_rainfall_mm - (sum(baseline_annuals) / len(baseline_annuals))
            rows.append(
                {
                    **base_row,
                    "annual_rainfall_mm": metrics.annual_rainfall_mm,
                    "rain_days_ge_1mm": metrics.rain_days_ge_1mm,
                    "rainfall_anomaly_mm": anomaly,
                    "climate_status": CLIMATE_STATUS_COMPUTED,
                    "not_computable_reason": None,
                }
            )

    return pd.DataFrame(rows, columns=list(CLIMATE_CONTEXT_SCHEMA.names))
