# Amendments and disclosed limitations

One register for two things that are otherwise scattered across
`docs/decisions/`, `docs/checkpoints/`, and plan files: every amendment
made to a plan or protocol **after it was in force**, and every
limitation the project has disclosed rather than corrected.

This file is derivative. The decision and checkpoint records it cites
remain authoritative; where they disagree with this summary, they win.

**Scope.** Amendments are changes to a rule that was already governing
execution — a frozen protocol, a plan decision under execution, an
acceptance criterion. Pre-execution plan revision (codex plan attack,
review rounds) is not an amendment and is not listed. Limitations are
statements the project has committed to carrying downstream, not open
bugs.

Compiled 2026-08-25, current to commit `ea9c2cd`. Forward-looking risk
for the batches not yet run, with the measurements behind it, is in
`docs/reviews/2026-08-25-remainder-critical-review.md`.

## 1. Amendments

| ID | Date | What changed | Digest impact | Record |
|---|---|---|---|---|
| A1 | 2026-08-16 | Execution host moved to luminosity `/mnt/data` | n/a | `checkpoints/batch-c-result.md` |
| A2 | 2026-08-21 | DPIRD-020 re-pinned to the SLIP public REST layer | none | `decisions/2026-08-21-dpird-020-repin.md` |
| A3 | 2026-08-21 | DEA tile-lattice origin corrected to `au-30` | none | `plans/2026-08-16-batch-d-implementation.md`, commit `ce021f5` |
| A4 | 2026-08-21 | Footprints outside every RDC polygon excluded | none | `decisions/2026-08-21-d3-outside-rdc-exclusion.md` |
| A5 | 2026-08-21 | Batch D decision 17: VSI curl cache is RAM, not disk | n/a | `plans/2026-08-16-batch-d-implementation.md`, commit `b1891d7` |
| A6 | 2026-08-23 | MINEDEX commodity codes + valid-member fraction | **new lineage** | `decisions/2026-08-23-d3-commodity-codes-and-valid-fraction.md` |
| A7 | 2026-08-25 | E5 Huntly gate re-scoped to engine parity on the jarrah pilot cube | none | `decisions/2026-08-25-e5-engine-parity-rescope.md` |
| A8 | 2026-08-25 | Public web page withdrawn; output re-scoped to GeoParquet + private QGIS project | none | `decisions/2026-08-25-public-web-page-descope.md` |

**A1 — execution host.** Batch C's measured volume re-derivation
(597.1 GB windowed, 3.30 TB whole-tile upper bound) excluded the
MacBook Air by measurement (35 GiB free) and excluded whole-tile
staging on every available machine (1.7 TB free < 3.30 TB). Extraction
runs as block-granular streaming reads from luminosity. The same
re-derivation replaced the design's provisional figures (367 tiles →
117 measured; 350 GB → 597.1 GB; 2.3 TB → 3.30 TB).

**A2 — DPIRD-020 re-pin.** The pinned bulk-download host answered the
first live fetch with a SLIP SSO redirect and 403. Re-pinned to the
public ArcGIS REST layer on the same Data WA record; licence
re-verified CC-BY-4.0 on 2026-08-21. Loader contract, snapshot layout,
manifests, and the frozen D3 region source names are unchanged.

**A3 — tile lattice origin.** The origin-at-(0, 0) formula frozen into
the Batch D plan on 2026-08-16 back-solved invented negative tile
indices and refused live tile x34y37. DEA collection 3 counts 96 km
tiles from (−4,416,000, −6,912,000); confirmed against live tiles
x31y37 and x34y37. This is a tile-identity validation check, not a
protocol parameter, so the digest is untouched.

**A4 — outside-RDC exclusion.** Twenty Perth-metropolitan quarry and
sand-pit footprints sit 4.8–23.9 km outside the nearest RDC polygon;
DPIRD-020 excludes the metropolitan area by construction, so the
"uncovered point is a refusal" rule fired on a population it did not
anticipate. Excluded with disclosure rather than reclassified, because
reclassifying (`other_wa`) or adding a metro stratum would change the
frozen protocol's region semantics. A 5% ceiling refuses the run
outright if the pattern indicates a wrong region snapshot. Input
scoping, so the digest is unchanged. See O1.

**A5 — decision 17.** `CPL_VSIL_CURL_CACHE_SIZE` is a RAM cache. The
first luminosity run with the 50 GB setting was OOM-killed at 10.5 GB
RSS after 91 minutes on a 15 GB box, with nothing written to disk. The
live run uses a 1 GB curl cache and `GDAL_CACHEMAX=1024`; no disk
requirement follows from the cache, and the transfer budget is
unchanged.

**A6 — commodity codes and valid-member fraction.** The only amendment
that changed protocol content. Two defects, both observed in
stratification counts and computability fractions and neither in an
accuracy result:

- `commodity_token_rules` matched English substrings while the register
  holds verbatim MINEDEX codes (`Au`, `Fe`, `Ni, Co`, `Bx`, `HM`), so
  every one of 1,252 footprints classified as `other` and 48 strata
  were inadequate. Replaced by exact-token `commodity_code_rules`.
- Batch D plan decision 11 (all member pixels valid in FC and a
  geomedian) is unattainable after 2012 — `dea_fc_pc` nodata over
  water, pit lakes, and shadow held `computable_fraction` at 0.75–0.89
  against the 0.90 floor at every support level. Replaced by
  `min_valid_member_fraction: 0.95`, with values computed over valid
  members.

Both are Batch D plan decisions, not D13-frozen content. The accuracy
criteria (NBR/NDMI P90 ≤ 0.03, FC P90 ≤ 5 pp, median Spearman ≥ 0.95,
computable fraction ≥ 0.90), support set, regions, groups, shape
classes, adequacy counts, selection, and replicates are unchanged, and
the decision records that no error, Spearman, or threshold value
informed it. The 2026-08-18 freeze
(`b2fa76f7d1dae1cfabad2f83828246e78024b1c246432eb6deeaa35598f90272`) is
superseded by the 2026-08-23 lineage
(`10875cef8450c96a1bdc606aebc47d6a1621e3bf72344d45afe1537c431d06fa`);
the superseded protocol directory is kept, not deleted, and the failed
run's outputs stay under their own date as the record that triggered
the change.

**A7 — E5 gate re-scope.** D13 E5's gate text never named what is
compared to what; the E4/E5 draft plan read it as monitor DEA-derived
footprint means against the jarrah Huntly reference, which measurement
showed is geometrically unpassable (a 9-pixel plot mean against the
411,895-pixel `a6ddd34a1d67` footprint mean at 1e-6). Re-scoped to the
design-§10 reading: the monitor's zonal engine samples jarrah's own
pilot composite COGs at jarrah's site points (3×3, mean over non-NaN)
and must reproduce `series_incumbent_w1.parquet`. Tolerances, the
validation-before-extraction ordering, and the verdict artefact as sole
unlock are unchanged, so no protocol digest is affected.

**A8 — public web page withdrawn.** The design §1 fixed output decision
("static site + map + GeoParquet") is amended by owner decision: the
output is versioned GeoParquet releases plus a private QGIS project
over the curated artefacts. A scope withdrawal, not a gate waiver — the
D5 Pages gate is recorded as never evaluated because its deliverable no
longer exists, and D13 §2's no-waiver rule is untouched. Batch G's
rendering tasks (site cards, tables, MapLibre/PMTiles) are withdrawn;
versioned releases and export gating are retained, and
`export_gate.export_public` is promoted from deferred (L11) to a
Batch G task as an `export-release` command. The L17 sharing disclosure
attaches to the `shared_footprint_site_count` schema field, data
dictionary, and QGIS styling instead of rendered pages. The Tier 0
public-RC lane (repository flip) is unaffected. Closes O5.

### What was deliberately not amended

Batch D's D3 criteria failed on the 2026-08-23 rerun and were left
alone. `spearman_median` fails in 25 criterion cells at every candidate
support, so no support passes; 144 px was disclosed as a forced
fallback with `criteria_passed=false`, per the design doc §8 D3
pre-registration ("never relaxed after seeing results"). Support
extension to 196–400 px and a site-level flatness exclusion were both
diagnosed and rejected; tolerance-gated concordance is flagged for
later review and not adopted. Closure record:
`checkpoints/batch-d-result.md`, commit `ea9c2cd`.

## 2. Disclosed limitations

### Estimand and population

**L1 — fixed 2019 mask.** Tier 1 measures the Maus v2 mining-land-use
extent as interpreted for 2019 (producer accuracy 78.9% for the mine
class). Post-2019 expansion, and land rehabilitated and omitted from
the 2019 mask, are outside the estimand. The fixed mask creates
temporal look-ahead and survivor exposure. Design doc §4; must be
stated on the site and in the data dictionary.

**L2 — claim boundary.** Outputs are spectral detections. Never
compliance findings, never performance findings, never operational
rehabilitation dates.

**L3 — zero eligible sites today.** The register carries 50,164 rows
with `eligible = 0`: `no_usable_footprint` 30,833,
`threshold_not_computed` 10,910, `crosswalk_not_high_confidence` 8,421,
`insufficient_pixel_support` 0. Nothing is eligible for Tier 1
extraction while `criteria_passed=false` stands.

### D3 threshold

**L4 — binding limitation.** At the fallback threshold n\* = 144 px,
`spearman_median` < 0.95 in 25 criterion cells, concentrated in
`dea_gm_ls8cls9c` NBR/NDMI over gold, nickel, and iron-ore strata.
Geomedian SWIR-index rank stability for spectrally flat footprints is
not attainable at any tested or diagnostically probed support
(9–400 px). Absolute-error and computability criteria are unaffected —
p90 and `computable_fraction` pass everywhere at 144 px. **Any Batch E
use of the register must carry this disclosure.**

**L5 — failure mechanism.** Per-site Spearman correlates with series
spread (r = 0.62 against log range). Sites whose entire 12-year
NBR/NDMI range sits below the protocol's own 0.03 p90 tolerance fail
96% of the time; range > 0.10 fails 26%. The failures are geological —
spectrally flat footprints whose year ranking is sub-tolerance noise —
not subsampling distorting real chronologies, and they span three
commodities, so excluding any one does not cure it.

**L6 — valid-member conditioning (statistical).** The simulation draws
`s` valid pixels and compares against the mean over all valid pixels,
while `apply-d3-threshold` compares n\* to the **geometric**
`effective_pixel_support_px`. A site with geometric support n\* may hold
as few as `ceil(0.95 × n*)` valid pixels in a given year, so
reduced-support error on such sites may be slightly understated. Batch
E extraction must apply the same `min_valid_member_fraction` rule per
site-year-collection and record `valid_support_px`, so trajectory
values are computed over the population the threshold was derived on.

**L7 — two commodity strata carry no evidence.** `bauxite_alumina` and
`mineral_sands` never reach 10 footprints per stratum (12 and 16
candidates statewide), so neither contributes to the threshold.
Disclosed in `stratum_summary`, not blocking.

**L8 — Perth-metro footprints have no threshold.** The 20 excluded
footprints receive no region-stratified threshold and their sites are
stamped `trajectory_status = no_usable_footprint` with `d3_eligible`
NULL — the same status a site with missing or invalid Maus geometry
receives. The two causes are not separable from `trajectory_status`
alone; a downstream reader must read
`footprint_support.parquet["support_not_computed_reason"]`.

**L9 — region-boundary slivers.** Pairwise interior intersections
between the nine RDC polygons are 1–429 m² digitising slivers, typed
MultiPolygon/GeometryCollection. The loader's overlap check refuses
only `Polygon` intersections, so they pass; the frozen protocol's
`boundary_tie` procedure resolves ambiguous points. Tightening the
check would need a protocol decision.

### Licence and export

**L10 — closed by `export-release`.** The stated gap was the ABSENCE of
a standalone, refusal-tested export command; `export-release`
(`cli.py`, `tests/test_cli_export_release.py`) now exists, is the sole
caller of `export_gate.export_public`, and has its row-gate refusal
pinned by `test_export_release_refuses_restricted_rows`. This is NOT
Batch G closure: ROADMAP row 5's product releases (trajectory packages)
stay gated on accepted Tier 1, and no wording here implies otherwise —
`export-release`'s registry (`release.PACKAGES`) carries exactly one
package (`footprint-areas`) today, and a register/trajectory package is
added only when a release of it is actually decided.

**L11 — coordinate-token half closed, non-null-licence half RE-SCOPED.**
Batch B finding 7. The coordinate-token half is closed:
`export_gate.COORDINATE_COLUMN_NAMES` now covers `lon`/`lat`/
`longitude`/`latitude` by exact name match, so `REGISTER_SCHEMA`'s
`lon`/`lat` columns are dropped at the boundary. The non-null-licence
half — design doc §4's "licence fields non-null on every row" — is
RE-SCOPED, not closed: `export-release` attaches `redistribute_public`
from `licence.SOURCES[spec.source_id]` per package, and `export_public`'s
existing row gate fail-closes on an absent, null, or non-bool value, so
the criterion is enforced at the boundary FOR EVERY PACKAGE THIS PROJECT
EXPORTS. The Tier 0 register itself is never exported — no register
release package exists, and a register row must refuse under the row
gate regardless — so the design §4 criterion AS WRITTEN AGAINST REGISTER
ROWS is never exercised by any test or live export. That residue is
recorded here explicitly, not implied closed.

**L12 — MINEDEX redistribution is closed.** D7 adjudicated a licence
conflict with a contrary notice; `minedex_redistribution_allowed` is
False. This is the fail-closed gate operating as designed, not a
failure, and it holds the repository private until a public-safe
tenements-plus-Maus Tier 0 release candidate exists and is audited
(D10 condition 2).

### Data and provenance

**L13 — snapshot date lags extract date.** DASC bundles fetched
2026-08-16 carry `extract_date: 2026-08-14`. Both dates are recorded in
the snapshot metadata; every Tier 0 count describes the 2026-08-14
extract.

**L14 — current-owner semantics rest on an extract property.** D8's
`owners_at_snapshot` filter has had no bite because every
`ProjectsOwners` row in the real extract has a blank `EndDate` (8,376
current + 0 ended). The property is now disclosed at fetch time and in
the register manifest, but the filter has never been exercised against
a mixed extract.

**L15 — asset metadata absent across the DEA catalogue.** Of 448,396
assets, `file:size` is missing on all of them and internal block size
on all of them; data type is missing on 103,476. Consequences: a
declared `compression_ratio` of 0.6 stands in for observed compressed
sizes in the volume estimate, and the expected range-request count is
reported `null` rather than assumed. Block layout was instead verified
directly on sampled assets (3,200×3,200 COGs, 800×800 deflate blocks).

**L17 — a "per-site" trajectory is a shared footprint mean.** Of the
10,372 sites that would be eligible at the forced-144 threshold, 10,185
(98.2%) sit on a Maus footprint shared with at least one other MINEDEX
site; the mean is 10.5 sites per footprint and one footprint carries
324. Sites sharing a footprint have byte-identical trajectories by
construction — the value is a function of `maus_id`, not `site_id`. Any
per-site presentation must say so. Measured 2026-08-25;
`docs/reviews/2026-08-25-remainder-critical-review.md` §2.3.

**L16 — tenement counts and coverage are three-way disclosed, not
flagged.** `n_tenements_intersecting` is nullable: 49,811 computed
(6,114 of them genuine zeros) + 353 not computed = 50,164. Coverage
counts reconcile the same way. A zero and a not-computed are never
conflated, but a reader who treats the column as a plain integer will
misread the nulls.

## 3. Open items

| ID | Item | Blocks |
|---|---|---|
| ~~O1~~ | ~~Outside-RDC fraction against `n_for_ceiling`~~ | **Closed 2026-08-25: 20/1,252 = 1.60%** |
| ~~O2~~ | ~~Batch E entry under the L4 disclosure needs an owner decision~~ | **Closed 2026-08-25: `decisions/2026-08-25-batch-e-forced-threshold-entry.md`** |
| O3 | Huntly validation verdict does not exist | Statewide extraction (`require_huntly_gate`) |
| O4 | Tolerance-gated concordance not evaluated | Nothing; deferred by choice |
| ~~O5~~ | ~~D5 Pages gate is pre-registered as recordable-failed~~ | **Closed 2026-08-25: `decisions/2026-08-25-public-web-page-descope.md`** |
| ~~O6~~ | ~~Shared-footprint product framing (L17) undecided~~ | **Closed 2026-08-25: `decisions/2026-08-25-tier1-product-framing.md`** |
| ~~O7~~ | ~~No SILO account or snapshot exists on either data root~~ | **Closed 2026-08-26: the gridded product is anonymous (no account exists on this route); `decisions/2026-08-26-silo-gridded-feed.md`** |
| ~~O8~~ | ~~Why the eligibility replay buckets 933 never-judged sites differently from the register build~~ | **Closed 2026-08-25: replay now calls the production function (`tests/test_diag_replay_parity.py`)** |

**O8 — closed 2026-08-25.** Replaying the eligibility join against
`curated/crosswalk/2026-08-16` had reproduced the judged population
exactly (10,910) but shifted 933 sites between `no_usable_footprint`
(31,766 replayed vs 30,833 recorded) and `crosswalk_not_high_confidence`
(7,488 vs 8,421). **Narrowed 2026-08-25:** the register run manifest's
recorded crosswalk digest (`10e1bfe0…`) matches
`curated/crosswalk/2026-08-16/crosswalk.parquet` on disk exactly, so
"built from a differently dated crosswalk" was refuted; the divergence
was the replay's own hand-rolled join, whose `judged` flag required
`maus_id`/support/confidence together and left everything else to fall
through to `no_usable_footprint` on a missing support value regardless of
confidence. Production's rule 1 (`no_usable_footprint`) is gated on
high confidence first — a low-confidence match whose `maus_id` carries no
computed support is `crosswalk_not_high_confidence` (rule 2), never
`no_usable_footprint`, because rule 2 does not consult support at all.
The 933-site shift is exactly the low-confidence, no-support population
the replay misrouted.
`scripts/diag_batch_e_readiness.py`'s `replay_eligibility` now calls
`register.assign_trajectory_eligibility` directly for every
`trajectory_status` bucket and only appends `maus_id`/`region` as
post-hoc lookups that cannot change a status; the reimplemented join is
retired.
`tests/test_diag_replay_parity.py::test_replay_counts_equal_production_counts`
pins replay/production count parity on a fixture built to include that
exact divergence class (`s3`), and
`test_replay_frame_carries_the_diagnostic_columns` pins that the replay
frame still carries the columns the diagnostics read. Full prior
statement in `docs/reviews/2026-08-25-batch-e-findings.md`.

**O1 — closed.** Read from the 2026-08-23 `footprint_support.parquet`:
20 of 1,252 Tier-1 footprints with usable Maus geometry are outside
every RDC polygon, **1.60%** against the 5% ceiling, so the ceiling has
headroom. The retracted 1.1% figure used 1,753 (all Maus WA footprints)
as its denominator; 1,252 is the correct one.

**O3.** The gate-as-drafted deadlock (a 9-pixel plot mean against a
411,895-pixel footprint mean at 1e-6, not passable at any parameter
setting) is resolved by amendment A7
(`decisions/2026-08-25-e5-engine-parity-rescope.md`): E5 now compares
the monitor's zonal engine against the jarrah pilot cube. O3 stays open
because the verdict artefact under `curated/huntly-validation/<date>/`
does not exist yet; it remains the sole unlock for statewide
extraction. Evidence in
`docs/reviews/2026-08-25-remainder-critical-review.md` §2.2.

**O2 — closed.** Owner decision 2026-08-25
(`decisions/2026-08-25-batch-e-forced-threshold-entry.md`): Batch E
operates under the forced-144 threshold with the Spearman failures
labelled, via the Task 0 forced-threshold eligibility path.
`criteria_passed=false` stands in the manifest; every row carries
`d3_forced_threshold=true` and the L4 disclosure travels downstream.

**O6 — closed.** Owner decision 2026-08-25
(`decisions/2026-08-25-tier1-product-framing.md`): Tier 1 stays
site-keyed with a mandatory `shared_footprint_site_count` field in the
Batch E partition schema; every rendered surface must state the sharing
when the count exceeds 1. Private-only: no non-MINEDEX public lane is
built, and the D5 Pages gate records failed exactly as pre-registered
(O5 unchanged).

**O3.** D13 E4 acceptance: `extract-trajectories` refuses statewide
mode until a verified passing Huntly verdict exists under
`curated/huntly-validation/<date>/`. The design's §10 rule is the same
one — the zonal engine reproduces the known Huntly trajectories within
declared tolerance before it touches statewide data. E1–E3 have landed;
E4/E5 is a draft plan.

**O5 — closed.** Owner decision 2026-08-25
(`decisions/2026-08-25-public-web-page-descope.md`): the public web
page is withdrawn from scope, so the D5 Pages gate is recorded as never
evaluated rather than failed. D13 §2's pre-registration (a failed gate
is never waived or bypassed) is untouched because no gate outcome is
being altered — the deliverable behind the gate no longer exists.
Batch G's remaining scope is versioned releases, the export-release
gate wiring, and the private QGIS project (amendment A8).
