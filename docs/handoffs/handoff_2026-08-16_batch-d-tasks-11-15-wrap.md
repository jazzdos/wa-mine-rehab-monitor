# Handoff — Batch D Tasks 11–15 built; forced-144 test and Task 16 outstanding

**Date:** 2026-08-16
**Branch:** `feature/batch-d-d3-threshold` in worktree `../wa-mine-rehab-monitor-batch-d`
**Plan:** `docs/plans/2026-08-16-batch-d-implementation.md` (amendments and decisions 6–17 override conflicting task text)

## State at wrap

The session was cancelled mid-run on request. Everything built is committed and
verified; nothing is half-applied.

Commits on the branch (base: main `ad019ca`):

- `163983f` — Tasks 1–2, 4–6, 8–10 (protocol, regions, pixel support, decode, simulation core)
- `e03cb0b` — Tasks 3+7 (`fetch-region-boundaries`, `freeze-d3-protocol` CLIs)
- `9faaba3` — Tasks 11–15 (simulation driver, `build-d3-inputs`, `evaluate_threshold`,
  `derive-d3-threshold`, register D5 columns + `apply-d3-threshold`)

Verification at `9faaba3`: `uv run ruff check src tests` clean, `ruff format --check`
clean, `mypy src` clean, `pytest -q` **672 passed** (baseline before Batch D: 554).

Build-flow run `wf_7dba1ee1-738` executed Tasks 11–14 with spec+quality reviews
clean. It blocked at Task 15's forced-144 test; the run was resumed with a ruling
(below) and then cancelled by Jarrod mid-Task-15. The Task 15 implementation itself
(schema, status assignment, CLI, four of five tests) was complete and green; only
the dead-end version of the forced-144 test was removed before committing.

## Outstanding work

1. **`test_apply_d3_threshold_forced_144_discloses` (Task 15, test 3).** The
   original fixture idea (`protocol_criteria_overrides` writing a relaxed
   tolerance into `d3.yaml`) can never work: `d3_protocol.load_protocol` refuses
   any criteria drift by design (plan decision 13), and the fixture rasters are
   per-pixel uniform so sampling error is always zero. **Binding ruling:** exercise
   `criteria_passed=False` via the computable-fraction criterion instead — extend
   `_seed_d3_inputs_chain` with `n_uncomputable_years: int = 0` (default preserves
   all existing tests); when positive, each footprint gets that many EXTRA
   epoch-covered years whose FC raster has one member pixel set to 255 (invalid →
   `year_computable` False). Fraction = 10/(10+n); with n=2 it is 10/12 ≈ 0.833 <
   the frozen 0.90 minimum, failing every reduced support deterministically →
   forced 144. No criteria overrides, no frozen-check changes, no noise tuning.
   The test then asserts: `derive-d3-threshold` writes `criteria_passed=False`;
   `apply-d3-threshold` still exits 0; all judged sites `threshold_not_computed`
   with `d3_threshold_px == 144`; manifest carries the failed-criteria disclosure.
2. **Task 16** — `tests/test_batch_d_acceptance.py` (six acceptance tests mapped to
   D13 §4 criteria) and `docs/checkpoints/batch-d-result.md` skeleton. Full task
   text is in the plan.
3. **Resume path:** relaunch kit:build-flow with `resumeFromRunId` is stale (the
   run was stopped mid-batch); a fresh launch with `startBatch=4` and the two
   remaining tasks (amended T15 prompt covering only the forced-144 test, plus
   T16) is cleaner. The full amended T15/T16 prompts and the ledger are embedded
   in this session's transcript (`576a44ff…`, Workflow call for task `w601j473j`).
4. After Task 16: `kit:verify`, then `kit:finish-branch`. Push/merge only on
   Jarrod's ask.
5. **Deferred live run** (unchanged from Batch C handoff): five-command chain on
   luminosity (`/mnt/data`), protocol committed before `build-d3-inputs`, disk
   check = block-cache bound (default 50 GB), transfer budget 597 GB–3.30 TB.
6. Housekeeping: worktree `../wa-mine-rehab-monitor-batch-c` cleanup still optional.

## Split-brain check

Main checkout clean apart from the pre-existing, not-this-session edit to
`docs/handoffs/handoff_2026-08-16_tier0-build-and-d6-d8-rework.md` (left alone).
