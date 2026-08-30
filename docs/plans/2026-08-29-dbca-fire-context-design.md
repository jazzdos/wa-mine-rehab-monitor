# DBCA-060 fire context (F3 + F4) — design

Status: plan executed 2026-08-29

Date: 2026-08-29. Owner-approved route: the ArcGIS mirror stays declined
and Batch F proceeds on the authoritative on-disk snapshot; F1's mirror
evidence adjudication is dissolved as objectless. Scope: decision record
+ amendment, F3 acquisition/validation, F4 fire-context product. The F6
trajectory join stays out of scope (needs E4).

## Prior-art scan (2026-08-29, four-scout workflow + env-health re-scout)

- jarrah-rehab: acquired DBCA-060 from SLIP (authenticated), verified
  schema and the zero-MR census; treats fire as a covariate, no per
  site-year status join. Reusable facts: layer
  `DBCA_Fire_History_DBCA_060`, 149,621 features, EPSG:4283 (GDA94),
  fields `fih_fire_type` (WF/PB/999), `fih_year1`, `fih_date1`,
  `fih_master_key`; CKAN dataset id
  `3ce8a891-b050-4c38-952b-c40ca8bdc042`, license_id `cc-by`; MR/PL
  tripwire pattern (`jarrah_rehab/events/dbca_fire.py`).
- env-health (dataplatform + Bushfire): ArcGIS mirror client exists but
  is not needed (mirror declined; mirror publisher is a third party,
  "Stantec", named only in a YAML comment — reinforces the decline).
  Reusable patterns: `999` sentinel is a REAL fire-type category (never
  blank it); `fih_year1` is a plain calendar year everywhere;
  bbox-prefilter + `gpd.sjoin(predicate="intersects")` per-footprint
  join shape (`Bushfire/src/bushfire/ros/covariates.py:143-179`);
  explicit-row-per-unit design ("emit an explicit FALSE rather than an
  absent row"); provider `coverage` dict with `start_year: 1937`.
  Neither project has GeoPackage validation, a fire_status vocabulary,
  or coverage-window logic — nothing duplicates F3/F4.
- All other local projects: no DBCA-060/fire-history prior art.

## 1. Decision record + amendment A10

`docs/decisions/2026-08-29-dbca-mirror-declined.md`: the mirror route
stays declined; F1 is dissolved as objectless because the authoritative
Data WA package is already on disk
(`~/data/jarrah-rehab/raw/dbca-060/2026-07-20/`, zip digests present),
following the 2026-08-26 SILO precedent for dissolving a D13 Batch F
precondition. Because D13 §6 called the F1 evidence gate a "hard
precondition", the dissolution is a post-freeze change to a binding
gate: amendment row **A10** in `docs/amendments-and-limitations.md`.
The record also:

- assigns the licence-evidence gap (licence.py holds a bare `"open"`
  with an empty URL) to F3 for closure;
- freezes the F4 coverage window (see §4) with its citation;
- adds limitation **L18**: DBCA-060's own scope is fires on DBCA-managed
  land or where DBCA incurred costs (Bushfire literature ref +
  CKAN notes); spatial completeness is not modelled, so `not_recorded`
  is a statement about the record, never about the ground.

## 2. F3 — `fetch-dbca-fire` (authoritative mode only)

New `src/wa_mine_monitor/sources/dbca.py` + typer command in `cli.py`.

- `--mode authoritative|mirror`; `mirror` refuses, citing the decision
  record (fail-closed). Authoritative mode takes `--source-dir`
  (default the jarrah snapshot path) and `--date` (house rule: caller
  supplies the snapshot date; never `date.today()`).
- Staging into `<data_root>/raw/dbca-060/<date>/`: copy the GDA94
  GeoPackage (custodian-native geometry per the snapshot's
  metadata.txt) plus the source `SHA256SUMS.txt` and metadata; verify
  the source zip digests where present; **compute and record the
  sha256 of the .gpkg itself** (the source sums cover only the zips).
- Validation before finalise (fail-closed refusals, one per check):
  layer `DBCA_Fire_History_DBCA_060` present; CRS is EPSG:4283;
  required fields present (`fih_fire_type`, `fih_year1`,
  `fih_master_key`; `fih_date1` optional-nullable); feature count > 0
  and recorded; `fih_fire_type` vocabulary ⊆ {WF, PB, 999} after
  `UPPER(TRIM(...))` normalisation (jarrah census precedent — the real
  GDA94 file carries one raw lowercase `wf`), with an MR/PL-style
  tripwire on any unexpected normalised code;
  `fih_year1` within [1900, snapshot year]; extent intersects WA.
  Counts by fire type recorded in metadata for later reconciliation.
- Licence evidence: capture the Data WA catalogue page for DBCA-060
  (single small HTTPS fetch) as a digested evidence file in the
  snapshot dir; update `licence.py` (real licence id `CC-BY-4.0`,
  catalogue URL, evidence digest in notes) and the licensing-matrix
  row. If the fetch fails the run refuses rather than staging without
  evidence.
- Finalise with the fetch-silo conventions: metadata.txt, SHA256SUMS,
  `verify_snapshot`, run manifest with `SourceAsset` rows;
  `_refuse_if_snapshot_already_finalized` and the stray-file gate
  before and after.

## 3. F4 — `fire_context.py` + `build-fire-context`

Mirrors `build-climate-context`'s gate structure exactly:

- GATE 1 inverted `--start-year/--end-year` refused before I/O; GATE 2
  latest `raw/dbca-060/<date>/` snapshot digest-verified with the
  GeoPackage named in `required_files` (an unlisted file added after
  finalisation otherwise passes verification); GATE 3 latest
  D3-annotated `curated/register/<date>/` (site selection via
  `trajectory_extract.select_eligible_sites`); GATE 4 crosswalk +
  raw Maus snapshot sha-tied to the crosswalk manifest. Refuse existing
  curated output for the same date.
- Spatial method: Maus **footprint polygons** (dissolved per
  `maus_id`, EPSG:4283 → common CRS with the fire layer) intersected
  with fire polygons; per-footprint bbox-prefiltered reads
  (pyogrio/geopandas, Bushfire sjoin template) so the 2.1 GB file is
  never loaded whole; fire year = `fih_year1`. One footprint-level
  join fanned out to member sites via the crosswalk, matching E4's
  footprint-keyed pattern.
- Output `curated/fire-context/<date>/fire_context.parquet`, one row
  per (site_id, year) for every selected site and requested year —
  explicit rows, never absent rows. D13 F4 schema verbatim: `site_id,
  maus_id, year, fire_status, fire_record_count, fire_source_version,
  fire_coverage_status, fire_snapshot_date, not_computable_reason`.
  Run manifest sidecar.

## 4. Three-state semantics (load-bearing)

- `recorded`: ≥1 intersecting fire polygon with `fih_year1 == year`.
  `fire_record_count` = the count (all types; WF, PB and 999 all
  count — every type is a recorded fire).
- `not_recorded`: zero intersections AND year inside the frozen
  coverage window **[1937, snapshot_year − 1]** — 1937 from the
  dataset's own earliest-records documentation, the open upper bound
  because the snapshot year is incomplete at extract time. Frozen in
  the decision record, not inferred from the data.
- `unknown`: everything else — year outside the window, or a footprint
  geometry that is empty or invalid. The reason lands in
  `not_computable_reason`; `fire_coverage_status` says which condition
  applied (`covered`, `outside_window`, `no_footprint`). An eligible
  site absent from the crosswalk, or a `maus_id` absent from the Maus
  snapshot, is an integrity violation refused by name (climate-context
  precedent; `maus_id` stays non-nullable per the D13 F4 schema) —
  never silently downgraded to `unknown`.
- Never a bare boolean; `not_recorded` is never a known-negative
  (claim boundary + L18). Acceptance: status counts reconcile —
  recorded + not_recorded + unknown == rows == selected sites ×
  requested years, mutation-tested.

## 5. Testing and verification

Fixture-first TDD: tiny synthetic GeoPackage fixtures (climate-context
`_seed_world` pattern) — no test touches the 2.1 GB real file or the
network (catalogue fetch monkeypatched). Coverage: every refusal gate
in F3 and F4, each of the three states, the type-vocabulary tripwire,
count reconciliation, mirror-mode refusal, manifest content. Battery:
ruff, format, mypy, pytest. The live F3 staging + F4 build against the
real snapshot run post-merge in-session (E5/Task 0 precedent), then
roadmap + licensing-matrix rows update.

Out of scope: F6 trajectory join, DFES/FIRMS, severity layers, any
mirror comparison harness.
