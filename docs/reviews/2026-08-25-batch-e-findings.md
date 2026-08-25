# Findings for review — Batch E readiness, 2026-08-25

Ten findings from the post-Batch-D review, one section each, each
self-contained: claim, evidence, the project sources it rests on,
how to reproduce it, what it costs, and the decision it needs.

Written to be read cold. Nothing here assumes the conversation that
produced it. Where a number is measured rather than quoted, the
reproduction line names the command that regenerates it.

Companion documents, all in this repo:

- `docs/reviews/2026-08-25-remainder-critical-review.md` — the narrative
  version of F1–F8, with the ranking and the recommended order.
- `docs/amendments-and-limitations.md` — the standing register (A1–A6,
  L1–L17, O1–O7). Findings that became limitations name their L-number.
- `docs/plans/2026-08-22-batch-e-e4-e5.md` — FINAL 2026-08-25. Already
  carries the fixes for F1, F2 and F3.
- `scripts/diag_batch_e_readiness.py` — regenerates F1's population,
  F2's amplification, F3's Huntly geometry, and F9's ratio.

## How to review this

Every finding carries a **Confidence** line with one of three values:

- **Measured** — a number produced by reading curated artefacts. Rerun
  the reproduction line; if it disagrees, the finding is wrong.
- **Read from code** — a claim about what the code does, citable to a
  line. Read the line.
- **Judgement** — a claim about consequence or product framing. This is
  where disagreement is legitimate and where the owner decisions sit.

Three findings need an owner decision before Batch E can run: **F1**
(forced-threshold path), **F3** (what the E5 gate compares), **F4**
(what a shared-footprint trajectory is allowed to claim). The rest are
either already fixed in the plan or are Batch F/G scheduling.

### Caveat carried on every population figure

The eligibility replays below join the 2026-08-23 register against
`curated/crosswalk/2026-08-16`. The register itself was built against a
crosswalk artefact whose `no_usable_footprint` and
`crosswalk_not_high_confidence` totals differ by 933 sites from that
join (31,766/7,488 replayed vs 30,833/8,421 recorded). **The judged
population matches exactly at 10,910**, which is the only population the
eligibility split depends on, so the 10,372/538 figures stand. The 933
shift sits entirely inside the two never-judged buckets. Resolving which
crosswalk artefact the register was built from is open item **O8** below.

---

## F1. `eligible` is zero by construction, so Batch E is inert, not merely gated

**Claim.** The closed Batch D result (`criteria_passed=false`) does not
slow Batch E down; it makes it produce nothing. Every one of the 10,910
judged sites is stamped `threshold_not_computed`, `eligible` is empty,
and the Batch E extraction command selects on `trajectory_status ==
"eligible"`. Reopening the lane requires a **code change**, not only an
owner decision.

**Confidence.** Read from code, plus measured population.

**Evidence.**

- `assign_trajectory_eligibility` rule 3: when `criteria_passed` is
  `False`, *every* judged row becomes `threshold_not_computed` — the
  `insufficient` / `eligible` split is never evaluated.
- `d3_eligible` is then forced `False` for those rows, and
  `d3_threshold_px` is stamped with `MIN_FULL_SUPPORT_PX`, not `n*`.
- The CLI already anticipates the fallback threshold but does not use it
  for selection: `applied_threshold_px = n_star if criteria_passed else
  d3_protocol.MIN_FULL_SUPPORT_PX`.
- The Batch E extraction reads `trajectory_status == "eligible"` to build
  its site list.

**Sources.**

| What | Where |
| --- | --- |
| Rule 3, the decisive branch | `src/wa_mine_monitor/register.py:1237` (function), `:1337` (`status.loc[judged_mask] = "threshold_not_computed"`) |
| `d3_eligible` forced False | `src/wa_mine_monitor/register.py:1346` |
| Fallback threshold stamped | `src/wa_mine_monitor/register.py:1349` |
| `criteria_passed` read | `src/wa_mine_monitor/cli.py:5025` |
| Fallback computed but unused for selection | `src/wa_mine_monitor/cli.py:5027` |
| Call site | `src/wa_mine_monitor/cli.py:5089-5094` |
| Selection predicate | `docs/plans/2026-08-22-batch-e-e4-e5.md:736` |
| The failure being closed | `docs/checkpoints/batch-d-result.md`; `docs/handoffs/` newest |
| Freeze rule that forbids flipping the flag | design doc §8 D3, "never relaxed after seeing results" |

**Reproduce.**

```
uv run python scripts/diag_batch_e_readiness.py --check eligibility
```

**Consequence.** Batch E tasks 1–7 are buildable and testable today, but
running them on live data yields an empty partition set. Batch F's
context join and Batch G's Tier 1 payload both inherit the emptiness.

**Decision needed.** Whether to authorise a forced-threshold path. The
plan's **Task 0** specifies it and is marked BLOCKING: a
`forced_threshold: bool = False` argument, a `d3_forced_threshold`
disclosure column on `D3_ELIGIBILITY_COLUMNS` and `REGISTER_SCHEMA`, and
`--forced-threshold` / `--decision-record` flags on `apply-d3-threshold`.
The decision must **not** be implemented by flipping `criteria_passed` —
that is the pre-registration violation the freeze rule exists to prevent.
See `docs/plans/2026-08-22-batch-e-e4-e5.md` Task 0.

---

## F2. The forced-144 population is 10,372 sites over 989 footprints

**Claim.** If the forced-threshold path in F1 is authorised at
`n* = 144 px`, the resulting Tier 1 population is 10,372 eligible sites,
538 insufficient, out of 10,910 judged, spread over 989 distinct Maus
footprints. D4's ≥30-site Tier 2 regional gate passes.

**Confidence.** Measured (judged population verified against the
register; see the caveat above).

**Evidence.**

| `trajectory_status` under forced-144 | Sites |
| --- | --- |
| `eligible` | 10,372 |
| `insufficient_pixel_support` | 538 |
| `no_usable_footprint` | 30,833 |
| `crosswalk_not_high_confidence` | 8,421 |
| `threshold_not_computed` | 0 |
| **total** | **50,164** |

Regional eligible counts against D4's ≥30 hard gate: Pilbara 1,115,
Goldfields-Esperance 5,449. Both pass with wide margin.

**Sources.**

- `~/data/wa-mine-monitor/curated/register/2026-08-23/register.parquet`
- `~/data/wa-mine-monitor/curated/crosswalk/2026-08-16/crosswalk.parquet`
- `~/data/wa-mine-monitor/curated/d3-inputs/2026-08-23/footprint_support.parquet`
- Expected-count table in `docs/plans/2026-08-22-batch-e-e4-e5.md:191`
  (Task 0's live-count assertion — this table is the acceptance test)

**Reproduce.**

```
uv run python scripts/diag_batch_e_readiness.py --check eligibility
```

**Consequence.** The population is large enough to be worth extracting
and large enough to be expensive; see F9.

**Decision needed.** None beyond F1. This is what F1's decision buys.

---

## F3. The extraction loop as drafted reads every footprint once per site: 25.4× amplification

**Claim.** The Batch E Task 6 read loop was keyed on `site_id`. Because
98.2% of eligible sites share a Maus footprint with at least one other
site, that loop re-reads the same pixels 25.4 times over.

**Confidence.** Measured.

**Evidence.**

| Quantity | Value |
| --- | --- |
| Member px per collection-year, loop over distinct footprints | 4.03 M |
| Member px per collection-year, loop over sites (as drafted) | 102.2 M |
| Amplification | **25.4×** |
| Eligible sites on a shared footprint | 10,186 of 10,372 (**98.2%**) |
| Sites per footprint | mean 10.5, max 324 |

Against Batch C's measured windowed-read budget of **597.1 GB** for the
statewide four-collection sweep, a 25.4× amplification implies roughly
**15 TB** of reads. The data disk had 1.7 TB free at Batch C time.

**Sources.**

| What | Where |
| --- | --- |
| Batch C measured read budget | `docs/checkpoints/batch-c-result.md:52-54` (597,113,825,460 bytes) |
| Free disk at Batch C | `docs/checkpoints/batch-c-result.md:90` |
| The drafted site-keyed loop | `docs/plans/2026-08-22-batch-e-e4-e5.md` Task 6 (pre-2026-08-25 draft) |
| The fix | same file, Task 6 read loop, now keyed on `maus_id` with per-site fan-out of `metric_rows` |
| The regression test | same file, Task 7, `test_extract_trajectories_reads_each_footprint_once_for_sites_that_share_it` (asserts `len(calls) == len(set(calls))` and `a.equals(b)`) |

**Reproduce.**

```
uv run python scripts/diag_batch_e_readiness.py --check sharing
```

**Consequence.** Avoided. The plan is already fixed: the loop iterates
`sorted(sites_by_maus_id.items())`, reads once, and fans the shared
`metric_rows` out to each member site via `trajectories.RowContext`.
`item_missing` and `read_failed` diagnostics also fan out, one row per
affected site, so the three-count reconciliation still balances.

**Decision needed.** None. Recorded here because the amplification is
the reason Task 6 changed shape, and a reviewer comparing the plan
against D13 E4 will otherwise see an unexplained divergence.

---

## F4. A per-site trajectory is a shared-footprint mean for 98.2% of sites

**Claim.** Under the fixed loop, 10,186 of 10,372 eligible sites receive
a trajectory that is **identical** to that of every other site on the
same footprint. On the largest footprint, 324 MINEDEX sites share one
series. The row is keyed `site_id` but the value is not site-specific.

**Confidence.** Measured (the counts); Judgement (that this is a product
problem rather than an acceptable disclosure).

**Evidence.** Same measurement as F3: mean 10.5 sites per footprint,
max 324, 98.2% non-unique.

**Sources.**

- Limitation **L17** in `docs/amendments-and-limitations.md`.
- Open item **O6** in the same file (product framing).
- Claim boundary: `AGENTS.md`, "outputs are spectral detections, never
  compliance or performance findings"; design doc §1.
- Tier 1 definition (trajectories over the fixed 2019 Maus v2 mask):
  `docs/plans/2026-08-15-wa-mine-rehab-monitor-design.md`.

**Reproduce.**

```
uv run python scripts/diag_batch_e_readiness.py --check sharing
```

**Consequence.** Batch G renders per-site cards and a per-site map. A
card that shows a footprint-level series under a site name, without
saying so, asserts something the data does not support. This is the
strongest claim-boundary risk remaining in the project.

**Decision needed.** One of:

1. Keep the site key, add a mandatory `shared_footprint_site_count`
   field, and require every rendered card and table to state it.
2. Re-key the Tier 1 product to `maus_id` and present MINEDEX sites as
   an attribute of the footprint rather than the unit of analysis.

Option 2 is the honest framing and also collapses the D7 exposure in F5,
since a footprint-keyed product's selection can be made non-MINEDEX.
Option 1 is cheaper and keeps the register as the spine. **This decision
should be taken before Batch E writes its partition schema**, because
the key choice propagates into every downstream artefact.

---

## F5. D7 blocks the entire public Tier 1 payload, not a subset of columns

**Claim.** The MINEDEX licence conflict closes redistribution of any
public payload whose **row selection** derives from MINEDEX. Tier 1's
row selection is the register, which is MINEDEX. Row filtering is
explicitly prohibited as a remedy: a mixed package fails as a whole.

**Confidence.** Read from decision records.

**Evidence.**

- D13 §2, Batch E row: "D7 blocks public release of MINEDEX-selected
  rows."
- D13 §2 closing paragraph: Batch G "may finish its private
  implementation with the Pages gate recorded as failed; the failure
  must not be waived or treated as a Batch G failure to preserve
  sequence." The failure is therefore **pre-registered**, not a
  surprise.
- G1 acceptance: "MINEDEX lineage or MINEDEX-derived selection refuses
  every public package while D7 is closed"; "Row filtering is
  prohibited; a mixed package fails as a whole"; "The current Tier 1
  output remains blocked from public export if its selection derives
  from MINEDEX."

**Sources.**

| What | Where |
| --- | --- |
| Dependency/gate summary | `docs/decisions/2026-08-16-d13-batches-c-g-detailing.md:34-48` |
| Pre-registered Pages failure | same file, `:46` |
| G1 export-gate acceptance | same file, `:990-1001` |
| G-batch preconditions and D7 restatement | same file, `:1164` |
| D7 itself | `docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md` |
| Limitations | L-series (licence and export) in `docs/amendments-and-limitations.md` |

**Reproduce.** Documentary; no command.

**Consequence.** Nothing public ships from Tier 1 while D7 is closed.
The private deliverable is unaffected. The portfolio value of a public
dashboard is not available through this lane.

**Decision needed.** Whether to build a non-MINEDEX selection for the
public lane — i.e. select rows on Maus footprints (CC-BY-SA-4.0) and
carry no MINEDEX identifier, lineage, or selection. F4 option 2 delivers
this as a side effect. Otherwise Batch G ships private-only and the
Pages gate is recorded failed exactly as pre-registered.

---

## F6. The E5 Huntly gate cannot pass as specified, for geometric reasons

**Claim.** D13 E5 requires NBR/NDMI agreement to `1e-6` between the
monitor and the jarrah Huntly reference. The jarrah reference is a 3×3
(9 px, 90 m) `nanmean` around a plot centroid. The monitor value for
Huntly would be the mean over a single Maus footprint of **411,895 px
(~370 km²)** — the largest footprint in WA, an entire bauxite operation.
No choice of tolerance parameter, site map, or reference variant makes
those two quantities agree to 1e-6.

**Confidence.** Measured.

**Evidence.**

| Fact | Value |
| --- | --- |
| Maus footprints covering the jarrah Huntly plots | **1** (`a6ddd34a1d67`) |
| Its effective pixel support | **411,895 px** |
| jarrah plot sites inside it | 206 of 1,074 (the other 868 fall outside the 2019 Maus mask) |
| MINEDEX sites high-confidence matched to it | 13 (plus 7 medium) |

All 13 candidate monitor sites resolve to the **same** footprint mean, so
a `--site-map` cannot make the comparison one-to-one either.

**Sources.**

| What | Where |
| --- | --- |
| E5 gate and tolerances | `docs/decisions/2026-08-16-d13-batches-c-g-detailing.md:635-661` |
| "zonal engine validated on the jarrah pilot cube FIRST" | design doc §10 |
| jarrah reference series | `~/data/jarrah-rehab/probe-out/detection_estimand/series_incumbent_w1.parquet` (1,074 sites, 38,714 rows, 1988–2025) |
| jarrah site geometry | `~/data/jarrah-rehab/probe-out/detection_estimand/site_meta.parquet` |
| Maus footprints | `~/data/wa-mine-monitor/raw/maus_v2/2026-08-16/wa_extract.gpkg` |
| jarrah sampling contract (3×3 nanmean, bands by description name, EPSG:3577, NaN-masked) | `/Users/jarrodbaker/Documents/jarrah-rehab/src/jarrah_rehab/detection/series.py` module docstring |

Note: the E4/E5 plan draft cited `~/Documents/jarrah-rehab-p7`. **That
path does not exist**; the repository is `~/Documents/jarrah-rehab`.

**Reproduce.**

```
uv run python scripts/diag_batch_e_readiness.py --check huntly
```

**Consequence.** As drafted, E5 is unpassable, and E4 statewide
extraction is gated on E5 passing. That is a hard deadlock independent of
F1.

**Decision needed.** Adopt the design-§10 reading: E5 is an **engine**
test on the **pilot cube**, not a product test against DEA. Sample
jarrah's own annual composite COGs
(`~/data/jarrah-rehab/interim/pilot/composites/nbart/nbart_<year>.tif`
and `.../fractional_cover/fractional_cover_<year>.tif`) with the
monitor's own zonal engine at jarrah's site points, 3×3 block clipped to
bounds, mean over non-NaN members, and compare against
`series_incumbent_w1.parquet`. Same rasters, same pixels, same formula —
the 1e-6 tolerance is then correct by construction, and a failure is a
real defect in the monitor's zonal reduction, which is the only thing
this gate can usefully protect.

This is an owner decision because it changes what a pre-registered gate
compares. It must be recorded in `docs/decisions/` before Task 8 is
written, exactly as the D3 protocol changes were. What it does **not**
change: the tolerances, the requirement that validation precedes any
statewide extraction, or the verdict artefact being the sole unlock.

The re-scope is already written into
`docs/plans/2026-08-22-batch-e-e4-e5.md` (section "E5: what the Huntly
gate compares (RE-SCOPED — owner decision required)", Task 8's
`sample_pilot_cube(composites_dir, sites, *, window=3)`, and the exact
CLI deltas: delete `--site-map` and `--fc-reference-scale`, add
`--composites-dir`, `--site-meta`, `--window`).

**Bonus.** Under the re-scope the whole of E5 becomes runnable today. It
touches no DEA data, needs no Batch D artefact, and does not wait on
F1's decision.

---

## F7. Batch F's two inputs are in opposite states: DBCA-060 present, SILO absent

**Claim.** The DBCA-060 mirror-provenance gate that D13 flags as a Batch
F risk is avoidable, because an authoritative copy is already on disk.
The SILO dependency is the real one: there is no snapshot and no
credential anywhere in the environment.

**Confidence.** Measured (file presence); Read from decision records
(the gate).

**Evidence.**

- DBCA-060: 4.6 GB at `~/data/jarrah-rehab/raw/dbca-060/2026-07-20`,
  authoritative acquisition, not a mirror. The conditional mirror
  provenance and licence-evidence adjudication D13 §2 names for Batch F
  does not need to be entered.
- SILO: no snapshot directory, no credential in the secrets store, no
  account.

**Sources.**

| What | Where |
| --- | --- |
| Batch F licence dependency ("DBCA-060 mirror use is conditional…; SILO credentials remain secret") | `docs/decisions/2026-08-16-d13-batches-c-g-detailing.md:41` |
| Secrets handling | `src/wa_mine_monitor/secrets.py` |
| Open item | **O7** in `docs/amendments-and-limitations.md` |

**Reproduce.**

```
du -sh ~/data/jarrah-rehab/raw/dbca-060/2026-07-20
ls ~/data/wa-mine-monitor/raw | grep -i silo   # expect: nothing
```

**Consequence.** Batch F can start its DBCA-060 work with no licence
adjudication. Its SILO work cannot start at all.

**Decision needed.** Register a SILO account and record the credential,
or drop the climate-context join from Batch F scope. This is a
lead-time item: register it now regardless, because account approval is
not instant and the join is the last thing in the batch.

---

## F8. The export boundary has never executed

**Claim.** `export_gate.export_public` has no caller anywhere in the
tree. Its enforcement has therefore never run against a real frame. Two
specific gaps are already known and deferred.

**Confidence.** Read from code and closeout record.

**Evidence.**

- `export_public` has no caller: `rg "export_public" src tests` returns
  only the definition and its own tests.
- `GEOMETRY_NAME_TOKENS = ("geom", "wkt", "wkb", "easting", "northing")`
  — it covers `easting`/`northing` but **not** `lon`/`lat`, which are
  `REGISTER_SCHEMA` columns.
- Design doc §4's Tier 0 acceptance criterion "licence fields non-null
  on every row" is unrepresented in code: `rg "licence_field" src tests`
  returns nothing.

**Sources.**

| What | Where |
| --- | --- |
| The finding, its deferral and rationale | `docs/checkpoints/batch-b-closeout.md:24` (finding 7), rationale at `:33-66` |
| Where it self-defers to | Batch G (G1) and the Tier 0 public-RC lane created by D12 item 6 |
| G1 acceptance ("becomes wired to the only release command") | `docs/decisions/2026-08-16-d13-batches-c-g-detailing.md:996` |
| Module | `src/wa_mine_monitor/export_gate.py` |

**Reproduce.**

```
rg -n "export_public" src tests
rg -n "GEOMETRY_NAME_TOKENS" src
rg -n "licence_field" src tests   # expect: nothing
```

**Consequence.** The deferral is correct and was recorded as
non-blocking — the drift is only live once something calls
`export_public` on a register-shaped frame. But it means the first real
exercise of the licence boundary happens in Batch G, on a payload that
F5 says is blocked anyway. The gate will get its first live run under
the least forgiving conditions.

**Decision needed.** None now. Flagged so the Batch G plan budgets for
fixing `GEOMETRY_NAME_TOKENS`, adding the licence-fields-non-null check,
and wiring the first caller — three items, not one.

---

## F9. Record correction: the candidate count is 989, not 1,232

**Claim.** `docs/checkpoints/batch-d-result.md` carried a bullet that
labelled 1,232 as the candidate footprint count. 1,232 is the number of
footprints with a **computed support value** that were then classified by
commodity. The candidate count — support ≥ 144 px and at least one epoch
year — is **989**.

**Confidence.** Measured.

**Sources.**

- `docs/checkpoints/batch-d-result.md` — bullet corrected 2026-08-25.
- `~/data/wa-mine-monitor/curated/d3-inputs/2026-08-23/footprint_support.parquet`,
  columns `candidate` and `selected`.

**Reproduce.**

```
uv run python scripts/diag_batch_e_readiness.py --check outside-rdc
```

**Consequence.** 989 is also the footprint count behind F2's 10,372
eligible sites, which is the consistency check that surfaced the error.

**Decision needed.** None. Correction applied.

---

## F10. Open item O1 is closed: the outside-RDC fraction is 1.60%

**Claim.** The DPIRD-020 outside-RDC exclusion decision set a 5% ceiling
on the fraction of the Tier 1 population excluded for falling outside
every RDC polygon. The measured fraction is **20 of 1,252 = 1.60%**.
Well inside the ceiling.

**Confidence.** Measured.

**Sources.**

- `docs/decisions/2026-08-21-d3-outside-rdc-exclusion.md` (the ceiling
  and the `n_for_ceiling` denominator definition).
- Open item **O1** in `docs/amendments-and-limitations.md`, now closed.
- `docs/checkpoints/batch-d-result.md` — bullet added 2026-08-25.

**Reproduce.**

```
uv run python scripts/diag_batch_e_readiness.py --check outside-rdc
```

**Consequence.** None adverse. Recorded because an open item with no
measured value is indistinguishable from an unnoticed failure, and this
one is neither.

**Decision needed.** None. Item closed.

---

## New open item

**O8. Which crosswalk artefact was the 2026-08-23 register built from?**

Replaying the eligibility join against `curated/crosswalk/2026-08-16`
reproduces the judged population exactly (10,910) but shifts 933 sites
between `no_usable_footprint` (31,766 replayed vs 30,833 recorded) and
`crosswalk_not_high_confidence` (7,488 vs 8,421). Both buckets are
never-judged, so no eligibility figure in this document depends on the
answer. It still needs resolving before Task 0's live-count assertion is
treated as a regression test, because that assertion pins all six
counts, not just the judged three.

Likely causes, in order: the register was built against a differently
dated crosswalk; or `assign_trajectory_eligibility`'s de-duplication of
ambiguous multi-footprint matches differs from the replay's
`sort_values(["site_id","maus_id"]).drop_duplicates(keep="first")`.

To resolve: read the run manifest beside
`curated/register/2026-08-23/` and compare its recorded crosswalk digest
against the SHA256SUMS of each dated crosswalk directory.

---

## Recommended order

1. **F6 decision** and E5 build. Independent of everything else,
   runnable today, and it is the gate that unlocks E4.
2. **F4 decision**. It must land before Batch E writes its partition
   schema, because the key choice propagates downstream.
3. **F1 decision** and Task 0. Gated on nothing but the owner.
4. **O8**. Cheap, and it hardens Task 0's acceptance test.
5. **F7** SILO registration. Lead-time item; start it in parallel.
6. E4 build with the F3 fix already in place, then E6 sensor overlap
   (computable from `support_inputs.parquet` with no new raster reads).
7. Batch F, then Batch G with F5 and F8 budgeted.
