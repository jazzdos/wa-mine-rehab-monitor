# Batch G (re-scoped): trajectory summary and private QGIS project — design

Date: 2026-08-30. Status: approved by owner (chat, 2026-08-30).
Supersedes nothing; narrows ROADMAP row 5 per the owner decisions below.

## 1. Owner decisions, fixed (this session)

1. **QGIS-only Batch G closure.** No trajectory, register, or context
   release package is added to `release.PACKAGES` in this batch. The
   docs' rule — "a register/trajectory package is added only when a
   release of it is actually decided" (amendments L10 note) — is
   exercised here as a deferral: the release decision is NOT taken.
   Public export of Tier 1 is deferred, which also defers the
   unresolved D7 question (trajectory rows are `site_id`-keyed;
   MINEDEX-derived row-level records and crosswalk membership must not
   cross the export boundary, and the `source_id`-based row gate would
   not catch this today). A decision record
   (`docs/decisions/2026-08-30-batch-g-qgis-only-rescope.md`) and an
   amendments entry (A11) ship with this batch.
2. **Context folded into the summary.** The Task 4 layer spec
   (post-descope plan, 2026-08-25) predates F6. Because D13 ties
   interpretation to displayed fire/climate context ("cause not
   determined" until context is shown), the per-site summary carries
   fire and climate context fields; no separate context layer.
3. **GeoPackage output.** The QGIS-facing artifact is a `.gpkg` with
   real point geometry (built from register lon/lat), not GeoParquet —
   loads in any QGIS ≥3.x regardless of Parquet driver availability.
   Curated parquet artifacts remain the citable spine; the gpkg is a
   consumption artifact with its own run manifest.
4. **Approach A.** A disciplined CLI command plus generated QML styles;
   the `.qgz` itself is saved interactively by the owner (per the
   post-descope plan Task 4, "manual").

## 2. `build-trajectory-summary` command

`wa-mine-monitor build-trajectory-summary --config <path> --date
<YYYY-MM-DD>`. Input resolution mirrors `build-context-join`: latest
curated register, latest trajectories tree, latest acceptance verdict,
latest context-join.

Gates, in order; every failure is a JSON refusal + exit 1:

1. Refuse-before-read if `curated/trajectory-summary/<date>/` exists.
2. Trajectory acceptance verdict required: exists, `passed == True`
   (boolean), `extraction_summary_sha256` and `parts_digest` re-verified
   against the actual trajectories tree (reuse
   `trajectory_qa.parts_digest`; identical gate to `build-context-join`),
   AND the verdict's `register_dir` must equal the register directory
   this summary consumes (planning amendment, 2026-08-30, codex
   plan-attack finding: the verdict records the register the acceptance
   inspected; unlike `build-context-join`, the summary consumes the
   register directly, so a newer register with drifted coordinates or
   `d3_forced_threshold` must refuse, not silently mix provenance).
3. Digest-verified manifest reads of register and context_join; the
   context-join manifest must cite the same trajectories snapshot being
   summarised (refuse on version skew).

Output: `curated/trajectory-summary/<date>/trajectory_summary.gpkg`
with TWO layers (planning amendment, 2026-08-30: the summary domain is
eligible-only, so a `trajectory_status` categorisation is degenerate on
it — the Task 4 "categorise on trajectory_status" reading belongs to a
full-register layer):

- `register_sites` — every located register site (site_id,
  trajectory_status, d3_forced_threshold, point geometry), the layer
  QGIS categorises on `trajectory_status`; unlocated sites cannot be
  points and their count is disclosed in the run manifest.
- `site_summary` — one row per eligible site (10,372 at current
  snapshots) carrying the full summary schema below.

Point geometry from register lon/lat (EPSG:4326; QGIS reprojects
against EPSG:3577 reference layers), plus the standard
`.run_manifest.json` sidecar via `manifests.write_run_manifest`.

## 3. Summary schema (pinned)

- Identity: `site_id`, `maus_id`, `trajectory_status` (register field;
  a processing status, not a performance verdict).
- Row-level disclosures: `shared_footprint_site_count` (L17),
  `d3_forced_threshold` (L4). Present on every row.
- Coverage: `year_min`, `year_max`, `years_observed`,
  `years_computable`, `years_not_computable`, `context_complete_years`
  (`years_observed` added as a planning amendment, 2026-08-30: the
  denominator that makes `years_not_computable` interpretable).
- Per-metric latest observed values: `<metric>_latest`,
  `<metric>_latest_year`, `<metric>_latest_collections`, metric list
  pinned from the trajectories schema constant. The collections column
  is the sensor-overlap disclosure (planning amendment, 2026-08-30,
  making the approved overlap ruling explicit in the pinned schema):
  when more than one collection is computable at a metric's latest
  year, `<metric>_latest` is NULL and `<metric>_latest_collections`
  discloses the count — disagreement between collections is preserved,
  never resolved by priority. Observed values only — no trend,
  recovery, or classification columns (claim boundary, design §1).
- Fire context, three-state preserved: `fire_status_latest` ∈
  {recorded, not_recorded, unknown}; `fire_years_recorded` (count);
  `last_recorded_fire_year` (null when none recorded — never coerced
  to a known-negative).
- Climate context: `rainfall_annual_mean` (series), `rainfall_latest`,
  `rainfall_latest_year`.

The summary is a private curated artifact; it crosses no export
boundary, so `site_id` and coordinates are permitted. `export_gate` is
not involved anywhere in this batch.

## 4. QGIS assets (`qgis/` at repo root, in git)

- `qgis/styles/register_sites.qml` — categorised renderer on
  `trajectory_status` (five categories), colorblind-safe palette, no
  red/green status semantics (design §1).
- `qgis/styles/site_summary.qml` — rule-based label "shared with N−1
  other sites" when `shared_footprint_site_count > 1` (L17); distinct
  dashed outline when `d3_forced_threshold = true` (L4).
- `qgis/styles/rdc_boundaries.qml` — outline-only reference styling.
- `qgis/README.md` — data-root variable setup (paths not
  machine-pinned); layer load order (RDC boundaries snapshot, then
  `trajectory_summary.gpkg`); applying the QML styles; the design §1
  claim-boundary sentence to paste verbatim into project title and
  layout footer; refresh procedure (re-run the command on a new curated
  date, re-point the layer source).
- `qgis/wa-mine-monitor.qgz` — saved interactively by the owner in
  QGIS ≥3.34 following the README. The coded deliverable ends at the
  README + QML.

## 5. Documentation shipped in this batch

- `docs/decisions/2026-08-30-batch-g-qgis-only-rescope.md` — the
  deferral decision (§1.1 above), citing ROADMAP row 5 and the L10
  "only when a release is actually decided" language.
- Amendments register: A11 entry citing that decision.
- ROADMAP: row 5 rewritten to the QGIS-only scope; stale "Current to
  2026-08-29" header corrected.
- `docs/checkpoints/batch-g-qgis.md` — checkpoint stub, populated only
  after the live run and the interactive `.qgz` save are verified.

## 6. Testing

Fixture-first TDD matching existing CLI tests (in-tmp data root,
prebuilt curated register / trajectories / acceptance / context-join
fixtures, `typer.testing.CliRunner`). Pinned behaviors:

- Refusals: missing verdict; `passed=false`; parts-digest mismatch;
  trajectories/context-join version skew; existing output dir.
- Success: gpkg + manifest written; exact pinned column set; one row
  per eligible site; geometry from register lon/lat.
- Semantics: three-state fire preserved (`not_recorded` never a
  known-negative; `last_recorded_fire_year` null-safe); L4/L17 columns
  on every row; no trend/classification columns.
- Styling-drift guard: QML files parse as XML and every field they
  reference exists in the pinned summary schema.

Environment check (during planning, before coding): confirm geopandas'
GeoPackage write engine (pyogrio) is importable in the uv environment.

## 7. Verification and closure

Full battery (`uv run ruff check src tests`, `ruff format --check`,
`mypy src scripts`, `pytest -q -rs`), then a live run against the
current curated tree (register 2026-08-29, trajectories 2026-08-29,
context-join 2026-08-30). The checkpoint is populated with live
figures; the batch closes when the owner has saved and opened the
`.qgz` against the lux-synced data root on the MacBook.
