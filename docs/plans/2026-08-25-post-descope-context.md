# Context for plan attack (2026-08-25)

Session decisions already fixed (do not relitigate):
- A8: public web page withdrawn (docs/decisions/2026-08-25-public-web-page-descope.md);
  output = GeoParquet releases + private QGIS project. D5 gate recorded
  never-evaluated, not waived. Tier 0 public-RC repo-flip lane unchanged.
- Phase 1 of the plan under attack adopts docs/plans/2026-08-22-batch-e-e4-e5.md
  (Status FINAL 2026-08-25) BY REFERENCE, unchanged. Do not re-review that
  plan's internals; attack only whether adopting it by reference is sound.

What is on disk and verified today:
- Batches A-D closed (docs/checkpoints/batch-d-result.md); 737 tests green.
- export_gate.export_public: defined and tested, ZERO production callers
  (its own docstring, src/wa_mine_monitor/export_gate.py:9-22). L10/L11 in
  docs/amendments-and-limitations.md record the gap.
- REGISTER_SCHEMA (src/wa_mine_monitor/register.py:101-114) carries lon/lat;
  GEOMETRY_NAME_TOKENS (export_gate.py:131) omits them.
- O8: scripts/diag_batch_e_readiness.py `_judged` (lines 78-104) reimplements
  the eligibility join and buckets 933 never-judged sites differently from
  register.assign_trajectory_eligibility (register.py:1237). Crosswalk digest
  identity already verified; input-identity hypothesis refuted.
- `forced_threshold` argument does NOT exist yet; it is Phase 1 Task 0 of the
  adopted plan. Plan-under-attack Task 3 depends on it.

Known-missing / accepted:
- SILO account absent (O7); Huntly verdict absent (O3, blocks statewide E4).
- QGIS task is manual/untestable by design; acceptance criteria substitute.
- licence.SOURCES field names in plan Task 2 are flagged as to-verify.
