# Checkpoint: E4 statewide trajectory extraction — accepted

Status: ACCEPTED 2026-08-30 (`accept-trajectories` verdict
`curated/trajectories-acceptance/2026-08-30/acceptance.json`,
`passed: true`, all 14 checks green).

## Live figures

- Extraction: `curated/trajectories/2026-08-29/` — 2,458,164 rows
  (10,372 sites x 237 site-metric-years), 99 partitions
  (39 FC year-partitions + 60 geomedian collection-year partitions),
  94,343 not-computable rows.
- Acceptance verdict: `curated/trajectories-acceptance/2026-08-30/acceptance.json`
  with run-manifest sidecar; bound to
  `extraction_summary_sha256 dd645c38229e37f3…` and
  `parts_digest a19ea2b9386ca3ca…` (sha256 over every verified part's
  manifest digest — `build-context-join` refuses a verdict whose digest
  no longer matches the tree it consumes).

## D13 E4 acceptance clauses, adjudicated

- Every partition independently verifies (digest + footer schema) and
  reconciles against the extraction summary: PASSED
  (`parts_digest_and_schema`, `partition_inventory`, `partition_count`
  = 99 exactly, `total_rows_match_summary` = 2,458,164,
  `not_computable_matches_summary` = 94,343).
- Only eligible register sites entered extraction; MINEDEX identifiers
  remain internal (no public artefact in this cycle carries them):
  PASSED (`partition_site_sets`, `summary_site_ids_match_register`
  — 10,372 sites in every partition, equal to the register's eligible
  set).
- The forced D3 threshold travels on every row (L4): PASSED
  (`forced_threshold_all_true` — 2,458,164 of 2,458,164 rows;
  `forced_threshold_register_consistency` — per-site agreement with the
  register).
- Shared-footprint identity (L17), exhaustive over every
  (maus_id, metric) group in every partition: PASSED
  (`shared_footprint_consistency`).
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
- `not_computable` composition by reason (94,343 total):
  `item_missing` 52,779; `insufficient_valid_fraction` 39,277;
  `zero_valid_pixels` 2,287.
- The register's ineligible rows (39,254 of 50,164) carry null
  `d3_forced_threshold` by design; the forced-threshold checks are
  defined against the eligible set only (a null on an ELIGIBLE row is a
  refusal, regression-tested).
