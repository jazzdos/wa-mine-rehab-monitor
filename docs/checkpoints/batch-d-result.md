# Batch D Result — D3 Protocol, Simulation, and Threshold

**Status:** CLOSED 2026-08-25 — 2026-08-23 rerun stands as the final Batch D record: forced-144 fallback, `criteria_passed=false`, per the pre-registered no-passing-support behaviour (design doc §8 D3: "If nothing through 144 passes, use 144 and label the failed criteria — never relaxed after seeing results"). Binding limitation and closure diagnostics below.

## Result parameters

- **Frozen protocol digest:** b2fa76f7d1dae1cfabad2f83828246e78024b1c246432eb6deeaa35598f90272 (freeze 2026-08-18)
- **Regions fetch date:** 2026-08-21 (DPIRD-020 SLIP REST GeoJSON, commit b5cc938)
- **Regions gpkg sha256:** f74cac479ed0075fb47f2a9c726eb6996d37bd1c8851a2a9c67901cbfb6d46b3 (input_manifest_digests.regions)
- **Candidate footprint counts per stratum:** 989 candidates; non-zero strata only — pilbara:other elongated/intermediate/compact 5/66/122, goldfields_esperance:other 4/111/478, other_wa:other 3/99/344. All 45 non-`other` strata: 0.
- **Selected footprint counts per stratum:** 180 selected — 30 in each of the 6 `*:other:{intermediate,compact}` strata; 0 elsewhere.
- **Footprint-years simulated:** 14,829
- **Footprint-years not computable:** 10,774 (20 footprints outside every RDC polygon excluded; 0 ambiguous boundary points; 0 spearman not computable; 0 ties)
- **n_star (threshold):** 144 px (= full support, fallback value; nominal_area 129,600 m²)
- **criteria_passed:** false
- **Per-criterion margins with counts:** 6 strata adequate / 48 inadequate. 8 failed criteria at full support: computable_fraction in goldfields_esperance:other:compact, other_wa:other:{compact,intermediate}, pilbara:other:{compact,intermediate}; nbr spearman_median in goldfields_esperance:other:intermediate and other_wa:other:compact; ndmi spearman_median in other_wa:other:compact (all dea_gm_ls8cls9c). Full cells in `curated/d3-threshold/2026-08-21/threshold.json` `per_support`.
- **Eligibility counts by trajectory_status:** register 50,164 rows — eligible 0; no_usable_footprint 30,833; threshold_not_computed 10,910; crosswalk_not_high_confidence 8,421; insufficient_pixel_support 0.

## Live run

Execution on luminosity (`/mnt/data`, per batch-c-result.md). Streaming reads with the bounded block cache (design decision 17).

**Disk requirement = cache bound (default 50 GB), transfer budget 597 GB–3.30 TB block-granular.**

Run completed 2026-08-22 on luminosity (tmux `wmm-d3`, `--read-workers 32`, commit b17d5ef, clean tree). build-d3-inputs exit=0 at 18:20 AWST; derive-d3-threshold and apply-d3-threshold ran at 18:29 (logs in `reports/{build-d3-inputs,derive-d3-threshold,apply-d3-threshold}-2026-08-21.log`). Outputs copied to lux at `~/Documents/wa-mine-monitor-data/curated/{d3-inputs,d3-threshold,register}/2026-08-21`.

## Defect found: commodity stratification

All 1,252 footprints in `footprint_support.parquet` carry `commodity_group = other`; every iron_ore/gold/bauxite_alumina/nickel/mineral_sands stratum has 0 footprints, which is why 48 strata are inadequate.

Root cause: the register's `commodity` column holds MINEDEX abbreviation codes (`Au` 4,460, `Au, Ag` 3,271, `Fe` 612, `Ni, Co` 234, `Ni` 148, `Bx` 49, … over the 11,001 high-confidence Tier 1 sites), but the frozen `commodity_token_rules` in `config/d3.yaml` match English substrings (`iron`, `gold`, `nickel`, `bauxite`, `mineral sands`, …). `d3_protocol.classify_commodity` therefore matches no rule and returns `other` for every site. `tests/test_d3_protocol.py::test_classify_commodity_first_rule_wins_and_other_is_catch_all` exercises English text (`"IRON ORE - Hematite"`), so the fixture suite did not catch it.

Consequence: the stratification is degenerate (region × shape only), and the criteria failures above are measured on mixed-commodity strata. `n_star = 144` and `criteria_passed = false` are not a usable D3 result.

Required fix is a protocol change (token rules over MINEDEX codes, e.g. `Fe`/`Mag` → iron_ore, `Au` → gold, `Bx`/`Al` → bauxite_alumina, `Ni` → nickel, `HM`/`Ilm`/`Rt`/`Zr` → mineral_sands), which changes the protocol digest and requires a new `freeze-d3-protocol` date and a full rerun of the chain. Per the `config/d3.yaml` freeze rule this is a design decision, not a code fix; record it in `docs/decisions/` before re-freezing. The Batch E E4/E5 gate stays closed.

## Rerun 2026-08-23 — commodity codes + valid-fraction protocol

Decision: `docs/decisions/2026-08-23-d3-commodity-codes-and-valid-fraction.md` (supersedes the 2026-08-18 freeze; `curated/d3-protocol/2026-08-18` moved to `curated/d3-protocol.superseded-2026-08-18` on luminosity).

- **Frozen protocol digest:** `10875cef8450c96a1bdc606aebc47d6a1621e3bf72344d45afe1537c431d06fa` (freeze 2026-08-23)
- **Commit:** 98488ae (`feat(d3): exact MINEDEX commodity codes and valid-member-fraction computability`, now on main)
- **Commodity classification of the footprints with computed support:** gold 726 / other 247 / iron_ore 136 / nickel 95 / mineral_sands 16 / bauxite_alumina 12; total 1,232; 17 adequate strata as expected (dry-run expectation was other 267 — actual 247; 20 footprints fall outside RDC regions and were dropped). **Correction 2026-08-25:** an earlier wording called these 1,232 the *candidate* counts. They are the classification of every footprint with a computed support value (1,252 − 20 outside-RDC). The **candidate** count — support ≥ 144 px and ≥ 1 epoch year — is **989**, as recorded by `n_candidate_footprints` in `footprint_support.parquet.run_manifest.json` and by the `candidate` column itself. Selected (413) is unaffected.
- **Footprints outside every RDC polygon:** 20 of 1,252 = **1.60%** against the 5% ceiling of decision `2026-08-21-d3-outside-rdc-exclusion.md`, read from the completed run as that decision requires (recorded 2026-08-25; the denominator is the Tier-1 population with usable Maus geometry, not the 1,753 total Maus footprints)
- **Selected footprint counts per stratum:** 413 total (gold 143 / other 131 / iron_ore 88 / nickel 51 / bauxite_alumina 0 / mineral_sands 0); dry-run expected 416
- **Footprint-years simulated:** 15,847 computable of 16,517 attempted (across the 17 adequate strata)
- **Footprint-years not computable:** 670 (4.1%)
- **computable_fraction per adequate stratum:** all 17 pass the ≥0.90 gate; range 0.9050 (other_wa:iron_ore:intermediate, 362/400) to 0.9750; most strata 0.94–0.975
- **n_star (threshold):** 144 px (forced-144 fallback — every candidate support size 9/16/25/36/49/64/100/144 px fails at least one criterion, so no support passes and 144 is disclosed, not selected)
- **criteria_passed:** false
- **Per-criterion margins with counts:** across all 8 supports, 2,584 criterion cells: computable_fraction 136/136 pass; p90_abs_error 1,100 pass / 124 fail; spearman_median 558 pass / 666 fail. At n_star=144 px, 25 cells still fail — all `spearman_median` (< 0.95), concentrated in `dea_gm_ls8cls9c` NBR/NDMI (plus a few `dea_gm_ls7e` and one `dea_fc_pc` NPV cell). Worst margins: goldfields_esperance:nickel:compact ls8c/ls9c NDMI 0.522, other_wa:gold:intermediate NDMI 0.528, NBR values 0.57–0.59 in the same strata; near-misses up to 0.9499. Geomedian SWIR-index rank stability at 144 px is the binding failure, not computability or absolute error.
- **Eligibility counts by trajectory_status:** 50,164 rows — eligible 0, insufficient_pixel_support 0, threshold_not_computed 10,910, no_usable_footprint 30,833, crosswalk_not_high_confidence 8,421 (`curated/register/2026-08-23/register.parquet` on luminosity)
- **Run timing:** freeze 2026-08-24 01:27; first build-d3-inputs attempt failed at 01:27 (stale `register/2026-08-21` moved aside), relaunched 01:28; build done 13:04, derive done 13:16, apply done 13:16 (log timestamps +10:00; `--read-workers 32`, tmux `wmm-d3`, logs `/mnt/data/wa-mine-monitor/reports/{freeze-d3-protocol,build-d3-inputs,derive-d3-threshold,apply-d3-threshold,d3-chain}-2026-08-23.log`)
- **Copied to lux:** 2026-08-24, to `~/data/wa-mine-monitor/curated/{d3-protocol,d3-threshold,register,d3-inputs}/2026-08-23` (d3-inputs without `support_inputs.parquet`, which stays on luminosity)

## Closure 2026-08-25 — forced-144 accepted as the pre-registered outcome

Owner decision (2026-08-25): accept the forced-144 result as final for
Batch D. No protocol amendment; the D13/design-doc freeze ("never relaxed
after seeing results") is honoured. The diagnostics below characterise the
failure and are recorded as a disclosed limitation, not as grounds for
change.

### Diagnostic 1 — larger supports (196/256/324/400 px)

Re-simulation of the 171 selected footprints in the 6 strata behind the
10 `dea_gm_ls8cls9c` NBR/NDMI cells failing spearman_median at 144 px
(script run 2026-08-24, log `reports/diag-supports-2026-08-24.log` on
luminosity; output `curated/d3-inputs/2026-08-23/diag_support_spearman_196_400.parquet`,
copied to lux; 118,600 rows, exit=0). Cells still below 0.95: 5/10 at
196 px, 2/10 at 256 px, 1/10 at 324 px, 1/10 at 400 px. The residual cell
(other_wa:gold:intermediate NDMI) rises only 0.923 → 0.945 across the
grid, so no plausible support extension passes; extension to ≥324 px
would also drop 3 of 17 adequate strata below min_footprints=10
(pilbara:gold compact/intermediate, other_wa:iron_ore:intermediate).

### Diagnostic 2 — flat-series mechanism

Per-site Spearman at 144 px correlates with full-footprint series spread
(r = 0.62 vs log range over the 281 failing-cell site×metric series).
Sites whose entire 12-year NBR/NDMI range is below the protocol's own
0.03 p90 error tolerance fail 96% of the time; range > 0.10 fails 26%.
The failure mechanism is spectrally flat sites whose year ranking is
sub-tolerance noise, not subsampling distorting real chronologies; it
spans gold, nickel, and iron_ore (excluding any one commodity does not
cure it). A site-level flatness exclusion was dry-run and rejected: at
the only internally justified floor (range > 1.0× tolerance) 5 cells
still fail; higher floors pass only non-monotonically and on cell
medians over 1–2 sites.

### Binding limitation (disclosed)

At the fallback threshold n_star = 144 px, spearman_median < 0.95 in 25
criterion cells (all spearman; concentrated in `dea_gm_ls8cls9c`
NBR/NDMI over gold/nickel/iron_ore strata). Geomedian SWIR-index rank
stability for spectrally flat footprints is not attainable at any tested
or diagnostically probed support (9–400 px). Any Batch E use of the
register must carry this disclosure; absolute-error and computability
criteria are unaffected (p90 and computable_fraction pass everywhere at
144 px).

### Flagged for later review (not adopted)

Tolerance-gated concordance: replace Spearman with a Kendall-style
statistic counting a year-pair discordant only when the full-value gap
exceeds the metric's p90 tolerance (anchored at 1×, no free constant).
Targets the flat-series mechanism directly and uses all sites. Not
evaluable from saved outputs (per-replicate reduced series were not
persisted); validation needs a ~2 h re-simulation of the failing-cell
sites at 144 px, and adoption would be a diagnostic-informed D13
amendment plus re-freeze and full rerun. Deliberately deferred.

Batch E E4/E5 gate: `criteria_passed=false` stands, so the gate does not
reopen automatically. Proceeding to Batch E now requires its own owner
decision to operate under the disclosed limitation above (forced-144
threshold, spearman failures labelled), recorded in `docs/decisions/`
before E4/E5 work starts.
