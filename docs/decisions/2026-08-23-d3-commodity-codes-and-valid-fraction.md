# D3: MINEDEX commodity codes and valid-fraction computability (2026-08-23)

**Trigger.** Batch D live run (luminosity, 2026-08-22, commit b17d5ef,
`docs/checkpoints/batch-d-result.md`) returned `criteria_passed=false`,
`n_star=144`, 0 eligible sites, under the 2026-08-18 protocol freeze
(digest b2fa76f7d1dae1cfabad2f83828246e78024b1c246432eb6deeaa35598f90272).
Two protocol defects were observed, both in stratification and
computability outputs, neither in an accuracy result:

1. `config/d3.yaml` `commodity_token_rules` matched English substrings
   (`iron`, `gold`, `bauxite`, `nickel`, `mineral sands`, ...) but the
   register `commodity` column holds verbatim MINEDEX `Commodities`
   codes (`Au`, `Au, Ag`, `Fe`, `Ni, Co`, `Bx`, `HM`, ...).
   `d3_protocol.classify_commodity` returned `other` for all 1,252
   footprints; 45 of 54 strata had 0 footprints and 48 were inadequate.
   The unit test used English text, so the fixture suite stayed green.
2. Batch D plan decision 11 (all member pixels valid in FC and a
   geomedian) is unattainable after 2012. Diagnostics
   (`scripts/diag_d3_computability.py`, `scripts/diag_d3_coverage.py`,
   luminosity `/mnt/data/wa-mine-monitor/reports/diag-*.log`) show
   catalogue coverage 100% for 1987-2025, geomedian sources 99-100%
   computable, the zero-denominator rule never firing, and every failure
   caused by `dea_fc_pc` nodata (all three FC bands 255 together: water,
   pit lakes, shadow). Where FC fails, the invalid share of member
   pixels is median 0.8%, p90 5.4%, clustered interior blobs.
   `computable_fraction` was 0.75-0.89 in 5 of 6 strata against the
   0.90 floor, identical at every support level, so it blocks the
   threshold search regardless of accuracy.

**What is and is not changed.** D13
(`docs/decisions/2026-08-16-d13-batches-c-g-detailing.md` Batch D) fixes
the six commodity groups and the >= 0.90 computable site-year fraction;
it does not specify the token vocabulary or the all-pixels-valid rule.
Both are Batch D plan decisions
(`docs/plans/2026-08-16-batch-d-implementation.md` decisions 10-11).
The accuracy criteria (NBR/NDMI P90 abs error <= 0.03, FC P90 <= 5 pp,
median Spearman >= 0.95, computable fraction >= 0.90), the support set,
regions, groups, shape classes, adequacy counts, selection, and
replicates are unchanged. This change follows observation of
stratification counts and computability fractions only; no P90 error,
Spearman, or threshold value informed it, and none may inform any
future protocol change.

**Options considered.** (a) Patch `classify_commodity` in code only and
keep the 2026-08-18 digest: rejected, the rules are protocol content and
the digest must change. (b) Keep all-pixels-valid and lower the 0.90
computable fraction: rejected, that criterion is D13-frozen. (c) Replace
the token vocabulary with exact MINEDEX codes and replace
all-pixels-valid with a valid-member fraction, computing values over
valid members: adopted.

**Decision.** (c), under a new single lineage dated 2026-08-23.

- `commodity_code_rules` replaces `commodity_token_rules`. Matching:
  split the raw `commodity` on `,`, strip, case-insensitive exact token
  match (no substring, so `fe` cannot hit `Fel`), first rule wins, a
  non-empty value matching nothing is `other`, null/blank is a refusal.
  Rule order and codes (Tier 1 vocabulary from
  `curated/register/2026-08-17` intersected with the high-confidence
  crosswalk): iron_ore = Fe, FeOre, Mag, Hem, Hem-MIO, Fe-DRI,
  Fe-Pellets, FeSpec, Fe2O3; bauxite_alumina = Bx, Al2O3Bayer, Alu, Al;
  nickel = Ni, MgsNi; mineral_sands = HM, Ilm, Zrn, Leu, Rt, Mnz, Grt,
  IlmRt-syn, Xen; gold = Au. Modal group per footprint and tie handling
  are unchanged.
- `adequacy.min_valid_member_fraction: 0.95`. A footprint-year-collection
  is computable iff `valid_support_px >= ceil(0.95 * full_support_px)`
  with validity from the existing `geomedian_valid_mask` /
  `fc_valid_mask`. A year is full-support computable iff FC computable
  and at least one geomedian collection computable (unchanged).
- Phase B computes the full value and every replicate value over valid
  members only; replicate draws sample from valid members;
  `valid_support_px` carries the valid count; `full_support_px` stays
  the geometric member count. Sub-full supports (<= 100) are always
  drawable (137 >= 100). The full-support row (144) is the reference
  itself: when `valid_support_px < 144` it is emitted with the sample
  equal to all valid members, so its errors are exactly zero and its
  Spearman series equals the full series; any other support above
  `valid_support_px` is a refusal. Footprints below 144 geometric
  members are still refused.

**Disclosed limitation (statistical).** The simulation now conditions on
the valid members: support `s` draws `s` valid pixels and is compared to
the mean over all valid pixels. `apply-d3-threshold` compares `n_star`
to the geometric `effective_pixel_support_px`, so a site with geometric
support `n_star` may have as few as `ceil(0.95 * n_star)` valid pixels
in a given year. The 0.95 floor bounds this discrepancy to 5% of
support, and FC nodata is spatially clustered, so reduced-support error
on such sites may be slightly understated relative to the simulation.
Batch E extraction must apply the same `min_valid_member_fraction` rule
per site-year-collection and record `valid_support_px`, so that every
trajectory value is computed over the same population the threshold
was derived on. This is recorded rather than corrected because the
alternative (per-year valid counts in eligibility) has no D13 basis.

**Supersession.** This decision supersedes the 2026-08-18 protocol
freeze. On luminosity `curated/d3-protocol/2026-08-18` is moved to
`curated/d3-protocol.superseded-2026-08-18` (kept, not deleted) and
`freeze-d3-protocol --date 2026-08-23` creates the only dated lineage.
The 2026-08-21 `d3-inputs`, `d3-threshold`, and `register` outputs stay
under their own date as the record of the failed run. Dry run of the new
rules against the 2026-08-21 `footprint_support` (same candidates, new
labels): gold 726 / other 267 / iron_ore 136 / nickel 95 /
mineral_sands 16 / bauxite_alumina 12 footprints; 17 adequate strata,
416 selected footprints (was 6 / 180). Bauxite_alumina and mineral_sands
never reach 10 per stratum; this is disclosed in `stratum_summary`, not
blocking.

**Consequence for D3.** Strata are now commodity-stratified as D13
intended. `n_star` and `criteria_passed` from the 2026-08-21 run are not
a usable D3 result and are retained only as the record that triggered
this decision. The Batch E E4/E5 gate stays closed until the 2026-08-23
rerun reports `criteria_passed=true`.
