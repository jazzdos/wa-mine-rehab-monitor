# E4 acceptance + F6 context join — design

Date: 2026-08-30. Approved by owner in-session (scope, 1986 handling,
F6 shape each confirmed by explicit choice). Cycle: design →
kit:writing-plans → codex plan gate → kit:build-flow → kit:verify →
live runs → checkpoints → commit → codex diff gate → merge.

## 1. Scope (owner decision)

This cycle covers (a) the E4 statewide-extraction acceptance battery
and record, and (b) the D13 §6 F6 context join. Full Batch E closure
(E6 sensor-overlap sensitivity, E7 `batch-e-result.md`) is explicitly
OUT of this cycle and must be disclosed as open wherever acceptance is
recorded. `docs/checkpoints/batch-e-result.md` is reserved for the
future E6/E7 cycle; this cycle writes
`docs/checkpoints/e4-statewide-extraction.md` instead.

## 2. Inputs (all live, 2026-08-29 lineage)

- `curated/trajectories/2026-08-29/` — 2,458,164 rows, 99 partitions
  (4 collections × years), `extraction_summary.json` +
  per-part manifests. inserted=2,458,164; not_computable=94,343
  (subset of inserted); existing=0; refused_empty=0.
- `curated/fire-context/2026-08-29/fire_context.parquet` — 404,508
  rows = 10,372 sites × 1987–2025; status counts recorded
  10,097 / not_recorded 388,990 / unknown 5,421.
- `curated/climate-context/2026-08-29/climate_context.parquet` —
  404,508 rows; computed 403,455 / not_computable 1,053.
- Row identity to assert: 2,458,164 = 10,372 × 237, where
  237 = 39 FC year-partitions × 3 metrics + 60 geomedian
  collection-year partitions × 2 metrics.

## 3. Part 1 — E4 acceptance

### Module `src/wa_mine_monitor/trajectory_qa.py`

Independent post-run verification, production functions only (the O8
rule — never re-implement logic the product owns):

1. Resolve the dated trajectories dir; digest-verify
   `extraction_summary.json` via its manifest
   (`cli._digest_verified_manifest` pattern; QA takes paths, CLI does
   resolution).
2. `trajectory_extract.existing_partitions` +
   `verified_parts(expected_schema=trajectories.TRAJECTORY_SCHEMA)`
   per partition — sha + footer-schema (incl. nullability).
3. `trajectories.validate_trajectories` on every partition frame.
4. Accounting identities:
   - sum(rows) == summary `inserted`;
   - sum(`computable == False`) == summary `not_computable`;
   - partition count == `n_partitions_written` == len(summary
     `partitions`) == 99;
   - each partition holds exactly (n eligible sites × its metric
     count) rows; distinct `site_id` per partition == the register's
     eligible set (digest-verified read of the latest curated
     register, `trajectory_status == "eligible"`);
   - summary `site_ids` == that same set.
5. L17 exhaustively (not sampled): within every
   (`maus_id`, `year`, `metric`, `collection_id`) group, values are
   byte-identical (all-equal or all-NaN with equal
   `not_computable_reason`), and `shared_footprint_site_count` equals
   the group's register-derived site count.
6. `d3_forced_threshold` true on every row (L4 travels).

Report/verdict structure follows `evidence.py`: per-check outcomes
reported, never raised; refusal (exit 1) only for unusable inputs.

### CLI `accept-trajectories`

`validate-huntly` shape: module-level typer options; refuse-if-exists
first; writes `curated/trajectories-acceptance/<date>/acceptance.json`
(+ run manifest citing the summary manifest, register manifest, and
part manifests' digests) with
`{passed, checks[], counts, failures[]}`; `passed: false` is a result
with a manifest, never a crash.

### Checkpoint `docs/checkpoints/e4-statewide-extraction.md`

House shape (status line → live figures → each D13 E4 acceptance
clause adjudicated → honesty flags). Honesty flags MUST include: the
D13 L626 serial-vs-concurrent test does not exist (disclosed, not
glossed); E6/E7 open; `not_computable` composition by reason.

## 4. Part 2 — F6 context join

### Module `src/wa_mine_monitor/context_join.py`

`CONTEXT_JOIN_SCHEMA`, one row per Tier 1 site-year:
**414,880 rows = 10,372 × 40 years (1986–2025)**. Columns:

- keys: `site_id` (nn), `maus_id` (nn), `year` int32 (nn)
- join level: `context_row_status` (nn) ∈ {`joined`,
  `no_context_row`}; `context_complete` bool (nn) — true iff
  `joined` AND `fire_coverage_status == "covered"` AND
  `climate_status == "computed"`. This field is the schema-level
  carrier of the "cause not determined" rendering contract (per the
  2026-08-25 descope decision the disclosure attaches to the schema
  field, data dictionary, and QGIS styling; no rendering code here).
- fire block (all nullable at the join level; null iff
  `no_context_row`): `fire_status`, `fire_record_count`,
  `fire_coverage_status`, `fire_source_version`,
  `fire_snapshot_date`, `fire_not_computable_reason` (renamed from
  fire_context's `not_computable_reason`).
- climate block (same nullability rule): `silo_cell_id`,
  `annual_rainfall_mm`, `rain_days_ge_1mm`, `rainfall_anomaly_mm`,
  `rainfall_baseline_start_year`, `rainfall_baseline_end_year`,
  `climate_status`, `silo_source_version`, `silo_snapshot_date`,
  `climate_not_computable_reason` (renamed likewise).
- `no_context_row_reason` string, nullable: set ONLY on 1986 rows,
  fixed text naming the 1987 context start. `no_context_row` is a
  join-level state and is NEVER expressed by widening `fire_status`
  (fire's three-state vocabulary is untouched; absence of a context
  row is not `unknown`).

Validation (`validate_context_join`): exact row count
n_sites × n_years; one row per (site_id, year), no duplicates;
`no_context_row` exactly for years outside the context range; payload
all-null iff `no_context_row`; fire status counts and climate status
counts each reconcile with the source products' counts;
missingness independence — fire nullity driven only by fire fields,
climate only by climate fields; `context_complete` recomputed and
matched; no column whose name implies causation (guard list).

### CLI `build-context-join`

Gates, in order (mirroring `build-climate-context` GATE 2/4
discipline):

1. Resolve + digest-verify the LATEST curated trajectories summary
   manifest; `verified_parts` over every partition (first downstream
   consumer of `curated/trajectories`).
2. **Acceptance gate**: resolve the LATEST
   `curated/trajectories-acceptance` verdict, digest-verify it, and
   refuse unless `passed: true` AND it cites the same trajectories
   dated dir being consumed — D13 §6 "Batch F follows accepted Batch
   E extraction", enforced like `require_huntly_gate`.
3. Resolve + digest-verify LATEST fire-context and climate-context.
4. Cross-checks: identical site sets across all three; identical
   (site, year) domain between the two contexts; `maus_id` agreement
   on every site across all three inputs.
5. Derive trajectory site-years from the verified partitions; assert
   context site-years == trajectory site-years restricted to
   1987–2025, and that the only uncovered trajectory years are the
   pre-1987 ones (currently 1986 only — derived, not hard-coded).
6. Assemble, validate, refuse-if-exists, write
   `curated/context-join/<date>/context_join.parquet` + run manifest
   citing trajectories, acceptance verdict, fire, climate (uri,
   upstream `output.sha256`, snapshot dates, licence fields).

No trajectory row is dropped or rewritten; trajectories remain the
authoritative product; consumers join on (site_id, year).

### Tests

- `tests/test_trajectory_qa.py` — QA unit tests on forged fixtures
  (good tree passes; each identity violation caught: row-count drift,
  tampered part, schema drift, shared-footprint divergence, forced
  false verdict is written not raised).
- `tests/test_context_join.py` — the five D13-named tests verbatimly
  honored: one context record per Tier 1 site-year; fire and climate
  missingness independent; trajectory rows never dropped for unknown
  context; rendering contract requires both contexts beside any onset
  interpretation (enforced via `context_complete` + data dictionary
  text assertion); "cause not determined" remains when either context
  absent. Plus: 1986 explicit-state tests, collision renames, each
  build gate refusal (missing/failed acceptance verdict, site-set
  mismatch, domain mismatch, maus_id disagreement, duplicate rows).
- `tests/test_batch_f_acceptance.py` — acceptance level: counts
  reconcile across the three products; source versions carried
  forward onto every joined row; no causal attribution (column-name
  guard + fixed wording assertions); checkpoint doc parses/exists.

### Checkpoint `docs/checkpoints/batch-f-result.md`

F3/F4/F5 live-run figures (already on record) + the F6 live run;
"mirror remained declined" (A10) recorded as D13 §6 acceptance
requires; claim-boundary adjudication; honesty flags (1986
no-context-row count, climate outside-grid `silo_cell_id` caveat
carried, E6/E7 status).

## 5. Execution order

Build + full battery green → live `accept-trajectories` (verdict on
the 2026-08-29 extraction) → live `build-context-join` → checkpoints
written from real figures only → docs (ROADMAP rows 3–4, amendments
if any) → commit → codex diff-review gate → merge to main locally.
No push without a fresh owner ask.

## 6. Out of scope

E6 sensor-overlap sensitivity; E7 `batch-e-result.md`; any Batch G
export/package wiring of the joined product; any rendering surface;
1986 context backfill (declined — explicit absent state chosen).
