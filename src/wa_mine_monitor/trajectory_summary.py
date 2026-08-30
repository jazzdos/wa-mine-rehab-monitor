"""Per-site trajectory summary for the private QGIS project (Batch G,
design `docs/plans/2026-08-30-batch-g-qgis-design.md`).

**Claim boundary (verbatim, as at every layer).** These are observed
spectral values and context beside them; no trend, recovery,
performance or cause is computed here or anywhere in this project.
`trajectory_status` is a processing status, never a performance
verdict.

**Sensor overlap is never resolved by priority.** Where more than one
collection computably covers a metric's latest year for a site, the
summary reports NULL for `<metric>_latest` and discloses the overlap in
`<metric>_latest_collections`; picking either collection's value would
resolve a disagreement the architecture preserves.

**Fire three-state is never widened or collapsed.**
`fire_status_latest` carries the fire RECORD's vocabulary
(`recorded`/`not_recorded`/`unknown`); `last_recorded_fire_year` is
NULL when no fire is recorded -- never a fabricated known-negative.

Pure: every read and refusal that follows from a read belongs to the
`build-trajectory-summary` CLI command. This module receives
already-verified frames.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from wa_mine_monitor import climate_context, context_join, fire_context, trajectories

#: WGS84: register lon/lat are always written in EPSG:4326 (see
#: `build-register`); QGIS reprojects against the EPSG:3577 layers.
GEOMETRY_CRS = "EPSG:4326"
REGISTER_LAYER = "register_sites"
SUMMARY_LAYER = "site_summary"

_IDENTITY_COLUMNS: tuple[str, ...] = (
    "site_id",
    "maus_id",
    "trajectory_status",
    "shared_footprint_site_count",
    "d3_forced_threshold",
)
_COVERAGE_COLUMNS: tuple[str, ...] = (
    "year_min",
    "year_max",
    "years_observed",
    "years_computable",
    "years_not_computable",
    "context_complete_years",
)
_FIRE_COLUMNS: tuple[str, ...] = (
    "fire_status_latest",
    "fire_years_recorded",
    "last_recorded_fire_year",
)
_CLIMATE_COLUMNS: tuple[str, ...] = (
    "rainfall_annual_mean",
    "rainfall_latest",
    "rainfall_latest_year",
)


def _metric_columns() -> tuple[str, ...]:
    columns: list[str] = []
    for metric in trajectories.METRICS:
        columns += [f"{metric}_latest", f"{metric}_latest_year", f"{metric}_latest_collections"]
    return tuple(columns)


#: The pinned summary schema, in output order. The QML drift-guard test
#: binds `qgis/styles/*.qml` field references to this tuple.
SUMMARY_COLUMNS: tuple[str, ...] = (
    _IDENTITY_COLUMNS + _COVERAGE_COLUMNS + _metric_columns() + _FIRE_COLUMNS + _CLIMATE_COLUMNS
)


class TrajectorySummaryError(ValueError):
    """Summary assembly or validation refused inconsistent inputs."""


def _single_value_per_site(traj_df: pd.DataFrame, column: str) -> pd.Series:
    """One value of `column` per site, refusing per-site conflicts --
    the trajectory row contract already guarantees consistency, so a
    conflict here is upstream corruption, not a case to average away."""
    pairs = traj_df[["site_id", column]].drop_duplicates()
    if pairs["site_id"].duplicated().any():
        conflicted = sorted(pairs.loc[pairs["site_id"].duplicated(), "site_id"].astype(str))
        raise TrajectorySummaryError(
            f"trajectories carry more than one {column!r} for site(s) {conflicted[:5]}"
        )
    return pairs.set_index("site_id")[column]


def assemble_summary(
    *,
    register_df: pd.DataFrame,
    traj_df: pd.DataFrame,
    context_df: pd.DataFrame,
) -> pd.DataFrame:
    """One `SUMMARY_COLUMNS` row per eligible register site. Refuses
    (`TrajectorySummaryError`) inputs whose site sets disagree -- the
    caller has already digest-verified every frame, so a mismatch is an
    integrity failure, never something to intersect away."""
    eligible = register_df.loc[register_df["trajectory_status"] == "eligible"]
    sites = sorted(eligible["site_id"].astype(str))
    for label, frame in (("trajectories", traj_df), ("context join", context_df)):
        other = set(frame["site_id"].astype(str))
        if set(sites) != other:
            raise TrajectorySummaryError(
                f"site sets differ: {len(sites)} eligible register site(s) vs "
                f"{len(other)} in the {label} -- e.g. only-register "
                f"{sorted(set(sites) - other)[:5]}, only-{label.split()[0]} "
                f"{sorted(other - set(sites))[:5]}"
            )

    out = pd.DataFrame(index=pd.Index(sites, name="site_id"))
    out["maus_id"] = _single_value_per_site(traj_df, "maus_id")
    out["trajectory_status"] = eligible.set_index("site_id")["trajectory_status"]
    out["shared_footprint_site_count"] = _single_value_per_site(
        traj_df, "shared_footprint_site_count"
    ).astype("int64")
    out["d3_forced_threshold"] = _single_value_per_site(traj_df, "d3_forced_threshold").astype(bool)

    grouped = traj_df.groupby("site_id")
    out["year_min"] = grouped["year"].min().astype("int64")
    out["year_max"] = grouped["year"].max().astype("int64")
    out["years_observed"] = grouped["year"].nunique().astype("int64")
    computable = traj_df.loc[traj_df["computable"].astype(bool)]
    out["years_computable"] = (
        computable.groupby("site_id")["year"].nunique().reindex(out.index, fill_value=0)
    ).astype("int64")
    out["years_not_computable"] = out["years_observed"] - out["years_computable"]
    complete = context_df.loc[context_df["context_complete"].astype(bool)]
    out["context_complete_years"] = (
        complete.groupby("site_id")["year"].nunique().reindex(out.index, fill_value=0)
    ).astype("int64")

    _add_metric_latest(out, computable)
    _add_fire_summary(out, context_df)
    _add_climate_summary(out, context_df)
    return out.reset_index()[list(SUMMARY_COLUMNS)]


def _add_metric_latest(out: pd.DataFrame, computable: pd.DataFrame) -> None:
    for metric in trajectories.METRICS:
        sub = computable.loc[computable["metric"] == metric]
        latest_year = sub.groupby("site_id")["year"].max()
        at_latest = sub.loc[sub["year"] == sub["site_id"].map(latest_year)]
        n_collections = at_latest.groupby("site_id")["collection_id"].nunique()
        # Sensor-overlap rule: >1 computable collection at the latest
        # year => NULL value, overlap disclosed. Never resolved by
        # priority (module docstring; architecture ruling in ROADMAP).
        value = at_latest.groupby("site_id")["value"].first().where(n_collections == 1)
        out[f"{metric}_latest"] = value.reindex(out.index).astype("Float64")
        out[f"{metric}_latest_year"] = latest_year.reindex(out.index).astype("Int64")
        out[f"{metric}_latest_collections"] = n_collections.reindex(out.index).astype("Int64")


def _add_fire_summary(out: pd.DataFrame, context_df: pd.DataFrame) -> None:
    joined = context_df.loc[context_df["context_row_status"] == context_join.CONTEXT_ROW_JOINED]
    latest_year = joined.groupby("site_id")["year"].max()
    at_latest = joined.loc[joined["year"] == joined["site_id"].map(latest_year)].set_index(
        "site_id"
    )
    out["fire_status_latest"] = at_latest["fire_status"].reindex(out.index)
    recorded = joined.loc[joined["fire_status"] == fire_context.FIRE_STATUS_RECORDED]
    out["fire_years_recorded"] = (
        recorded.groupby("site_id")["year"].nunique().reindex(out.index, fill_value=0)
    ).astype("int64")
    out["last_recorded_fire_year"] = (
        recorded.groupby("site_id")["year"].max().reindex(out.index).astype("Int64")
    )


def _add_climate_summary(out: pd.DataFrame, context_df: pd.DataFrame) -> None:
    joined = context_df.loc[context_df["context_row_status"] == context_join.CONTEXT_ROW_JOINED]
    computed = joined.loc[joined["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED]
    out["rainfall_annual_mean"] = (
        computed.groupby("site_id")["annual_rainfall_mm"].mean().reindex(out.index)
    ).astype("Float64")
    latest_year = computed.groupby("site_id")["year"].max()
    at_latest = computed.loc[computed["year"] == computed["site_id"].map(latest_year)].set_index(
        "site_id"
    )
    out["rainfall_latest"] = at_latest["annual_rainfall_mm"].reindex(out.index).astype("Float64")
    out["rainfall_latest_year"] = latest_year.reindex(out.index).astype("Int64")


#: Columns that must be non-null on every summary row -- the identity
#: block and the coverage counts. Everything else is legitimately NULL
#: (no computable metric row, no joined context, no recorded fire).
_NON_NULLABLE: tuple[str, ...] = _IDENTITY_COLUMNS + _COVERAGE_COLUMNS + ("fire_years_recorded",)

_FIRE_VOCABULARY: frozenset[str] = frozenset(
    {
        fire_context.FIRE_STATUS_RECORDED,
        fire_context.FIRE_STATUS_NOT_RECORDED,
        fire_context.FIRE_STATUS_UNKNOWN,
    }
)


def validate_summary(df: pd.DataFrame, *, site_ids: list[str]) -> None:
    """Refuse a summary the module itself would not have assembled --
    the same "never write a state the module would refuse to read back"
    discipline `validate_eligible_register` applies. Every check that
    fires is collected and reported together."""
    problems: list[str] = []
    if list(df.columns) != list(SUMMARY_COLUMNS):
        problems.append(
            f"columns differ from SUMMARY_COLUMNS: missing "
            f"{sorted(set(SUMMARY_COLUMNS) - set(df.columns))}, unexpected "
            f"{sorted(set(df.columns) - set(SUMMARY_COLUMNS))}"
        )
    else:
        if sorted(df["site_id"].astype(str)) != sorted(site_ids):
            problems.append("summary rows do not cover exactly the eligible site set")
        nulls = sorted(c for c in _NON_NULLABLE if df[c].isna().any())
        if nulls:
            problems.append(f"non-nullable column(s) contain nulls: {nulls}")
        if (df["trajectory_status"] != "eligible").any():
            problems.append("summary carries a non-eligible trajectory_status row")
        bad_fire = set(df["fire_status_latest"].dropna()) - _FIRE_VOCABULARY
        if bad_fire:
            problems.append(
                f"fire_status_latest outside the three-state vocabulary: {sorted(bad_fire)}"
            )
        if ((df["fire_years_recorded"] == 0) != df["last_recorded_fire_year"].isna()).any():
            problems.append("last_recorded_fire_year must be null iff fire_years_recorded is 0")
        for metric in trajectories.METRICS:
            overlap = df[f"{metric}_latest_collections"].fillna(1) > 1
            if df.loc[overlap, f"{metric}_latest"].notna().any():
                problems.append(
                    f"{metric}_latest carries a value where more than one collection "
                    "covers the latest year -- sensor overlap resolved by priority"
                )
    for name in df.columns:
        if any(frag in name.lower() for frag in context_join.FORBIDDEN_NAME_FRAGMENTS):
            problems.append(f"column name implies causation: {name}")
    if problems:
        raise TrajectorySummaryError("; ".join(problems))


def write_summary_gpkg(*, summary_df: pd.DataFrame, register_df: pd.DataFrame, path: Path) -> int:
    """Write the two-layer GeoPackage: `register_sites` (every LOCATED
    register site -- an unlocated site cannot be a point; the skipped
    count is returned for the run manifest to disclose) and
    `site_summary` (one point per eligible site; an unlocated ELIGIBLE
    site is refused, not skipped -- eligibility requires a usable
    footprint, so a missing location there is corruption).

    `d3_forced_threshold` on the register layer is written as nullable
    Int64 (1/0/NULL): NULL means the site was never judged (D13 D5
    rules 1/2), and GeoPackage has no three-state boolean.
    Returns the number of unlocated register sites skipped."""
    located = register_df.loc[register_df["lon"].notna() & register_df["lat"].notna()]
    n_unlocated = len(register_df) - len(located)
    register_layer = gpd.GeoDataFrame(
        {
            "site_id": located["site_id"].astype(str),
            "trajectory_status": located["trajectory_status"].astype(str),
            "d3_forced_threshold": located["d3_forced_threshold"].astype("boolean").astype("Int64"),
        },
        geometry=gpd.points_from_xy(located["lon"], located["lat"]),
        crs=GEOMETRY_CRS,
    )

    coords = register_df.set_index("site_id")[["lon", "lat"]]
    merged = summary_df.merge(coords, left_on="site_id", right_index=True, how="left")
    unlocated_eligible = sorted(
        merged.loc[merged["lon"].isna() | merged["lat"].isna(), "site_id"].astype(str)
    )
    if unlocated_eligible:
        raise TrajectorySummaryError(
            f"eligible site(s) without a register location: {unlocated_eligible[:5]} "
            f"({len(unlocated_eligible)} total)"
        )
    summary_layer = gpd.GeoDataFrame(
        summary_df.copy(),
        geometry=gpd.points_from_xy(merged["lon"], merged["lat"]),
        crs=GEOMETRY_CRS,
    )

    register_layer.to_file(path, layer=REGISTER_LAYER, driver="GPKG")
    summary_layer.to_file(path, layer=SUMMARY_LAYER, driver="GPKG", mode="a")
    return n_unlocated
