# Handoff: Tier 0 build and the D6–D8 DASC/licence/owners rework

Topic file, edited in place on later sessions. Covers the project from its
kickoff (2026-08-15 pivot out of the jarrah repo) through the D6–D8 rework
landing (2026-08-16). Conventions here mirror the jarrah repo's
(`~/Documents/08. remote sensing/CLAUDE.md`): dated immutable snapshots,
fail-closed licence gates at the export boundary, descriptive claim language
only, structured-JSON refusals, declared parquet schemas, count
reconciliation before trust.

## Status

**DONE — committed and pushed (2026-08-16, later session).** The
zero-commits period ended with director rulings D9–D12
(`docs/decisions/2026-08-16-d9-d12-commit-remote-naming-sequencing.md`):
foundation commit `dd70b54` (51 files, battery green at 407), private
remote `jazzdos/wa-mine-rehab-monitor` created and pushed per D10 (stays
private under D10's nine public-flip criteria), first CI run green, and
the D12.2–3 Batch B closeout merged as `72cf280` (battery green at 440).
The public flip (Tier 0 RC) and Pages (Tier 1, ruling D5) remain gated.
The narrative below records the build up to that point.

- Design (`docs/plans/2026-08-15-wa-mine-rehab-monitor-design.md`), approved
  by the delegated codex director with rulings D1–D5. Descriptive claim scope
  binds all wording: site chronologies are never compliance or performance
  findings.
- Implementation plan
  (`docs/plans/2026-08-15-wa-mine-rehab-monitor-implementation.md`): Batches
  A–B (Tasks 1–10) detailed; later batches at heading level.
- Tasks 1–10: repo scaffold, ported jarrah governance machinery (snapshots
  with SHA256SUMS finalization, run manifests with secret scrubbing, licence
  registry, declared-schema parquet writes, config/CLI), and the five Tier 0
  commands: `fetch-minedex`, `fetch-tenements`, `fetch-maus-extract`,
  `build-register`, `build-crosswalk`.
- Task 11 live acceptance, partial: the Maus lane finalized clean
  (`~/data/wa-mine-monitor/raw/maus_v2/2026-08-16/`, wa_extract.gpkg, 1,753
  features, verify (2,0,0)). The pinned SLIP direct-download URLs proved
  auth-gated behind Landgate SSO; the validation gate refused the login HTML
  correctly and left those snapshots unfinalized.
- The DMIRS DASC route was reverse-engineered and measured (product ids,
  members, columns, CRS, counts, the in-bundle CC-BY-4.0 licence PDF), and
  the codex director issued rulings D6–D8, recorded verbatim in
  `docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md`
  — read that file before touching acquisition, licence or owners code; it
  is the source of authority.
- Task TA (D6/D7): DASC acquisition rework — atomic two-bundle MINEDEX
  snapshots, byte-identical licence-PDF evidence extraction, the new
  `adjudicate-minedex-licence` command (gate stays closed:
  `contrary_notice: true`). Review blocked twice; both findings resolved by
  the orchestrator: `licence.minedex_evidence_is_hashed` now digest-verifies
  `licence_evidence.json` itself (an unsigned post-finalize edit can no
  longer flip the redistribution gate open — the adjudication flow re-signs
  via `snapshots.update_snapshot_entry`), and the missing-Data-WA-record
  refusal branch is tested
  (`test_adjudicate_minedex_licence_refuses_a_snapshot_missing_the_datawa_record`).
- Task TB (D8 + DASC inputs): register rework — `owners_at_snapshot` from
  ProjectsOwners.csv current rows (canonical `Owner A (60%); Owner B (40%)`
  rendering, `(holding not stated)` for missing shares), the six owner-join
  disclosure counts in the manifest, the measured STAGE_TO_INCLUSION
  vocabulary (Shut→closed, Undeveloped→deposit, etc., stage kept verbatim),
  nullable lon/lat with the 353 null-coordinate sites disclosed rather than
  dropped, and `build-register` reading Sites/ProjectsOwners from the CSV
  zip (utf-8-sig) and CurrentTenements.shp via `/vsizip/`.

**Numbers that matter.** Full battery measured directly at wrap time
(2026-08-16): ruff check, ruff format --check, mypy, and pytest all green,
**407 passed**. The TB agent's own closing summary said 393; the wrap-time
direct run is the authoritative count, and the discrepancy is in the safe
direction (tests added by fix rounds after the agent's count), but it was not
individually reconciled. Live MINEDEX extract (2026-08-14): 50,164 sites,
stage counts reconciling exactly to the total; 30,456 tenements; 8,376
all-current ProjectsOwners rows; 4,145 of 4,922 projects with a current
owner; 5,822 sites without ProjectCode; 353 sites without coordinates.

## Resume point

**Live Tier 0 acceptance: PASSED, 2026-08-16.** The full sequence above ran
end to end against the live DASC endpoints and every criterion passed —
snapshot triples tenements (2,0,0) / minedex (6,0,0) / maus (2,0,0), stage
reconciliation PASS at 50,164, adjudication recorded with
`minedex_redistribution_allowed: false`, register export blocked, all six
owner-join disclosure counts present, and every count table reconciled by
direct arithmetic or re-measurement against the parquet. Full record:
`docs/checkpoints/tier0-result.md`. The stale SLIP-refusal dirs were
inspected and cleared first, per step 1 as previously written here.

The director consult landed the same day as rulings D9–D12, recorded
verbatim in `docs/decisions/2026-08-16-d9-d12-commit-remote-naming-sequencing.md`
and fully executed: D9 foundation commit `dd70b54`, D10 private remote
(`jazzdos/wa-mine-rehab-monitor`, CI green), D11 names confirmed
unchanged, D12.1–3 Batch B closeout built via workflow, merged as
`72cf280` (440 tests), artefacts rebuilt with the new disclosures
(`docs/checkpoints/batch-b-closeout.md`).

D12.4 is DONE (later the same day): ruling D13 (Batches C–G detailing and
reuse adjudication, produced by detached codex consult) is recorded at
`docs/decisions/2026-08-16-d13-batches-c-g-detailing.md` and committed with
the implementation plan's reuse-review note as `9697473`. The Batch C
implementation plan was then drafted via the writing-plans skill at
`docs/plans/2026-08-16-batch-c-implementation.md` (UNCOMMITTED) — 14 tasks
covering D13 §3 tasks C1–C6 in failing-test-first steps with complete code.
A detached codex plan attack returned three finding clusters (C5 estimator
drifts from D13's required inputs; two manifest-API/sequencing breaks
against the real `manifests.root_relative_path`; three red steps that fail
for the wrong reason), all checked and standing; they are recorded IN the
plan itself under "PRE-BUILD AMENDMENTS REQUIRED" at the top.

**Next: apply the plan's PRE-BUILD AMENDMENTS section (editing the named
tasks in place, then deleting the section), then execute the plan with the
kit:build-flow skill.** Read order for a cold resume: the plan's amendments
section → D13 §3 → the plan's tasks. The Tier 0 public-RC lane (D13 §8)
may run in parallel.

Doc-reconciliation note: implementation-plan Task 11 still says the evidence
json should show `adjudicated: false` — written before the D6–D8 rework added
the adjudication step to the acceptance sequence; the sequence in this
handoff (and the checkpoint) supersedes it, alongside the plan's known mixed
SLIP/DASC route references.

## Honesty flags

- The live DASC acceptance run HAS now happened and passed (2026-08-16, see
  the Resume point above and `docs/checkpoints/tier0-result.md`); the D9
  initial commit and D10 private remote follow from the director's rulings
  in `docs/decisions/2026-08-16-d9-d12-commit-remote-naming-sequencing.md`.
- D8 owners semantics rest on a property of the 2026-08-14 extract (every
  ProjectsOwners row has an empty EndDate, i.e. current-only); a future
  extract that includes historical rows changes the "current owner" filter's
  bite. Closed by the D12.2 closeout (2026-08-16): the composition is now
  disclosed as `n_owner_rows_current` / `n_owner_rows_ended` in the
  fetch-minedex validation summary and the build-register manifest
  (`owner_row_composition`), pinned by tests against a mixed extract — see
  `docs/checkpoints/batch-b-closeout.md`.
- The DASC numeric file ids (2056/3978/3981) are pinned with product-identity
  validation per D6, but DMIRS could renumber; a refusal naming missing
  members is the designed failure mode.

## Open threads

- Seven MINOR review findings deliberately deferred in the workflow ledger
  are now triaged per D12 item 3 — six closed with a pinning test each, one
  (export-boundary drift: `REGISTER_SCHEMA` lon/lat vs `export_gate.
  GEOMETRY_NAME_TOKENS`, design §4's licence-fields-non-null criterion,
  `export_gate.export_public` having no caller) explicitly deferred to
  Batch G / the Tier 0 public-RC lane, where `export_public` gains its
  first caller. The `n_tenements_intersecting` not-computed-vs-fired-zero
  split named here previously is one of the six now closed. Full record,
  findings table and rationale: `docs/checkpoints/batch-b-closeout.md`.
- Surface to Jarrod: the jarrah repo's `CLAUDE.md` licence-gate list still
  calls DMIRS-001 "clean"; the current Data WA record says cc-nc, and this
  project's D7 adjudication records the conflict formally. The jarrah file
  is not edited from this line — its CLAUDE.md/AGENTS.md pair is maintained
  in lockstep there and the edit belongs to that project's own session.
- ArcGIS REST fallback (D6) is documented, non-automatic; building its
  acquisition mode is future work only if DASC breaks.
- Later batches (heading level in the implementation plan): DEA epoch
  coverage, threshold derivation, Tier 1 trajectory extraction with
  Huntly-cube validation first, fire/climate context, export + static site,
  Tier 2 with a LEARNINGS.md + pre-registration guard of this repo's own.
