"""E4 statewide-extraction acceptance battery (D13 E4 acceptance clauses).

Independent post-run verification of a `curated/trajectories/<date>/` tree
against its own `extraction_summary.json` and the eligible-site register.
Everything here CALLS production functions (`trajectory_extract.
existing_partitions`/`verified_parts`, `trajectories.validate_trajectories`,
`trajectory_extract.select_eligible_sites`) rather than re-implementing
their logic -- acceptance that re-derives the rules it checks would only
ever agree with itself.

Two failure classes, deliberately not conflated (the `evidence.py`
discipline):

- `TrajectoryQaError` -- the inputs are unusable (a summary missing its
  keys, a register missing its columns, a partition tree that cannot even
  be inventoried). Always raised; the CLI turns it into a structured
  refusal.
- A failed `AcceptanceCheck` -- the inputs were readable and the check ran,
  but the extraction does not hold up (a tampered part, a row-count drift,
  a shared-footprint divergence). These are REPORTED, never raised: a
  `passed: false` acceptance verdict is a result with a manifest, never a
  crash (the `validate-huntly` precedent).

**Claim boundary (verbatim).** Outputs are spectral detections only; no
causal attribution is generated here or anywhere in this project; never a
compliance or performance finding, never an operational rehabilitation
date.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from wa_mine_monitor import d3_inputs, manifests, tables, trajectories, trajectory_extract


class TrajectoryQaError(ValueError):
    """The acceptance inputs are unusable -- never a failed check."""


#: The D13 statewide extraction shape, pinned as the EXACT
#: (collection_id, year) tuples, not merely their count: 39 FC
#: year-partitions (1987-2025) + 60 geomedian collection-years, including
#: the real ls5 catalogue hole at 2001-2002. A protocol constant (like the
#: climate baseline years), not derivable from code -- a count alone
#: cannot detect a required collection-year swapped for a stray one that
#: the summary agrees with, so the acceptance CLI checks the tuple set.
_EXPECTED_STATEWIDE_YEARS: dict[str, tuple[range, ...]] = {
    "ga_ls_fc_pc_cyear_3": (range(1987, 2026),),
    "ga_ls5t_gm_cyear_3": (range(1986, 2001), range(2003, 2012)),
    "ga_ls7e_gm_cyear_3": (range(1999, 2022),),
    "ga_ls8cls9c_gm_cyear_3": (range(2013, 2026),),
}
EXPECTED_STATEWIDE_DOMAIN: frozenset[tuple[str, int]] = frozenset(
    (collection_id, year)
    for collection_id, spans in _EXPECTED_STATEWIDE_YEARS.items()
    for span in spans
    for year in span
)
EXPECTED_STATEWIDE_PARTITIONS = len(EXPECTED_STATEWIDE_DOMAIN)


@dataclass(frozen=True)
class AcceptanceCheck:
    """One named acceptance check: `detail` says what was compared and, on
    failure, exactly what diverged (naming partitions/sites, never a bare
    boolean)."""

    name: str
    passed: bool
    detail: str


@dataclass
class AcceptanceReport:
    """Every check's outcome plus the accounting counts a checkpoint needs.

    `failures` repeats the `detail` of every failed check so a reader of
    the verdict JSON sees the reasons without re-scanning `checks`.
    """

    checks: list[AcceptanceCheck] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    not_computable_by_reason: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[str]:
        return [check.detail for check in self.checks if not check.passed]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks
            ],
            "counts": dict(self.counts),
            "not_computable_by_reason": dict(self.not_computable_by_reason),
            "failures": self.failures,
        }


def expected_metric_set(collection_id: str) -> frozenset[str]:
    """The metric vocabulary a partition of `collection_id` must carry,
    derived from the SAME production constants the extractor writes from
    (never a hand-copied list that could drift)."""
    for source_id, kind in d3_inputs.D3_COLLECTION_KIND.items():
        if trajectory_extract.collection_id_for_source(source_id) == collection_id:
            if kind == "geomedian":
                return frozenset(d3_inputs.GEOMEDIAN_METRIC_BANDS)
            return frozenset(d3_inputs.FC_METRIC_ASSETS)
    raise TrajectoryQaError(f"unknown collection_id {collection_id!r}")


_SUMMARY_REQUIRED_KEYS = ("inserted", "not_computable", "partitions", "site_ids")


def accept_trajectories(
    trajectories_dir: Path,
    *,
    summary: Mapping[str, Any],
    register_df: pd.DataFrame,
    expected_partition_count: int,
    expected_domain: frozenset[tuple[str, int]] | None = None,
    maus_id_by_site: Mapping[str, str] | None = None,
) -> AcceptanceReport:
    """Run every E4 acceptance check against `trajectories_dir`.

    The caller (the `accept-trajectories` CLI) resolves and digest-verifies
    `summary` and the register before calling; this function trusts neither
    beyond shape (missing keys/columns raise `TrajectoryQaError`).
    `expected_partition_count` is the protocol's fixed partition count
    (`EXPECTED_STATEWIDE_PARTITIONS` for the live run), checked against
    BOTH the on-disk tree and the summary. The count alone cannot detect a
    required collection-year swapped for a stray one the summary agrees
    with, so `expected_domain` (`EXPECTED_STATEWIDE_DOMAIN` for the live
    run; None only for non-protocol fixture trees) pins the exact tuple
    set. `maus_id_by_site` is the Tier 1 crosswalk's per-site footprint
    assignment (None only for fixture trees without a crosswalk): the
    independent lineage anchor for every row's `maus_id`, without which
    the L17 check could only compare the trajectory product against
    itself.
    """
    missing_keys = [k for k in _SUMMARY_REQUIRED_KEYS if k not in summary]
    if missing_keys:
        raise TrajectoryQaError(f"extraction summary is missing key(s): {missing_keys}")
    try:
        eligible = trajectory_extract.select_eligible_sites(register_df)
    except trajectory_extract.TrajectoryExtractError as exc:
        raise TrajectoryQaError(str(exc)) from exc
    if "d3_forced_threshold" not in register_df.columns:
        raise TrajectoryQaError(
            "register frame is missing 'd3_forced_threshold' -- run apply-d3-threshold first"
        )

    report = AcceptanceReport()
    try:
        on_disk = set(trajectory_extract.existing_partitions(Path(trajectories_dir)))
    except trajectory_extract.TrajectoryExtractError as exc:
        raise TrajectoryQaError(str(exc)) from exc
    claimed = {(str(p["collection_id"]), int(p["year"])) for p in summary["partitions"]}
    if on_disk == claimed:
        report.checks.append(
            AcceptanceCheck(
                "partition_inventory",
                True,
                f"{len(on_disk)} partition(s) on disk exactly match the summary's claim",
            )
        )
    else:
        report.checks.append(
            AcceptanceCheck(
                "partition_inventory",
                False,
                (
                    f"on-disk partitions differ from the summary: only on disk "
                    f"{sorted(on_disk - claimed)}, only in summary {sorted(claimed - on_disk)}"
                ),
            )
        )
    report.checks.append(
        AcceptanceCheck(
            "partition_count",
            len(on_disk) == expected_partition_count
            and len(summary["partitions"]) == expected_partition_count,
            (
                f"{len(on_disk)} partition(s) on disk, {len(summary['partitions'])} in the "
                f"summary; the protocol expects exactly {expected_partition_count}"
            ),
        )
    )
    if expected_domain is not None:
        missing_tuples = sorted(set(expected_domain) - on_disk)
        stray_tuples = sorted(on_disk - set(expected_domain))
        report.checks.append(
            AcceptanceCheck(
                "partition_domain",
                not missing_tuples and not stray_tuples,
                (
                    "on-disk partitions exactly match the protocol's pinned "
                    f"(collection_id, year) domain of {len(expected_domain)} tuple(s)"
                    if not missing_tuples and not stray_tuples
                    else (
                        f"domain mismatch: missing from disk {missing_tuples}, "
                        f"stray on disk {stray_tuples}"
                    )
                ),
            )
        )
    eligible_set = set(eligible)
    # The forced-threshold checks are defined against the ELIGIBLE set: the
    # live register carries null d3_forced_threshold on ineligible rows
    # (no_usable_footprint, crosswalk_not_high_confidence), which never enter
    # extraction. A null on an ELIGIBLE row means the lineage cannot be
    # adjudicated at all -- an unusable input, not a failed check.
    eligible_rows = register_df[register_df["site_id"].astype(str).isin(eligible_set)]
    if eligible_rows["d3_forced_threshold"].isna().any():
        null_sites = sorted(
            eligible_rows.loc[eligible_rows["d3_forced_threshold"].isna(), "site_id"].astype(str)
        )
        raise TrajectoryQaError(
            "register carries null d3_forced_threshold on eligible site(s) "
            f"{null_sites[:5]} ({len(null_sites)} total) -- run apply-d3-threshold first"
        )
    forced_by_site = dict(
        zip(
            eligible_rows["site_id"].astype(str),
            eligible_rows["d3_forced_threshold"].astype(bool),
            strict=True,
        )
    )

    unverifiable: list[str] = []
    contract_violations: list[str] = []
    row_count_offenders: list[str] = []
    site_set_offenders: list[str] = []
    metric_set_offenders: list[str] = []
    key_column_offenders: list[str] = []
    forced_offenders: list[str] = []
    maus_offenders: list[str] = []
    shared_footprint_offenders: list[str] = []

    total_rows = 0
    total_not_computable = 0
    forced_true_rows = 0
    all_sites: set[str] = set()
    reason_counts: dict[str, int] = {}

    for collection_id, year in sorted(on_disk):
        partition = trajectory_extract.partition_dir(Path(trajectories_dir), collection_id, year)
        label = f"{collection_id}/year={year}"
        try:
            parts = trajectory_extract.verified_parts(
                partition, expected_schema=trajectories.TRAJECTORY_SCHEMA
            )
        except trajectory_extract.TrajectoryExtractError as exc:
            unverifiable.append(f"{label}: {exc}")
            continue
        frame = pd.concat([tables.read_table(p) for p in parts], ignore_index=True)
        try:
            trajectories.validate_trajectories(frame)
        except trajectories.TrajectoryError as exc:
            contract_violations.append(f"{label}: {exc}")
            continue

        metrics = expected_metric_set(collection_id)
        expected_rows = len(eligible_set) * len(metrics)
        if len(frame) != expected_rows:
            row_count_offenders.append(
                f"{label}: {len(frame)} rows, expected {len(eligible_set)} sites x "
                f"{len(metrics)} metrics = {expected_rows}"
            )
        partition_sites = set(frame["site_id"].astype(str))
        if partition_sites != eligible_set:
            missing = sorted(eligible_set - partition_sites)[:5]
            extra = sorted(partition_sites - eligible_set)[:5]
            site_set_offenders.append(
                f"{label}: site set diverges from the eligible register "
                f"(missing e.g. {missing}, extra e.g. {extra})"
            )
        if set(frame["metric"]) != metrics:
            metric_set_offenders.append(
                f"{label}: metric set {sorted(set(frame['metric']))} != {sorted(metrics)}"
            )
        if not ((frame["year"] == year).all() and (frame["collection_id"] == collection_id).all()):
            key_column_offenders.append(
                f"{label}: rows carry a year/collection_id that differs from the partition path"
            )
        mapped_forced = frame["site_id"].astype(str).map(forced_by_site)
        forced_mismatch = frame.loc[
            mapped_forced.isna()
            | (
                mapped_forced.fillna(False).astype(bool)
                != frame["d3_forced_threshold"].astype(bool)
            ),
            "site_id",
        ]
        if len(forced_mismatch):
            forced_offenders.append(
                f"{label}: d3_forced_threshold diverges from the register for site(s) "
                f"{sorted(set(forced_mismatch.astype(str)))[:5]}"
            )

        # The crosswalk anchor: every row's maus_id must match the Tier 1
        # crosswalk's per-site assignment. Without this, the L17 check
        # below only compares the trajectory product against itself -- a
        # SELF-CONSISTENT wrong footprint assignment (site moved onto
        # another maus_id, values and sharing counts moved with it) would
        # pass every group-internal comparison.
        if maus_id_by_site is not None:
            mapped_maus = frame["site_id"].astype(str).map(maus_id_by_site)
            maus_mismatch = frame.loc[
                mapped_maus.isna() | (mapped_maus != frame["maus_id"].astype(str)),
                "site_id",
            ]
            if len(maus_mismatch):
                maus_offenders.append(
                    f"{label}: maus_id diverges from the Tier 1 crosswalk for site(s) "
                    f"{sorted(set(maus_mismatch.astype(str)))[:5]}"
                )

        # L17, exhaustively (never sampled): a (maus_id, metric) group in
        # one partition is every site sharing that footprint. Values must be
        # byte-identical (all equal, or all NaN with one identical reason),
        # and shared_footprint_site_count must be constant AND equal the
        # group's own distinct-site count.
        grouped = frame.groupby(["maus_id", "metric"], sort=False)
        for (maus_id, metric), group in grouped:
            if group["value"].nunique(dropna=False) > 1:
                shared_footprint_offenders.append(
                    f"{label}: ({maus_id}, {metric}) carries diverging values across "
                    "sites sharing the footprint"
                )
                continue
            if group["not_computable_reason"].nunique(dropna=False) > 1:
                shared_footprint_offenders.append(
                    f"{label}: ({maus_id}, {metric}) carries diverging "
                    "not_computable_reason across sites sharing the footprint"
                )
                continue
            counts = set(group["shared_footprint_site_count"].astype(int))
            n_group_sites = group["site_id"].nunique()
            if counts != {n_group_sites}:
                shared_footprint_offenders.append(
                    f"{label}: ({maus_id}, {metric}) records "
                    f"shared_footprint_site_count {sorted(counts)} but holds "
                    f"{n_group_sites} distinct site(s)"
                )

        total_rows += len(frame)
        not_computable = ~frame["computable"].astype(bool)
        total_not_computable += int(not_computable.sum())
        forced_true_rows += int(frame["d3_forced_threshold"].astype(bool).sum())
        all_sites.update(partition_sites)
        for reason, count in (
            frame.loc[not_computable, "not_computable_reason"].value_counts().items()
        ):
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + int(count)

    def aggregate(name: str, offenders: list[str], ok_detail: str) -> None:
        if offenders:
            report.checks.append(AcceptanceCheck(name, False, "; ".join(offenders)))
        else:
            report.checks.append(AcceptanceCheck(name, True, ok_detail))

    aggregate(
        "parts_digest_and_schema",
        unverifiable,
        "every part digest-verified against its own manifest and footer-schema-verified",
    )
    aggregate(
        "row_contract", contract_violations, "validate_trajectories passed on every partition"
    )
    aggregate(
        "partition_row_counts",
        row_count_offenders,
        "every partition holds exactly (eligible sites x metric count) rows",
    )
    aggregate(
        "partition_site_sets",
        site_set_offenders,
        "every partition's site set equals the register's eligible set",
    )
    aggregate(
        "partition_metric_sets",
        metric_set_offenders,
        "every partition carries exactly its collection's metric vocabulary",
    )
    aggregate(
        "partition_key_columns",
        key_column_offenders,
        "every row's year/collection_id matches its partition path",
    )
    aggregate(
        "shared_footprint_consistency",
        shared_footprint_offenders,
        "every shared-footprint group is byte-identical with a correct site count",
    )
    if maus_id_by_site is not None:
        aggregate(
            "maus_crosswalk_consistency",
            maus_offenders,
            "every row's maus_id matches the Tier 1 crosswalk's per-site assignment",
        )
    aggregate(
        "forced_threshold_register_consistency",
        forced_offenders,
        "every row's d3_forced_threshold matches the register's per-site value",
    )
    report.checks.append(
        AcceptanceCheck(
            "forced_threshold_all_true",
            forced_true_rows == total_rows,
            (
                f"{forced_true_rows} of {total_rows} rows carry d3_forced_threshold=true; "
                "L4 forces the D3 threshold on every statewide row"
            ),
        )
    )

    report.checks.append(
        AcceptanceCheck(
            "total_rows_match_summary",
            total_rows == int(summary["inserted"]),
            f"partition rows sum to {total_rows}; summary claims {summary['inserted']}",
        )
    )
    report.checks.append(
        AcceptanceCheck(
            "not_computable_matches_summary",
            total_not_computable == int(summary["not_computable"]),
            (
                f"not-computable rows sum to {total_not_computable}; summary claims "
                f"{summary['not_computable']}"
            ),
        )
    )
    report.checks.append(
        AcceptanceCheck(
            "summary_site_ids_match_register",
            sorted(str(s) for s in summary["site_ids"]) == sorted(all_sites),
            (
                f"summary names {len(summary['site_ids'])} site(s); the partitions actually "
                f"hold {len(all_sites)} distinct site(s)"
            ),
        )
    )

    report.counts.update(
        {
            "n_partitions": len(on_disk),
            "rows": total_rows,
            "not_computable_rows": total_not_computable,
            "n_sites": len(all_sites),
            "n_forced_threshold_true_rows": forced_true_rows,
        }
    )
    report.not_computable_by_reason = dict(sorted(reason_counts.items()))
    return report


def parts_digest(trajectories_dir: Path) -> str:
    """One digest binding an acceptance verdict to the exact part bytes it
    accepted: sha256 over the sorted list of every verified part's own
    manifest digest. `build-context-join` recomputes this and refuses a
    verdict whose digest no longer matches -- a part replaced after
    acceptance (even with a freshly written, self-consistent sidecar)
    changes this digest and invalidates the verdict."""
    lines: list[str] = []
    try:
        partitions = sorted(trajectory_extract.existing_partitions(Path(trajectories_dir)))
        for collection_id, year in partitions:
            partition = trajectory_extract.partition_dir(
                Path(trajectories_dir), collection_id, year
            )
            for part in trajectory_extract.verified_parts(partition):
                manifest = json.loads(
                    Path(str(part) + manifests.MANIFEST_SUFFIX).read_text(encoding="utf-8")
                )
                lines.append(f"{collection_id}/{year}/{part.name}:{manifest['output']['sha256']}")
    except trajectory_extract.TrajectoryExtractError as exc:
        raise TrajectoryQaError(str(exc)) from exc
    return hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()
