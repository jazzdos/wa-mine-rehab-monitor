# Tier 0 live acceptance result — 2026-08-16

First end-to-end run of the Tier 0 chain against the live DMIRS DASC
endpoints, per the resume sequence in
`docs/handoffs/handoff_2026-08-16_tier0-build-and-d6-d8-rework.md`. All five
commands under the D6–D8 rework
(`docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md`).
Run from the MacBook Air, working tree only (zero commits at run time).

## Pre-run clearance

The two stale unfinalized dirs from the refused SLIP-route fetches
(`raw/dmirs_001_minedex/2026-08-16/` and `raw/dmirs_003_tenements/2026-08-16/`,
each holding an 11,719-byte Landgate SSO login HTML mis-saved as `.gpkg`,
metadata naming the old `data-downloads.slip.wa.gov.au` endpoints, no
SHA256SUMS) were inspected, confirmed as the SLIP refusals the handoff
describes, and deleted. `maus_v2/2026-08-16` was already finalized clean and
was not touched.

## Commands and results

| Step | Command (`--date 2026-08-16`) | Result |
|---|---|---|
| 1 | `fetch-tenements` | Finalized. 30,456 features, EPSG:7844, extract_date 2026-08-14, all 8 pinned members incl. `Licence_CCBY4.pdf`. Verify (ok 2, missing 0, bad 0). |
| 2 | `fetch-minedex` | Finalized. Two-bundle snapshot; verify (ok 6, missing 0, bad 0). Sites.csv 50,164 rows; shapefile 48,402 features. |
| 3 | `adjudicate-minedex-licence` | Adjudicated. `decision: "licence conflict; redistribution closed"`, `contrary_notice: true`, `minedex_redistribution_allowed: false`. Evidence digest-verified pre-edit (`evidence_json_sha256_before` recorded); SHA256SUMS re-signed (before/after digests in the output), superseding manifest chained. |
| 4 | `build-register` | `reconciliation: PASS`; `minedex_public_export_blocked: true`; register.parquet + manifest written under `curated/register/2026-08-16/`. |
| 5 | `build-crosswalk` | crosswalk.parquet + manifest under `curated/crosswalk/2026-08-16/`. 47,077 input rows; confidence high 11,001 / medium 4,076 / low 4,345 / none 27,655. |

## Reconciliations performed (each by direct arithmetic or re-measurement)

- **Stage counts sum exactly to the sites total**: 2,189 (Care and
  Maintenance) + 4,717 (Operating) + 4,727 (Proposed) + 20,578 (Shut) +
  236 (Under Development) + 17,717 (Undeveloped) = 50,164 = Sites.csv row
  count. The register's inclusion-status counts map the same six stages
  (closed 20,578, deposit 17,717, operating 4,717, prospect 4,727,
  care_and_maintenance 2,189, other 236) and sum to the same 50,164.
- **Crosswalk confidence counts sum to the input population**: 11,001 +
  4,076 + 4,345 + 27,655 = 47,077 = `n_crosswalk_input_rows`.
- **Crosswalk exclusions**: the categories 2,738 (duplicate site_id) + 353
  (no usable location) exceed `n_excluded_total` 3,087 by 4. Re-measured
  against the register parquet: exactly 4 rows are BOTH duplicated and
  coordinate-less; the union is 3,087 and 50,164 − 3,087 = 47,077. The
  total counts the union, the categories the marginals — reconciles, and
  the overlap is now on record here.
- **Owner-join disclosures**: `n_sites_no_current_owner` =
  `n_sites_unmatched_project_code` = 14,222. Re-measured against the
  parquet: 20,044 rows carry an empty `owners_at_snapshot`, which is
  14,222 + 5,822 (`n_sites_missing_project_code`) exactly — the two equal
  fields count the same population by definition (a project code matching
  no current ProjectsOwners row), disclosed separately from code-less
  sites. Definitional overlap, not a defect.
- **Extract identity**: both DASC bundles carry `extract_date: 2026-08-14`,
  matching the extract the fixtures and the handoff's expected numbers were
  measured against (50,164 sites; 30,456 tenements; 4,922 projects; 4,145
  with a current owner; 5,822 without ProjectCode; 353 without
  coordinates; duplicate site_code 1,327 values / 2,738 rows). Every one
  of those matched the live run.

## Acceptance criteria from the handoff, adjudicated

- Snapshot verify triples (n, 0, 0): PASS — tenements (2,0,0), minedex
  (6,0,0), maus (2,0,0, pre-existing).
- Stage-count reconciliation: PASS.
- `minedex_redistribution_allowed` still False after adjudication: PASS.
- Export gate blocking MINEDEX-derived rows: PASS as evidenced by
  `minedex_public_export_blocked: true` in the register manifest and the
  closed adjudication record; no standalone export command exists yet to
  exercise a live refusal, so this criterion is met at the manifest/registry
  layer only.
- Owner-join disclosure counts present in the register manifest: PASS (all
  six fields, values above).

## Honesty flags

- Snapshot date 2026-08-16 vs bundle extract date 2026-08-14: DMIRS's
  bundles lag the fetch date; both dates are recorded in the snapshot
  metadata. Counts here describe the 2026-08-14 extract.
- The export-gate criterion was verified at the manifest field, not by a
  refused live export (no export command exists in Tier 0).
- The D8 current-owner semantics still rest on the extract property that
  every ProjectsOwners row has an empty EndDate; unchanged from the handoff,
  still not pinned by validation.
