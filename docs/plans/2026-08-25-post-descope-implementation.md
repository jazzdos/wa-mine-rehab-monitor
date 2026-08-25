# Post-Descope Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Advance the roadmap's build sequence after the public-web-page
descope (amendment A8): execute the live Batch E plan unchanged, then wire
the export boundary (`export-release`, closing L10 and re-scoping L11),
fix the O8 eligibility-replay divergence, and deliver the private QGIS
project.

**NOT in this plan** (still gated, per `docs/ROADMAP.md`): Batch F context
joins (SILO account absent, O7; DBCA route blocked), trajectory/register
release packages (ROADMAP row 5 gates Batch G's product releases on
accepted Tier 1 — this plan builds the release *mechanism* and one
intermediate-artefact package, which is not Batch G closure), and Tier 2.

**Architecture:** Phase 1 is the existing FINAL plan
`docs/plans/2026-08-22-batch-e-e4-e5.md`, adopted by reference and NOT
duplicated here — execute it exactly as written (E5 engine parity, Task 0
forced-threshold path, E4 extraction). Phase 2 adds the post-descope work
this plan fully specifies: `export_gate.export_public` gains its first
production caller via a package-registry `export-release` command; the
coordinate-column gap (`lon`/`lat`) closes with exact-name matching, not
substring tokens; the O8 replay is fixed by deleting the diag script's
parallel reimplementation in favour of the production eligibility function;
the QGIS project replaces Batch G rendering per A8.

**Tech Stack:** Python 3.12, uv, typer, pandas/pyarrow/geopandas, pytest.
Conventions: `kit:code-standards` (python) before editing; fixture-first
TDD; immutable dated artefacts; JSON refusals via `typer.echo` + `typer.Exit(1)`.

**Task ordering:** Phase 2 Tasks 1–2 are independent of Phase 1 and can run
first or in parallel with E5 — that covers the CODE and its tests. Emitting
a real dated release under `<data_root>/releases/` is a separate act:
`footprint-areas` (an intermediate Maus-derived table) may be emitted once
the command lands, but trajectory/register packages are added to the
registry only after accepted Tier 1 (ROADMAP row 5), and no register edit
made by this plan may describe Batch G as closed. Task 3 REQUIRES Phase 1
Task 0 (the `forced_threshold` argument must exist). Task 4 requires E4
output and is manual. Task 5 is the battery.

---

## Phase 1: Execute the live Batch E plan (by reference)

**Files:** `docs/plans/2026-08-22-batch-e-e4-e5.md` (Status: FINAL 2026-08-25)

Execute its tasks in its own order, unchanged:
- E5 engine parity (runnable immediately; no DEA reads; verdict artefact
  under `curated/huntly-validation/<date>/` is the sole unlock for
  statewide extraction — open item O3),
- Task 0 forced-threshold eligibility path (`d3_forced_threshold` column,
  `--forced-threshold`/`--decision-record` flags, `criteria_passed=False`
  preserved; decision record
  `docs/decisions/2026-08-25-batch-e-forced-threshold-entry.md`),
- E4 footprint-keyed extraction with `shared_footprint_site_count` and
  `valid_support_px` on every row.

Nothing in the descope (A8) alters that plan: it contains no rendering or
web-page tasks (verified by grep, 2026-08-25).

---

## Phase 2, Task 1: Coordinate columns close the geometry name gap

L11: `export_gate.GEOMETRY_NAME_TOKENS` omits `lon`/`lat` while
`REGISTER_SCHEMA` declares both (`src/wa_mine_monitor/register.py:108-109`).
A substring token is WRONG here: `"lat"` is a substring of `cumulative`, so
the fix is a separate exact-name set, not two new tokens.

**Files:**
- Modify: `src/wa_mine_monitor/export_gate.py:131-137`
- Test: `tests/test_export_gate.py` (append)

**Step 1: Write the failing tests**

```python
def test_coordinate_names_are_geometry_bearing() -> None:
    series = pd.Series([115.8, 116.1])
    for name in ("lon", "lat", "longitude", "latitude", "LON", "Latitude"):
        assert export_gate.is_geometry_column(name, series), name


def test_coordinate_matching_is_exact_not_substring() -> None:
    # "lat" must not fire inside an ordinary word: a substring rule would
    # drop `cumulative_area_m2` (contains "lat") from every export.
    series = pd.Series([1.0, 2.0])
    assert not export_gate.is_geometry_column("cumulative_area_m2", series)
    assert not export_gate.is_geometry_column("dilation_px", series)


def test_export_public_drops_lon_lat() -> None:
    frame = pd.DataFrame(
        {"site_id": ["a"], "lon": [115.8], "lat": [-31.9], "redistribute_public": [True]}
    )
    published = export_gate.export_public(frame)
    assert sorted(published.columns) == ["site_id"]
    assert "lon" in published.attrs["export_public"]["dropped_columns"]
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_export_gate.py -q -k coordinate or drops_lon`
Expected: FAIL (`is_geometry_column("lon", ...)` returns False today).

**Step 3: Implement**

In `export_gate.py`, after `GEOMETRY_NAME_TOKENS` (keep the existing tuple
unchanged), add and wire:

```python
#: Column NAMES that are a coordinate by themselves. Exact match on the
#: lower-cased name -- NOT substring tokens like the set above, because
#: "lat" is a substring of "cumulative" and "dilation", and a licence gate
#: that silently drops an ordinary measured column is the quietly-lossy
#: failure this module's docstring names. `REGISTER_SCHEMA` declares
#: `lon`/`lat` (register.py); a point coordinate is geometry however it is
#: spelled, same reasoning as `easting`/`northing` above.
COORDINATE_COLUMN_NAMES: frozenset[str] = frozenset({"lon", "lat", "longitude", "latitude"})


def _has_geometry_name(column: str) -> bool:
    """True when the column's name marks it as geometry-bearing."""
    lowered = column.lower()
    if lowered in COORDINATE_COLUMN_NAMES:
        return True
    return any(token in lowered for token in GEOMETRY_NAME_TOKENS)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_export_gate.py -q`
Expected: PASS, no regressions in the existing gate tests.

---

## Phase 2, Task 2: `export-release` — the gate's first production caller

Closes L10 (its stated gap is the ABSENCE of a standalone, refusal-tested
export command) and re-scopes — does NOT close — the L11 half about the
non-null licence criterion: the command attaches `redistribute_public` from
`licence.SOURCES` per package, and `export_public`'s existing row gate
already fail-closes on absent/null/non-bool values, so the criterion is
enforced at the boundary FOR ANYTHING EXPORTED. The Tier 0 register itself
is never exported (no register package exists, and MINEDEX rows must
refuse), so the design §4 criterion as written against register rows is
never exercised; the L11 register edit must say exactly that, not "closed".

Start with ONE package — `footprint-areas` (wholly Maus CC-BY-SA-4.0
lineage, no geometry columns, `redistribute_public=True`) — in an
extensible registry. YAGNI: trajectory/register packages are added only
when a release of them is actually decided (register rows are
MINEDEX-restricted and must refuse; that refusal is tested now).

**Files:**
- Create: `src/wa_mine_monitor/release.py`
- Modify: `src/wa_mine_monitor/cli.py` (new command, mirror
  `build-maus-footprint-areas` at `cli.py:2444-2617` for config load, git
  state, snapshot verification, refusal guard, manifest write)
- Modify: `src/wa_mine_monitor/export_gate.py:9-22` (delete the
  "enforces NOTHING" paragraph — its own text instructs this once the
  caller exists)
- Modify: `docs/amendments-and-limitations.md` (L10/L11 wording: gate now
  wired; keep the share-alike-scalar caveat, which stays true)
- Test: `tests/test_release.py` (create), `tests/test_cli_export_release.py` (create)

**Step 1: Write the failing tests for the package registry**

```python
# tests/test_release.py
import pandas as pd
import pytest

from wa_mine_monitor import release


def test_footprint_areas_package_is_registered() -> None:
    spec = release.PACKAGES["footprint-areas"]
    assert spec.curated_dir == "maus_footprint_areas"
    assert spec.filename == "footprint_areas.parquet"
    assert spec.source_id == "maus_v2"
    assert spec.output_licence == "CC-BY-SA-4.0"
    assert spec.share_alike is True
    # CC-BY-SA requires a modification statement in the released package
    # (licence.py maus_v2 notes: "attribution, source link and modification
    # statement"); it is package-specific, so it lives on the spec.
    assert "Maus" in spec.modification_statement


def test_attribution_block_carries_the_full_grant() -> None:
    # The attribution artefact is assembled from the licence registry, never
    # hand-written per release: attribution text, source link, licence link,
    # and the package's modification statement, all non-empty.
    block = release.attribution_block(release.PACKAGES["footprint-areas"])
    assert "Maus" in block
    assert "PANGAEA" in block                       # source link
    assert "creativecommons.org/licenses/by-sa" in block
    assert release.PACKAGES["footprint-areas"].modification_statement in block


def test_prepare_for_export_attaches_row_gate_from_source() -> None:
    frame = pd.DataFrame({"maus_id": ["m1"], "footprint_area_m2": [900.0]})
    prepared = release.prepare_for_export(frame, release.PACKAGES["footprint-areas"])
    assert prepared["redistribute_public"].tolist() == [True]


def test_prepare_for_export_refuses_unknown_package_source() -> None:
    # A package whose source is not in licence.SOURCES must refuse, never
    # default: an unregistered licence is UNKNOWN, and unknown is not
    # permitted (same rule as the row gate's null case).
    bad = release.PackageSpec(
        curated_dir="x", filename="x.parquet", source_id="not_a_source",
        output_licence="CC-BY-4.0", share_alike=False,
    )
    with pytest.raises(KeyError):
        release.prepare_for_export(pd.DataFrame({"a": [1]}), bad)
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_release.py -q`
Expected: FAIL with `ModuleNotFoundError: wa_mine_monitor.release`.

**Step 3: Implement `release.py`**

```python
"""Release package specs: what `export-release` may publish, and under what
licence lineage. One spec per package; the registry is the closed list of
things this project releases -- a package absent here cannot be exported.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from wa_mine_monitor import export_gate, licence


@dataclass(frozen=True)
class PackageSpec:
    curated_dir: str      # under <data_root>/curated/
    filename: str         # artefact inside the dated directory
    source_id: str        # key into licence.SOURCES for the row gate
    output_licence: str   # recorded in the release manifest
    share_alike: bool     # recorded in the release manifest
    modification_statement: str  # CC-BY-SA obligation; package-specific


PACKAGES: dict[str, PackageSpec] = {
    "footprint-areas": PackageSpec(
        curated_dir="maus_footprint_areas",
        filename="footprint_areas.parquet",
        source_id="maus_v2",
        output_licence="CC-BY-SA-4.0",
        share_alike=True,
        modification_statement=(
            "Modified from the Maus et al. v2 polygons: WA extract, "
            "reprojected to EPSG:3577, per-footprint areas computed; "
            "no polygon geometry is included in this table."
        ),
    ),
}


def attribution_block(spec: PackageSpec) -> str:
    """The licence notice shipped WITH the released package.

    Assembled from `licence.SOURCES` (attribution text, source URL, licence
    URL) plus the package's modification statement -- the three CC-BY-SA
    obligations the maus_v2 registry entry names. Never hand-written per
    release; the registry is the single source.
    """
    source = licence.SOURCES[spec.source_id]
    return "\n\n".join(
        [
            source.attribution_text,
            f"Source: {source.source_url}",
            f"Licence: {source.licence_id} ({source.licence_url})",
            spec.modification_statement,
        ]
    )


def prepare_for_export(frame: pd.DataFrame, spec: PackageSpec) -> pd.DataFrame:
    """Attach the row gate from the source's own licence registry entry.

    `export_public` then enforces it: absent, null, or False refuses the
    whole frame. The licence decision is made once, in `licence.SOURCES`,
    and carried here -- never asserted per-call.
    """
    source = licence.SOURCES[spec.source_id]  # KeyError = unknown source, refuse
    prepared = frame.copy()
    prepared[export_gate.REDISTRIBUTE_COLUMN] = bool(source.redistribute_public)
    return prepared
```

(Verified 2026-08-25: `licence.SOURCES` is a public dict of `SourceLicence`
entries carrying `redistribute_public`, `attribution_text`, `source_url`,
`licence_id`, `licence_url` — `licence.py:215-226`. Do NOT add a second
licence lookup path.)

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_release.py -q`
Expected: PASS.

**Step 5: Write the failing CLI test**

```python
# tests/test_cli_export_release.py -- follow the fixture pattern of the
# existing CLI tests (in-tmp data_root, prebuilt curated snapshot dir with
# manifest, typer.testing.CliRunner).

def test_export_release_writes_gated_package(tmp_data_root, cli_runner) -> None:
    # Arrange: curated/maus_footprint_areas/2026-08-25/footprint_areas.parquet
    # exists with a valid manifest (reuse the existing test fixture builder
    # for build-maus-footprint-areas outputs).
    result = cli_runner.invoke(
        app, ["export-release", "--package", "footprint-areas",
              "--date", "2026-08-25", "--config", str(tmp_config)],
    )
    assert result.exit_code == 0, result.output
    out = tmp_data_root / "releases" / "2026-08-25" / "footprint-areas"
    published = pd.read_parquet(out / "footprint_areas.parquet")
    assert "redistribute_public" not in published.columns
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["resolved_args"]["output_licence"] == "CC-BY-SA-4.0"
    assert manifest["resolved_args"]["output_share_alike"] is True
    # The CC-BY-SA obligations ship WITH the package, not only in the
    # manifest: attribution, source link, licence link, modification
    # statement (licence.py maus_v2 notes).
    attribution = (out / "ATTRIBUTION.txt").read_text()
    assert attribution == release.attribution_block(
        release.PACKAGES["footprint-areas"]
    )


def test_export_release_refuses_restricted_rows(tmp_data_root, cli_runner) -> None:
    # A frame carrying redistribute_public=False anywhere must refuse the
    # WHOLE package (PermissionError surfaced as a JSON refusal, exit 1) --
    # never filter. This pins D13 Batch G: "Row filtering is prohibited; a
    # mixed package fails as a whole."
    ...


def test_export_release_refuses_existing_output(tmp_data_root, cli_runner) -> None:
    # Second run against the same --date refuses before reading anything,
    # same guard as build-maus-footprint-areas.
    ...
```

**Step 6: Run to verify failure**

Run: `uv run pytest tests/test_cli_export_release.py -q`
Expected: FAIL (`export-release` not a registered command).

**Step 7: Implement the CLI command**

In `cli.py`, mirror `build-maus-footprint-areas` exactly (docstring
discipline included):

- `@app.command("export-release")` with `--package` (must be in
  `release.PACKAGES`, else JSON refusal), `--date`, `--config`.
- Refuse-before-read: `_refuse_if_curated_output_already_exists`-equivalent
  guard on `<data_root>/releases/<date>/<package>/`.
- Read `<data_root>/curated/<spec.curated_dir>/<date>/<spec.filename>`
  through the existing digest-verified-manifest helper
  (`_digest_verified_manifest`, cli.py:729) — an unverified artefact never
  exports.
- `release.prepare_for_export` → `export_gate.export_public` (let
  `PermissionError` become a JSON refusal + exit 1).
- Write parquet + `ATTRIBUTION.txt` (content =
  `release.attribution_block(spec)`, byte-exact) + immutable run manifest
  (one `SourceAsset` input: the curated artefact actually read;
  `resolved_args` carrying package name, dates, `output_licence`,
  `output_share_alike`, the attribution block, and the
  `attrs["export_public"]["dropped_columns"]` record).

**Step 8: Run to verify pass**

Run: `uv run pytest tests/test_cli_export_release.py tests/test_release.py tests/test_export_gate.py -q`
Expected: PASS.

**Step 9: Delete the "enforces NOTHING" paragraph**

`export_gate.py:9-22` instructs its own deletion once the caller exists.
Replace with two sentences: enforcement happens in `export-release`
(`cli.py`), and the share-alike-scalar caveat below still holds.
Update `docs/amendments-and-limitations.md`:
- L10: "closed by `export-release`, commit <sha>" — the stated gap was the
  absence of a standalone, refusal-tested export command, and that command
  now exists with its refusal pinned by test. This is NOT Batch G closure:
  ROADMAP row 5's product releases (trajectory packages) stay gated on
  accepted Tier 1, and no wording here may imply otherwise.
- L11: coordinate-token half closed by Task 1. Non-null-licence half
  RE-SCOPED, not closed: enforced by the row gate at the boundary for every
  exported package; the Tier 0 register itself is never exported, so the
  design §4 criterion as written against register rows is not exercised —
  record that residue explicitly.

**Step 10: Run the export-gate and CLI test files once more**

Run: `uv run pytest tests/test_export_gate.py tests/test_cli_export_release.py -q`
Expected: PASS.

---

## Phase 2, Task 3: O8 — replay calls the production function (REQUIRES Phase 1 Task 0)

Open item O8: `scripts/diag_batch_e_readiness.py` replays eligibility with
its own `_judged` join (`diag_batch_e_readiness.py:78-104`) and buckets 933
never-judged sites differently from
`register.assign_trajectory_eligibility` (31,766 vs 30,833
`no_usable_footprint`; 7,488 vs 8,421 `crosswalk_not_high_confidence`).
The narrowed hypothesis (register run manifest digest matches the on-disk
crosswalk, so input identity is refuted) is that the two implementations'
bucketing semantics differ — e.g. production rule 1 sends a
low-confidence-matched site whose `maus_id` has no computed support to
`no_usable_footprint`, while `_judged` reads confidence first.

The fix is structural, not a patch to `_judged`: a diagnostic that
reimplements the join can drift again. Delete the reimplementation; call
the production function.

**Files:**
- Modify: `scripts/diag_batch_e_readiness.py:78-104` (delete `_judged`),
  `check_eligibility`, `check_sharing` (both consume `_judged`'s frame)
- Test: `tests/test_diag_replay_parity.py` (create)

**Step 1: Write the failing parity test**

Build minimal fixtures covering every status bucket AND the divergence
class. Exact fixture rows:

```python
# tests/test_diag_replay_parity.py
"""Pins O8: the diag replay and the production eligibility function must
produce identical trajectory_status counts on a population that contains
the never-judged divergence class (a low-confidence match whose maus_id
carries no computed support)."""

def _fixture_frames():
    register_df = pd.DataFrame({"site_id": [f"s{i}" for i in range(6)], ...})
    crosswalk_df = pd.DataFrame({
        # s0: high-confidence match, support computed, >=144  -> eligible
        # s1: high-confidence match, support computed, <144   -> insufficient_pixel_support
        # s2: high-confidence match, maus_id has NO support row -> no_usable_footprint (rule 1)
        # s3: LOW-confidence match, maus_id has NO support row  -> the O8 class:
        #     production must bucket it identically on both paths
        # s4: low-confidence match, support computed            -> crosswalk_not_high_confidence
        # s5: absent from crosswalk entirely                    -> no_usable_footprint
        ...
    })
    footprint_support_df = pd.DataFrame({...})
    return register_df, crosswalk_df, footprint_support_df


def test_replay_counts_equal_production_counts() -> None:
    register_df, crosswalk_df, support_df = _fixture_frames()
    production = register.assign_trajectory_eligibility(
        register_df, crosswalk_df, support_df,
        n_star=144, criteria_passed=False, forced_threshold=True,
    )["trajectory_status"].value_counts().to_dict()
    replay = diag_batch_e_readiness.replay_eligibility(
        register_df, crosswalk_df, support_df
    )["trajectory_status"].value_counts().to_dict()
    assert replay == production


def test_replay_frame_carries_the_diagnostic_columns() -> None:
    # The production function returns the register plus the four
    # eligibility columns ONLY (register.py:1352-1357) — it does not carry
    # `maus_id` or `region`, which `check_eligibility`/`check_sharing`
    # read. `replay_eligibility` must append them, or the diagnostics
    # KeyError at runtime while a counts-only parity test stays green.
    register_df, crosswalk_df, support_df = _fixture_frames()
    replay = diag_batch_e_readiness.replay_eligibility(
        register_df, crosswalk_df, support_df
    )
    for column in (
        "trajectory_status",
        "effective_pixel_support_px",
        "maus_id",
        "region",
    ):
        assert column in replay.columns, column
    # The appended columns are lookups, never judgements: the maus_id for a
    # judged site must be the same one production's dedup rule picked.
    judged = replay["trajectory_status"] == "eligible"
    assert replay.loc[judged, "maus_id"].notna().all()
```

(`forced_threshold=True` is Phase 1 Task 0's signature; import the script
via its path with `importlib` or move the replay into a small importable
helper — follow whatever pattern `tests/` already uses for `scripts/`,
check `uv run mypy scripts` stays green.)

**Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_diag_replay_parity.py -q`
Expected: FAIL — `replay_eligibility` does not exist yet (and `_judged`'s
counts would diverge on the s3 fixture row if it did).

**Step 3: Implement**

In `diag_batch_e_readiness.py`: add `replay_eligibility(register_df,
crosswalk_df, support_df)` that:

1. calls `register.assign_trajectory_eligibility(..., 
   n_star=FULL_SUPPORT_PX, criteria_passed=False, forced_threshold=True)`
   — ALL bucketing comes from the production function;
2. appends the two diagnostic-only columns the check functions read, as
   pure lookups that cannot change any status: `maus_id` via the SAME
   deterministic dedup production uses (`sort_values(["site_id",
   "maus_id"], kind="stable").drop_duplicates("site_id", keep="first")`,
   mapped on `site_id`), and `region` mapped from the support frame on
   that `maus_id`;
3. returns the combined frame.

Rewrite `check_eligibility`/`check_sharing` to consume it, reading
`trajectory_status` (production's verdict) instead of recomputing
`judged`/`eligible` from `support_px` and `confidence`; the production
frame already carries `effective_pixel_support_px`, which replaces the old
`support_px`. Delete `_judged`. The printed six-count section now
reproduces `apply-d3-threshold --forced-threshold` by construction, and
the replay frame demonstrably feeds both diagnostics
(`test_replay_frame_carries_the_diagnostic_columns`).

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_diag_replay_parity.py -q && uv run mypy scripts`
Expected: PASS / no issues.

**Step 5: Close O8 in the register**

`docs/amendments-and-limitations.md`: strike O8 with "Closed <date>:
replay now calls the production function
(`tests/test_diag_replay_parity.py`); the 933-site divergence was the
replay's own join semantics, retired with the reimplementation." (Adjust
wording to what the fixture actually demonstrates.)

---

## Phase 2, Task 4: Private QGIS project (REQUIRES E4 output; manual)

Not TDD-able and mostly interactive; acceptance criteria replace tests.
Per A8 (`docs/decisions/2026-08-25-public-web-page-descope.md`) this
replaces all Batch G rendering.

**Files:**
- Create: `qgis/README.md` (layer sources, load order, styling rules)
- Create: `qgis/wa-mine-monitor.qgz` (saved interactively in QGIS ≥ 3.34)

**Steps:**
1. Layers: eligible register (site points from curated register parquet),
   trajectory summary joined on `site_id`, RDC boundaries snapshot.
2. Styling: categorise on `trajectory_status`; label rule shows
   "shared with N−1 other sites" when `shared_footprint_site_count > 1`;
   `d3_forced_threshold=true` styled with a distinct outline — the L4/L17
   disclosures travel visually.
3. Claim boundary: project title and layout footer carry the README's
   claim-boundary sentence verbatim; no red/green status styling
   (design §1: "no unqualified red/green status styling").
4. Acceptance: opens on the MacBook against the lux-synced data root;
   every layer loads; disclosures render; `qgis/README.md` documents the
   data-root variable so paths are not machine-pinned.

---

## Phase 2, Task 5: Full verification battery

Run, in CI order, after each task and finally for the whole plan:

```
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src scripts
uv run pytest -q -rs
```

Expected: all green; pytest count strictly greater than the 737 baseline
(2026-08-25). Then `kit:verify` before any completion claim.
