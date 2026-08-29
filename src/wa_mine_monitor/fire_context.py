"""Fire-context row assembly: DBCA-060 fire history displayed beside
trajectory rows.

**Claim boundary (verbatim, repeated at every layer that touches fire
context).** These are context rows displayed beside trajectories; no
causal attribution is generated here or anywhere in this project. Fire
history is context only, never a cause of rehabilitation status, and it
is never a compliance or performance finding. The F6 trajectory join
(out of scope here) is the only place this project ever puts a
trajectory and a fire row side by side for a reader to draw an
inference from -- and even there it states explicitly that it has not
determined one. This module never joins fire history to trajectory rows
and never says why a trajectory moved.

**`not_recorded` is a statement about the record, never the ground**
(limitation L18). DBCA-060's own scope is fires on DBCA-managed land or
where DBCA incurred costs; spatial completeness is not modelled, so the
absence of a fire record for a site-year says nothing about whether a
fire actually occurred there. `not_recorded` is NEVER a known-negative
fire label.

**Pure, and why that split exists.** Every read (the DBCA-060
GeoPackage, the crosswalk, the site register, the Maus footprint
snapshot) and every refusal that follows from a read belongs to the CLI
command that will call this module (Task 7). This module receives
already-computed inputs -- per-(maus_id, year) fire counts, no-footprint
reasons -- as plain mappings and turns them into schema-conformant rows.
That split makes `assemble_rows` testable with dicts and no filesystem.

**Row-count invariant.** `assemble_rows` returns exactly one row per
`(site_id, year)` pair for every entry in `site_maus_pairs` crossed with
every year in `years` -- unconditionally, whether or not that site-year
is computable. A row is the caller's record that fire context was
CONSIDERED for that site-year, not that it succeeded; dropping rows
would make "no row" ambiguous between "not evaluated" and "evaluated,
unknown" and silently shrink every downstream frame that joins on
`(site_id, year)`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd
import pyarrow as pa

from wa_mine_monitor.sources import dbca

FIRE_CONTEXT_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string(), nullable=False),
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("year", pa.int32(), nullable=False),
        pa.field("fire_status", pa.string(), nullable=False),
        pa.field("fire_record_count", pa.int32(), nullable=True),
        pa.field("fire_source_version", pa.string(), nullable=False),
        pa.field("fire_coverage_status", pa.string(), nullable=False),
        pa.field("fire_snapshot_date", pa.string(), nullable=False),
        pa.field("not_computable_reason", pa.string(), nullable=True),
    ]
)

#: The only three permitted `fire_status` values.
FIRE_STATUS_RECORDED = "recorded"
FIRE_STATUS_NOT_RECORDED = "not_recorded"
FIRE_STATUS_UNKNOWN = "unknown"

#: The only three permitted `fire_coverage_status` values.
COVERAGE_COVERED = "covered"
COVERAGE_OUTSIDE_WINDOW = "outside_window"
COVERAGE_NO_FOOTPRINT = "no_footprint"


class FireContextError(ValueError):
    """Fire-context assembly refused a structurally invalid input."""


def coverage_window(snapshot_year: int) -> tuple[int, int]:
    """Frozen DBCA-060 coverage window `[1937, snapshot_year - 1]`
    (decision 2026-08-29). Separate from and narrower than `dbca.YEAR_MIN`,
    which is only a validation-time sanity bound.
    """
    return (dbca.COVERAGE_START_YEAR, snapshot_year - 1)


def assemble_rows(
    *,
    site_maus_pairs: Sequence[tuple[str, str]],
    counts_by_maus_year: Mapping[tuple[str, int], int],
    no_footprint_by_maus: Mapping[str, str],
    years: Sequence[int],
    snapshot_year: int,
    snapshot_date: str,
    source_version: str,
) -> pd.DataFrame:
    """Assemble `FIRE_CONTEXT_SCHEMA`-conformant rows, one per
    `(site_id, year)` for every pair in `site_maus_pairs` crossed with
    every year in `years`.

    Per site-year, in this exact precedence order:

    1. `maus_id in no_footprint_by_maus` -- `unknown`, `no_footprint`
       coverage, `not_computable_reason` = that entry, `fire_record_count`
       null, for EVERY year of that site.
    2. `counts_by_maus_year.get((maus_id, year), 0) > 0` -- `recorded`;
       `covered` if the year is inside `coverage_window(snapshot_year)`,
       else `outside_window` (a record outside the window is still a
       record); `fire_record_count` = the count; reason null.
    3. count == 0 and the year is inside the window -- `not_recorded`,
       `covered`, `fire_record_count` = 0, reason null.
    4. count == 0 and the year is outside the window -- `unknown`,
       `outside_window`, count null, `not_computable_reason` states the
       window was not covered.

    Raises `FireContextError` on empty `years`, empty `site_maus_pairs`,
    or a negative count in `counts_by_maus_year`.

    A `maus_id` missing from `no_footprint_by_maus` but also missing from
    the caller's Maus/crosswalk data is NOT this function's concern -- an
    un-crosswalked eligible site or a `maus_id` absent from the Maus
    snapshot is a CLI-level integrity refusal (climate-context
    precedent), never encoded here; `maus_id` stays non-nullable.
    """
    if not years:
        raise FireContextError("years is empty -- assemble_rows requires at least one year")
    if not site_maus_pairs:
        raise FireContextError(
            "site_maus_pairs is empty -- assemble_rows requires at least one (site_id, maus_id) pair"
        )
    for key, count in counts_by_maus_year.items():
        if count < 0:
            raise FireContextError(
                f"counts_by_maus_year[{key!r}] = {count} -- fire record counts cannot be negative"
            )

    window_lo, window_hi = coverage_window(snapshot_year)
    outside_window_reason = f"year outside the declared coverage window [{window_lo}, {window_hi}]"

    rows: list[dict[str, object]] = []
    for site_id, maus_id in site_maus_pairs:
        no_footprint_reason = no_footprint_by_maus.get(maus_id)
        for year in years:
            base_row = {
                "site_id": site_id,
                "maus_id": maus_id,
                "year": year,
                "fire_source_version": source_version,
                "fire_snapshot_date": snapshot_date,
            }

            if no_footprint_reason is not None:
                rows.append(
                    {
                        **base_row,
                        "fire_status": FIRE_STATUS_UNKNOWN,
                        "fire_record_count": None,
                        "fire_coverage_status": COVERAGE_NO_FOOTPRINT,
                        "not_computable_reason": no_footprint_reason,
                    }
                )
                continue

            count = counts_by_maus_year.get((maus_id, year), 0)
            in_window = window_lo <= year <= window_hi

            if count > 0:
                rows.append(
                    {
                        **base_row,
                        "fire_status": FIRE_STATUS_RECORDED,
                        "fire_record_count": count,
                        "fire_coverage_status": COVERAGE_COVERED
                        if in_window
                        else COVERAGE_OUTSIDE_WINDOW,
                        "not_computable_reason": None,
                    }
                )
                continue

            if in_window:
                rows.append(
                    {
                        **base_row,
                        "fire_status": FIRE_STATUS_NOT_RECORDED,
                        "fire_record_count": 0,
                        "fire_coverage_status": COVERAGE_COVERED,
                        "not_computable_reason": None,
                    }
                )
            else:
                rows.append(
                    {
                        **base_row,
                        "fire_status": FIRE_STATUS_UNKNOWN,
                        "fire_record_count": None,
                        "fire_coverage_status": COVERAGE_OUTSIDE_WINDOW,
                        "not_computable_reason": outside_window_reason,
                    }
                )

    frame = pd.DataFrame(rows, columns=list(FIRE_CONTEXT_SCHEMA.names))
    frame["year"] = frame["year"].astype("int32")
    frame["fire_record_count"] = frame["fire_record_count"].astype("Int32")
    return frame


def validate_row_counts(frame: pd.DataFrame, *, n_pairs: int, n_years: int) -> None:
    """Raise `FireContextError` unless `frame` has exactly `n_pairs *
    n_years` rows and its `fire_status` value counts sum to that same
    row count (the D13 F4 reconciliation acceptance: recorded +
    not_recorded + unknown == rows == selected sites x requested years).
    """
    expected = n_pairs * n_years
    actual = len(frame)
    if actual != expected:
        raise FireContextError(
            f"fire-context frame has {actual} rows, expected {n_pairs} pairs x {n_years} years = {expected}"
        )

    status_sum = int(frame["fire_status"].value_counts().sum())
    if status_sum != actual:
        raise FireContextError(
            f"fire_status value counts sum to {status_sum}, expected {actual} -- reconciliation failed"
        )
