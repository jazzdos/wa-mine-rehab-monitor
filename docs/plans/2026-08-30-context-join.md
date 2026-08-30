# E4 Acceptance + F6 Context Join Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Independently accept the 2026-08-29 statewide trajectory extraction
(E4) with a machine-written verdict, then build the F6 site-year context-join
product that puts fire and climate context beside trajectories — gated on that
verdict.

**Architecture:** Two new pure modules (`trajectory_qa.py`,
`context_join.py`) plus two new typer commands in `cli.py`
(`accept-trajectories`, `build-context-join`) that follow the house shapes
exactly: `validate-huntly` for the verdict-writing command (a failed
acceptance is a RESULT with a manifest, never a crash) and
`build-climate-context` for the gated builder (numbered gates, digest-verified
inputs, structured JSON refusals, refuse-if-exists before work). The O8 rule
governs the QA module: it calls production functions
(`trajectory_extract.existing_partitions`/`verified_parts`,
`trajectories.validate_trajectories`, `trajectory_extract.select_eligible_sites`)
and never re-implements their logic. Design doc:
`docs/plans/2026-08-30-context-join-design.md`.

**Tech Stack:** Python 3.12, uv, pandas, pyarrow, typer. Verification
battery (run from the worktree root): `uv run ruff check src tests`,
`uv run ruff format --check src tests`, `uv run mypy src scripts`,
`uv run pytest -q -rs`.

---

## Context the implementer must know

- **Live figures (2026-08-29 lineage, referenced in tests only as synthetic
  analogues — tests NEVER touch the real data root):** trajectories:
  2,458,164 rows over 99 partitions, `not_computable` 94,343; fire context
  404,508 rows (10,372 sites × 1987–2025); climate context 404,508 rows.
- **Partition layout:** `curated/trajectories/<date>/collection_id=<id>/year=<y>/part-NNNN.parquet`
  with a `.run_manifest.json` sidecar per part, and top-level
  `extraction_summary.json` (+ its own run-manifest sidecar). Summary keys:
  `date, existing, inserted, not_computable, partitions (list of
  {collection_id, path, year}), protocol_digest, refused_empty, scope,
  site_ids (sorted list)`.
- **Metric vocabulary:** geomedian collections (`ga_ls5t_gm_cyear_3`,
  `ga_ls7e_gm_cyear_3`, `ga_ls8cls9c_gm_cyear_3`) carry metrics
  `('nbr', 'ndmi')`; the FC collection (`ga_ls_fc_pc_cyear_3`) carries
  `('bare_soil', 'photosynthetic_vegetation', 'non_photosynthetic_vegetation')`.
  Derive this mapping from production constants
  (`d3_inputs.D3_COLLECTION_KIND`, `d3_inputs.GEOMEDIAN_METRIC_BANDS`,
  `d3_inputs.FC_METRIC_ASSETS`,
  `trajectory_extract.collection_id_for_source`) — never hard-code it.
- **House CLI conventions (copy, don't improvise):** module-level typer
  Option singletons (B008); `_load_config_or_exit`;
  `_collect_git_state_disclosing_gaps(_REPO_ROOT)`;
  `_refuse_if_curated_output_already_exists` before any read/write;
  `_latest_curated_dated_dir`; `_digest_verified_manifest`;
  `_write_table_or_refuse`; every refusal is
  `typer.echo(json.dumps({"refusal": ...}, indent=2, sort_keys=True))` +
  `raise typer.Exit(1)` (` from None` when inside an except).
- **Test conventions:** `runner = CliRunner()`; config fixture writes
  `run:\n  data_root: "<tmp>"\n  redistribute_public: false\n`
  (`tests/test_climate_context.py::_write_config`); curated inputs are
  seeded with `tables.write_table` + `manifests.write_run_manifest(output=...,
  inputs=[SourceAsset(uri="test://fixture", sha256=None)], config={"run":
  {"data_root": str(data_root)}}, git_state={"sha": "testsha", "dirty":
  False, "diff": ""})`; verified trajectory parts are built exactly like
  `tests/test_trajectory_extract.py::_write_verified_part`; CLI tests
  monkeypatch `cli.collect_git_state`? No — they pass real
  `_collect_git_state_disclosing_gaps`, which degrades gracefully; the
  climate-context tests run it as-is. Mirror `tests/test_climate_context.py`.
- **Claim boundary (verbatim, goes in every new module docstring):** outputs
  are spectral detections and context rows only; no causal attribution is
  generated here or anywhere in this project; never a compliance or
  performance finding.
- Line length ≤ 100 (ruff). All new code needs `from __future__ import
  annotations`. mypy is strict enough to need full annotations on public
  functions.

---

### Task 1: `trajectory_qa.py` — report types, metric-set mapping, partition inventory

**Files:**
- Create: `src/wa_mine_monitor/trajectory_qa.py`
- Create: `tests/test_trajectory_qa.py`

**Step 1: Write the failing tests**

Create `tests/test_trajectory_qa.py`:

```python
"""Tests for the E4 acceptance battery (trajectory_qa).

Fixtures forge tiny trajectories trees with the SAME production writers the
extractor uses (`trajectories.write_trajectories` + a real
`manifests.write_run_manifest` sidecar), so the QA module is exercised
against artefacts byte-shaped like the real thing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from wa_mine_monitor import manifests, trajectories, trajectory_extract, trajectory_qa
from wa_mine_monitor.provenance import SourceAsset

GM_COLLECTION = "ga_ls5t_gm_cyear_3"
FC_COLLECTION = "ga_ls_fc_pc_cyear_3"


def test_expected_metric_set_derives_from_production_constants() -> None:
    assert trajectory_qa.expected_metric_set(GM_COLLECTION) == frozenset({"nbr", "ndmi"})
    assert trajectory_qa.expected_metric_set(FC_COLLECTION) == frozenset(
        {"bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"}
    )
    with pytest.raises(trajectory_qa.TrajectoryQaError):
        trajectory_qa.expected_metric_set("ga_not_a_collection")


def _trajectory_rows(
    *,
    sites_maus: list[tuple[str, str]],
    year: int,
    collection_id: str,
    metrics: list[str],
    forced: bool = True,
    value: float | None = 0.5,
    reason: str | None = None,
) -> pd.DataFrame:
    shared: dict[str, int] = {}
    for _s, m in sites_maus:
        shared[m] = shared.get(m, 0) + 1
    rows = []
    for site_id, maus_id in sites_maus:
        for metric in metrics:
            rows.append(
                {
                    "site_id": site_id,
                    "maus_id": maus_id,
                    "year": year,
                    "metric": metric,
                    "value": value,
                    "sensor": "ls5t" if collection_id == GM_COLLECTION else None,
                    "collection_id": collection_id,
                    "item_id": f"{collection_id}-x-{year}",
                    "product_version": "4.0.0",
                    "geomad_count": 5 if collection_id == GM_COLLECTION else None,
                    "n_member_pixels": 10,
                    "n_valid_pixels": 9,
                    "effective_pixel_support_px": 9,
                    "computable": value is not None,
                    "not_computable_reason": reason,
                    "value_out_of_documented_range": 0,
                    "transition_adjacent": False,
                    "shared_footprint_site_count": shared[maus_id],
                    "d3_forced_threshold": forced,
                    "source_snapshot_date": "2026-08-29",
                    "geometry": b"\x01\x02",
                }
            )
    return pd.DataFrame(rows)


def _write_partition(root: Path, collection_id: str, year: int, df: pd.DataFrame) -> Path:
    partition = trajectory_extract.partition_dir(root, collection_id, year)
    partition.mkdir(parents=True, exist_ok=True)
    path = partition / trajectory_extract.PART_FILENAME_TEMPLATE.format(version=0)
    trajectories.write_trajectories(df, path)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    return path


def _summary_for(root: Path, partitions: list[tuple[str, int]], **totals: object) -> dict:
    return {
        "date": "2026-08-29",
        "scope": "statewide",
        "existing": 0,
        "refused_empty": 0,
        "protocol_digest": "0" * 64,
        "partitions": [
            {
                "collection_id": c,
                "year": y,
                "path": str(trajectory_extract.partition_dir(root, c, y) / "part-0000.parquet"),
            }
            for c, y in partitions
        ],
        **totals,
    }


def _register_frame(sites_forced: list[tuple[str, bool]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "site_id": [s for s, _f in sites_forced],
            "trajectory_status": ["eligible"] * len(sites_forced),
            "d3_forced_threshold": [f for _s, f in sites_forced],
        }
    )


def test_partition_inventory_mismatch_is_a_reported_failure(tmp_path: Path) -> None:
    df = _trajectory_rows(
        sites_maus=[("S1", "M1")], year=2000, collection_id=GM_COLLECTION, metrics=["nbr", "ndmi"]
    )
    _write_partition(tmp_path, GM_COLLECTION, 2000, df)
    # Summary claims a partition that is not on disk.
    summary = _summary_for(
        tmp_path,
        [(GM_COLLECTION, 2000), (GM_COLLECTION, 2001)],
        inserted=2,
        not_computable=0,
        site_ids=["S1"],
    )
    report = trajectory_qa.accept_trajectories(
        tmp_path,
        summary=summary,
        register_df=_register_frame([("S1", True)]),
        expected_partition_count=2,
    )
    assert report.passed is False
    assert any("partition_inventory" == c.name and not c.passed for c in report.checks)


def test_partition_count_below_protocol_expectation_fails(tmp_path: Path) -> None:
    # On-disk and summary AGREE (1 partition each) but the protocol
    # expects 2 -- agreement with its own summary must not be enough.
    df = _trajectory_rows(
        sites_maus=[("S1", "M1")], year=2000, collection_id=GM_COLLECTION, metrics=["nbr", "ndmi"]
    )
    _write_partition(tmp_path, GM_COLLECTION, 2000, df)
    summary = _summary_for(
        tmp_path, [(GM_COLLECTION, 2000)], inserted=2, not_computable=0, site_ids=["S1"]
    )
    report = trajectory_qa.accept_trajectories(
        tmp_path,
        summary=summary,
        register_df=_register_frame([("S1", True)]),
        expected_partition_count=2,
    )
    assert report.passed is False
    assert any(c.name == "partition_count" and not c.passed for c in report.checks)
    assert any(c.name == "partition_inventory" and c.passed for c in report.checks)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trajectory_qa.py -q`
Expected: FAIL / collection error with `ModuleNotFoundError` or
`AttributeError` (module does not exist yet).

**Step 3: Write the module skeleton**

Create `src/wa_mine_monitor/trajectory_qa.py`:

```python
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

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from wa_mine_monitor import d3_inputs, trajectories, trajectory_extract


class TrajectoryQaError(ValueError):
    """The acceptance inputs are unusable -- never a failed check."""


#: The D13 statewide extraction shape: 39 FC year-partitions + 60 geomedian
#: collection-year partitions. A protocol constant (like the climate
#: baseline years), not derivable from code -- the acceptance CLI defaults
#: to it so an extraction that silently dropped a whole collection-year
#: cannot pass merely by agreeing with its own summary.
EXPECTED_STATEWIDE_PARTITIONS = 99


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
) -> AcceptanceReport:
    """Run every E4 acceptance check against `trajectories_dir`.

    The caller (the `accept-trajectories` CLI) resolves and digest-verifies
    `summary` and the register before calling; this function trusts neither
    beyond shape (missing keys/columns raise `TrajectoryQaError`).
    `expected_partition_count` is the protocol's fixed partition count
    (`EXPECTED_STATEWIDE_PARTITIONS` for the live run) -- checked against
    BOTH the on-disk tree and the summary, because they can agree with each
    other while both missing a collection-year.
    """
    missing_keys = [k for k in _SUMMARY_REQUIRED_KEYS if k not in summary]
    if missing_keys:
        raise TrajectoryQaError(f"extraction summary is missing key(s): {missing_keys}")
    eligible = trajectory_extract.select_eligible_sites(register_df)
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
    report.counts["n_partitions"] = len(on_disk)
    return report
```

**Step 4: Run the tests**

Run: `uv run pytest tests/test_trajectory_qa.py -q`
Expected: PASS (2 tests).

---

### Task 2: `trajectory_qa.py` — per-partition checks and accounting

**Files:**
- Modify: `src/wa_mine_monitor/trajectory_qa.py` (extend `accept_trajectories`)
- Test: `tests/test_trajectory_qa.py`

**Step 1: Write the failing tests**

Append to `tests/test_trajectory_qa.py`:

```python
def _good_world(tmp_path: Path) -> tuple[Path, dict, pd.DataFrame]:
    """Two partitions (one GM year, one FC year), two sites sharing one
    footprint plus one solo site -- the smallest tree that exercises the
    shared-footprint (L17), metric-set and accounting checks at once."""
    sites_maus = [("S1", "M1"), ("S2", "M1"), ("S3", "M2")]
    gm = _trajectory_rows(
        sites_maus=sites_maus, year=2000, collection_id=GM_COLLECTION, metrics=["nbr", "ndmi"]
    )
    fc = _trajectory_rows(
        sites_maus=sites_maus,
        year=2001,
        collection_id=FC_COLLECTION,
        metrics=["bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"],
    )
    fc.loc[fc["site_id"] == "S3", ["value"]] = None
    fc.loc[fc["site_id"] == "S3", "computable"] = False
    fc.loc[fc["site_id"] == "S3", "not_computable_reason"] = "zero_valid_pixels"
    _write_partition(tmp_path, GM_COLLECTION, 2000, gm)
    _write_partition(tmp_path, FC_COLLECTION, 2001, fc)
    summary = _summary_for(
        tmp_path,
        [(GM_COLLECTION, 2000), (FC_COLLECTION, 2001)],
        inserted=15,
        not_computable=3,
        site_ids=["S1", "S2", "S3"],
    )
    register = _register_frame([("S1", True), ("S2", True), ("S3", True)])
    return tmp_path, summary, register


def test_good_tree_passes_with_full_accounting(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is True, report.failures
    assert report.counts["rows"] == 15
    assert report.counts["not_computable_rows"] == 3
    assert report.counts["n_sites"] == 3
    assert report.counts["n_forced_threshold_true_rows"] == 15
    assert report.not_computable_by_reason == {"zero_valid_pixels": 3}


def test_tampered_part_is_a_written_failure_not_a_crash(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    part = next(root.glob("collection_id=*/year=*/part-0000.parquet"))
    part.write_bytes(part.read_bytes() + b"tamper")
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is False
    assert any(c.name == "parts_digest_and_schema" and not c.passed for c in report.checks)


def test_row_count_drift_fails_totals(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    summary["inserted"] = 999
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is False
    assert any(c.name == "total_rows_match_summary" and not c.passed for c in report.checks)


def test_missing_site_in_a_partition_fails_site_sets(tmp_path: Path) -> None:
    sites_maus = [("S1", "M1"), ("S2", "M2")]
    gm = _trajectory_rows(
        sites_maus=sites_maus, year=2000, collection_id=GM_COLLECTION, metrics=["nbr", "ndmi"]
    )
    _write_partition(tmp_path, GM_COLLECTION, 2000, gm)
    summary = _summary_for(
        tmp_path, [(GM_COLLECTION, 2000)], inserted=4, not_computable=0,
        site_ids=["S1", "S2", "S3"],
    )
    register = _register_frame([("S1", True), ("S2", True), ("S3", True)])
    report = trajectory_qa.accept_trajectories(
        tmp_path, summary=summary, register_df=register, expected_partition_count=1
    )
    assert report.passed is False
    failed = {c.name for c in report.checks if not c.passed}
    assert "partition_site_sets" in failed
    assert "summary_site_ids_match_register" in failed


def test_forced_threshold_divergence_from_register_fails(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    register.loc[register["site_id"] == "S1", "d3_forced_threshold"] = False
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is False
    assert any(
        c.name == "forced_threshold_register_consistency" and not c.passed for c in report.checks
    )


def test_non_forced_lineage_fails_even_when_register_agrees(tmp_path: Path) -> None:
    # Rows and register CONSISTENTLY say forced=False -- consistency alone
    # must not accept it: L4 requires the forced threshold on every
    # statewide row (design: d3_forced_threshold true everywhere).
    sites_maus = [("S1", "M1")]
    gm = _trajectory_rows(
        sites_maus=sites_maus,
        year=2000,
        collection_id=GM_COLLECTION,
        metrics=["nbr", "ndmi"],
        forced=False,
    )
    _write_partition(tmp_path, GM_COLLECTION, 2000, gm)
    summary = _summary_for(
        tmp_path, [(GM_COLLECTION, 2000)], inserted=2, not_computable=0, site_ids=["S1"]
    )
    report = trajectory_qa.accept_trajectories(
        tmp_path,
        summary=summary,
        register_df=_register_frame([("S1", False)]),
        expected_partition_count=1,
    )
    assert report.passed is False
    failed = {c.name for c in report.checks if not c.passed}
    assert "forced_threshold_all_true" in failed
    assert "forced_threshold_register_consistency" not in failed
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trajectory_qa.py -q`
Expected: the new tests FAIL (checks not implemented; counts missing).

**Step 3: Implement the per-partition loop**

In `accept_trajectories`, after the inventory check, replace the trailing
`report.counts[...]`/`return` with:

```python
    forced_by_site = dict(
        zip(
            register_df["site_id"].astype(str),
            register_df["d3_forced_threshold"].astype(bool),
            strict=True,
        )
    )
    eligible_set = set(eligible)

    unverifiable: list[str] = []
    contract_violations: list[str] = []
    row_count_offenders: list[str] = []
    site_set_offenders: list[str] = []
    metric_set_offenders: list[str] = []
    key_column_offenders: list[str] = []
    forced_offenders: list[str] = []

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
        if not (
            (frame["year"] == year).all() and (frame["collection_id"] == collection_id).all()
        ):
            key_column_offenders.append(
                f"{label}: rows carry a year/collection_id that differs from the partition path"
            )
        forced_mismatch = frame.loc[
            frame["site_id"].astype(str).map(forced_by_site).astype(bool)
            != frame["d3_forced_threshold"].astype(bool),
            "site_id",
        ]
        if len(forced_mismatch):
            forced_offenders.append(
                f"{label}: d3_forced_threshold diverges from the register for site(s) "
                f"{sorted(set(forced_mismatch.astype(str)))[:5]}"
            )

        total_rows += len(frame)
        not_computable = ~frame["computable"].astype(bool)
        total_not_computable += int(not_computable.sum())
        forced_true_rows += int(frame["d3_forced_threshold"].astype(bool).sum())
        all_sites.update(partition_sites)
        for reason, count in frame.loc[not_computable, "not_computable_reason"].value_counts().items():
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
    aggregate("row_contract", contract_violations, "validate_trajectories passed on every partition")
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
            sorted(str(s) for s in summary["site_ids"]) == eligible,
            (
                f"summary names {len(summary['site_ids'])} site(s); register's eligible set "
                f"holds {len(eligible)}"
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
```

Add `tables` to the module imports:
`from wa_mine_monitor import d3_inputs, tables, trajectories, trajectory_extract`.

**Step 4: Run the tests**

Run: `uv run pytest tests/test_trajectory_qa.py -q`
Expected: PASS.

---

### Task 3: `trajectory_qa.py` — exhaustive shared-footprint check (L17)

**Files:**
- Modify: `src/wa_mine_monitor/trajectory_qa.py`
- Test: `tests/test_trajectory_qa.py`

**Step 1: Write the failing tests**

Append to `tests/test_trajectory_qa.py`:

```python
def test_shared_footprint_value_divergence_fails_l17(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    partition = trajectory_extract.partition_dir(root, GM_COLLECTION, 2000)
    part = partition / "part-0000.parquet"
    df = pd.read_parquet(part)
    # S1 and S2 share M1 -- give S2 a different nbr value than S1.
    df.loc[(df["site_id"] == "S2") & (df["metric"] == "nbr"), "value"] = 0.9
    trajectories.write_trajectories(df, part)
    manifest_path = Path(str(part) + manifests.MANIFEST_SUFFIX)
    manifest_path.unlink()
    manifests.write_run_manifest(
        output=part,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is False
    assert any(c.name == "shared_footprint_consistency" and not c.passed for c in report.checks)


def test_shared_footprint_count_divergence_fails_l17(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    partition = trajectory_extract.partition_dir(root, GM_COLLECTION, 2000)
    part = partition / "part-0000.parquet"
    df = pd.read_parquet(part)
    df["shared_footprint_site_count"] = 7
    trajectories.write_trajectories(df, part)
    manifest_path = Path(str(part) + manifests.MANIFEST_SUFFIX)
    manifest_path.unlink()
    manifests.write_run_manifest(
        output=part,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is False
    assert any(c.name == "shared_footprint_consistency" and not c.passed for c in report.checks)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trajectory_qa.py -q`
Expected: the two new tests FAIL (no `shared_footprint_consistency` check).

**Step 3: Implement the L17 check**

Inside the per-partition loop of `accept_trajectories` (after the forced-
threshold block, before the totals accumulation), add an offender list
`shared_footprint_offenders: list[str] = []` (declared with the others) and:

```python
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
```

and after the loop, with the other aggregates:

```python
    aggregate(
        "shared_footprint_consistency",
        shared_footprint_offenders,
        "every shared-footprint group is byte-identical with a correct site count",
    )
```

**Step 4: Run the tests**

Run: `uv run pytest tests/test_trajectory_qa.py -q`
Expected: PASS (all trajectory_qa tests).

---

### Task 4: `accept-trajectories` CLI command

**Files:**
- Modify: `src/wa_mine_monitor/cli.py` (insert the command immediately before
  `def _yaml_marked_error_detail`, cli.py:8410; add `trajectory_qa` to the
  `from wa_mine_monitor import ...` block near the top)
- Test: `tests/test_trajectory_qa.py`

**Step 1: Write the failing tests**

Append to `tests/test_trajectory_qa.py`:

```python
from typer.testing import CliRunner

from wa_mine_monitor import tables
from wa_mine_monitor import register as register_mod
from wa_mine_monitor.cli import app

runner = CliRunner()


def _write_config(tmp_path: Path, data_root: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f'run:\n  data_root: "{data_root}"\n  redistribute_public: false\n')
    return cfg


def _seed_register(data_root: Path, date_str: str, sites_forced: list[tuple[str, bool]]) -> None:
    output_dir = data_root / "curated" / "register" / date_str
    output_dir.mkdir(parents=True)
    rows = []
    for site_id, forced in sites_forced:
        rows.append(
            {
                "site_id": site_id,
                "site_name": site_id,
                "commodity": "GOLD",
                "stage": "x",
                "owners_at_snapshot": "o",
                "snapshot_date": "2026-08-15",
                "lon": 116.0,
                "lat": -32.0,
                "n_tenements_intersecting": 1,
                "inclusion_status": "included",
                "n_dea_gm_ls5t_epochs": 1,
                "n_dea_gm_ls7e_epochs": 1,
                "n_dea_gm_ls8cls9c_epochs": 1,
                "n_dea_fc_pc_epochs": 1,
                "effective_pixel_support_px": 200,
                "d3_threshold_px": 144,
                "d3_eligible": True,
                "trajectory_status": "eligible",
                "d3_forced_threshold": forced,
            }
        )
    df = pd.DataFrame(rows)[list(register_mod.ELIGIBLE_REGISTER_SCHEMA.names)]
    path = output_dir / "register.parquet"
    tables.write_table(df, path, register_mod.ELIGIBLE_REGISTER_SCHEMA)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )


def _seed_trajectories(data_root: Path, date_str: str, tmp_path: Path) -> dict:
    """Seed a good two-partition trajectories tree under the data root and
    write its digest-manifested extraction summary."""
    root = data_root / "curated" / "trajectories" / date_str
    root.mkdir(parents=True)
    _root, summary, _register = _good_world(root)
    summary_path = root / "extraction_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
    manifests.write_run_manifest(
        output=summary_path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    return summary


def test_accept_trajectories_cli_writes_a_passing_verdict(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cfg = _write_config(tmp_path, data_root)
    _seed_trajectories(data_root, "2026-08-29", tmp_path)
    _seed_register(data_root, "2026-08-29", [("S1", True), ("S2", True), ("S3", True)])
    result = runner.invoke(
        app, ["accept-trajectories", "--config", str(cfg), "--date", "2026-08-30",
         "--expected-partitions", "2"]
    )
    assert result.exit_code == 0, result.output
    verdict_path = (
        data_root / "curated" / "trajectories-acceptance" / "2026-08-30" / "acceptance.json"
    )
    verdict = json.loads(verdict_path.read_text())
    assert verdict["passed"] is True
    assert verdict["counts"]["rows"] == 15
    assert verdict["extraction_summary_sha256"]
    assert len(verdict["parts_digest"]) == 64  # binds the verdict to the part bytes
    assert Path(str(verdict_path) + manifests.MANIFEST_SUFFIX).exists()


def test_accept_trajectories_cli_writes_a_failing_verdict_not_a_crash(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cfg = _write_config(tmp_path, data_root)
    summary = _seed_trajectories(data_root, "2026-08-29", tmp_path)
    # Register disagrees with the tree: an extra eligible site.
    _seed_register(
        data_root, "2026-08-29", [("S1", True), ("S2", True), ("S3", True), ("S9", True)]
    )
    result = runner.invoke(
        app, ["accept-trajectories", "--config", str(cfg), "--date", "2026-08-30",
         "--expected-partitions", "2"]
    )
    assert result.exit_code == 0, result.output
    verdict_path = (
        data_root / "curated" / "trajectories-acceptance" / "2026-08-30" / "acceptance.json"
    )
    verdict = json.loads(verdict_path.read_text())
    assert verdict["passed"] is False
    assert verdict["failures"]


def test_accept_trajectories_cli_refuses_a_second_run(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cfg = _write_config(tmp_path, data_root)
    _seed_trajectories(data_root, "2026-08-29", tmp_path)
    _seed_register(data_root, "2026-08-29", [("S1", True), ("S2", True), ("S3", True)])
    first = runner.invoke(
        app, ["accept-trajectories", "--config", str(cfg), "--date", "2026-08-30",
         "--expected-partitions", "2"]
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        app, ["accept-trajectories", "--config", str(cfg), "--date", "2026-08-30",
         "--expected-partitions", "2"]
    )
    assert second.exit_code == 1
    assert "refusal" in second.output
```

Note: `_good_world` is reused against a `curated/trajectories/<date>/` root;
it already returns `(root, summary, register_frame)` and writes partitions
under whatever root it is given.

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trajectory_qa.py -q`
Expected: the three CLI tests FAIL (`No such command 'accept-trajectories'`).

**Step 3: Add `parts_digest` to the QA module**

The verdict must be bound to the exact part BYTES it accepted, not just the
extraction summary: a part can be replaced after acceptance with a freshly
written, self-consistent sidecar, and `build-context-join`'s own
re-verification would pass it while the stale verdict still matches on
summary sha. Append to `src/wa_mine_monitor/trajectory_qa.py` (add
`hashlib` and `json` to its imports, and `manifests` to the
`from wa_mine_monitor import ...` block):

```python
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
```

(`verified_parts` has already proven each manifest's `output.sha256`
matches the file's actual bytes, so hashing the recorded digests IS
hashing the content.)

**Step 4: Implement the command**

Insert immediately before `def _yaml_marked_error_detail` in
`src/wa_mine_monitor/cli.py` (the option singleton goes with the house
module-level options near the top; shown here beside the command for
locality):

```python
ExpectedPartitionsOption = typer.Option(
    trajectory_qa.EXPECTED_STATEWIDE_PARTITIONS,
    "--expected-partitions",
    help="Exact partition count the extraction must hold (D13 statewide shape: 99).",
)


@app.command("accept-trajectories")
def accept_trajectories_cmd(
    config: Path = ConfigOption,
    date: str = DateOption,
    expected_partitions: int = ExpectedPartitionsOption,
) -> None:
    """Run the E4 acceptance battery against the LATEST curated
    trajectories tree and write the verdict
    `curated/trajectories-acceptance/<date>/acceptance.json` that
    `build-context-join` gates on.

    The command writes a `passed: false` verdict just as readily as a
    `passed: true` one -- a failing acceptance is a RESULT with a manifest,
    never a crash (the `validate-huntly` precedent). Only unusable inputs
    (`trajectory_qa.TrajectoryQaError` -- e.g. an uninventoriable partition
    tree, a summary missing its keys) are a refusal.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    out_dir = data_root / "curated" / "trajectories-acceptance" / date
    output_path = out_dir / "acceptance.json"
    _refuse_if_curated_output_already_exists(
        output_path, config=resolved_config, git_state=git_state
    )

    try:
        trajectories_dir = _latest_curated_dated_dir(
            data_root / "curated" / "trajectories", label="curated/trajectories"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    summary_path = trajectories_dir / "extraction_summary.json"
    summary_manifest = _digest_verified_manifest(summary_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    try:
        register_dir = _latest_curated_dated_dir(
            data_root / "curated" / "register", label="curated/register"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    register_path = register_dir / "register.parquet"
    register_manifest = _digest_verified_manifest(register_path)
    register_df = read_table(register_path)

    try:
        report = trajectory_qa.accept_trajectories(
            trajectories_dir,
            summary=summary,
            register_df=register_df,
            expected_partition_count=expected_partitions,
        )
        parts_binding = trajectory_qa.parts_digest(trajectories_dir)
    except (trajectory_qa.TrajectoryQaError, trajectory_extract.TrajectoryExtractError) as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    payload: dict[str, object] = {
        **report.as_dict(),
        "date": date,
        "trajectories_dir": str(trajectories_dir),
        "extraction_summary_sha256": summary_manifest["output"]["sha256"],
        "parts_digest": parts_binding,
        "register_dir": str(register_dir),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    input_assets = [
        SourceAsset(
            uri=str(summary_path),
            sha256=summary_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(trajectories_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(register_path),
            sha256=register_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(register_dir.name),
            licence=licence.SOURCES["dmirs_001_minedex"].licence_id,
            redistribute_public=False,
        ),
    ]
    try:
        manifests.write_run_manifest(
            output=output_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                "expected_partitions": expected_partitions,
                "trajectories_dir": str(trajectories_dir),
                "register_dir": str(register_dir),
            },
        )
    except FileExistsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {**payload, "output_path": str(output_path),
             "manifest_path": str(output_path) + manifests.MANIFEST_SUFFIX},
            indent=2,
            sort_keys=True,
        )
    )
```

Add `trajectory_qa` to the existing `from wa_mine_monitor import ...` block
at the top of `cli.py` (alphabetical position).

**Step 5: Run the tests**

Run: `uv run pytest tests/test_trajectory_qa.py -q`
Expected: PASS (all tests in the file).

**Step 6: Lint/type the touched files**

Run: `uv run ruff check src tests && uv run mypy src scripts`
Expected: clean.

---

### Task 5: `context_join.py` — schema, constants, `assemble_rows`

**Files:**
- Create: `src/wa_mine_monitor/context_join.py`
- Create: `tests/test_context_join.py`

**Step 1: Write the failing tests**

Create `tests/test_context_join.py`. The first three of the five D13 §6
F6-named tests live here (the other two arrive with `validate_context_join`
in Task 6):

```python
"""Tests for the F6 context join (D13 §6).

The five D13-named behaviours are tested under their own names:
one context record per Tier 1 site-year; fire and climate missingness
independent; no trajectory row dropped for unknown context; the rendering
contract requires both contexts; "cause not determined" when context is
absent.
"""

from __future__ import annotations

import pandas as pd
import pytest

from wa_mine_monitor import climate_context, context_join, fire_context


def _fire_df(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=list(fire_context.FIRE_CONTEXT_SCHEMA.names))
    frame["year"] = frame["year"].astype("int32")
    frame["fire_record_count"] = frame["fire_record_count"].astype("Int32")
    return frame


def _climate_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(climate_context.CLIMATE_CONTEXT_SCHEMA.names))


def _fire_row(site: str, maus: str, year: int, **over: object) -> dict:
    row = {
        "site_id": site,
        "maus_id": maus,
        "year": year,
        "fire_status": fire_context.FIRE_STATUS_NOT_RECORDED,
        "fire_record_count": 0,
        "fire_source_version": "DBCA-060 v1",
        "fire_coverage_status": fire_context.COVERAGE_COVERED,
        "fire_snapshot_date": "2026-08-29",
        "not_computable_reason": None,
    }
    row.update(over)
    return row


def _climate_row(site: str, maus: str, year: int, **over: object) -> dict:
    row = {
        "site_id": site,
        "maus_id": maus,
        "year": year,
        "silo_cell_id": "-32.000_116.000",
        "annual_rainfall_mm": 650.0,
        "rain_days_ge_1mm": 80,
        "rainfall_anomaly_mm": 12.5,
        "rainfall_baseline_start_year": climate_context.BASELINE_START_YEAR,
        "rainfall_baseline_end_year": climate_context.BASELINE_END_YEAR,
        "climate_status": climate_context.CLIMATE_STATUS_COMPUTED,
        "not_computable_reason": None,
        "silo_source_version": "SILO v1",
        "silo_snapshot_date": "2026-08-29",
    }
    row.update(over)
    return row


def _small_world() -> tuple[pd.DataFrame, pd.DataFrame, list[int]]:
    """Two sites, context years 1987-1988, trajectory years 1986-1988 --
    the smallest domain with a pre-context (no_context_row) year."""
    fire = _fire_df(
        [_fire_row(s, m, y) for (s, m) in [("S1", "M1"), ("S2", "M2")] for y in (1987, 1988)]
    )
    climate = _climate_df(
        [_climate_row(s, m, y) for (s, m) in [("S1", "M1"), ("S2", "M2")] for y in (1987, 1988)]
    )
    return fire, climate, [1986, 1987, 1988]


def test_one_context_record_per_tier1_site_year() -> None:
    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    assert len(df) == 2 * 3
    assert not df.duplicated(["site_id", "year"]).any()
    assert set(zip(df["site_id"], df["year"].astype(int))) == {
        (s, y) for s in ("S1", "S2") for y in years
    }


def test_fire_and_climate_missingness_are_independent() -> None:
    fire, climate, years = _small_world()
    # S1/1987: fire unknown (outside window), climate computed.
    fire.loc[
        (fire["site_id"] == "S1") & (fire["year"] == 1987),
        ["fire_status", "fire_record_count", "fire_coverage_status", "not_computable_reason"],
    ] = [fire_context.FIRE_STATUS_UNKNOWN, None, fire_context.COVERAGE_OUTSIDE_WINDOW, "window"]
    # S2/1988: climate not computable, fire untouched.
    climate.loc[
        (climate["site_id"] == "S2") & (climate["year"] == 1988),
        ["annual_rainfall_mm", "rain_days_ge_1mm", "rainfall_anomaly_mm",
         "climate_status", "not_computable_reason"],
    ] = [None, None, None, climate_context.CLIMATE_STATUS_NOT_COMPUTABLE, "gap"]
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    s1_1987 = df[(df["site_id"] == "S1") & (df["year"] == 1987)].iloc[0]
    assert s1_1987["fire_status"] == fire_context.FIRE_STATUS_UNKNOWN
    assert s1_1987["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED
    assert s1_1987["annual_rainfall_mm"] == 650.0
    s2_1988 = df[(df["site_id"] == "S2") & (df["year"] == 1988)].iloc[0]
    assert s2_1988["climate_status"] == climate_context.CLIMATE_STATUS_NOT_COMPUTABLE
    assert s2_1988["fire_status"] == fire_context.FIRE_STATUS_NOT_RECORDED


def test_no_trajectory_row_dropped_for_unknown_context() -> None:
    # 1986 has NO context rows at all; the join still emits one explicit
    # row per site for it -- absence is a state, never a dropped row.
    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    absent = df[df["year"] == 1986]
    assert len(absent) == 2
    assert (absent["context_row_status"] == context_join.CONTEXT_ROW_NO_CONTEXT).all()
    assert absent["fire_status"].isna().all()
    assert absent["climate_status"].isna().all()
    # And the absent state is never expressed through fire's vocabulary.
    assert fire_context.FIRE_STATUS_UNKNOWN not in set(absent["fire_status"].dropna())
    joined = df[df["year"] != 1986]
    assert (joined["context_row_status"] == context_join.CONTEXT_ROW_JOINED).all()


def test_no_context_row_reason_names_the_context_start() -> None:
    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    reasons = set(df.loc[df["year"] == 1986, "no_context_row_reason"])
    assert len(reasons) == 1
    reason = reasons.pop()
    assert "1986" in reason and "1987" in reason
    assert df.loc[df["year"] != 1986, "no_context_row_reason"].isna().all()


def test_collision_columns_are_prefixed() -> None:
    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    assert "fire_not_computable_reason" in df.columns
    assert "climate_not_computable_reason" in df.columns
    assert "not_computable_reason" not in df.columns


def test_context_complete_requires_joined_covered_and_computed() -> None:
    fire, climate, years = _small_world()
    fire.loc[
        (fire["site_id"] == "S1") & (fire["year"] == 1987),
        ["fire_status", "fire_record_count", "fire_coverage_status", "not_computable_reason"],
    ] = [fire_context.FIRE_STATUS_UNKNOWN, None, fire_context.COVERAGE_OUTSIDE_WINDOW, "window"]
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    by_key = df.set_index(["site_id", "year"])
    assert bool(by_key.loc[("S1", 1988), "context_complete"]) is True
    assert bool(by_key.loc[("S1", 1987), "context_complete"]) is False  # fire not covered
    assert bool(by_key.loc[("S1", 1986), "context_complete"]) is False  # no context row
    assert df["context_complete"].notna().all()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda fire, climate: fire.drop(fire.index[:1]),  # domain mismatch
        lambda fire, climate: pd.concat([fire, fire.iloc[[0]]], ignore_index=True),  # dup
    ],
)
def test_inconsistent_context_inputs_are_refused(mutate) -> None:
    fire, climate, years = _small_world()
    bad_fire = mutate(fire, climate)
    with pytest.raises(context_join.ContextJoinError):
        context_join.assemble_rows(fire_df=bad_fire, climate_df=climate, years=years)


def test_maus_disagreement_between_contexts_is_refused() -> None:
    fire, climate, years = _small_world()
    climate.loc[climate["site_id"] == "S1", "maus_id"] = "M9"
    with pytest.raises(context_join.ContextJoinError):
        context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context_join.py -q`
Expected: collection FAILs (`ModuleNotFoundError: wa_mine_monitor.context_join`).

**Step 3: Implement the module**

Create `src/wa_mine_monitor/context_join.py`:

```python
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
```

Note the `strict=True` on every `zip` (house style) and that `merged` keeps
fire's `maus_id` (climate's is dropped after the agreement check).

**Step 4: Run the tests**

Run: `uv run pytest tests/test_context_join.py -q`
Expected: PASS.

---

### Task 6: `context_join.py` — `validate_context_join` + rendering-contract tests

**Files:**
- Modify: `src/wa_mine_monitor/context_join.py`
- Test: `tests/test_context_join.py`

**Step 1: Write the failing tests**

Append to `tests/test_context_join.py`:

```python
def _assembled() -> tuple[pd.DataFrame, dict, dict, list[int]]:
    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    fire_counts = fire["fire_status"].value_counts().to_dict()
    climate_counts = climate["climate_status"].value_counts().to_dict()
    return df, fire_counts, climate_counts, years


def test_rendering_contract_requires_both_contexts_beside_any_interpretation() -> None:
    # The schema-level rendering contract: context_complete is non-null
    # bool everywhere, and True ONLY where the row is joined AND fire
    # coverage is covered AND climate is computed. The data dictionary
    # wording is asserted at the acceptance level (test_batch_f_acceptance).
    df, fire_counts, climate_counts, years = _assembled()
    recomputed = (
        (df["context_row_status"] == context_join.CONTEXT_ROW_JOINED)
        & (df["fire_coverage_status"] == fire_context.COVERAGE_COVERED)
        & (df["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED)
    )
    assert (df["context_complete"] == recomputed).all()


def test_cause_not_determined_when_context_absent() -> None:
    # Either context absent/incomplete => context_complete False, and no
    # column name in the product implies causation.
    fire, climate, years = _small_world()
    climate.loc[
        (climate["site_id"] == "S1") & (climate["year"] == 1987),
        ["annual_rainfall_mm", "rain_days_ge_1mm", "rainfall_anomaly_mm",
         "climate_status", "not_computable_reason"],
    ] = [None, None, None, climate_context.CLIMATE_STATUS_NOT_COMPUTABLE, "gap"]
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    by_key = df.set_index(["site_id", "year"])
    assert bool(by_key.loc[("S1", 1987), "context_complete"]) is False
    for name in df.columns:
        assert not any(
            fragment in name.lower() for fragment in context_join.FORBIDDEN_NAME_FRAGMENTS
        ), name


def test_validate_context_join_accepts_the_assembled_product() -> None:
    df, fire_counts, climate_counts, years = _assembled()
    context_join.validate_context_join(
        df,
        site_ids=["S1", "S2"],
        years=years,
        fire_status_counts=fire_counts,
        climate_status_counts=climate_counts,
    )


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda df: df.drop(df.index[:1]),
        lambda df: df.assign(context_complete=True),
        lambda df: df.assign(
            fire_status=df["fire_status"].where(df["year"] != 1986, "not_recorded")
        ),
        lambda df: df.rename(columns={"fire_status": "fire_cause"}),
        lambda df: df.assign(no_context_row_reason=None),
    ],
)
def test_validate_context_join_catches_each_violation(corrupt) -> None:
    df, fire_counts, climate_counts, years = _assembled()
    with pytest.raises(context_join.ContextJoinError):
        context_join.validate_context_join(
            corrupt(df),
            site_ids=["S1", "S2"],
            years=years,
            fire_status_counts=fire_counts,
            climate_status_counts=climate_counts,
        )


def test_validate_context_join_catches_status_count_drift() -> None:
    df, fire_counts, climate_counts, years = _assembled()
    fire_counts[fire_context.FIRE_STATUS_RECORDED] = 99
    with pytest.raises(context_join.ContextJoinError):
        context_join.validate_context_join(
            df,
            site_ids=["S1", "S2"],
            years=years,
            fire_status_counts=fire_counts,
            climate_status_counts=climate_counts,
        )
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context_join.py -q`
Expected: the new tests FAIL (`validate_context_join` missing).

**Step 3: Implement the validator**

Append to `src/wa_mine_monitor/context_join.py`:

```python
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
    null_on_joined = sorted(
        c for c in source_non_nullable if joined[c].isna().any()
    )
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
```

Also confirm the schema-level guard is testable: `FORBIDDEN_NAME_FRAGMENTS`
must not match any `CONTEXT_JOIN_SCHEMA` name (it doesn't; the acceptance
test in Task 9 asserts this permanently).

**Step 4: Run the tests**

Run: `uv run pytest tests/test_context_join.py -q`
Expected: PASS.

---

### Task 7: `build-context-join` CLI — gates 1–2 (trajectories + acceptance verdict)

**Files:**
- Modify: `src/wa_mine_monitor/cli.py` (insert `build-context-join`
  immediately after the new `accept-trajectories` command; add
  `context_join` to the `from wa_mine_monitor import ...` block)
- Test: `tests/test_context_join.py`

**Step 1: Write the failing tests**

Append to `tests/test_context_join.py` (reuse the seed helpers from
`tests/test_trajectory_qa.py` by importing them — they are module-level
functions):

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from wa_mine_monitor import manifests, tables
from wa_mine_monitor.cli import app
from wa_mine_monitor.provenance import SourceAsset
from tests.test_trajectory_qa import (
    _good_world,
    _seed_register,
    _seed_trajectories,
    _write_config,
)

runner = CliRunner()


def _seed_context(
    data_root: Path, kind: str, date_str: str, df: pd.DataFrame, schema
) -> None:
    output_dir = data_root / "curated" / kind / date_str
    output_dir.mkdir(parents=True)
    filename = "fire_context.parquet" if kind == "fire-context" else "climate_context.parquet"
    path = output_dir / filename
    tables.write_table(df, path, schema)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )


def _seed_full_world(tmp_path: Path, *, accept: bool = True) -> tuple[Path, Path]:
    """Trajectories + register + (optionally) a passing acceptance verdict
    + both context products, all consistent with `_good_world`'s three
    sites over trajectory years {2000, 2001} and context year {2001}."""
    data_root = tmp_path / "data"
    cfg = _write_config(tmp_path, data_root)
    _seed_trajectories(data_root, "2026-08-29", tmp_path)
    _seed_register(data_root, "2026-08-29", [("S1", True), ("S2", True), ("S3", True)])
    if accept:
        result = runner.invoke(
            app, ["accept-trajectories", "--config", str(cfg), "--date", "2026-08-29",
             "--expected-partitions", "2"]
        )
        assert result.exit_code == 0, result.output
    pairs = [("S1", "M1"), ("S2", "M1"), ("S3", "M2")]
    fire = _fire_df([_fire_row(s, m, 2001) for s, m in pairs])
    climate = _climate_df([_climate_row(s, m, 2001) for s, m in pairs])
    _seed_context(data_root, "fire-context", "2026-08-29", fire, fire_context.FIRE_CONTEXT_SCHEMA)
    _seed_context(
        data_root, "climate-context", "2026-08-29", climate,
        climate_context.CLIMATE_CONTEXT_SCHEMA,
    )
    return cfg, data_root


def test_build_context_join_refuses_without_an_acceptance_verdict(tmp_path: Path) -> None:
    cfg, _data_root = _seed_full_world(tmp_path, accept=False)
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 1
    assert "refusal" in result.output
    assert "accept-trajectories" in result.output


def test_build_context_join_refuses_a_failed_acceptance_verdict(tmp_path: Path) -> None:
    cfg, data_root = _seed_full_world(tmp_path, accept=False)
    # Produce a FAILED verdict by making the register disagree, then
    # restoring it: run accept against a register with an extra site.
    import shutil

    register_dir = data_root / "curated" / "register" / "2026-08-29"
    backup = tmp_path / "register-backup"
    shutil.copytree(register_dir, backup)
    shutil.rmtree(register_dir)
    _seed_register(
        data_root, "2026-08-29", [("S1", True), ("S2", True), ("S3", True), ("S9", True)]
    )
    result = runner.invoke(
        app, ["accept-trajectories", "--config", str(cfg), "--date", "2026-08-29",
             "--expected-partitions", "2"]
    )
    assert result.exit_code == 0, result.output
    build = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert build.exit_code == 1
    assert "did not pass" in build.output


def test_build_context_join_refuses_when_parts_changed_after_acceptance(tmp_path: Path) -> None:
    # A part rewritten AFTER acceptance -- with a fresh, self-consistent
    # sidecar so digest/schema re-verification passes -- must still be
    # refused: the verdict is bound to the part bytes it accepted.
    cfg, data_root = _seed_full_world(tmp_path)
    troot = data_root / "curated" / "trajectories" / "2026-08-29"
    part = next(troot.glob("collection_id=*/year=*/part-0000.parquet"))
    df = pd.read_parquet(part)
    trajectories.write_trajectories(df.iloc[::-1].reset_index(drop=True), part)
    Path(str(part) + manifests.MANIFEST_SUFFIX).unlink()
    manifests.write_run_manifest(
        output=part,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 1
    assert "parts_digest" in result.output
```

(`trajectories` needs importing at the top of `tests/test_context_join.py`
alongside the other `wa_mine_monitor` imports for this test.)

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context_join.py -q`
Expected: new tests FAIL (`No such command 'build-context-join'`).

**Step 3: Implement gates 1–2**

Insert after `accept_trajectories_cmd` in `src/wa_mine_monitor/cli.py`:

```python
@app.command("build-context-join")
def build_context_join_cmd(config: Path = ConfigOption, date: str = DateOption) -> None:
    """Build the F6 site-year context-join product (D13 §6): fire and
    climate context beside the trajectory site-year domain, one row per
    Tier 1 site-year, `curated/context-join/<date>/context_join.parquet`.

    First downstream consumer of `curated/trajectories`: every partition
    is digest- and schema-re-verified, and the build REFUSES unless the
    latest `accept-trajectories` verdict passed AND covers the exact
    extraction summary being consumed (D13 §6: Batch F follows accepted
    Batch E extraction -- the `require_huntly_gate` discipline applied one
    stage downstream). Context only, never causes: see `context_join.py`'s
    claim boundary.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    output_dir = data_root / "curated" / "context-join" / date
    output_path = output_dir / "context_join.parquet"
    _refuse_if_curated_output_already_exists(
        output_path, config=resolved_config, git_state=git_state
    )

    # GATE 1 -- the latest curated trajectories tree: summary digest-
    # verified, every partition's every part digest- and schema-verified.
    try:
        trajectories_dir = _latest_curated_dated_dir(
            data_root / "curated" / "trajectories", label="curated/trajectories"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    summary_path = trajectories_dir / "extraction_summary.json"
    summary_manifest = _digest_verified_manifest(summary_path)
    summary_sha256 = summary_manifest["output"]["sha256"]

    traj_frames: list[pd.DataFrame] = []
    try:
        partitions = trajectory_extract.existing_partitions(trajectories_dir)
        for collection_id, year in sorted(partitions):
            partition = trajectory_extract.partition_dir(trajectories_dir, collection_id, year)
            for part in trajectory_extract.verified_parts(
                partition, expected_schema=trajectories.TRAJECTORY_SCHEMA
            ):
                traj_frames.append(
                    pq.read_table(part, columns=["site_id", "maus_id", "year"]).to_pandas()
                )
    except trajectory_extract.TrajectoryExtractError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    traj = pd.concat(traj_frames, ignore_index=True)

    # GATE 2 -- the acceptance verdict: latest, digest-verified, passed,
    # and covering the SAME extraction summary this build consumes.
    try:
        acceptance_dir = _latest_curated_dated_dir(
            data_root / "curated" / "trajectories-acceptance",
            label="curated/trajectories-acceptance",
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{exc} -- run accept-trajectories first: Batch F follows "
                        "ACCEPTED Batch E extraction (D13 §6)"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    verdict_path = acceptance_dir / "acceptance.json"
    verdict_manifest = _digest_verified_manifest(verdict_path)
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    if not bool(verdict.get("passed")):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"the trajectory acceptance at {verdict_path} did not pass -- "
                        "the context join is refused until the extraction is accepted"
                    ),
                    "failures": verdict.get("failures", []),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if verdict.get("extraction_summary_sha256") != summary_sha256:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"the acceptance verdict at {verdict_path} covers extraction "
                        f"summary sha256 {str(verdict.get('extraction_summary_sha256'))[:12]}"
                        f"..., but this build consumes {summary_sha256[:12]}... -- run "
                        "accept-trajectories against the current extraction first"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    try:
        actual_parts_digest = trajectory_qa.parts_digest(trajectories_dir)
    except trajectory_qa.TrajectoryQaError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    if verdict.get("parts_digest") != actual_parts_digest:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"the acceptance verdict at {verdict_path} accepted different part "
                        "bytes (parts_digest mismatch) -- a trajectory part changed after "
                        "acceptance; run accept-trajectories again against the current tree"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    typer.echo(json.dumps({"status": "gates 1-2 passed"}, indent=2, sort_keys=True))
```

(The trailing `status` echo is a placeholder Task 8 replaces with gates 3–5
and the build; it exists only so this task is runnable and its two refusal
tests pass.)

**Step 4: Run the tests**

Run: `uv run pytest tests/test_context_join.py -q`
Expected: PASS (the two new gate tests; earlier tests unaffected).

---

### Task 8: `build-context-join` CLI — gates 3–5, assembly, write, manifest

**Files:**
- Modify: `src/wa_mine_monitor/cli.py` (replace the placeholder echo)
- Test: `tests/test_context_join.py`

**Step 1: Write the failing tests**

Append to `tests/test_context_join.py`:

```python
def test_build_context_join_happy_path_writes_product_and_manifest(tmp_path: Path) -> None:
    cfg, data_root = _seed_full_world(tmp_path)
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 0, result.output
    out = data_root / "curated" / "context-join" / "2026-08-30" / "context_join.parquet"
    assert out.exists()
    assert Path(str(out) + manifests.MANIFEST_SUFFIX).exists()
    df = tables.read_table(out)
    # 3 sites x trajectory years {2000, 2001}; 2000 has no context rows.
    assert len(df) == 6
    absent = df[df["year"] == 2000]
    assert (absent["context_row_status"] == context_join.CONTEXT_ROW_NO_CONTEXT).all()
    payload = json.loads(result.output)
    assert payload["rows"] == 6
    assert payload["n_sites"] == 3
    assert payload["n_no_context_rows"] == 3
    # The manifest cites all four inputs.
    manifest = json.loads(Path(str(out) + manifests.MANIFEST_SUFFIX).read_text())
    assert len(manifest["inputs"]) == 4


def test_build_context_join_refuses_site_set_mismatch_with_contexts(tmp_path: Path) -> None:
    cfg, data_root = _seed_full_world(tmp_path)
    # Rebuild fire context missing S3.
    import shutil

    fire_dir = data_root / "curated" / "fire-context" / "2026-08-29"
    shutil.rmtree(fire_dir)
    pairs = [("S1", "M1"), ("S2", "M1")]
    fire = _fire_df([_fire_row(s, m, 2001) for s, m in pairs])
    _seed_context(data_root, "fire-context", "2026-08-29", fire, fire_context.FIRE_CONTEXT_SCHEMA)
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_build_context_join_refuses_maus_disagreement_with_trajectories(tmp_path: Path) -> None:
    cfg, data_root = _seed_full_world(tmp_path)
    import shutil

    for kind, filename, schema, mk_rows in (
        ("fire-context", "fire_context.parquet", fire_context.FIRE_CONTEXT_SCHEMA, _fire_row),
        (
            "climate-context",
            "climate_context.parquet",
            climate_context.CLIMATE_CONTEXT_SCHEMA,
            _climate_row,
        ),
    ):
        shutil.rmtree(data_root / "curated" / kind / "2026-08-29")
        pairs = [("S1", "M9"), ("S2", "M1"), ("S3", "M2")]  # S1's maus diverges
        rows = [mk_rows(s, m, 2001) for s, m in pairs]
        df = _fire_df(rows) if kind == "fire-context" else _climate_df(rows)
        _seed_context(data_root, kind, "2026-08-29", df, schema)
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 1
    assert "maus_id" in result.output


def test_build_context_join_refuses_a_second_run(tmp_path: Path) -> None:
    cfg, _data_root = _seed_full_world(tmp_path)
    first = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert second.exit_code == 1
    assert "refusal" in second.output
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context_join.py -q`
Expected: the new tests FAIL (placeholder echo, no product written).

**Step 3: Implement gates 3–5 and the build**

Replace the placeholder `typer.echo(...)` at the end of
`build_context_join_cmd` with:

```python
    # GATE 3 -- latest fire and climate context products, digest-verified.
    context_inputs: dict[str, tuple[Path, Path, dict[str, Any]]] = {}
    for kind, filename in (
        ("fire-context", "fire_context.parquet"),
        ("climate-context", "climate_context.parquet"),
    ):
        try:
            dated_dir = _latest_curated_dated_dir(
                data_root / "curated" / kind, label=f"curated/{kind}"
            )
        except register.NoSnapshotFoundError as exc:
            typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
            raise typer.Exit(1) from None
        artefact = dated_dir / filename
        context_inputs[kind] = (dated_dir, artefact, _digest_verified_manifest(artefact))
    fire_df = read_table(context_inputs["fire-context"][1])
    climate_df = read_table(context_inputs["climate-context"][1])

    # GATE 4 -- cross-input identity: one site set, one maus_id per site,
    # across trajectories, fire and climate.
    traj_sites = set(traj["site_id"].astype(str))
    fire_sites = set(fire_df["site_id"].astype(str))
    climate_sites = set(climate_df["site_id"].astype(str))
    if not (traj_sites == fire_sites == climate_sites):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "site sets differ across inputs: "
                        f"{len(traj_sites)} trajectory site(s), {len(fire_sites)} fire, "
                        f"{len(climate_sites)} climate -- e.g. only-trajectory "
                        f"{sorted(traj_sites - fire_sites)[:5]}, only-fire "
                        f"{sorted(fire_sites - traj_sites)[:5]}"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    traj_maus = (
        traj[["site_id", "maus_id"]].astype(str).drop_duplicates().set_index("site_id")["maus_id"]
    )
    if traj_maus.index.duplicated().any():
        typer.echo(
            json.dumps(
                {"refusal": "trajectories carry more than one maus_id for a site"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    fire_maus = fire_df[["site_id", "maus_id"]].astype(str).drop_duplicates()
    disagreeing = sorted(
        site
        for site, maus in zip(fire_maus["site_id"], fire_maus["maus_id"], strict=True)
        if traj_maus.get(site) != maus
    )
    if disagreeing:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"maus_id disagrees between trajectories and fire context for "
                        f"site(s) {disagreeing[:5]} ({len(disagreeing)} total)"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # GATE 5 -- year-domain shape: the only trajectory years without
    # context are the pre-context ones (derived, never hard-coded).
    traj_years = sorted(set(traj["year"].astype(int)))
    context_years = sorted(set(fire_df["year"].astype(int)))
    uncovered = sorted(set(traj_years) - set(context_years))
    context_start = context_years[0] if context_years else None
    holes = [y for y in uncovered if context_start is not None and y >= context_start]
    if holes:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"trajectory year(s) {holes} inside the context coverage "
                        f"(from {context_start}) have no context rows -- a hole in the "
                        "middle of context coverage is an integrity failure, not an "
                        "absent-state year"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    try:
        joined = context_join.assemble_rows(
            fire_df=fire_df, climate_df=climate_df, years=traj_years
        )
        context_join.validate_context_join(
            joined,
            site_ids=sorted(traj_sites),
            years=traj_years,
            fire_status_counts=fire_df["fire_status"].value_counts().to_dict(),
            climate_status_counts=climate_df["climate_status"].value_counts().to_dict(),
        )
    except context_join.ContextJoinError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_table_or_refuse(joined, output_path, context_join.CONTEXT_JOIN_SCHEMA)

    fire_dir, fire_path, fire_manifest = context_inputs["fire-context"]
    climate_dir, climate_path, climate_manifest = context_inputs["climate-context"]
    input_assets = [
        SourceAsset(
            uri=str(summary_path),
            sha256=summary_sha256,
            collection=None,
            snapshot_date=dt_date.fromisoformat(trajectories_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(verdict_path),
            sha256=verdict_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(acceptance_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(fire_path),
            sha256=fire_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(fire_dir.name),
            licence=licence.SOURCES["dbca_060"].licence_id,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(climate_path),
            sha256=climate_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(climate_dir.name),
            licence=licence.SOURCES["silo"].licence_id,
            redistribute_public=False,
        ),
    ]
    try:
        manifests.write_run_manifest(
            output=output_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                "trajectories_dir": str(trajectories_dir),
                "acceptance_dir": str(acceptance_dir),
                "fire_context_dir": str(fire_dir),
                "climate_context_dir": str(climate_dir),
                "n_trajectory_partitions": len(partitions),
            },
        )
    except FileExistsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    n_no_context = int(
        (joined["context_row_status"] == context_join.CONTEXT_ROW_NO_CONTEXT).sum()
    )
    typer.echo(
        json.dumps(
            {
                "output_path": str(output_path),
                "manifest_path": str(output_path) + manifests.MANIFEST_SUFFIX,
                "rows": len(joined),
                "n_sites": len(traj_sites),
                "n_years": len(traj_years),
                "n_no_context_rows": n_no_context,
                "n_context_complete": int(joined["context_complete"].astype(bool).sum()),
                "year_min": traj_years[0],
                "year_max": traj_years[-1],
            },
            indent=2,
            sort_keys=True,
        )
    )
```

Check `licence.SOURCES` for the exact DBCA source key first
(`grep -n "dbca" src/wa_mine_monitor/licence.py`) — use the registered id
(it may be `dbca_060` or similar; use whatever `build-fire-context` uses).

**Step 4: Run the tests**

Run: `uv run pytest tests/test_context_join.py tests/test_trajectory_qa.py -q`
Expected: PASS.

**Step 5: Lint/type**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src scripts`
Expected: clean (run `uv run ruff format src tests` first if format-check
complains).

---

### Task 9: Checkpoint documents + `tests/test_batch_f_acceptance.py`

**Files:**
- Create: `docs/checkpoints/e4-statewide-extraction.md`
- Create: `docs/checkpoints/batch-f-result.md`
- Create: `tests/test_batch_f_acceptance.py`

**Step 1: Write the checkpoint skeletons**

Both docs follow the house checkpoint shape (see
`docs/checkpoints/tier0-public-rc.md` for tone). Live figures are filled by
the operator AFTER the live runs; the committed structure and adjudication
prose are written now with explicit `PENDING LIVE RUN` markers so the
acceptance tests can pin the structure and the claim-boundary wording
without fabricating figures.

`docs/checkpoints/e4-statewide-extraction.md`:

```markdown
# Checkpoint: E4 statewide trajectory extraction — accepted

Status: PENDING LIVE RUN (structure committed; figures land after
`accept-trajectories` runs against the 2026-08-29 extraction).

## Live figures

- Extraction: `curated/trajectories/2026-08-29/` — PENDING LIVE RUN
  (rows, partitions, not_computable by reason, acceptance verdict path
  and digest).

## D13 E4 acceptance clauses, adjudicated

- Every partition independently verifies (digest + footer schema) and
  reconciles against the extraction summary: PENDING LIVE RUN
  (`accept-trajectories` check names `parts_digest_and_schema`,
  `partition_inventory`, `total_rows_match_summary`).
- Only eligible register sites entered extraction; MINEDEX identifiers
  remain internal (no public artefact in this cycle carries them):
  PENDING LIVE RUN (`partition_site_sets`,
  `summary_site_ids_match_register`).
- Huntly gate: satisfied before extraction
  (`trajectory_extract.require_huntly_gate`; verdict recorded in
  `docs/checkpoints/tier1-huntly-validation.md` lineage).

## Claim boundary

Outputs are spectral detections, never compliance or performance
findings, never operational rehabilitation dates. The acceptance battery
verifies accounting identities and row contracts; it does not interpret
a single trajectory.

## Honesty flags

- The D13 L626 serial-vs-concurrent extraction equivalence test does NOT
  exist; extraction ran serially and no concurrency claim is made.
- E6 (sensor-overlap sensitivity) and E7 (Batch E closure,
  `batch-e-result.md`) remain OPEN; this checkpoint accepts E4 only.
- `not_computable` composition by reason: PENDING LIVE RUN.
```

`docs/checkpoints/batch-f-result.md`:

```markdown
# Checkpoint: Batch F — context products and the F6 join

Status: PENDING LIVE RUN (structure committed; figures land after
`build-context-join` runs).

## Live runs

- F3/F4 (fire context): `curated/fire-context/2026-08-29/` — 404,508
  rows; recorded 10,097 / not_recorded 388,990 / unknown 5,421.
- F5 (climate context): `curated/climate-context/2026-08-29/` — 404,508
  rows; computed 403,455 / not_computable 1,053.
- F6 (context join): PENDING LIVE RUN (rows, no-context-row count,
  context_complete count, verdict lineage).

## D13 §6 acceptance, adjudicated

- Counts reconcile across the three products: PENDING LIVE RUN
  (`validate_context_join` status-count reconciliation).
- Source versions carried forward onto every joined row: enforced by
  schema (fire_source_version/silo_source_version non-null on joined
  rows) — PENDING LIVE RUN for the live confirmation.
- No causal attribution anywhere in the product: enforced by
  `context_join.FORBIDDEN_NAME_FRAGMENTS` and the claim-boundary tests.
- Mirror decision: the raw-source mirror REMAINED DECLINED (A10); no
  mirror was created in this cycle.

## Claim boundary

Context rows are displayed beside trajectories; no causal attribution is
generated here or anywhere in this project. A row with
`context_complete = false` must be rendered with cause not determined; a
row with `context_complete = true` still carries no cause.

## Honesty flags

- 1986 carries no context rows (fire and climate coverage begins 1987);
  those site-years are explicit `no_context_row` rows, never dropped and
  never expressed through fire's `unknown`. Count: PENDING LIVE RUN.
- `silo_cell_id` on outside-grid footprints is centroid-minted, not a
  real grid cell (climate-context caveat, carried forward).
- E6/E7 remain open (see e4-statewide-extraction.md).
```

**Step 2: Write the acceptance tests**

Create `tests/test_batch_f_acceptance.py`:

```python
"""Batch F acceptance-level tests (D13 §6): the cross-product contracts
and the committed record, independent of any one module's unit tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from wa_mine_monitor import climate_context, context_join, fire_context

REPO_ROOT = Path(__file__).resolve().parents[1]
E4_CHECKPOINT = REPO_ROOT / "docs" / "checkpoints" / "e4-statewide-extraction.md"
F_CHECKPOINT = REPO_ROOT / "docs" / "checkpoints" / "batch-f-result.md"


def test_schema_carries_no_causal_column_names() -> None:
    for name in context_join.CONTEXT_JOIN_SCHEMA.names:
        assert not any(
            fragment in name.lower() for fragment in context_join.FORBIDDEN_NAME_FRAGMENTS
        ), name


def test_payload_columns_enumerate_every_context_column() -> None:
    keys = {"site_id", "maus_id", "year", "context_row_status", "context_complete",
            "no_context_row_reason"}
    assert set(context_join.CONTEXT_PAYLOAD_COLUMNS) == set(
        context_join.CONTEXT_JOIN_SCHEMA.names
    ) - keys


def test_source_versions_travel_onto_every_joined_row() -> None:
    from tests.test_context_join import _small_world

    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    joined = df[df["context_row_status"] == context_join.CONTEXT_ROW_JOINED]
    assert joined["fire_source_version"].notna().all()
    assert joined["silo_source_version"].notna().all()
    assert joined["fire_snapshot_date"].notna().all()
    assert joined["silo_snapshot_date"].notna().all()


def test_status_vocabularies_are_carried_verbatim_not_widened() -> None:
    from tests.test_context_join import _small_world

    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    fire_values = set(df["fire_status"].dropna())
    assert fire_values <= {
        fire_context.FIRE_STATUS_RECORDED,
        fire_context.FIRE_STATUS_NOT_RECORDED,
        fire_context.FIRE_STATUS_UNKNOWN,
    }
    climate_values = set(df["climate_status"].dropna())
    assert climate_values <= {
        climate_context.CLIMATE_STATUS_COMPUTED,
        climate_context.CLIMATE_STATUS_NOT_COMPUTABLE,
    }


def test_e4_checkpoint_exists_with_required_sections() -> None:
    text = E4_CHECKPOINT.read_text(encoding="utf-8")
    for heading in (
        "## D13 E4 acceptance clauses, adjudicated",
        "## Claim boundary",
        "## Honesty flags",
    ):
        assert heading in text
    assert "serial-vs-concurrent" in text  # the missing-test disclosure
    assert "E6" in text and "E7" in text


def test_batch_f_checkpoint_exists_with_required_sections() -> None:
    text = F_CHECKPOINT.read_text(encoding="utf-8")
    for heading in ("## D13 §6 acceptance, adjudicated", "## Claim boundary", "## Honesty flags"):
        assert heading in text
    assert "REMAINED DECLINED" in text  # A10 mirror decision
    assert "no causal attribution is generated" in text.lower()
    assert "cause not determined" in text
```

**Step 3: Run the tests**

Run: `uv run pytest tests/test_batch_f_acceptance.py -q`
Expected: PASS.

---

### Task 10: Full verification battery

**Step 1: Format**

Run: `uv run ruff format src tests`
Expected: files reformatted or unchanged.

**Step 2: Full battery, CI order**

Run:
```
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src scripts
uv run pytest -q -rs
```
Expected: all four clean; pytest total = previous 1152 plus the new tests,
0 failures. Fix anything that fails before reporting done.

---

## After build-flow (operator steps, NOT tasks for implementation agents)

1. Live run: `uv run wa-mine-monitor accept-trajectories --config config/base.yaml --date 2026-08-30`
   against the real data root; the default `--expected-partitions` (99)
   is the gate. Confirm `passed: true` and the real counts (expect rows
   2,458,164; not_computable 94,343; 99 partitions; 10,372 sites) and
   that the verdict carries `parts_digest`.
2. Live run: `uv run wa-mine-monitor build-context-join --config config/base.yaml --date 2026-08-30`;
   expect 414,880 rows = 10,372 × 40 years, 10,372 no-context rows (1986).
3. Fill both checkpoint docs' `PENDING LIVE RUN` markers with the real
   figures; flip their Status lines.
4. Commit (never staging `docs/plans/*.md`), codex diff gate
   (codex-consult), merge to main locally. No push without a fresh ask.
```
