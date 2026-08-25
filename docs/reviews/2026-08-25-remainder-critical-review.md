# Critical review — the remainder of the project after Batch D

Date: 2026-08-25. Written against commit `ea9c2cd` (Batch D closed,
forced-144, `criteria_passed=false`).

**Read:** design doc §1–§10, the Tier 0 / Batch B / Batch C / Batch D
checkpoints, all seven decisions, D13 §2 and §5–§8 (Batches E, F, G and
the Tier 0 public-RC lane), and the Batch E plans (`2026-08-18`,
`2026-08-21` E3, `2026-08-22` E4/E5 draft).

**Measured, not assumed.** Every count below comes from a read-only
probe of `~/data/wa-mine-monitor/curated` on lux (register, crosswalk,
`footprint_support.parquet` from the 2026-08-23 run) and
`~/data/jarrah-rehab` (Huntly pilot cube and site tables). No raster was
re-read and nothing was rebuilt.

---

## 1. What the Batch D failure actually does downstream

### The mechanism is a hard stop, not a warning

`register.assign_trajectory_eligibility` rule 3: when `criteria_passed`
is `False`, **every** matched, high-confidence, support-computed site is
stamped `threshold_not_computed` with `d3_eligible=False`. That is the
whole 10,910-site judged population. `eligible` is therefore 0, by
construction, not by data.

`extract-trajectories` (E4 plan, Task 4) selects on
`trajectory_status == "eligible"`. With zero eligible sites the command
extracts nothing, every partition is empty, and Task 3's empty-write
refusal fires. **Batch E is inert, not merely gated.** No amount of
Batch E implementation changes that; the block is one boolean upstream.

### Reopening it is a code change, not just a decision

The owner decision flagged in `batch-d-result.md` cannot be executed by
writing a decision file alone. `criteria_passed=false` is a recorded
fact and must not be flipped — flipping it *is* the post-hoc relaxation
the pre-registration forbids. What is needed is a third path in rule 3:
a forced-threshold eligibility mode that keeps `criteria_passed=false`
in the manifest, stamps eligibility at n\* = 144 anyway, and carries the
disclosure on every row it produces. That is roughly 30 lines plus tests
in `register.py`, plus an `apply-d3-threshold` flag, plus the decision
record that authorises it.

### The population that would result

Reproducing the eligibility join with the threshold forced to 144 px:

| Outcome | Sites |
|---|---|
| **Eligible at n\* = 144** | **10,372** |
| `insufficient_pixel_support` (support < 144) | 538 |
| Judged population (matches the register's `threshold_not_computed`) | 10,910 |

Over **989 distinct footprints** — exactly the D3 candidate count in the
run manifest. By region: goldfields\_esperance 5,449 sites / 480
footprints; other\_wa 3,808 / 343; pilbara 1,115 / 166.

### Consequences that follow

- **Tier 2 survives.** D4's hard gate is ≥30 high-confidence
  D3-eligible sites in the candidate region. Pilbara has 1,115 and
  Goldfields-Esperance 5,449. Batch D's failure does not close Tier 2's
  entry gate; the D4 ranking can still run after Tier 1.
- **The Batch C volume budget still applies.** 989 footprints is within
  the 1,252 the 597 GB windowed estimate was measured over — provided
  extraction reads per footprint, which the current plan does not (§2.1).
- **The disclosure has to travel.** Every Tier 1 row, the Batch E
  checkpoint, the release manifest (G2 already has a `D3 and Huntly
  checkpoint digests` field), and any site page must carry the
  forced-144 limitation. If it only lives in `batch-d-result.md` it will
  be lost by Batch G.

---

## 2. Expected failures in the remainder

Ranked by certainty × cost of discovering them late.

### 2.1 Read amplification in `extract-trajectories` — CERTAIN, cheap to fix now

The E4 plan's extraction loop (Task 6, Step 3) is
`for source → for year → for site`, and reads the footprint's pixels
inside the site loop. But 98.2% of eligible sites share a footprint with
another site:

| Measure | Value |
|---|---|
| Eligible sites / distinct footprints | 10,372 / 989 |
| Sites per footprint | mean 10.5, median 5, **max 324** |
| Sites sitting on a shared footprint | 10,185 (98.2%) |
| Member pixels per collection-year, **per footprint** | 4.03 M |
| Member pixels per collection-year, **as the plan loops** | 102.2 M |

**25.4× amplification.** It is worse than the 10.5× site/footprint ratio
because the largest footprints carry the most sites. Against the Batch C
measured budget of 597 GB windowed / 3.30 TB whole-tile, the plan as
written implies roughly 15 TB of transfer for an identical result. The
1 GB block cache (decision 17, amended) holds ~400 of the 800×800
float32 blocks and cannot absorb this.

The values are *identical* for sites sharing a footprint — the trajectory
is a function of `maus_id`, not `site_id`. Fix: loop distinct
`maus_id`, compute the metric rows once, fan out to the sites that map
to it. Nothing else in the plan changes.

### 2.2 The E5 Huntly gate cannot pass as specified — CERTAIN

The E4/E5 draft already flags four "Needs Jarrod" blockers. Measurement
shows the situation is worse than "unconfirmed parameters": the
comparison the plan describes is geometrically impossible.

| Fact | Value |
|---|---|
| Maus footprints covering the jarrah Huntly plots | **1** (`a6ddd34a1d67`) |
| Its effective pixel support | **411,895 px** (~370 km², the largest footprint in the state) |
| jarrah plot sites inside it | 206 of 1,074 |
| MINEDEX sites high-confidence matched to it | 13 (plus 7 medium) |

D13 E5's gate is `|Δnbr|, |Δndmi| ≤ 1e-6`. The reference is a 3×3
(90 m, 9-pixel) `nanmean` around a plot centroid; the monitor's value
would be the mean over 411,895 pixels of a whole bauxite operation. The
two differ by orders of magnitude more than the tolerance, and no
`--site-map` fixes it — all 13 candidate monitor sites resolve to the
same single footprint mean. 868 of the 1,074 plots are not inside any
Maus footprint at all, which is limitation L1 (the fixed 2019 mask)
appearing in the flesh.

**The draft plan is reading D13 E5 as a product comparison. The design
doc reads as an engine comparison:** §10, "the zonal engine is validated
on the jarrah pilot cube FIRST — it must reproduce the known Huntly
trajectories within declared tolerance before touching statewide data."
Under that reading the tolerances are not only achievable, they are the
right ones: run the monitor's zonal engine over **jarrah's own annual
composite COGs** (`interim/pilot/composites/{nbart,fractional_cover}/
*_<year>.tif`, EPSG:3577, 30 m, NaN-masked, bands read by description
name) at the same 3×3 windows around the same plot points, and compare
against `series_incumbent_w1.parquet`. Same rasters, same pixels, same
formula, so 1e-6 is a real test of the engine and a failure means a real
defect.

That re-scope also dissolves all four blockers: the reference is
confirmed (jarrah's own documented output), the FC scale question
disappears (same rasters, same units), pixel counts become computable on
both sides from the same block, and no site-key mapping is needed
because both sides use jarrah `site_id`s. It requires an owner decision
because it changes what a pre-registered gate compares — but it is the
only reading under which the gate can ever pass, and it can be built and
run **today**, with no DEA reads and no dependency on the Batch D
outcome.

### 2.3 "Per-site trajectory" is a shared-footprint mean — HIGH

The same 98.2% figure is a product problem, not just a performance one.
Design §1 promises "per-site pages" with "an annual spectral
trajectory". For 10,185 of 10,372 sites that trajectory is shared with
between 1 and 323 other sites, byte-identical. A site page showing an
individual curve, without saying it is a footprint mean shared with N
other MINEDEX records, misstates what was measured — and that is exactly
the class of claim the project's own claim boundary exists to prevent.

This needs a decision before Batch G G3/G4 build the site: either the
public product is footprint-keyed with the MINEDEX sites listed as
members, or site pages carry an explicit "shared footprint (N sites)"
label. It is measurable now and does not depend on any extraction.

### 2.4 D7 blocks the public Tier 1 payload — CERTAIN, and pre-registered

D13 §2 and G1/G3 are unambiguous: MINEDEX-derived *selection* refuses
every public package while D7 is closed. Tier 1's population is defined
by the MINEDEX crosswalk, so the entire Tier 1 product is
publicly unavailable as currently selected, and D5 Pages is expected to
record a failure rather than deploy. D13 pre-registers that outcome as
acceptable, so this is not a surprise — but it means the project's
visible deliverable (a public map of per-site trajectories) does not
exist at the end of the planned sequence unless a non-MINEDEX selection
is defined, e.g. "all Maus v2 WA footprints" with MINEDEX identity
withheld. That is a design question answerable now, on paper, and it
should be answered before Batch G builds a site that cannot ship.

### 2.5 Batch F inputs — MEDIUM, partly resolved by inspection

- **DBCA-060 fire:** an authoritative snapshot is already on this
  machine (`~/data/jarrah-rehab/raw/dbca-060/2026-07-20`, 4.6 GB). The
  ArcGIS mirror route can stay declined, which means F1's heavy
  evidence-adjudication gate is avoidable for v1 — F3's authoritative
  staged-file path is enough. Confirmable in minutes.
- **SILO climate:** no SILO snapshot and no credential is present in
  either data root. F5 is blocked on an account, and D13 makes the
  credential a hard precondition. Worth resolving early; it is the only
  external account the project needs.

### 2.6 Sensor-overlap disagreement (E6) — MEDIUM, testable without new reads

The geomedian collections overlap 1999–2011 and 2013–2021. If
cross-sensor deltas in overlap years are large relative to the
trajectory signal, Tier 1 chronologies become sensor artefacts and E6
turns from a disclosure into a blocker. This is testable from
`support_inputs.parquet` (full-support per footprint-year-collection
values, on luminosity) with no new raster reads — the same table the D3
simulation already wrote.

### 2.7 The export boundary has never executed — MEDIUM

`export_public` still has no caller, `GEOMETRY_NAME_TOKENS` omits
`lon`/`lat`, and the Tier 0 acceptance criterion "licence fields
non-null on every row" has no check in the tree (Batch B finding 7,
deferred to Batch G). G1 wires all three at once, in the batch with the
least slack. The lon/lat token gap and the licence-fields check are each
an hour's work and could be closed now rather than during the export
build.

---

## 3. What can be tested now

| # | Test | Settles | Cost |
|---|---|---|---|
| T1 | Footprint-level dedup in the extraction loop | 2.1 read amplification | Plan edit + one test |
| T2 | Engine parity against the jarrah pilot cube | 2.2 whether the zonal engine is correct at all | ~1 day, no DEA reads, no Batch D dependency |
| T3 | Shared-footprint distribution (done — table in 2.3) | 2.3 product framing | Done |
| T4 | Define a non-MINEDEX public selection on paper | 2.4 whether anything ships publicly | Half a day, no code |
| T5 | Cross-sensor overlap deltas from `support_inputs.parquet` | 2.6 whether Tier 1 is sensor-stable | Hours, on luminosity |
| T6 | SILO account + DBCA-060 authoritative snapshot validation | 2.5 Batch F inputs | Hours |
| T7 | Forced-144 eligibility path in `register.py` | Unblocks Batch E at all | Half a day + decision record |

T2 is the highest-value item on the list. It is the only test that can
still find a defect in the measurement engine itself, it is independent
of every Batch D question, and under the current plan it would be run
*after* a statewide extraction has already been attempted.

---

## 4. Recommended order

1. **T7 + decision.** Record the owner decision to proceed under the
   forced-144 disclosure, and implement the forced-threshold eligibility
   path (`criteria_passed` stays false in the manifest). Without this
   nothing downstream can run.
2. **T2.** Re-scope E5 to engine parity on the pilot cube and run it.
   If the engine disagrees with jarrah at 1e-6 on identical pixels,
   everything after it is worthless, and that is knowable this week.
3. **T1.** Fix the extraction loop before any statewide run — 25×
   transfer is the difference between a two-day run and a month.
4. **T3/T4 decisions.** Settle the shared-footprint product framing and
   the public-selection question before Batch G exists.
5. **T5, T6.** Clear the Batch E/F input risks in parallel.

---

## 5. Corrections to existing records

- `docs/checkpoints/batch-d-result.md`, 2026-08-23 section, labels
  "Candidate footprint counts per stratum … total 1,232". 1,232 is the
  count of footprints with a **computed** support value (1,252 minus the
  20 outside-RDC exclusions). The **candidate** count — support ≥ 144 px
  and ≥ 1 epoch year — is **989**, which is what
  `footprint_support.parquet.run_manifest.json` records as
  `n_candidate_footprints` and what `sup["candidate"].sum()` returns.
  The per-stratum figures quoted there are commodity classifications of
  the 1,232, not candidate counts.
- Open item O1 in `docs/amendments-and-limitations.md` is now answered:
  20 of 1,252 Tier-1 footprints with usable geometry fall outside every
  RDC polygon, **1.60%** against the 5% ceiling. The ceiling has
  headroom; the earlier retracted figure (1.1% against 1,753) was the
  wrong denominator, and the correct one is 1,252.
