# Context for codex review — E4 acceptance + F6 context join plan

Decisions (owner-approved, fixed):
- Design authority: docs/plans/2026-08-30-context-join-design.md.
- 1986 = explicit `no_context_row` state; never widened into fire's
  `unknown`; reason text derives the 1987 start from data.
- Collision renames: fire_/climate_not_computable_reason.
- `context_complete` = joined AND fire covered AND climate computed.
- accept-trajectories: passed:false is a written result, exit 0
  (validate-huntly precedent); refusal exit 1 only for unusable inputs.
- build-context-join gates on a digest-verified acceptance verdict whose
  `extraction_summary_sha256` matches the summary being consumed
  (require_huntly_gate discipline, one stage downstream).
- O8: QA calls production functions (existing_partitions,
  verified_parts, validate_trajectories, select_eligible_sites).
- E6/E7 out of scope, disclosed; checkpoint files are
  docs/checkpoints/e4-statewide-extraction.md and batch-f-result.md,
  committed with PENDING LIVE RUN markers, figures filled after live runs.

On disk (live, 2026-08-29 lineage, NOT touched by tests):
- curated/trajectories/2026-08-29: 2,458,164 rows = 10,372 sites x 237,
  99 partitions, not_computable 94,343; extraction_summary.json keys:
  date/existing/inserted/not_computable/partitions/protocol_digest/
  refused_empty/scope/site_ids (no n_partitions_written key).
- fire-context and climate-context 2026-08-29: 404,508 rows each
  (10,372 x 1987-2025). F6 target 414,880 = 10,372 x 40 (1986-2025).

Known-missing / accepted:
- D13 L626 concurrency test does not exist (disclosed in checkpoint).
- licence key for DBCA in Task 8 is to be confirmed against
  licence.SOURCES / build-fire-context before use (noted in plan).
