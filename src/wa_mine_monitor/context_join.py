"""F6 site-year context join (D13 §6): fire and climate context beside
trajectories, one row per Tier 1 site-year.

**Claim boundary (verbatim, repeated at every layer that touches context).**
These are context rows displayed beside trajectories; no causal attribution
is generated here or anywhere in this project. This is the one place the
project puts fire and climate context beside a trajectory domain for a
reader to draw an inference from -- and it states explicitly that it has
not determined one: `context_complete` is the schema-level carrier of the
"cause not determined" rendering contract. A row with `context_complete =
False` MUST be rendered with cause not determined; a row with
`context_complete = True` still carries no cause -- only the context a
reader needs to know that none was determined for them.

**Absence is a state, never a widened vocabulary.** A year with no context
rows at all (currently 1986: trajectories start at the LS5T 1986 annual
geomedian; both context products begin at 1987) is emitted as an explicit
`context_row_status = "no_context_row"` row with an all-null payload and a
`no_context_row_reason` naming the context start year. It is NEVER
expressed by widening `fire_status` -- fire's three-state vocabulary
(`recorded`/`not_recorded`/`unknown`) makes statements about the fire
RECORD, and "this project built no context row for this year" is not one
of them.

**Collision renames.** Both context schemas carry `not_computable_reason`;
here they become `fire_not_computable_reason` and
`climate_not_computable_reason` so neither silently shadows the other.

Pure: every read and refusal that follows from a read belongs to the
`build-context-join` CLI command. This module receives already-verified
frames and turns them into one schema-conformant product.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd
import pyarrow as pa

from wa_mine_monitor import climate_context, fire_context

CONTEXT_ROW_JOINED = "joined"
CONTEXT_ROW_NO_CONTEXT = "no_context_row"

#: Every column that carries fire/climate payload -- all null exactly when
#: `context_row_status == "no_context_row"`. Kept as one tuple so the
#: assembler, the validator and the tests enumerate the same set.
CONTEXT_PAYLOAD_COLUMNS: tuple[str, ...] = (
    "fire_status",
    "fire_record_count",
    "fire_coverage_status",
    "fire_source_version",
    "fire_snapshot_date",
    "fire_not_computable_reason",
    "silo_cell_id",
    "annual_rainfall_mm",
    "rain_days_ge_1mm",
    "rainfall_anomaly_mm",
    "rainfall_baseline_start_year",
    "rainfall_baseline_end_year",
    "climate_status",
    "climate_not_computable_reason",
    "silo_source_version",
    "silo_snapshot_date",
)

CONTEXT_JOIN_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string(), nullable=False),
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("year", pa.int32(), nullable=False),
        pa.field("context_row_status", pa.string(), nullable=False),
        pa.field("context_complete", pa.bool_(), nullable=False),
        pa.field("fire_status", pa.string(), nullable=True),
        pa.field("fire_record_count", pa.int32(), nullable=True),
        pa.field("fire_coverage_status", pa.string(), nullable=True),
        pa.field("fire_source_version", pa.string(), nullable=True),
        pa.field("fire_snapshot_date", pa.string(), nullable=True),
        pa.field("fire_not_computable_reason", pa.string(), nullable=True),
        pa.field("silo_cell_id", pa.string(), nullable=True),
        pa.field("annual_rainfall_mm", pa.float64(), nullable=True),
        pa.field("rain_days_ge_1mm", pa.int32(), nullable=True),
        pa.field("rainfall_anomaly_mm", pa.float64(), nullable=True),
        pa.field("rainfall_baseline_start_year", pa.int32(), nullable=True),
        pa.field("rainfall_baseline_end_year", pa.int32(), nullable=True),
        pa.field("climate_status", pa.string(), nullable=True),
        pa.field("climate_not_computable_reason", pa.string(), nullable=True),
        pa.field("silo_source_version", pa.string(), nullable=True),
        pa.field("silo_snapshot_date", pa.string(), nullable=True),
        pa.field("no_context_row_reason", pa.string(), nullable=True),
    ]
)

#: Name fragments no column of this product may carry: the join provides
#: context, never causes. Checked by `validate_context_join` against both
#: the frame and `CONTEXT_JOIN_SCHEMA` itself.
FORBIDDEN_NAME_FRAGMENTS: tuple[str, ...] = ("caus", "attribut", "driver", "explan")


class ContextJoinError(ValueError):
    """Context-join assembly or validation refused inconsistent inputs."""


def _int32(series: pd.Series) -> pd.Series:
    return series.astype("Int32")


def assemble_rows(
    *,
    fire_df: pd.DataFrame,
    climate_df: pd.DataFrame,
    years: Sequence[int],
) -> pd.DataFrame:
    """One `CONTEXT_JOIN_SCHEMA` row per (site, year) for every site in the
    context frames crossed with every year in `years` (the caller derives
    `years` from the trajectory product, so no trajectory site-year is ever
    dropped for unknown context -- D13 §6).

    Refuses (`ContextJoinError`) inputs that cannot be joined honestly:
    duplicate (site_id, year) keys in either frame, differing (site, year)
    domains between fire and climate, `maus_id` disagreement on any site,
    a context year outside `years`, or empty `years`.
    """
    requested_years = sorted({int(y) for y in years})
    if not requested_years:
        raise ContextJoinError("years is empty -- assemble_rows requires at least one year")

    fire = fire_df.rename(columns={"not_computable_reason": "fire_not_computable_reason"}).copy()
    climate = climate_df.rename(
        columns={"not_computable_reason": "climate_not_computable_reason"}
    ).copy()
    for name, frame in (("fire", fire), ("climate", climate)):
        if frame.duplicated(["site_id", "year"]).any():
            raise ContextJoinError(f"{name} context carries duplicate (site_id, year) rows")

    fire_keys = set(zip(fire["site_id"].astype(str), fire["year"].astype(int), strict=True))
    climate_keys = set(
        zip(climate["site_id"].astype(str), climate["year"].astype(int), strict=True)
    )
    if fire_keys != climate_keys:
        raise ContextJoinError(
            "fire and climate context cover different (site_id, year) domains: "
            f"{len(fire_keys - climate_keys)} only in fire, "
            f"{len(climate_keys - fire_keys)} only in climate"
        )

    fire_maus = dict(zip(fire["site_id"].astype(str), fire["maus_id"].astype(str), strict=True))
    climate_maus = dict(
        zip(climate["site_id"].astype(str), climate["maus_id"].astype(str), strict=True)
    )
    disagreeing = sorted(s for s, m in fire_maus.items() if climate_maus.get(s) != m)
    if disagreeing:
        raise ContextJoinError(
            f"maus_id disagrees between fire and climate context for site(s) "
            f"{disagreeing[:5]} ({len(disagreeing)} total)"
        )

    context_years = sorted({y for _s, y in fire_keys})
    stray_years = sorted(set(context_years) - set(requested_years))
    if stray_years:
        raise ContextJoinError(
            f"context year(s) {stray_years} fall outside the requested year domain "
            f"{requested_years[0]}-{requested_years[-1]}"
        )

    merged = fire.merge(
        climate.drop(columns=["maus_id"]),
        on=["site_id", "year"],
        how="inner",
        validate="one_to_one",
    )
    merged["context_row_status"] = CONTEXT_ROW_JOINED
    merged["no_context_row_reason"] = None

    frames = [merged]
    missing_years = [y for y in requested_years if y not in set(context_years)]
    if missing_years:
        context_start = context_years[0]
        sites = sorted(fire_maus)
        absent_rows = []
        for year in missing_years:
            reason = (
                f"no context rows exist for {year}: fire and climate context "
                f"coverage begins at {context_start}"
            )
            for site_id in sites:
                absent_rows.append(
                    {
                        "site_id": site_id,
                        "maus_id": fire_maus[site_id],
                        "year": year,
                        "context_row_status": CONTEXT_ROW_NO_CONTEXT,
                        "no_context_row_reason": reason,
                        **{column: None for column in CONTEXT_PAYLOAD_COLUMNS},
                    }
                )
        frames.append(pd.DataFrame(absent_rows))

    out = pd.concat(frames, ignore_index=True)
    out["context_complete"] = (
        (out["context_row_status"] == CONTEXT_ROW_JOINED)
        & (out["fire_coverage_status"] == fire_context.COVERAGE_COVERED)
        & (out["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED)
    ).astype(bool)

    out["year"] = out["year"].astype("int32")
    for column in (
        "fire_record_count",
        "rain_days_ge_1mm",
        "rainfall_baseline_start_year",
        "rainfall_baseline_end_year",
    ):
        out[column] = _int32(out[column])
    out = out.sort_values(["site_id", "year"], kind="stable").reset_index(drop=True)
    return out[list(CONTEXT_JOIN_SCHEMA.names)]


def validate_context_join(
    df: pd.DataFrame,
    *,
    site_ids: Sequence[str],
    years: Sequence[int],
    fire_status_counts: Mapping[str, int],
    climate_status_counts: Mapping[str, int],
) -> None:
    """Refuse (`ContextJoinError`) any frame that breaks the F6 product
    contract. `fire_status_counts`/`climate_status_counts` are the SOURCE
    products' status value-counts -- the D13 §6 acceptance requires the
    join to reconcile against them, so a joined row can never be silently
    dropped or duplicated without this failing.
    """
    expected_sites = sorted({str(s) for s in site_ids})
    expected_years = sorted({int(y) for y in years})

    missing_columns = [c for c in CONTEXT_JOIN_SCHEMA.names if c not in df.columns]
    if missing_columns:
        raise ContextJoinError(f"missing column(s): {missing_columns}")
    forbidden = [
        name
        for name in df.columns
        if any(fragment in name.lower() for fragment in FORBIDDEN_NAME_FRAGMENTS)
    ]
    if forbidden:
        raise ContextJoinError(
            f"column name(s) {forbidden} imply causal attribution -- this product carries "
            "context only, never causes"
        )

    expected_rows = len(expected_sites) * len(expected_years)
    if len(df) != expected_rows:
        raise ContextJoinError(
            f"{len(df)} rows, expected {len(expected_sites)} sites x "
            f"{len(expected_years)} years = {expected_rows}"
        )
    if df.duplicated(["site_id", "year"]).any():
        raise ContextJoinError("duplicate (site_id, year) rows")
    actual_domain = set(zip(df["site_id"].astype(str), df["year"].astype(int), strict=True))
    expected_domain = {(s, y) for s in expected_sites for y in expected_years}
    if actual_domain != expected_domain:
        raise ContextJoinError("(site_id, year) domain does not equal sites x years")

    status = df["context_row_status"]
    unknown_status = sorted(set(status) - {CONTEXT_ROW_JOINED, CONTEXT_ROW_NO_CONTEXT})
    if unknown_status:
        raise ContextJoinError(f"unknown context_row_status value(s): {unknown_status}")
    absent = df[status == CONTEXT_ROW_NO_CONTEXT]
    joined = df[status == CONTEXT_ROW_JOINED]

    if not absent[list(CONTEXT_PAYLOAD_COLUMNS)].isna().all().all():
        raise ContextJoinError("a no_context_row row carries non-null context payload")
    if absent["no_context_row_reason"].isna().any():
        raise ContextJoinError("a no_context_row row is missing no_context_row_reason")
    if joined["no_context_row_reason"].notna().any():
        raise ContextJoinError("a joined row carries no_context_row_reason")
    source_non_nullable = (
        "fire_status",
        "fire_coverage_status",
        "fire_source_version",
        "fire_snapshot_date",
        "silo_cell_id",
        "rainfall_baseline_start_year",
        "rainfall_baseline_end_year",
        "climate_status",
        "silo_source_version",
        "silo_snapshot_date",
    )
    null_on_joined = sorted(c for c in source_non_nullable if joined[c].isna().any())
    if null_on_joined:
        raise ContextJoinError(
            f"joined rows carry null(s) in source-non-nullable column(s): {null_on_joined}"
        )
    absent_years = sorted(set(absent["year"].astype(int)))
    joined_years = sorted(set(joined["year"].astype(int)))
    overlap = sorted(set(absent_years) & set(joined_years))
    if overlap:
        raise ContextJoinError(
            f"year(s) {overlap} carry both joined and no_context_row rows -- a year has "
            "context or it does not"
        )

    actual_fire = joined["fire_status"].value_counts().to_dict()
    if {k: int(v) for k, v in actual_fire.items()} != {
        k: int(v) for k, v in fire_status_counts.items() if int(v)
    }:
        raise ContextJoinError(
            f"fire_status counts {actual_fire} do not reconcile with the fire-context "
            f"product's {dict(fire_status_counts)}"
        )
    actual_climate = joined["climate_status"].value_counts().to_dict()
    if {k: int(v) for k, v in actual_climate.items()} != {
        k: int(v) for k, v in climate_status_counts.items() if int(v)
    }:
        raise ContextJoinError(
            f"climate_status counts {actual_climate} do not reconcile with the "
            f"climate-context product's {dict(climate_status_counts)}"
        )

    recomputed = (
        (df["context_row_status"] == CONTEXT_ROW_JOINED)
        & (df["fire_coverage_status"] == fire_context.COVERAGE_COVERED)
        & (df["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED)
    )
    if df["context_complete"].isna().any():
        raise ContextJoinError("context_complete must be non-null on every row")
    if (df["context_complete"].astype(bool) != recomputed).any():
        raise ContextJoinError(
            "context_complete does not equal (joined AND fire covered AND climate computed)"
        )
