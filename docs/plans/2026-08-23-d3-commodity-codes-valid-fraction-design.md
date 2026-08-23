# D3 protocol revision: MINEDEX commodity codes + valid-fraction computability — design

**Status:** approved by Jarrod 2026-08-23 (brainstorming session). Supersedes the
2026-08-18 protocol freeze once the decision doc is committed.

## Why

Batch D live run (luminosity, 2026-08-22, commit b17d5ef; `docs/checkpoints/batch-d-result.md`)
returned `criteria_passed=false`, `n_star=144`, 0 eligible sites. Two protocol
defects, neither an accuracy result:

1. **Commodity rules never match.** `config/d3.yaml` `commodity_token_rules` are
   English substrings (`iron`, `gold`, …); the register `commodity` column is
   verbatim MINEDEX `Commodities` codes (`Au`, `Au, Ag`, `Fe`, `Ni, Co`, `Bx`, `HM` …).
   `d3_protocol.classify_commodity` returned `other` for all 1,252 footprints;
   45 of 54 strata had 0 footprints, 48 were inadequate. The unit test used
   English text, so the fixture suite stayed green.
2. **Decision 11 all-pixels-valid computability is unattainable post-2012.**
   `d3_inputs.year_computable` requires every member pixel valid in FC and a
   geomedian. Diagnostics (`scripts/diag_d3_computability.py`,
   `scripts/diag_d3_coverage.py`, logs in luminosity
   `/mnt/data/wa-mine-monitor/reports/diag-*.log`): catalogue coverage 100%
   for 1987–2025; geomedian sources 99–100% computable; zero-denominator rule
   never fires; failures are `dea_fc_pc` nodata (all three FC bands 255
   together — water/pit lakes/shadow). When FC fails the invalid share of
   member pixels is median 0.8%, p90 5.4%, interior clustered blobs, 1.4%
   touch the footprint bbox edge. `computable_fraction` was 0.75–0.89 in 5/6
   strata against the 0.90 floor, identical at every support, so it blocks
   the threshold search regardless of accuracy.

D13 (`docs/decisions/2026-08-16-d13-batches-c-g-detailing.md` §Batch D) fixes
the six groups and the ≥0.90 computable fraction; it does not specify the
token vocabulary or all-pixels-valid. Both are Batch D plan decisions
(`docs/plans/2026-08-16-batch-d-implementation.md` decisions 10–11). The
accuracy criteria (p90 errors, Spearman ≥0.95, fraction ≥0.90) are not
changed. The change is made after observing computability and
stratification results, never accuracy results; the decision doc says so.

## Change 1 — `commodity_code_rules`

Replace `commodity_token_rules` with `commodity_code_rules` in `config/d3.yaml`.
Matching: split raw `commodity` on `,`, strip, case-insensitive **exact** token
match (no substring — `fe` would otherwise hit `Fel`), first rule wins, a
non-empty value matching nothing is `other`, null/blank is a refusal
(unchanged). Rule order and codes (Tier 1 vocabulary from
`curated/register/2026-08-17` ∩ high-confidence crosswalk):

| group | codes |
|---|---|
| iron_ore | Fe, FeOre, Mag, Hem, Hem-MIO, Fe-DRI, Fe-Pellets, FeSpec, Fe2O3 |
| bauxite_alumina | Bx, Al2O3Bayer, Alu, Al |
| nickel | Ni, MgsNi |
| mineral_sands | HM, Ilm, Zrn, Leu, Rt, Mnz, Grt, IlmRt-syn, Xen |
| gold | Au |

Modal-group-per-footprint and tie handling (`d3_inputs.assign_footprint_commodities`)
unchanged. Procedures text `commodity_rule` updated to describe token matching.

Dry run against the 2026-08-21 `footprint_support` (same candidates, new
labels): gold 726 / other 267 / iron_ore 136 / nickel 95 / mineral_sands 16 /
bauxite_alumina 12 footprints; **17 adequate strata, 416 selected
footprints** (was 6 / 180). Bauxite_alumina and mineral_sands never reach 10
per stratum — disclosed in `stratum_summary`, not blocking (criteria run over
adequate strata only).

## Change 2 — valid-fraction computability (replaces decision 11 wording)

New protocol key `adequacy.min_valid_member_fraction: 0.95`. A
footprint-year-collection is computable iff
`valid_support_px >= ceil(0.95 * full_support_px)` where valid = existing
`geomedian_valid_mask` / `fc_valid_mask`. A year is full-support computable
iff FC computable AND ≥1 geomedian collection computable (unchanged).

Phase B (`simulate_footprint_year`): full value and all replicate reduced
values are computed over the **valid members only**; replicate draws sample
from valid members; `valid_support_px` (already in `D3_SUPPORT_INPUTS_SCHEMA`)
carries the count; `full_support_px` stays the geometric member count. Sub-full
supports (≤100) are always drawable (137 ≥ 100); the full-support row (144)
is the reference itself and is emitted with zero error when
`valid_support_px < 144` (codex plan-attack finding 1). Disclosed limitation
(codex finding 2): `apply-d3-threshold` compares `n_star` to geometric
support while the simulation conditions on valid members; the 0.95 floor
bounds the gap to 5%, Batch E extraction must apply the same rule per
site-year, and this is recorded in the decision doc rather than corrected. Footprints below
144 geometric members are still refused (unchanged).

`_fraction_cell` in `d3_threshold` is unchanged in form; its inputs now
reflect the new rule.

## Process

1. Decision doc `docs/decisions/2026-08-23-d3-commodity-codes-and-valid-fraction.md`
   (required by the single-lineage rule in `freeze-d3-protocol`).
2. Code + tests (TDD), `config/d3.yaml` edit, procedures consistency check.
3. On luminosity: `mv curated/d3-protocol/2026-08-18 curated/d3-protocol.superseded-2026-08-18`
   (and likewise the 2026-08-21 `d3-inputs`, `d3-threshold`, `register` outputs
   are kept as-is under their own date — the new run uses `--date 2026-08-23`).
4. `freeze-d3-protocol --date 2026-08-23`, then `build-d3-inputs --read-workers 32`
   in tmux with the existing disk guard, followed by the derive/apply watcher.
   Expected ~16 h (2.3× the 180-footprint run).
5. Copy to lux only the small tables (`footprint_support`, `stratum_summary`,
   `support_spearman`, `d3-threshold`, `register`) — skip `support_inputs.parquet`
   (~0.5 GB) while on the hotspot.
6. Fill `docs/checkpoints/batch-d-result.md` §"Rerun 2026-08-23"; reopen the
   Batch E gate only if `criteria_passed=true`.

## Testing

- `classify_commodity`: real codes (`Au, Ag` → gold; `Fe, Mag` → iron_ore;
  `Fel` → other; `Ni, Cu, Co` → nickel; blank → refusal); rule-order test.
- `year_computable` / `simulate_footprint_year`: fixtures at 94% and 96% valid
  members; values computed over valid members only; `valid_support_px`
  recorded; replicate draws never include an invalid member.
- Protocol digest changes; `build-d3-inputs` refuses against the old freeze
  (existing gate test).
- Existing oracle + chain tests remain green.
