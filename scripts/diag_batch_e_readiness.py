"""Standalone diagnostic: what population and what cost would Batch E have,
given the closed Batch D result, and is the E5 Huntly gate constructible?

Read-only. Reproduces every measured figure in
`docs/reviews/2026-08-25-batch-e-findings.md` from the curated lake plus the
jarrah-rehab pilot outputs. Touches no raster, rebuilds nothing, writes
nothing outside stdout.

Four questions, one section of output each:

  1. `--check outside-rdc` -- the fraction of Tier-1 footprints excluded as
     outside every DPIRD-020 RDC polygon, against the denominator decision
     `docs/decisions/2026-08-21-d3-outside-rdc-exclusion.md` names
     (`n_for_ceiling`: the population with usable Maus geometry), and the
     candidate count that decision's 5% ceiling must NOT be read against.

  2. `--check eligibility` -- replays `register.assign_trajectory_eligibility`'s
     join with the threshold forced to 144 px, which is what an owner
     decision to proceed under the disclosed forced-144 limitation would
     authorise. Reports the `trajectory_status` split that
     `apply-d3-threshold --forced-threshold` must reproduce exactly, and the
     per-region eligible-site counts D4's >= 30-site Tier 2 gate is read
     against.

  3. `--check sharing` -- how many eligible sites share a Maus footprint, and
     the read cost of the per-site extraction loop against the per-footprint
     one, in member pixels per collection-year. This is the amplification
     figure behind finding F2.

  4. `--check huntly` -- whether the jarrah Huntly plot sites fall inside any
     Maus footprint, how large that footprint is, and which MINEDEX sites
     crosswalk onto it. This is what makes the E5 gate unconstructible as
     drafted (finding F3).

Run from the repo root on a machine holding both data roots (lux):

    uv run python scripts/diag_batch_e_readiness.py --check all

Defaults match the lux layout recorded in `docs/checkpoints/batch-d-result.md`
("Copied to lux"); override with `--curated-root` / `--jarrah-root` if the
data lives elsewhere.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

from wa_mine_monitor import register

DEFAULT_CURATED_ROOT = Path.home() / "data" / "wa-mine-monitor" / "curated"
DEFAULT_RAW_ROOT = Path.home() / "data" / "wa-mine-monitor" / "raw"
DEFAULT_JARRAH_ROOT = Path.home() / "data" / "jarrah-rehab"

#: The D3 run these figures are measured against (the Batch D closure record).
DEFAULT_D3_DATE = "2026-08-23"
#: `d3_protocol.MIN_FULL_SUPPORT_PX` -- the forced fallback threshold.
FULL_SUPPORT_PX = 144


def _load(curated_root: Path, d3_date: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """`(footprint_support, register, crosswalk)` for the named D3 run.

    The crosswalk is the Batch B artefact (2026-08-16); it is not re-dated by
    a D3 rerun, so the latest dated directory is taken rather than `d3_date`.
    """
    support = pd.read_parquet(curated_root / "d3-inputs" / d3_date / "footprint_support.parquet")
    register = pd.read_parquet(curated_root / "register" / d3_date / "register.parquet")
    crosswalk_dirs = sorted(
        path for path in (curated_root / "crosswalk").iterdir() if path.is_dir()
    )
    crosswalk_dir = [path for path in crosswalk_dirs if path.name[:4].isdigit()][-1]
    crosswalk = pd.read_parquet(crosswalk_dir / "crosswalk.parquet")
    return support, register, crosswalk


def replay_eligibility(
    register_df: pd.DataFrame, crosswalk_df: pd.DataFrame, support_df: pd.DataFrame
) -> pd.DataFrame:
    """What `apply-d3-threshold --forced-threshold` would produce, by calling
    the production function directly (O8: a hand-rolled join here can drift
    from `register.assign_trajectory_eligibility`'s own bucketing rules --
    see the 933-site divergence this replaced).

    All `trajectory_status` bucketing comes from
    `register.assign_trajectory_eligibility` itself, called with
    `n_star=FULL_SUPPORT_PX, criteria_passed=False, forced_threshold=True`
    (the Batch E Task 0 forced-144 entry path). Two diagnostic-only columns
    the check functions below read are appended afterwards, as pure lookups
    that cannot change any status the production call already assigned:
    `maus_id`, via the SAME deterministic dedup production uses internally
    (sort by `(site_id, maus_id)`, keep first per `site_id`), and `region`,
    mapped from `support_df` on that `maus_id`.
    """
    production = register.assign_trajectory_eligibility(
        register_df,
        crosswalk_df,
        support_df,
        n_star=FULL_SUPPORT_PX,
        criteria_passed=False,
        forced_threshold=True,
    )
    crosswalk_dedup = crosswalk_df.sort_values(
        ["site_id", "maus_id"], na_position="last", kind="stable"
    ).drop_duplicates(subset="site_id", keep="first")
    maus_id_by_site = crosswalk_dedup.set_index("site_id")["maus_id"]
    region_by_maus = support_df.drop_duplicates(subset="maus_id", keep="first").set_index(
        "maus_id"
    )["region"]

    matched_maus_id = production["site_id"].map(maus_id_by_site)
    production["maus_id"] = matched_maus_id
    production["region"] = matched_maus_id.map(region_by_maus)
    return production


def check_outside_rdc(support: pd.DataFrame) -> None:
    """Open item O1: the uncovered fraction against the right denominator."""
    print("== 1. outside-RDC exclusion (decision 2026-08-21) ==")
    total = len(support)
    computed = int(support["effective_pixel_support_px"].notna().sum())
    outside = total - computed
    print(f"Tier-1 footprints with usable Maus geometry (n_for_ceiling): {total}")
    print(f"  support computed:                                         {computed}")
    print(f"  outside every RDC polygon:                                {outside}")
    print(
        f"  fraction against the 5% ceiling:                          {100 * outside / total:.2f}%"
    )
    print(
        f"candidate footprints (support >= {FULL_SUPPORT_PX} px, >= 1 epoch year): "
        f"{int(support['candidate'].sum())}"
    )
    print(
        f"selected footprints:                                        "
        f"{int(support['selected'].sum())}"
    )
    print("NOTE: the candidate count is NOT the ceiling's denominator, and is not")
    print("      the 1,232 footprints that merely have a computed support value.")
    print()


def check_eligibility(replay: pd.DataFrame, register_current: pd.DataFrame) -> None:
    """What `apply-d3-threshold --forced-threshold` would produce."""
    print("== 2. eligibility under a forced-144 threshold ==")
    print("current register, as built with criteria_passed=false:")
    print(register_current["trajectory_status"].value_counts(dropna=False).to_string())
    print()
    judged = replay["trajectory_status"].isin(["insufficient_pixel_support", "eligible"])
    eligible = replay["trajectory_status"] == "eligible"
    insufficient = replay["trajectory_status"] == "insufficient_pixel_support"
    print(f"judged population (must equal threshold_not_computed above): {int(judged.sum())}")
    print(
        f"  would become eligible at n_star={FULL_SUPPORT_PX}:                     "
        f"{int(eligible.sum())}"
    )
    print(f"  would become insufficient_pixel_support:                   {int(insufficient.sum())}")
    print(
        f"  distinct footprints behind the eligible sites:             "
        f"{replay.loc[eligible, 'maus_id'].nunique()}"
    )
    print()
    print("eligible sites by region (D4 Tier 2 hard gate: >= 30):")
    print(replay.loc[eligible, "region"].value_counts(dropna=False).to_string())
    print()


def check_sharing(replay: pd.DataFrame, support: pd.DataFrame) -> None:
    """Footprint sharing, and the cost of a per-site read loop."""
    print("== 3. footprint sharing and read amplification ==")
    eligible = replay["trajectory_status"] == "eligible"
    per_footprint = replay.loc[eligible, "maus_id"].value_counts()
    print(f"eligible sites: {int(eligible.sum())} over {per_footprint.size} footprints")
    print(
        f"sites per footprint: mean {per_footprint.mean():.1f} "
        f"median {int(per_footprint.median())} max {int(per_footprint.max())}"
    )
    shared = per_footprint[per_footprint > 1]
    print(f"footprints carrying more than one site: {shared.size}")
    print(
        f"sites sitting on a shared footprint:    {int(shared.sum())} "
        f"({100 * shared.sum() / eligible.sum():.1f}%)"
    )
    print()
    by_maus = support.drop_duplicates(subset="maus_id", keep="first").set_index("maus_id")
    footprint_px = by_maus.loc[
        by_maus.index.isin(per_footprint.index), "effective_pixel_support_px"
    ]
    site_px = replay.loc[eligible, "maus_id"].map(by_maus["effective_pixel_support_px"])
    print("member pixels read per collection-year:")
    print(f"  loop over distinct footprints: {footprint_px.sum() / 1e6:.3f} M")
    print(f"  loop over sites (as drafted):  {site_px.sum() / 1e6:.3f} M")
    print(f"  amplification:                 {site_px.sum() / footprint_px.sum():.1f}x")
    print(
        f"eligible footprint support px: min {int(footprint_px.min())} "
        f"median {int(footprint_px.median())} max {int(footprint_px.max())}"
    )
    print()


def check_huntly(
    support: pd.DataFrame, crosswalk: pd.DataFrame, raw_root: Path, jarrah_root: Path
) -> None:
    """Is the E5 comparison constructible against the jarrah Huntly plots?"""
    print("== 4. the E5 Huntly gate ==")
    meta = pd.read_parquet(jarrah_root / "probe-out" / "detection_estimand" / "site_meta.parquet")
    series = pd.read_parquet(
        jarrah_root / "probe-out" / "detection_estimand" / "series_incumbent_w1.parquet"
    )
    print(
        f"jarrah reference: {len(series)} rows, {series['site_id'].nunique()} sites, "
        f"{int(series['year'].min())}-{int(series['year'].max())}"
    )

    sites = gpd.GeoDataFrame(
        meta,
        geometry=gpd.points_from_xy(meta["x_incumbent"], meta["y_incumbent"]),
        crs="EPSG:3577",
    )
    gpkgs = sorted((raw_root / "maus_v2").glob("*/*.gpkg"))
    maus = gpd.read_file(gpkgs[-1]).to_crs("EPSG:3577")
    joined = gpd.sjoin(sites, maus[["maus_id", "geometry"]], predicate="within", how="left")
    inside = joined[joined["maus_id"].notna()]
    print(f"jarrah plot sites inside a Maus footprint: {len(inside)} of {len(sites)}")
    print(f"distinct footprints they fall in:          {inside['maus_id'].nunique()}")

    hit = set(inside["maus_id"].astype(str))
    in_population = support[support["maus_id"].astype(str).isin(hit)]
    if not in_population.empty:
        print()
        print(
            in_population[
                [
                    "maus_id",
                    "region",
                    "commodity_group",
                    "shape_class",
                    "effective_pixel_support_px",
                    "candidate",
                    "selected",
                ]
            ].to_string(index=False)
        )
    matched = crosswalk[crosswalk["maus_id"].astype(str).isin(hit)]
    print()
    print(f"crosswalk rows onto those footprints: {len(matched)}")
    print(matched["confidence"].value_counts(dropna=False).to_string())
    print()
    print("A 3x3 (9 px) plot mean cannot be compared to a footprint mean of this")
    print("size at D13 E5's 1e-6 tolerance; see the findings document.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        choices=("all", "outside-rdc", "eligibility", "sharing", "huntly"),
        default="all",
    )
    parser.add_argument("--curated-root", type=Path, default=DEFAULT_CURATED_ROOT)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--jarrah-root", type=Path, default=DEFAULT_JARRAH_ROOT)
    parser.add_argument("--d3-date", default=DEFAULT_D3_DATE)
    args = parser.parse_args()

    support, register_current, crosswalk = _load(args.curated_root, args.d3_date)
    replay = replay_eligibility(register_current, crosswalk, support)

    if args.check in ("all", "outside-rdc"):
        check_outside_rdc(support)
    if args.check in ("all", "eligibility"):
        check_eligibility(replay, register_current)
    if args.check in ("all", "sharing"):
        check_sharing(replay, support)
    if args.check in ("all", "huntly"):
        check_huntly(support, crosswalk, args.raw_root, args.jarrah_root)


if __name__ == "__main__":
    main()
