# Road map

Current to 2026-08-30. This file is derivative, like the amendments
register: the decision and checkpoint records it cites remain
authoritative and win on any disagreement.

## Where the project stands

Batches A–D are closed (`checkpoints/batch-d-result.md`). Built and
verified: the Tier 0 statewide register (50,164 MINEDEX sites, snapshot
extract 2026-08-14), the MINEDEX–Maus crosswalk, DEA epoch coverage,
and the D3 pixel-support threshold chain. D3 closed on the
pre-registered fallback: no candidate support passed the Spearman
criterion, so n\* = 144 px stands with `criteria_passed=false` and the
L4 disclosure travelling on every downstream row. Under the
forced-threshold owner decision, 10,372 sites across 989 Maus
footprints are eligible for Tier 1 extraction, and the forced-144
register exists at `curated/register/2026-08-29/`. E5 engine parity
passed 2026-08-29, so statewide extraction is unlocked. Verification
battery: ruff, format, mypy, 1152 tests green (2026-08-29).

## Build sequence

| # | Step | Gate / blocker | Record |
|---|---|---|---|
| 1 | ~~**E5 engine parity**~~ **PASSED 2026-08-29**: official verdict `curated/huntly-validation/2026-08-29/validation.json`, `passed: true`, zero failures (204,060 rows accounted, 196,560 value-compared, all within `1e-6`/`0.1 pp`); reference corrected w1→w3 with pixel-count waiver (A9) | None — `require_huntly_gate` accepts the verdict; statewide extraction UNLOCKED, O3 closed | `checkpoints/huntly-zonal-validation.md`; A7, A9 |
| 2 | ~~**Task 0 forced-threshold path**~~ **Done 2026-08-29**: code landed in `c463a0a`; six-count replay verified (`diag_batch_e_readiness.py --check eligibility`); forced-144 register built at `curated/register/2026-08-29/` — 10,372 eligible / 538 insufficient / 30,833 / 8,421 / 0, total 50,164, `criteria_passed=false` preserved, decision record in the manifest | None — O8 closed 2026-08-25 (replay calls the production function) | `decisions/2026-08-25-batch-e-forced-threshold-entry.md` |
| 3 | **E4 statewide extraction**: site × year × metric GeoParquet; footprint-keyed reads fanned out to sites; `shared_footprint_site_count` and `valid_support_px` on every row | Ran live 2026-08-29 (curated/trajectories/2026-08-29: 2,458,164 rows, 99 partitions, 94,343 not-computable) and ACCEPTED 2026-08-30 (docs/checkpoints/e4-statewide-extraction.md) | `plans/2026-08-22-batch-e-e4-e5.md` (FINAL) |
| 4 | **Batch F context**: DBCA-060 fire overlap (three-state, never inferred absence) and SILO climate joined to trajectories. `fetch-silo` (owner-run, not yet run against the real bucket) downloads the SILO open-data grids; `build-climate-context` builds the curated climate context from those grids | Mirror declined, F1 dissolved 2026-08-29 (A10); fetch-dbca-fire + build-fire-context landed AND run live 2026-08-29 (snapshot 149,621 features verified; fire_context.parquet 404,508 rows, 1987-2025); SILO fetched live 2026-08-29 (39 annual grids 1987-2025, digest verify 40 ok/0 bad/0 missing) and climate_context.parquet built (404,508 rows = 10,372 sites x 39 years, 1,053 not-computable disclosed, matching fire_context row count); E4 accepted 2026-08-30 (accept-trajectories: 14/14 checks, 2,458,164 rows / 99 partitions / 10,372 sites, verdict digest-bound to part bytes) and F6 joined live 2026-08-30 (context_join.parquet 414,880 rows = 10,372 x 40 years 1986-2025; 10,372 explicit no_context_row rows for 1986; 398,034 context_complete) | D13 §6 |
| 5 | **Batch G, QGIS-only**: gate satisfied (E4 accepted 2026-08-30); scope narrowed by owner decision — deliverables are the `build-trajectory-summary` curated GeoPackage (`register_sites` + `site_summary` layers), QML styles, and the private QGIS project; no release package added to `release.PACKAGES`, the release decision is deferred (not taken) | None — gate satisfied; release-package decision deferred by decision, not blocked | `decisions/2026-08-30-batch-g-qgis-only-rescope.md`; A8, A11 |
| 6 | **Tier 2 deep-dive** (conditional): region chosen by pre-registered ranking; runs only if hard gates pass (≥30 eligible high-confidence sites, calibration cases, compute budget) | May legitimately not run | design §8 D4 |

Parallel items: the Tier 0 public-RC lane completed 2026-08-29 — the
D13 §8 P1-P6 build (licence states, evidence ledger, Tier 0 fallback
packages, public tree/payload audits, flip checkpoint) merged to main,
CI green, full-history secret scan clean, and the repository flipped
public on the owner's explicit authorization
(`checkpoints/tier0-public-rc.md`, all 16 fields true;
`reviews/2026-08-29-public-rc-audit.md`).
SILO registration is no longer one of them: the anonymous gridded route
needs no account, which is what closed O7 (A7).

## Architectural decisions, justified

| Decision | Justification | Record |
|---|---|---|
| Python-only, single machine, no cloud/Terraform | Owner decision, fixed. Measured volumes (597 GB windowed reads, block-granular streaming from luminosity) fit one machine; cloud infra would add cost and surface without adding to any claim the product makes | design §1; A1 |
| Immutable dated snapshots, run-manifest sidecars, digest-verified reads, fixture-first TDD | This is what makes the dataset citable and every published figure reproducible; without it the product is unverifiable parquet | D13 §1 |
| Maus v2 polygons as the sole measurement footprint | One consistent, externally published estimand instead of ad-hoc buffers; the 2019-mask and shared-footprint costs are disclosed (L1, L17), not hidden | design §8 D1 |
| D3 protocol frozen before results; criteria never relaxed; failure handled by the pre-registered labelled fallback (forced-144), and the one protocol defect fixed by a new frozen lineage, not a patch | Prevents post-hoc threshold tuning; keeps the eligibility rule falsifiable and the record honest when it fails | design §8 D3; A6; L4 |
| Fail-closed licensing at the export boundary; MINEDEX redistribution closed on conflicting licence evidence | Legal exposure is asymmetric: a wrongly open gate cannot be un-published. The private product loses nothing | D7; L12; `licensing-matrix.md` |
| Tier 1 stays site-keyed with mandatory `shared_footprint_site_count` disclosure | The register is the spine and MINEDEX sites the unit of analysis; 98.2% footprint sharing is disclosed on the row rather than re-keying the product | `decisions/2026-08-25-tier1-product-framing.md` |
| Engine validated against the jarrah reference before any statewide read | Falsifies the measurement engine on ground truth first; a defect found after a 597 GB extraction is a rerun, found before it is a fix | design §10; A7 |
| Annual granularity; sensor-overlap disagreement preserved, never resolved by priority; "cause not determined" until fire/climate context; no causal attribution ever | Matches what Landsat annual composites can support; anything finer or causal would breach the claim boundary the product is built on | D13 E3/E6/F6 |
| Private product: GeoParquet releases consumed through a QGIS project, no web page | Every remaining use case (triage, citable dataset, event-timing input) is data-level and private; a rendered site was cost and claim-boundary risk with no audience | A8 |
| Adapt dataplatform HTTP/zonal primitives, decline its schema, CRS, and storage | Raster-native site × year × metric keying and EPSG:3577 are load-bearing; the observation-store model fits station feeds, not batch raster chronologies | D13 batch rulings |

## Where authority lives

1. `docs/decisions/` — binding owner/director decisions.
2. `docs/checkpoints/` — accepted batch results.
3. `docs/amendments-and-limitations.md` — every post-freeze change
   (A1–A9), every disclosed limitation (L1–L17), open items (O1–O8).
4. `docs/plans/` — the design (`2026-08-15-...-design.md`), the Batch D
   implementation plan (kept: cited by amendments A3/A5), and the live
   Batch E plan (`2026-08-22-batch-e-e4-e5.md`, FINAL 2026-08-25).
5. `docs/reviews/` — evidence cited by the register.
6. `docs/archive/` — superseded handoffs, executed plans, and
   pre-execution reviews; see `archive/README.md`.
