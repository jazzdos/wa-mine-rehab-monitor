# Batch D Result — D3 Protocol, Simulation, and Threshold

**Status:** Live run COMPLETE (2026-08-21) — `criteria_passed: false`, forced
`n_star = 144`; see "Findings requiring a decision" before opening Batch E.

## Result parameters

- **Frozen protocol digest:** `b2fa76f7d1dae1cfabad2f83828246e78024b1c246432eb6deeaa35598f90272`
  (`curated/d3-protocol/2026-08-18`)
- **Regions fetch date:** 2026-08-21 (`raw/wa_rdc_regions/2026-08-21/regions.geojson`,
  SLIP Boundaries MapServer layer 25, CC-BY-4.0)
- **Regions geojson sha256:** `3ba7bbb40ab3b0666a0cd7c38ba571924f09c7167cfa9fc654a9c53ba734d3cd`
  (the fetch writes GeoJSON, not gpkg)
- **Tier-1 footprints:** 1,252; 20 excluded as outside every RDC polygon
  (Perth metro; decision 2026-08-21, 1.6% against the 5% ceiling); 0 ambiguous
  boundary points; 0 commodity ties.
- **Candidate footprints (≥144 px):** 989. **Selected:** 180.
- **Candidate / adequate / selected counts per stratum** (all strata are
  `commodity_group = other`; every iron_ore/gold/bauxite_alumina/nickel/
  mineral_sands stratum has 0 footprints — see findings):

  | region | shape | n_footprints | n_adequate | adequate | n_selected |
  |---|---|---|---|---|---|
  | pilbara | compact | 122 | 98 | yes | 30 |
  | pilbara | intermediate | 66 | 55 | yes | 30 |
  | pilbara | elongated | 5 | 4 | no | 0 |
  | goldfields_esperance | compact | 478 | 352 | yes | 30 |
  | goldfields_esperance | intermediate | 111 | 74 | yes | 30 |
  | goldfields_esperance | elongated | 4 | 4 | no | 0 |
  | other_wa | compact | 344 | 227 | yes | 30 |
  | other_wa | intermediate | 99 | 71 | yes | 30 |
  | other_wa | elongated | 3 | 2 | no | 0 |

  6 strata adequate, 48 inadequate (45 of the 48 have 0 footprints).
- **Footprint-years simulated:** 14,829 (Phase B, 180 footprints × full-support years)
- **Footprint-years not computable:** 10,774 (Phase A, of 989 candidates)
- **n_star (threshold):** 144 px (forced-144 fallback; nominal area 129,600 m²)
- **criteria_passed:** false
- **Per-criterion margins with counts** (`threshold.json.per_support`, 114
  cells per support = 6 strata × (4 collections × metrics × 2 criteria + 1)):

  | support_px | cells failed | computable_fraction | spearman_median | p90_abs_error | min Spearman median |
  |---|---|---|---|---|---|
  | 9 | 83 | 5 | 54 | 24 | 0.610 |
  | 16 | 57 | 5 | 51 | 1 | 0.725 |
  | 25 | 44 | 5 | 39 | 0 | 0.786 |
  | 36 | 36 | 5 | 31 | 0 | 0.835 |
  | 49 | 27 | 5 | 22 | 0 | 0.868 |
  | 64 | 20 | 5 | 15 | 0 | 0.896 |
  | 100 | 8 | 5 | 3 | 0 | 0.934 |
  | 144 | 5 | 5 | 0 | 0 | 0.962 |

  At 144 px every sampling-error criterion passes (Spearman median ≥ 0.95,
  NBR/NDMI P90 ≤ 0.03, FC P90 ≤ 5 pp). The only failures are
  `computable_site_year_fraction_min: 0.90` in 5 of 6 strata:
  goldfields_esperance/compact 0.781 (937/1200), goldfields_esperance/
  intermediate 0.909 (pass), other_wa/compact 0.748 (897/1200),
  other_wa/intermediate 0.816 (978/1198), pilbara/compact 0.892 (1070/1200),
  pilbara/intermediate 0.875. This criterion is a data-availability property
  (missing/invalid source-years), identical at every support level, so no
  support level can pass under the frozen protocol.
- **Eligibility counts by trajectory_status** (`curated/register/2026-08-21`,
  50,164 rows): `threshold_not_computed` 10,910, `insufficient_pixel_support`
  0, `eligible` 0, `no_usable_footprint` 30,833,
  `crosswalk_not_high_confidence` 8,421. With `criteria_passed = false` every
  judged site is `threshold_not_computed` and `d3_eligible = False`
  (`register.py` status rule 3); `d3_threshold_px` carries 144.

## Live run

Host luminosity (`jarrod@192.168.1.75`, 4 cores, 15 GB RAM, `/mnt/data`),
repo `~/wa-mine-rehab-monitor` at `b17d5ef` (`worktree-fix-dpird-020-repin`),
data root `/mnt/data/wa-mine-monitor` (708 MB after the run; final d3-inputs
= 221 MB, `support_inputs.parquet` 212 MB).

Chain and outcomes:

1. `fetch-region-boundaries --date 2026-08-21` — ok.
2. `freeze-d3-protocol` — frozen 2026-08-18 reused (immutable).
3. `build-d3-inputs --date 2026-08-21 --protocol-config config/d3.yaml`:
   - Attempt 1 (2026-08-21 22:40): OOM-killed after 91 min, anon RSS 10.5 GB,
     exit 137, nothing written. Cause: `CPL_VSIL_CURL_CACHE_SIZE` is a RAM
     cache, not disk (Batch D plan decision 17 amended); 50 GB was wrong.
   - Attempt 2 (2026-08-22 00:11): 1 GB caches, serial reads. RSS plateau
     3.0 GB, ~3 MB/s (reads are round-trip-latency bound at ~0.25 s/asset;
     GDAL env tuning made it slower). Projected 1.5–2 days; killed before
     any output in favour of read concurrency.
   - Attempt 3 (11:06, `--read-workers 8`, commit `b17d5ef`): 6 MB/s at
     2 min; superseded at 13:27 by an operator relaunch with
     `--read-workers 32` (same env), which completed 18:20, exit 0 — about
     4.9 h. Env: `AWS_NO_SIGN_REQUEST=YES AWS_REGION=ap-southeast-2
     GDAL_CACHEMAX=1024 CPL_VSIL_CURL_CACHE_SIZE=1073741824
     GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR GDAL_HTTP_MAX_RETRY=5
     GDAL_HTTP_RETRY_DELAY=5`. Outputs are byte-identical across worker
     counts by construction (serial-order result consumption; tested 1 vs 4).
   - Disk guard (`~/wmm_guard.sh`, tmux `wmm-guard`): kill at >1 TB data root
     or <200 GB free; never triggered (peak data root well under 1 GB —
     block-granular reads do not persist to disk).
4. `derive-d3-threshold --date 2026-08-21` — `criteria_passed: false`,
   `n_star: 144`.
5. `apply-d3-threshold --date 2026-08-21` — register written, 0 eligible.

Outputs rsynced to the Mac: `~/data/wa-mine-monitor/curated/{d3-inputs,
d3-threshold,register}/2026-08-21` and `~/data/wa-mine-monitor/reports/`.
Logs: `reports/build-d3-inputs-2026-08-21.log`,
`derive-d3-threshold-2026-08-21.log`, `apply-d3-threshold-2026-08-21.log`.

## Findings requiring a decision

1. **Commodity stratification is empty.** MINEDEX `commodity` holds
   abbreviation codes (`Au` 4,460 Tier-1 sites, `Au, Ag` 3,271, `Fe` 612,
   `Ni, Co` 234, `Ni` 148, `Bx` 49, `HM, Ilm, Zrn` 32, …). The frozen
   `commodity_token_rules` match words (`iron`, `gold`, `nickel`, `bauxite`,
   `ilmenite`, …) by case-insensitive substring, so `classify_commodity`
   returned `other` for all 1,252 footprints. The protocol was applied
   faithfully; the intended iron_ore/gold/nickel/bauxite_alumina/
   mineral_sands strata (design decisions 9/10) never populated. A fix
   changes frozen protocol content (token rules are inside the digest):
   freeze a new protocol version with a code-based rule set (e.g. split on
   `, ` and match whole codes `Fe`/`Mag` → iron_ore, `Au` → gold, `Ni` →
   nickel, `Bx`/`Al` → bauxite_alumina, `HM`/`Ilm`/`Zrn`/`Rt`/`Mnz`/`Gnt` →
   mineral_sands), then re-run steps 3–5 (~5 h at 32 workers). Stratum
   adequacy will change: gold-dominated strata will be large, several others
   thin.
2. **`computable_site_year_fraction_min = 0.90` cannot pass at any support.**
   Observed fractions 0.75–0.91 across adequate strata. Either the criterion
   is relaxed in the new freeze, or the forced-144 fallback is accepted as
   the D3 outcome and Batch E proceeds with `threshold_not_computed` sites
   handled explicitly (D13 says extraction input = `eligible` rows, which is
   currently empty).

Until one of these is decided, the Batch E gate stays closed: there are no
`eligible` rows to extract.
