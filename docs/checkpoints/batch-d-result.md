# Batch D Result — D3 Protocol, Simulation, and Threshold

**Status:** 2026-08-21 live run FAILED (commodity rules never matched); protocol re-frozen 2026-08-23 per decision doc — rerun _pending_

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
- **Candidate footprint counts per stratum:** gold 726 / other 247 / iron_ore 136 / nickel 95 / mineral_sands 16 / bauxite_alumina 12; total 1,232; 17 adequate strata as expected (dry-run expectation was other 267 — actual 247; 20 footprints fall outside RDC regions and were dropped)
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

Batch E E4/E5 gate: reopen only if `criteria_passed=true`.
