# Batch C result — DEA catalogue, epoch coverage, volume re-derivation

Status: LIVE RUN COMPLETE (2026-08-16). The fixture acceptance suite passes
(`tests/test_batch_c_acceptance.py`); the figures below are from the live
capture chain run on the real data root.

Artefact dates: the DEA snapshot, footprint areas, and volume report are
dated `2026-08-16` (the fetch day). The enriched register is dated
`2026-08-17` because `curated/register/2026-08-16/` already holds the
accepted Batch B register (its source), and `build-dea-coverage` refuses to
overwrite an existing dated output — the date is the artefact's version
label, chosen explicitly, mirroring the convention in `test_cli.py` (source
register and enriched register carry distinct dates).

## Live run record

- Fetch date (`--date` of `fetch-dea-catalogue`): **2026-08-16**. Snapshot
  at `raw/dea_stac/2026-08-16/`, 180 files, one run manifest.
- Collection extent dates (temporal extent read from each captured
  `collection.json` — NOT the fetch date):
  - `ga_ls_fc_pc_cyear_3` (dea_fc_pc): 1987-01-01 → 2025-01-01
  - `ga_ls5t_gm_cyear_3` (dea_gm_ls5t): 1986-01-01 → 2011-01-01
  - `ga_ls7e_gm_cyear_3` (dea_gm_ls7e): 1999-01-01 → 2021-01-01
  - `ga_ls8cls9c_gm_cyear_3` (dea_gm_ls8cls9c): 2013-01-01 → 2025-01-01
- Product version (`odc:dataset_version` from captured items): **4.0.0**
  uniformly — every captured item in all four collections carries 4.0.0
  (13,748 + 8,052 + 8,105 + 4,587 items checked).
- Per-collection live item counts (all non-zero):
  - dea_fc_pc: **13,748** (69 pages; `reported_item_count` 13,748, agrees)
  - dea_gm_ls5t: **8,052** (41 pages; reported 8,052, agrees)
  - dea_gm_ls7e: **8,105** (41 pages; reported 8,105, agrees)
  - dea_gm_ls8cls9c: **4,587** (23 pages; reported 4,587, agrees)
- Snapshot verify counts: **ok 180 / bad 0 / missing 0**.
- Coverage disclosures (identical across all four collections, reconciled):
  `n_sites_coverage_computed` 49,811 + `n_sites_coverage_not_computed` 353
  = 50,164 register rows (rows before = rows after = 50,164);
  `n_sites_coverage_zero` 7 (within computed); duplicate items refused: 0
  in every collection. Distinct items indexed per collection match the
  catalogue counts above.
- Footprint scalars (`curated/maus_footprint_areas/2026-08-16/`):
  **1,753 footprints**; area min 308.7 m², median 308,745.8 m² (≈0.31 km²),
  max 370,703,627 m² (≈370.7 km²). Of the 11,001 eligible sites in the
  estimate, **2,742 size at the declared floor window** (67 px) and
  **8,259 size from their own footprint**.
- Volume estimate (`reports/dea-volume/2026-08-16/estimate.json`):
  - Eligible sites: **11,001** (37,752 register sites unmatched to a
    high-confidence crosswalk footprint; 0 eligible sites had coverage
    not computed).
  - Distinct footprints: **1,252**.
  - Distinct tiles: **117**. Tile-years per collection: dea_fc_pc 4,563;
    dea_gm_ls5t 2,693; dea_gm_ls7e 2,691; dea_gm_ls8cls9c 1,521.
  - Windowed-read bytes (per collection): dea_fc_pc 84.6 GB;
    dea_gm_ls5t 199.9 GB; dea_gm_ls7e 199.7 GB; dea_gm_ls8cls9c 112.9 GB.
    Total **597,113,825,460 bytes ≈ 597.1 GB**.
  - Upper-bound bytes (per collection, whole tile-years, uncompressed):
    dea_fc_pc 467.3 GB; dea_gm_ls5t 1,103.1 GB; dea_gm_ls7e 1,102.2 GB;
    dea_gm_ls8cls9c 623.0 GB. Total **3,295,539,200,000 bytes ≈ 3.30 TB**.
  - Scratch space: **3.30 TB** (worst case, whole tiles staged — equals
    the upper bound by the declared formula).
  - Expected range requests: **null** — block size (`block_width_px`/
    `block_height_px`) is absent for all 448,396 assets, so the range-
    request formula cannot evaluate; disclosed rather than assumed.
- Asset-metadata completeness (448,396 assets across four collections):
  `file:size` missing for **448,396/448,396** (none observed); block size
  missing for **448,396/448,396**; data type missing for **103,476/448,396**
  (observed on the rest — `bytes_per_pixel` is `observed`: 1 byte/px for
  dea_fc_pc, 4 bytes/px for the three geomedian collections;
  `tile_pixels_per_side` 3,200 is `observed` in all four). Declared
  assumptions used: **compression_ratio 0.6** on the windowed-read
  estimate (because `file:size` is absent everywhere, compressed sizes
  cannot be observed); window policy floor 67 px / 300 m buffer /
  30 m pixels / 1 px alignment pad as declared in `WindowPolicy`.
- Provisional figures replaced (comparison, recorded in the estimate as
  `provisional_figures_comparison_only`):
  - Tiles: 367 provisional → **117 measured** distinct tiles
    (11,468 tile-years across collections).
  - Windowed estimate: 350 GB provisional → **597.1 GB measured**.
  - Upper bound: 2.3 TB provisional → **3.30 TB measured**.

## Gates

- The enriched register remains INTERNAL (D7 closed; manifest records
  `minedex_public_export_blocked: true`). Live-run manifest:
  `curated/register/2026-08-17/register.parquet.run_manifest.json`.
- `derive-dea-volume`'s Maus-digest equality gate passed live: the
  crosswalk manifest and the footprint-areas manifest record the same
  Maus GeoPackage sha256 (both from `raw/maus_v2/2026-08-16/`).
- Execution-host decision from measured scratch-space need: **luminosity,
  scratch on `/mnt/data`**, verified live this session over Tailscale —
  1.7 TB free on the 7.3 TB data disk vs the 597.1 GB windowed-read
  budget. The MacBook Air is excluded by measurement (35 GiB free).
  Whole-tile staging (3.30 TB) is excluded as an execution mode on every
  available machine (1.7 TB < 3.30 TB); extraction runs as windowed
  streaming reads. Fallback if `/mnt/data` free space shrinks below the
  budget before extraction runs: ~1 TB can be partitioned on the same
  8 TB HDD (offered by Jarrod; not needed at the current 1.7 TB free).
  Because block-size metadata is absent (range-request
  count null), the extraction plan must verify actual COG block layout
  on a sampled asset before committing to the streaming budget.
- COG block layout VERIFIED live (same day, from luminosity, one sampled
  asset per collection read via rasterio 1.5.1 over HTTPS): all four
  collections serve 3,200×3,200 tiled COGs with **800×800 internal
  blocks**, deflate compression, overview levels 2/4/8/16/32; dtypes
  uint8 (dea_fc_pc) and float32 (geomedians), consistent with the
  estimate's observed `bytes_per_pixel`. Consequence for the streaming
  plan: reads are block-granular — a floor window (67 px) intersects
  1–4 blocks, so each site-year-asset read transfers whole 800×800
  blocks (0.64–2.56 MB uncompressed each, less after deflate), not the
  window's naive byte count. Effective transfer sits between the 597 GB
  windowed estimate and the 3.30 TB whole-tile bound; the extraction
  plan (Batch E) must budget per-block, not per-window.
