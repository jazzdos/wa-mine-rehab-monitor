# Handoff: Tier 0 build and the D6–D8 DASC/licence/owners rework

Topic file, edited in place on later sessions. Covers the project from its
kickoff (2026-08-15 pivot out of the jarrah repo) through the D6–D8 rework
landing (2026-08-16). Conventions here mirror the jarrah repo's
(`~/Documents/08. remote sensing/CLAUDE.md`): dated immutable snapshots,
fail-closed licence gates at the export boundary, descriptive claim language
only, structured-JSON refusals, declared parquet schemas, count
reconciliation before trust.

## Status

**DONE — all uncommitted.** The repo has ZERO commits by design: commits, the
private GitHub remote (ruling D2), the public flip (Tier 0 RC) and Pages
(Tier 1, ruling D5) are decisions delegated to the codex director and not yet
taken. Everything below is working tree only.

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

Next: the codex director consult on commits/remote/naming (D9–D11, possibly
D12 on batch sequencing) — launched detached 2026-08-16; record the rulings
verbatim in `docs/decisions/` when they land, then act on them. After that,
the batch detailing pass for Batches C–G, recording a deliberate reuse
decision (port vs adopt vs build) per batch.

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
  bite, and validation does not currently pin that property.
- The DASC numeric file ids (2056/3978/3981) are pinned with product-identity
  validation per D6, but DMIRS could renumber; a refusal naming missing
  members is the designed failure mode.

## Open threads

- Seven MINOR review findings deliberately deferred in the workflow ledger
  (recorded there); one was made live by real data — `n_tenements_intersecting`
  conflating not-computed with a fired zero for the 353 coordinate-less
  sites — TB's disclosure counts cover the reporting half; the semantic
  split remains open.
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
