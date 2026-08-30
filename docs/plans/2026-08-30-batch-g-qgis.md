# Batch G QGIS-Only Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Close Batch G (re-scoped, QGIS-only): a gated
`build-trajectory-summary` command producing a two-layer GeoPackage for
the private QGIS project, plus QML styles, `qgis/README.md`, and the
rescope decision/amendment/ROADMAP documentation.

**Architecture:** A pure assembly module
(`src/wa_mine_monitor/trajectory_summary.py`, mirroring
`context_join.py`'s purity split) turns already-verified frames into a
per-site summary; the CLI command owns every read and refusal, reusing
`build-context-join`'s acceptance-verdict gates verbatim
(`src/wa_mine_monitor/cli.py:8607-8736` is the reference
implementation). Output is a curated GeoPackage with two layers
(`register_sites`: all located register sites; `site_summary`: eligible
sites with disclosures + context), written with a run-manifest sidecar.
QML styles are repo artifacts under `qgis/styles/` with a drift-guard
test binding them to the pinned schema.

**Tech Stack:** Python 3.12, uv, typer CLI, pandas/pyarrow, geopandas
1.1.4 + pyogrio 0.13 (verified importable in this worktree), pytest
with `typer.testing.CliRunner`.

**Design doc:** `docs/plans/2026-08-30-batch-g-qgis-design.md` (approved
2026-08-30). Claim boundary applies to every artifact: observed values
only, no trend/recovery/cause columns; fire three-state preserved;
sensor-overlap disagreement never resolved by priority (overlap at a
metric's latest year ⇒ value NULL, `<metric>_latest_collections`
discloses why).

**Verbatim reference points in the existing code (read before coding):**

- Gate pattern to mirror: `src/wa_mine_monitor/cli.py:8607-8736`
  (`build-context-join` GATES 1-2), `cli.py:3065-3107` (eligibility-
  annotation column checks on the register).
- Helpers to reuse: `_load_config_or_exit`,
  `_collect_git_state_disclosing_gaps`,
  `_refuse_if_curated_output_already_exists`,
  `_latest_curated_dated_dir`, `_digest_verified_manifest`,
  `read_table`, `trajectory_extract.existing_partitions` /
  `partition_dir` / `verified_parts` / `select_eligible_sites`,
  `trajectory_qa.parts_digest`, `manifests.write_run_manifest`,
  `provenance.SourceAsset`.
- Test fixtures to reuse: `tests/test_trajectory_qa.py`
  (`_seed_register`, `_seed_trajectories`, `_seed_crosswalk`,
  `_write_config`) and `tests/test_context_join.py`
  (`_seed_full_world`, `_seed_context`, `_fire_row`, `_climate_row`,
  `_fire_df`, `_climate_df`). Cross-file imports from `tests.` are the
  established pattern (see `tests/test_context_join.py:19-24`).
- Vocabulary constants (never re-literal them):
  `fire_context.FIRE_STATUS_RECORDED`, `fire_context.COVERAGE_COVERED`,
  `climate_context.CLIMATE_STATUS_COMPUTED`,
  `context_join.CONTEXT_ROW_JOINED`,
  `context_join.FORBIDDEN_NAME_FRAGMENTS`, `trajectories.METRICS`
  (5 metrics: nbr, ndmi, bare_soil, photosynthetic_vegetation,
  non_photosynthetic_vegetation).

---

## Task 1: `trajectory_summary` module — constants, identity and coverage assembly

**Files:**
- Create: `src/wa_mine_monitor/trajectory_summary.py`
- Create: `tests/test_trajectory_summary.py`

**Step 1: Write the failing tests**

```python
"""Tests for the Batch G per-site trajectory summary (design
2026-08-30). Claim-boundary tests carry the same discipline as
test_context_join.py: no column may imply causation, fire three-state
is never widened, sensor overlap is never resolved by priority."""

from __future__ import annotations

import pandas as pd
import pytest

from wa_mine_monitor import (
    climate_context,
    context_join,
    fire_context,
    trajectories,
    trajectory_summary,
)


def _register_df() -> pd.DataFrame:
    rows = [
        ("S1", "eligible", True),
        ("S2", "eligible", False),
        ("S9", "insufficient_pixel_support", False),
    ]
    return pd.DataFrame(
        {
            "site_id": [r[0] for r in rows],
            "trajectory_status": [r[1] for r in rows],
            "d3_forced_threshold": pd.array([r[2] for r in rows], dtype="boolean"),
            "lon": [116.0, 117.0, 118.0],
            "lat": [-32.0, -33.0, -31.0],
        }
    )


def _traj_row(site: str, year: int, metric: str, **over: object) -> dict:
    row: dict = {
        "site_id": site,
        "maus_id": "M1" if site == "S1" else "M2",
        "year": year,
        "metric": metric,
        "value": 0.5,
        "computable": True,
        "collection_id": "ga_ls8cls9c",
        "shared_footprint_site_count": 2 if site == "S1" else 1,
        "d3_forced_threshold": site == "S1",
    }
    row.update(over)
    return row


def _traj_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _context_row(site: str, year: int, **over: object) -> dict:
    row: dict = {
        "site_id": site,
        "maus_id": "M1" if site == "S1" else "M2",
        "year": year,
        "context_row_status": context_join.CONTEXT_ROW_JOINED,
        "context_complete": True,
        "fire_status": "not_recorded",
        "climate_status": climate_context.CLIMATE_STATUS_COMPUTED,
        "annual_rainfall_mm": 400.0,
    }
    row.update(over)
    return row


def _context_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _small_world() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    traj = _traj_df(
        [
            _traj_row("S1", 2000, "nbr"),
            _traj_row("S1", 2001, "nbr", value=0.7),
            _traj_row("S1", 2001, "ndmi", value=None, computable=False),
            _traj_row("S2", 2000, "nbr", value=0.2),
            _traj_row("S2", 2001, "nbr", value=0.3),
        ]
    )
    ctx = _context_df(
        [
            _context_row("S1", 2000),
            _context_row("S1", 2001, fire_status=fire_context.FIRE_STATUS_RECORDED),
            _context_row("S2", 2000, context_complete=False, climate_status="not_computable",
                         annual_rainfall_mm=None),
            _context_row("S2", 2001),
        ]
    )
    return _register_df(), traj, ctx


def test_summary_columns_are_pinned_and_carry_no_causal_names() -> None:
    for name in trajectory_summary.SUMMARY_COLUMNS:
        assert not any(
            frag in name.lower() for frag in context_join.FORBIDDEN_NAME_FRAGMENTS
        ), name
    for metric in trajectories.METRICS:
        assert f"{metric}_latest" in trajectory_summary.SUMMARY_COLUMNS
        assert f"{metric}_latest_year" in trajectory_summary.SUMMARY_COLUMNS
        assert f"{metric}_latest_collections" in trajectory_summary.SUMMARY_COLUMNS


def test_assemble_is_one_row_per_eligible_site_with_disclosures() -> None:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    )
    assert list(df.columns) == list(trajectory_summary.SUMMARY_COLUMNS)
    assert sorted(df["site_id"]) == ["S1", "S2"]  # S9 is not eligible
    s1 = df.set_index("site_id").loc["S1"]
    assert s1["maus_id"] == "M1"
    assert s1["shared_footprint_site_count"] == 2
    assert bool(s1["d3_forced_threshold"]) is True
    assert s1["trajectory_status"] == "eligible"


def test_assemble_coverage_counts() -> None:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s1 = df.loc["S1"]
    assert (s1["year_min"], s1["year_max"]) == (2000, 2001)
    assert s1["years_observed"] == 2
    assert s1["years_computable"] == 2  # nbr computable in both years
    assert s1["years_not_computable"] == 0
    assert s1["context_complete_years"] == 2
    assert df.loc["S2"]["context_complete_years"] == 1


def test_assemble_refuses_site_set_mismatch() -> None:
    register, traj, ctx = _small_world()
    with pytest.raises(trajectory_summary.TrajectorySummaryError):
        trajectory_summary.assemble_summary(
            register_df=register, traj_df=traj[traj["site_id"] != "S2"], context_df=ctx
        )


def test_assemble_refuses_conflicting_per_site_disclosures() -> None:
    register, traj, ctx = _small_world()
    traj = traj.copy()
    traj.loc[traj.index[-1], "shared_footprint_site_count"] = 99
    traj.loc[traj.index[-1], "site_id"] = "S1"
    traj.loc[traj.index[-1], "maus_id"] = "M1"
    with pytest.raises(trajectory_summary.TrajectorySummaryError):
        trajectory_summary.assemble_summary(
            register_df=register, traj_df=traj, context_df=ctx
        )
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trajectory_summary.py -q`
Expected: FAIL / ERROR with `ModuleNotFoundError: wa_mine_monitor.trajectory_summary`

**Step 3: Write the module (identity + coverage only; metric/fire/climate columns emitted as all-NA placeholders so the pinned column list is complete from the first commit)**

```python
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
    out["d3_forced_threshold"] = _single_value_per_site(traj_df, "d3_forced_threshold").astype(
        bool
    )

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
        at_latest = sub.merge(
            latest_year.rename("_latest_year"), left_on="site_id", right_index=True
        )
        at_latest = at_latest.loc[at_latest["year"] == at_latest["_latest_year"]]
        n_collections = at_latest.groupby("site_id")["collection_id"].nunique()
        # Sensor-overlap rule: >1 computable collection at the latest
        # year => NULL value, overlap disclosed. Never resolved by
        # priority (module docstring; architecture ruling in ROADMAP).
        value = at_latest.groupby("site_id")["value"].first().where(n_collections == 1)
        out[f"{metric}_latest"] = value.reindex(out.index).astype("Float64")
        out[f"{metric}_latest_year"] = latest_year.reindex(out.index).astype("Int64")
        out[f"{metric}_latest_collections"] = n_collections.reindex(out.index).astype("Int64")


def _add_fire_summary(out: pd.DataFrame, context_df: pd.DataFrame) -> None:
    joined = context_df.loc[
        context_df["context_row_status"] == context_join.CONTEXT_ROW_JOINED
    ]
    latest_year = joined.groupby("site_id")["year"].max()
    at_latest = joined.merge(
        latest_year.rename("_latest_year"), left_on="site_id", right_index=True
    )
    at_latest = at_latest.loc[at_latest["year"] == at_latest["_latest_year"]].set_index(
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
    joined = context_df.loc[
        context_df["context_row_status"] == context_join.CONTEXT_ROW_JOINED
    ]
    computed = joined.loc[
        joined["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED
    ]
    out["rainfall_annual_mean"] = (
        computed.groupby("site_id")["annual_rainfall_mm"].mean().reindex(out.index)
    ).astype("Float64")
    latest_year = computed.groupby("site_id")["year"].max()
    at_latest = computed.merge(
        latest_year.rename("_latest_year"), left_on="site_id", right_index=True
    )
    at_latest = at_latest.loc[at_latest["year"] == at_latest["_latest_year"]].set_index(
        "site_id"
    )
    out["rainfall_latest"] = at_latest["annual_rainfall_mm"].reindex(out.index).astype(
        "Float64"
    )
    out["rainfall_latest_year"] = latest_year.reindex(out.index).astype("Int64")
```

**Step 4: Run the Task 1 tests and make sure they pass**

Run: `uv run pytest tests/test_trajectory_summary.py -q`
Expected: PASS (5 tests)

---

## Task 2: Sensor-overlap and metric-latest semantics

**Files:**
- Modify: `tests/test_trajectory_summary.py` (append)

**Step 1: Write the failing tests** (they may already pass given Task 1's
implementation — that is fine; they pin the semantics against
regression, the same way `test_context_join.py` pins the rendering
contract)

```python
def test_metric_latest_takes_the_latest_computable_year() -> None:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s1 = df.loc["S1"]
    assert s1["nbr_latest_year"] == 2001
    assert s1["nbr_latest"] == 0.7
    assert s1["nbr_latest_collections"] == 1
    # ndmi has no computable row for S1 -> all three NULL.
    assert pd.isna(s1["ndmi_latest"])
    assert pd.isna(s1["ndmi_latest_year"])
    assert pd.isna(s1["ndmi_latest_collections"])


def test_sensor_overlap_at_latest_year_is_disclosed_never_resolved() -> None:
    register, traj, ctx = _small_world()
    traj = pd.concat(
        [traj, _traj_df([_traj_row("S1", 2001, "nbr", value=0.9, collection_id="ga_ls7e")])],
        ignore_index=True,
    )
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s1 = df.loc["S1"]
    assert s1["nbr_latest_year"] == 2001
    assert s1["nbr_latest_collections"] == 2
    assert pd.isna(s1["nbr_latest"])  # neither 0.7 nor 0.9 wins
```

**Step 2: Run tests**

Run: `uv run pytest tests/test_trajectory_summary.py -q`
Expected: PASS. If the overlap test fails, fix `_add_metric_latest`
(the `.where(n_collections == 1)` guard) — do not weaken the test.

---

## Task 3: Fire and climate summary semantics

**Files:**
- Modify: `tests/test_trajectory_summary.py` (append)

**Step 1: Write the tests**

```python
def test_fire_three_state_is_preserved_and_never_widened() -> None:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s1, s2 = df.loc["S1"], df.loc["S2"]
    assert s1["fire_status_latest"] == fire_context.FIRE_STATUS_RECORDED
    assert s1["fire_years_recorded"] == 1
    assert s1["last_recorded_fire_year"] == 2001
    # S2: no recorded fire. The count is a genuine 0 (the record was
    # consulted); the year is NULL, never a fabricated known-negative.
    assert s2["fire_status_latest"] == "not_recorded"
    assert s2["fire_years_recorded"] == 0
    assert pd.isna(s2["last_recorded_fire_year"])


def test_no_context_rows_leave_context_fields_null_not_zeroed() -> None:
    register, traj, ctx = _small_world()
    ctx = ctx.copy()
    ctx["context_row_status"] = context_join.CONTEXT_ROW_NO_CONTEXT
    ctx["context_complete"] = False
    for col in ("fire_status", "climate_status", "annual_rainfall_mm"):
        ctx[col] = None
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s1 = df.loc["S1"]
    assert pd.isna(s1["fire_status_latest"])
    assert s1["fire_years_recorded"] == 0
    assert pd.isna(s1["last_recorded_fire_year"])
    assert pd.isna(s1["rainfall_annual_mean"])
    assert pd.isna(s1["rainfall_latest"])
    assert s1["context_complete_years"] == 0


def test_climate_summary_uses_computed_rows_only() -> None:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    ).set_index("site_id")
    s2 = df.loc["S2"]
    # S2's 2000 row is not_computable: the mean and latest come from
    # 2001 alone.
    assert s2["rainfall_annual_mean"] == 400.0
    assert s2["rainfall_latest"] == 400.0
    assert s2["rainfall_latest_year"] == 2001
```

**Step 2: Run tests**

Run: `uv run pytest tests/test_trajectory_summary.py -q`
Expected: PASS. Any failure is a defect in Task 1's
`_add_fire_summary`/`_add_climate_summary` — fix there.

---

## Task 4: `validate_summary`

**Files:**
- Modify: `src/wa_mine_monitor/trajectory_summary.py`
- Modify: `tests/test_trajectory_summary.py` (append)

**Step 1: Write the failing tests**

```python
def _valid_summary() -> tuple[pd.DataFrame, list[str]]:
    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    )
    return df, ["S1", "S2"]


def test_validate_accepts_the_assembled_product() -> None:
    df, site_ids = _valid_summary()
    trajectory_summary.validate_summary(df, site_ids=site_ids)


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda df: df.drop(df.index[:1]),                       # dropped site
        lambda df: df.drop(columns=["fire_status_latest"]),     # missing column
        lambda df: df.rename(columns={"nbr_latest": "nbr_cause"}),  # causal name
        lambda df: df.assign(trajectory_status="closed"),       # non-eligible status
        lambda df: df.assign(fire_status_latest="burned"),      # widened vocabulary
        lambda df: df.assign(shared_footprint_site_count=pd.NA),  # null disclosure
        lambda df: df.assign(                                   # resolved overlap
            nbr_latest_collections=pd.array([2, 1], dtype="Int64")
        ),
    ],
)
def test_validate_catches_each_violation(corrupt) -> None:
    df, site_ids = _valid_summary()
    with pytest.raises(trajectory_summary.TrajectorySummaryError):
        trajectory_summary.validate_summary(corrupt(df), site_ids=site_ids)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trajectory_summary.py -q -k validate`
Expected: FAIL with `AttributeError: ... has no attribute 'validate_summary'`

**Step 3: Implement `validate_summary`** (append to the module)

```python
#: Columns that must be non-null on every summary row -- the identity
#: block and the coverage counts. Everything else is legitimately NULL
#: (no computable metric row, no joined context, no recorded fire).
_NON_NULLABLE: tuple[str, ...] = _IDENTITY_COLUMNS + _COVERAGE_COLUMNS + (
    "fire_years_recorded",
)

_FIRE_VOCABULARY: frozenset[str] = frozenset(
    {"recorded", "not_recorded", "unknown"}
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
            problems.append(f"fire_status_latest outside the three-state vocabulary: {sorted(bad_fire)}")
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
```

Note: the fire-vocabulary frozenset duplicates three string literals —
if `fire_context` exports status constants for all three states
(check for `FIRE_STATUS_NOT_RECORDED` / `FIRE_STATUS_UNKNOWN` next to
`FIRE_STATUS_RECORDED`), build the frozenset from those instead.

**Step 4: Run the full test file**

Run: `uv run pytest tests/test_trajectory_summary.py -q`
Expected: PASS (all tests so far)

---

## Task 5: GeoPackage writer (two layers, point geometry)

**Files:**
- Modify: `src/wa_mine_monitor/trajectory_summary.py`
- Modify: `tests/test_trajectory_summary.py` (append)

**Step 1: Write the failing test**

```python
def test_gpkg_round_trip_two_layers(tmp_path) -> None:
    import pyogrio

    register, traj, ctx = _small_world()
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    )
    path = tmp_path / "trajectory_summary.gpkg"
    n_unlocated = trajectory_summary.write_summary_gpkg(
        summary_df=df, register_df=register, path=path
    )
    assert n_unlocated == 0
    layers = {name for name, _ in pyogrio.list_layers(path)}
    assert layers == {trajectory_summary.REGISTER_LAYER, trajectory_summary.SUMMARY_LAYER}
    import geopandas as gpd

    summary = gpd.read_file(path, layer=trajectory_summary.SUMMARY_LAYER)
    assert len(summary) == 2
    assert summary.crs is not None and summary.crs.to_epsg() == 4326
    # EXACT pinned column set (design section 3): nothing extra may ride
    # along, nothing pinned may be dropped by the gpkg round trip.
    assert set(summary.columns) == {*trajectory_summary.SUMMARY_COLUMNS, "geometry"}
    register_layer = gpd.read_file(path, layer=trajectory_summary.REGISTER_LAYER)
    assert len(register_layer) == 3  # S9 included: located, ineligible
    assert set(register_layer["trajectory_status"]) == {
        "eligible",
        "insufficient_pixel_support",
    }


def test_gpkg_refuses_an_unlocated_eligible_site(tmp_path) -> None:
    register, traj, ctx = _small_world()
    register = register.copy()
    register.loc[register["site_id"] == "S1", "lon"] = None
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    )
    with pytest.raises(trajectory_summary.TrajectorySummaryError):
        trajectory_summary.write_summary_gpkg(
            summary_df=df, register_df=register, path=tmp_path / "x.gpkg"
        )


def test_gpkg_skips_unlocated_ineligible_sites_and_discloses_the_count(tmp_path) -> None:
    register, traj, ctx = _small_world()
    register = register.copy()
    register.loc[register["site_id"] == "S9", "lat"] = None
    df = trajectory_summary.assemble_summary(
        register_df=register, traj_df=traj, context_df=ctx
    )
    path = tmp_path / "trajectory_summary.gpkg"
    n_unlocated = trajectory_summary.write_summary_gpkg(
        summary_df=df, register_df=register, path=path
    )
    assert n_unlocated == 1
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trajectory_summary.py -q -k gpkg`
Expected: FAIL with `AttributeError: ... 'write_summary_gpkg'`

**Step 3: Implement** (append to module; add `import geopandas as gpd`
and `from pathlib import Path` at the top)

```python
def write_summary_gpkg(
    *, summary_df: pd.DataFrame, register_df: pd.DataFrame, path: Path
) -> int:
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
            "d3_forced_threshold": located["d3_forced_threshold"]
            .astype("boolean")
            .astype("Int64"),
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
```

**Step 4: Run tests**

Run: `uv run pytest tests/test_trajectory_summary.py -q`
Expected: PASS. Known risk: pyogrio may refuse a pandas nullable dtype
(`Int64`/`Float64`) on write. If it does, convert the frame's nullable
columns to numpy float64 (NULL→NaN) immediately before `to_file` inside
`write_summary_gpkg` — keep the nullable dtypes in the returned
assembly frames and the tests' semantics unchanged. Use kit:debugging
if the failure mode is anything else.

---

## Task 6: CLI command — refusal gates

**Files:**
- Modify: `src/wa_mine_monitor/cli.py` (add command after
  `build_context_join_cmd`, ~line 8930)
- Create: `tests/test_cli_trajectory_summary.py`

**Step 1: Write the failing tests.** The fixture reuses
`_seed_full_world` from `tests/test_context_join.py` (which seeds
trajectories, an eligible register, a crosswalk, a passing acceptance
verdict, and both context products) and then runs `build-context-join`
to produce the context-join input.

```python
"""CLI tests for build-trajectory-summary (Batch G). Fixture world:
tests/test_context_join.py's `_seed_full_world` -- three sites S1/S2/S3
over trajectory years {2000, 2001}, context year {2001} -- with the F6
context join built on top."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from tests.test_context_join import _seed_full_world
from tests.test_trajectory_qa import _seed_register
from wa_mine_monitor import manifests, trajectory_summary
from wa_mine_monitor.cli import app

runner = CliRunner()


def _seed_world_with_join(tmp_path: Path) -> tuple[Path, Path]:
    cfg, data_root = _seed_full_world(tmp_path)
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 0, result.output
    return cfg, data_root


def _build(cfg: Path, date: str = "2026-08-30"):
    return runner.invoke(
        app, ["build-trajectory-summary", "--config", str(cfg), "--date", date]
    )


def test_refuses_without_a_context_join(tmp_path: Path) -> None:
    cfg, _data_root = _seed_full_world(tmp_path)  # no build-context-join
    result = _build(cfg)
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_refuses_without_an_acceptance_verdict(tmp_path: Path) -> None:
    cfg, data_root = _seed_world_with_join(tmp_path)
    import shutil

    shutil.rmtree(data_root / "curated" / "trajectories-acceptance")
    result = _build(cfg)
    assert result.exit_code == 1
    assert "accept-trajectories" in result.output


def test_refuses_when_parts_changed_after_acceptance(tmp_path: Path) -> None:
    # Same TOCTOU discipline as build-context-join: a part rewritten
    # after acceptance -- with a fresh self-consistent sidecar -- is
    # refused on parts_digest.
    from wa_mine_monitor import trajectories
    from wa_mine_monitor.provenance import SourceAsset

    cfg, data_root = _seed_world_with_join(tmp_path)
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
    result = _build(cfg)
    assert result.exit_code == 1
    assert "parts_digest" in result.output or "part bytes" in result.output


def test_refuses_existing_output(tmp_path: Path) -> None:
    # The dated directory itself is the sentinel (design section 2 gate 1):
    # an existing -- even empty -- `curated/trajectory-summary/<date>/`
    # must refuse; checking only the .gpkg would let a stale or partial
    # directory be built into (codex plan-attack, 2026-08-30, finding 2).
    cfg, data_root = _seed_world_with_join(tmp_path)
    out_dir = data_root / "curated" / "trajectory-summary" / "2026-08-30"
    out_dir.mkdir(parents=True)  # deliberately empty: no gpkg inside
    result = _build(cfg)
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_refuses_context_join_built_from_different_trajectories(tmp_path: Path) -> None:
    # Version-skew gate: the context join's manifest must cite the SAME
    # trajectories directory this build consumes.
    cfg, data_root = _seed_world_with_join(tmp_path)
    join_path = (
        data_root / "curated" / "context-join" / "2026-08-30" / "context_join.parquet"
    )
    manifest_path = Path(str(join_path) + manifests.MANIFEST_SUFFIX)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["resolved_args"]["trajectories_dir"] = str(
        data_root / "curated" / "trajectories" / "1999-01-01"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = _build(cfg)
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_refuses_a_register_newer_than_the_acceptance(tmp_path: Path) -> None:
    # Register-binding gate (codex plan-attack, 2026-08-30, finding 1):
    # the acceptance verdict records `register_dir` (accept-trajectories
    # payload, cli.py); the summary consumes the register DIRECTLY, so a
    # register snapshot newer than the accepted one would let coordinates
    # or `d3_forced_threshold` drift out from under the accepted
    # trajectories. Seed a newer register after acceptance; refuse.
    cfg, data_root = _seed_world_with_join(tmp_path)
    _seed_register(data_root, "2026-08-30", [("S1", True), ("S2", True), ("S3", True)])
    result = _build(cfg)
    assert result.exit_code == 1
    assert "refusal" in result.output
    assert "register" in result.output
```

Note on the last test: rewriting the manifest invalidates nothing the
digest gate checks (the manifest's `output.sha256` still matches the
parquet bytes), so the refusal must come from the version-skew check
itself. If `_digest_verified_manifest` also validates manifest
self-integrity and refuses earlier, the test still passes — but confirm
by reading the refusal text during GREEN, and tighten the assertion to
the skew message if possible.

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_trajectory_summary.py -q`
Expected: FAIL with exit code 2 / "No such command 'build-trajectory-summary'"

**Step 3: Implement the command** (in `cli.py`, directly after
`build_context_join_cmd`; add `trajectory_summary` to the existing
`from wa_mine_monitor import (...)` block)

```python
@app.command("build-trajectory-summary")
def build_trajectory_summary_cmd(config: Path = ConfigOption, date: str = DateOption) -> None:
    """Build the Batch G per-site trajectory summary GeoPackage for the
    private QGIS project (design 2026-08-30):
    `curated/trajectory-summary/<date>/trajectory_summary.gpkg`, layers
    `register_sites` (all located register sites, categorised by QGIS on
    `trajectory_status`) and `site_summary` (one point per eligible
    site: coverage, per-metric latest observed values, fire and climate
    context, L4/L17 disclosures).

    Downstream consumer of `curated/trajectories`: the same
    accepted-extraction gates as `build-context-join` (verdict passed,
    summary sha256 and part bytes covered), plus a register-binding gate
    (the verdict's `register_dir` must be the very register this summary
    consumes) and a version-skew gate -- the latest context join must
    have been built FROM the same trajectories tree this summary
    consumes. Private curated artifact:
    it crosses no export boundary and `export_gate` is not involved.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    output_dir = data_root / "curated" / "trajectory-summary" / date
    output_path = output_dir / "trajectory_summary.gpkg"
    # The dated directory is the sentinel, not just the .gpkg: a stale or
    # partial `<date>/` left by an interrupted run must refuse too, or
    # `mkdir(exist_ok=True)` below would build mixed-run contents into an
    # immutable dated directory (design section 2 gate 1).
    if output_dir.exists():
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{output_dir} already exists -- dated curated directories are "
                        "immutable once written (even partially); move the existing "
                        "directory aside or choose a new --date"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    _refuse_if_curated_output_already_exists(
        output_path, config=resolved_config, git_state=git_state
    )

    # GATE 1 -- latest curated register, D3-eligibility-annotated
    # (same checks as fetch-silo GATE 3 / extract-trajectories GATE 2).
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
    for column in ("trajectory_status", "d3_forced_threshold"):
        if column not in register_df.columns:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"latest curated register is missing {column!r} -- run "
                            "apply-d3-threshold first"
                        ),
                        "register_path": str(register_path),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1)

    # GATE 2 -- trajectories tree: summary digest-verified, every part
    # digest- and schema-verified (build-context-join GATE 1).
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
    traj_columns = [
        "site_id",
        "maus_id",
        "year",
        "metric",
        "value",
        "computable",
        "collection_id",
        "shared_footprint_site_count",
        "d3_forced_threshold",
    ]
    traj_frames: list[pd.DataFrame] = []
    try:
        partitions = trajectory_extract.existing_partitions(trajectories_dir)
        for collection_id, year in sorted(partitions):
            partition = trajectory_extract.partition_dir(trajectories_dir, collection_id, year)
            for part in trajectory_extract.verified_parts(
                partition, expected_schema=trajectories.TRAJECTORY_SCHEMA
            ):
                traj_frames.append(pq.read_table(part, columns=traj_columns).to_pandas())
    except trajectory_extract.TrajectoryExtractError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    traj = pd.concat(traj_frames, ignore_index=True)

    # GATE 3 -- acceptance verdict: passed, covering the SAME summary
    # and part bytes (build-context-join GATE 2, verbatim discipline).
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
                        f"{exc} -- run accept-trajectories first: the QGIS summary "
                        "follows ACCEPTED Batch E extraction"
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
                        "the summary is refused until the extraction is accepted"
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
                        f"the acceptance verdict at {verdict_path} covers a different "
                        "extraction summary -- run accept-trajectories against the "
                        "current extraction first"
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
                        f"the acceptance verdict at {verdict_path} accepted different "
                        "part bytes (parts_digest mismatch) -- run accept-trajectories "
                        "again against the current tree"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if verdict.get("register_dir") != str(register_dir):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"the acceptance verdict at {verdict_path} accepted the "
                        f"extraction against the register at "
                        f"{verdict.get('register_dir')}, but this summary consumes "
                        f"{register_dir} -- the register_sites layer and the "
                        "site_summary disclosures must come from the very register "
                        "the acceptance inspected; run accept-trajectories against "
                        "the current register first"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # GATE 4 -- latest context join, digest-verified, built FROM this
    # trajectories tree (version-skew gate).
    try:
        join_dir = _latest_curated_dated_dir(
            data_root / "curated" / "context-join", label="curated/context-join"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    join_path = join_dir / "context_join.parquet"
    join_manifest = _digest_verified_manifest(join_path)
    cited_trajectories = join_manifest.get("resolved_args", {}).get("trajectories_dir")
    if cited_trajectories != str(trajectories_dir):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"the context join at {join_path} was built from "
                        f"{cited_trajectories}, but this summary consumes "
                        f"{trajectories_dir} -- rebuild the context join first"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    context_df = read_table(join_path)

    # Assemble, validate, write.
    try:
        summary_df = trajectory_summary.assemble_summary(
            register_df=register_df, traj_df=traj, context_df=context_df
        )
        eligible_sites = trajectory_extract.select_eligible_sites(register_df)
        trajectory_summary.validate_summary(summary_df, site_ids=eligible_sites)
        output_dir.mkdir(parents=True, exist_ok=True)
        n_unlocated = trajectory_summary.write_summary_gpkg(
            summary_df=summary_df, register_df=register_df, path=output_path
        )
    except trajectory_summary.TrajectorySummaryError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    input_assets = [
        SourceAsset(
            uri=str(register_path),
            sha256=register_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(register_dir.name),
            licence=None,
            redistribute_public=False,
        ),
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
            uri=str(join_path),
            sha256=join_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(join_dir.name),
            licence=None,
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
                "register_dir": str(register_dir),
                "trajectories_dir": str(trajectories_dir),
                "acceptance_dir": str(acceptance_dir),
                "context_join_dir": str(join_dir),
                "n_register_sites_unlocated": n_unlocated,
                "layers": [
                    trajectory_summary.REGISTER_LAYER,
                    trajectory_summary.SUMMARY_LAYER,
                ],
            },
        )
    except FileExistsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {
                "output_path": str(output_path),
                "manifest_path": str(output_path) + manifests.MANIFEST_SUFFIX,
                "n_eligible_sites": len(summary_df),
                "n_register_sites": int(register_df.shape[0]),
                "n_register_sites_unlocated": n_unlocated,
            },
            indent=2,
            sort_keys=True,
        )
    )
```

**Step 4: Run the refusal tests**

Run: `uv run pytest tests/test_cli_trajectory_summary.py -q`
Expected: the four refusal tests PASS; there is no success-path test
yet. If `_refuse_if_curated_output_already_exists`'s signature differs,
read its definition and match the existing call at `cli.py:8628` — do
not invent a variant.

---

## Task 7: CLI command — success path

**Files:**
- Modify: `tests/test_cli_trajectory_summary.py` (append)

**Step 1: Write the test**

```python
def test_build_trajectory_summary_writes_gpkg_and_manifest(tmp_path: Path) -> None:
    import geopandas as gpd
    import pyogrio

    cfg, data_root = _seed_world_with_join(tmp_path)
    result = _build(cfg)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    out = Path(payload["output_path"])
    assert out.exists()
    manifest = json.loads(
        Path(payload["manifest_path"]).read_text(encoding="utf-8")
    )
    assert manifest["resolved_args"]["n_register_sites_unlocated"] == 0
    assert {"register_sites", "site_summary"} == {
        name for name, _ in pyogrio.list_layers(out)
    }
    summary = gpd.read_file(out, layer=trajectory_summary.SUMMARY_LAYER)
    assert sorted(summary["site_id"]) == ["S1", "S2", "S3"]
    # EXACT pinned column set (design section 3): nothing extra may ride
    # along, nothing pinned may be dropped by the gpkg round trip.
    assert set(summary.columns) == {*trajectory_summary.SUMMARY_COLUMNS, "geometry"}
    # L4/L17 disclosures survive the round trip on every row.
    assert summary["shared_footprint_site_count"].notna().all()
    assert summary["d3_forced_threshold"].notna().all()


def test_second_run_refuses_the_existing_output(tmp_path: Path) -> None:
    cfg, _data_root = _seed_world_with_join(tmp_path)
    assert _build(cfg).exit_code == 0
    result = _build(cfg)
    assert result.exit_code == 1
    assert "refusal" in result.output
```

**Step 2: Run the CLI test file**

Run: `uv run pytest tests/test_cli_trajectory_summary.py -q`
Expected: PASS (8 tests). If the fixture world's context year (2001)
vs trajectory years {2000, 2001} interacts badly with a coverage
count, the defect is in the assembly, not the fixture — use
kit:debugging before touching either.

**Step 3: Run the module + CLI suites together**

Run: `uv run pytest tests/test_trajectory_summary.py tests/test_cli_trajectory_summary.py -q`
Expected: PASS

---

## Task 8: QML styles with schema drift-guard

**Files:**
- Create: `qgis/styles/register_sites.qml`
- Create: `qgis/styles/site_summary.qml`
- Modify: `tests/test_trajectory_summary.py` (append)

**Step 1: Write the failing drift-guard test**

```python
def test_qml_styles_reference_only_pinned_fields() -> None:
    """Every field a QML style references must exist in the layer it
    styles -- symbology silently degrades in QGIS when a field is
    renamed, so drift is caught here instead."""
    import re
    import xml.etree.ElementTree as ET
    from pathlib import Path

    styles_dir = Path(__file__).resolve().parents[1] / "qgis" / "styles"
    layer_fields = {
        "register_sites.qml": {"site_id", "trajectory_status", "d3_forced_threshold"},
        "site_summary.qml": set(trajectory_summary.SUMMARY_COLUMNS),
    }
    for name, allowed in layer_fields.items():
        path = styles_dir / name
        root = ET.parse(path).getroot()  # must parse as XML at all
        referenced: set[str] = set()
        for elem in root.iter():
            attr = elem.get("attr") or elem.get("fieldName")
            if attr:
                referenced.add(attr)
            for key in ("expression", "filter", "label"):
                value = elem.get(key)
                if value:
                    referenced.update(re.findall(r'"([A-Za-z0-9_]+)"', value))
        unknown = referenced - allowed
        assert not unknown, f"{name} references unknown field(s): {sorted(unknown)}"
        assert referenced, f"{name} references no fields at all"


def test_register_qml_categorises_every_trajectory_status() -> None:
    import xml.etree.ElementTree as ET
    from pathlib import Path

    from wa_mine_monitor.register import _TRAJECTORY_STATUSES

    path = Path(__file__).resolve().parents[1] / "qgis" / "styles" / "register_sites.qml"
    root = ET.parse(path).getroot()
    values = {c.get("value") for c in root.iter("category")}
    assert set(_TRAJECTORY_STATUSES) <= values
```

(`_TRAJECTORY_STATUSES` is private; if importing it is unacceptable to
the reviewer, add a public `TRAJECTORY_STATUSES` alias in `register.py`
re-exporting the same tuple, and use that in both the test and any
future callers.)

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_trajectory_summary.py -q -k qml`
Expected: FAIL (files do not exist)

**Step 3: Write `qgis/styles/register_sites.qml`.** Categorised
renderer on `trajectory_status`, Okabe-Ito colorblind-safe palette,
deliberately avoiding plain red/green semantics (design §1):

```xml
<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology" version="3.34.0">
  <renderer-v2 type="categorizedSymbol" attr="trajectory_status" forceraster="0" enableorderby="0">
    <categories>
      <category value="eligible" symbol="0" label="eligible (Tier 1 domain)" render="true"/>
      <category value="insufficient_pixel_support" symbol="1" label="insufficient pixel support" render="true"/>
      <category value="no_usable_footprint" symbol="2" label="no usable footprint" render="true"/>
      <category value="crosswalk_not_high_confidence" symbol="3" label="crosswalk not high confidence" render="true"/>
      <category value="threshold_not_computed" symbol="4" label="threshold not computed" render="true"/>
    </categories>
    <symbols>
      <symbol type="marker" name="0" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="0,114,178,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="size" type="QString" value="2.2"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="marker" name="1" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="230,159,0,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="size" type="QString" value="1.6"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="marker" name="2" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="204,121,167,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="size" type="QString" value="1.6"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="marker" name="3" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="86,180,233,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="size" type="QString" value="1.6"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="marker" name="4" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="153,153,153,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="size" type="QString" value="1.6"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
</qgis>
```

**Step 4: Write `qgis/styles/site_summary.qml`.** Rule-based renderer:
base symbol for every site, a distinct dashed-outline rule when
`d3_forced_threshold` (L4 visual disclosure), and a label rule showing
footprint sharing when `shared_footprint_site_count > 1` (L17):

```xml
<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology|Labeling" version="3.34.0" labelsEnabled="1">
  <renderer-v2 type="RuleRenderer" forceraster="0" enableorderby="0">
    <rules key="root">
      <rule key="criteria" filter="&quot;d3_forced_threshold&quot; = 0" label="eligible (criteria path)" symbol="0"/>
      <rule key="forced" filter="&quot;d3_forced_threshold&quot; = 1" label="eligible under forced-144 threshold (L4 disclosure)" symbol="1"/>
    </rules>
    <symbols>
      <symbol type="marker" name="0" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="0,114,178,255"/>
            <Option name="outline_color" type="QString" value="35,35,35,255"/>
            <Option name="outline_style" type="QString" value="solid"/>
            <Option name="size" type="QString" value="2.6"/>
          </Option>
        </layer>
      </symbol>
      <symbol type="marker" name="1" alpha="1">
        <layer class="SimpleMarker">
          <Option type="Map">
            <Option name="color" type="QString" value="0,114,178,255"/>
            <Option name="outline_color" type="QString" value="213,94,0,255"/>
            <Option name="outline_style" type="QString" value="dash"/>
            <Option name="outline_width" type="QString" value="0.8"/>
            <Option name="size" type="QString" value="2.6"/>
          </Option>
        </layer>
      </symbol>
    </symbols>
  </renderer-v2>
  <labeling type="rule-based">
    <rules key="labelroot">
      <rule key="shared" filter="&quot;shared_footprint_site_count&quot; &gt; 1"
            description="L17: footprint-level series shared with other MINEDEX sites">
        <settings>
          <text-style fieldName="'shared with ' || (&quot;shared_footprint_site_count&quot; - 1) || ' other sites'"
                      isExpression="1" fontSize="8"/>
        </settings>
      </rule>
    </rules>
  </labeling>
</qgis>
```

**Step 5: Run the drift-guard tests**

Run: `uv run pytest tests/test_trajectory_summary.py -q -k qml`
Expected: PASS. These QML files are hand-authored repo artifacts; the
tests bind them to the schema, and QGIS is the final validator when the
owner loads them (checkpoint step). Exact QML attribute spellings that
QGIS rejects at load time get fixed in the checkpoint pass, with the
drift tests keeping field references honest.

---

## Task 9: `qgis/README.md`

**Files:**
- Create: `qgis/README.md`

**Step 1: Write the README** (content, verbatim — adjust only the
claim-boundary sentence, which MUST be copied exactly from
`docs/plans/2026-08-15-wa-mine-rehab-monitor-design.md` §1; read that
section first and paste its claim sentence where indicated):

```markdown
# Private QGIS project

The private consumption surface for the Tier 1 product (Batch G,
re-scoped: `docs/decisions/2026-08-25-public-web-page-descope.md`,
`docs/decisions/2026-08-30-batch-g-qgis-only-rescope.md`). Nothing in
this directory or the data it displays crosses the public export
boundary.

## Claim boundary

The project title and every print layout footer must carry, verbatim:

> [PASTE design §1 claim-boundary sentence here]

Styling rules that enforce it: no red/green status styling anywhere;
`trajectory_status` is a processing status, not a performance verdict;
fire/climate context is displayed beside trajectories with cause not
determined.

## Data root

Set a QGIS project variable `data_root` (Project ▸ Properties ▸
Variables) pointing at this machine's data root. Layer sources below
are written against that variable so the project is not machine-pinned.

## Layers, load order

1. **RDC boundaries** — the raw DPIRD-020 snapshot under
   `<data_root>/raw/wa_rdc_regions/<date>/` (reference outline only).
   Apply `styles/rdc_boundaries.qml` if present, else a no-fill outline.
2. **register_sites** — layer `register_sites` of
   `<data_root>/curated/trajectory-summary/<date>/trajectory_summary.gpkg`.
   Apply `styles/register_sites.qml` (categorised on
   `trajectory_status`; all five categories, colorblind-safe, no
   red/green semantics).
3. **site_summary** — layer `site_summary` of the same GeoPackage.
   Apply `styles/site_summary.qml`:
   - dashed orange outline = site judged under the forced-144
     threshold (`d3_forced_threshold`, L4 disclosure);
   - label "shared with N−1 other sites" where
     `shared_footprint_site_count > 1` (L17 disclosure);
   - sensor overlap at a metric's latest year leaves
     `<metric>_latest` NULL with `<metric>_latest_collections > 1` —
     never attribute a value to a site whose collections disagree.

`register_sites` omits sites with no register location (they cannot be
points); the omitted count is in the GeoPackage's run manifest
(`n_register_sites_unlocated`). `d3_forced_threshold` on that layer is
1/0/NULL — NULL means the site was never judged.

## Saving the project

Save as `qgis/wa-mine-monitor.qgz` (QGIS ≥ 3.34) after: setting the
`data_root` variable, loading the three layers, applying the styles,
and pasting the claim-boundary sentence into the project title and any
layout footer.

## Refresh

New curated date → re-run `wa-mine-monitor build-trajectory-summary`,
then re-point the two GeoPackage layers at the new dated directory.
The gpkg is immutable per date; never edit one in place.
```

**Step 2: Verify the claim sentence was actually pasted**

Run: `grep -c "PASTE design" qgis/README.md`
Expected: `0` (the placeholder must be gone, replaced by the verbatim
design §1 sentence).

---

## Task 10: Decision record, amendment A11, ROADMAP, checkpoint stub

**Files:**
- Create: `docs/decisions/2026-08-30-batch-g-qgis-only-rescope.md`
- Modify: `docs/amendments-and-limitations.md` (append A11 next to A10)
- Modify: `docs/ROADMAP.md` (header + row 5)
- Create: `docs/checkpoints/batch-g-qgis.md`

**Step 1: Read the neighbouring records first** — the latest decision
record and A10's entry — and match their format exactly (dated heading,
"Decision", "Why", "What changes", citations). Content requirements:

- **Decision record:** Owner decision 2026-08-30 (chat): Batch G closes
  QGIS-only. No trajectory/register/context package is added to
  `release.PACKAGES`; the release decision is explicitly NOT taken, and
  the D7 question a public `site_id`-keyed trajectory release would
  raise (MINEDEX row-level records / crosswalk membership at the export
  boundary; the `source_id`-based row gate would not catch `site_id`
  columns) is deferred WITH the release, recorded here so it cannot be
  forgotten if a release is later decided. Deliverables: the
  `build-trajectory-summary` curated GeoPackage, QML styles, README,
  interactively-saved `.qgz`. Cites: ROADMAP row 5, A8
  (`2026-08-25-public-web-page-descope.md`), L10's "only when a release
  of it is actually decided" language, the approved design doc.
- **A11:** one amendments-register entry, same style as A10, pointing
  at the decision record; note L10/L11 wording is unchanged (they were
  already closed/re-scoped; this narrows ROADMAP row 5 only).
- **ROADMAP:** header "Current to 2026-08-29" → "Current to
  2026-08-30"; row 5 rewritten: gate satisfied (E4 accepted
  2026-08-30), scope narrowed per the decision record — deliverables
  now `build-trajectory-summary` + private QGIS project; release
  packages deferred by decision, with the decision record cited in the
  Record column.
- **Checkpoint stub:** `docs/checkpoints/batch-g-qgis.md` matching the
  house checkpoint format (see `docs/checkpoints/e4-statewide-extraction.md`),
  with empty fields for: build date, gpkg path + sha256, n_eligible
  (expect 10,372), n_register_sites, n_register_sites_unlocated,
  verification battery result, and the owner's `.qgz` save + open
  confirmation. Populated only during the live run.

**Step 2: Verify docs consistency**

Run: `grep -n "2026-08-29" docs/ROADMAP.md | head -3`
Expected: no "Current to 2026-08-29" header line remains (dated
citations inside rows are fine).

---

## Task 11: Licence conformance, full battery

The licence-conformance suite pins asset-declaration line numbers in
`cli.py` and has broken on unrelated insertions before (2026-08-30:
"exemptions have stale line numbers"). This task absorbs that.

**Step 1: Run the conformance test**

Run: `uv run pytest tests/test_licence_conformance.py -q`
Expected: PASS, or failures naming shifted line numbers / new literals.

**Step 2: If it failed:** update the exemption entries for the shifted
lines exactly as the 2026-08-30 fix did (see commit `8e085ff`). The new
command declares no new licence literals (`licence=None` on every
SourceAsset), so only line-shift updates should be needed; any other
conformance failure is real — kit:debugging, do not blanket-exempt.

**Step 3: Full battery, CI order**

Run:
```
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src scripts
uv run pytest -q -rs
```
Expected: all four green (baseline was 1205 passed; expect ~+20).
mypy note: geopandas/pyogrio are already imported by `cli.py`
(public-RC lane) under the existing override config — new imports
follow the same pattern; do not add new global ignores.

---

## Live run and checkpoint (after merge — owner-paced, not part of the build)

Not executed by build-flow. Recorded here so the checkpoint's shape is
known: `uv run wa-mine-monitor build-trajectory-summary --config
<config> --date 2026-08-30` against the real data root (expect
n_eligible_sites=10,372); populate `docs/checkpoints/batch-g-qgis.md`;
owner opens QGIS ≥3.34, follows `qgis/README.md`, saves
`qgis/wa-mine-monitor.qgz`, confirms both layers render with styles and
the claim-boundary text in place.
