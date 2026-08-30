# Checkpoint: Batch F — context products and the F6 join

Status: COMPLETE 2026-08-30 (`build-context-join` ran live;
`curated/context-join/2026-08-30/context_join.parquet` written with
run-manifest sidecar citing all four inputs).

## Live runs

- F3/F4 (fire context): `curated/fire-context/2026-08-29/` — 404,508
  rows; recorded 10,097 / not_recorded 388,990 / unknown 5,421.
- F5 (climate context): `curated/climate-context/2026-08-29/` — 404,508
  rows; computed 403,455 / not_computable 1,053.
- F6 (context join): `curated/context-join/2026-08-30/` — 414,880 rows
  = 10,372 sites x 40 years (1986–2025); 10,372 `no_context_row` rows
  (1986); 398,034 `context_complete` rows. Built only after the E4
  acceptance verdict passed its gates (verdict digest-verified, and both
  `extraction_summary_sha256` and `parts_digest` matched the consumed
  tree — see `e4-statewide-extraction.md`).

## D13 §6 acceptance, adjudicated

- Counts reconcile across the three products: PASSED
  (`validate_context_join` status-count reconciliation against both
  source products; 398,034 = 404,508 joined − 5,421 fire-uncovered
  − 1,053 climate-not-computable, exact).
- Source versions carried forward onto every joined row: enforced by
  schema (fire_source_version/silo_source_version non-null on joined
  rows) and confirmed by the live validator pass.
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
  never expressed through fire's `unknown`. Count: 10,372 (one per
  site).
- `silo_cell_id` on outside-grid footprints is centroid-minted, not a
  real grid cell (climate-context caveat, carried forward).
- E6/E7 remain open (see e4-statewide-extraction.md).
- The acceptance verdict this product's gate 2 verified against predates
  the same-day diff-gate strengthening of the E4 battery (domain pin,
  crosswalk maus anchor, digest bracket); the strengthened battery was
  re-run post-hoc against the same digest-bound trajectories tree and
  passed (see e4-statewide-extraction.md honesty flags).
