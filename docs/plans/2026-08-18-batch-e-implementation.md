# Batch E Implementation Plan — Tier 1 Trajectory Extraction

**Status:** DRAFT
**Superseded (2026-08-21):** Tasks 18–20 are replaced by
`2026-08-21-batch-d-live-run-and-batch-e-e3.md` (E3) and a follow-on E4–E7
plan. CLI name per D13 §5 is `extract-trajectories`. Task 17 is done (90a4f50).
**Lineage:** Derived from `2026-08-15-wa-mine-rehab-monitor-implementation.md`
**Scope:** Windowed zonal reads over geomedian + FC percentiles, sensor/version/count columns, overlap-year sensitivity runs, and validation against the jarrah Huntly cube.

## Reuse Adjudication (from `dataplatform-reuse-assessment_2026-08-16.md`)
- **`core/zonal.py` (Memoised zonal stats): ADAPT.** We will adopt the adapter-neutral raster×polygon zonal stats with memoised polygon→pixel assignment. The empty case (polygons too small relative to 30m grid) will be caught and will interact with our D3 pixel-support threshold (yielding `insufficient_pixel_support`).
- **`core/qa.py` (Envelopes & Drift Checks): ADOPT.** We will port the envelope checking logic to validate spectral boundaries (e.g., NDVI ∈ [-1, 1], FC ∈ [0, 100]) directly after zonal reduction.

## Task Breakdown

### Task 17: core/zonal.py Port and D3 Integration
**Files:**
- Create: `src/wa_mine_monitor/core/zonal.py`
- Test: `tests/core/test_zonal.py`

**Description:**
Port the memoised polygon-to-pixel assignment logic from the dataplatform repo. Strip out any PG-catalog dependencies. Ensure it accepts stacked `(n, H, W)` arrays and omits zero-valid-pixel regions rather than fabricating data.
**Test loop:** Ensure byte-identical parity with a known fixture and explicit handling for zero-valid-pixel geometries.

### Task 18: build-trajectory-extract (CLI)
**Files:**
- Modify: `src/wa_mine_monitor/cli.py`
- Create: `src/wa_mine_monitor/trajectories.py`
- Test: `tests/test_trajectories.py`

**Description:**
Implement the `build-trajectory-extract` command.
- **Inputs:** `curated/d3-threshold/<date>`, `curated/d3-inputs/<date>`, and STAC fixtures.
- **Logic:** For each year, execute a windowed zonal read over the geomedian and FC percentile rasters using the memoised zonal assignment. Reduce pixels to site-level median/mean per the `D3_PROTOCOL`.
- **Output:** `curated/trajectories/<date>/trajectories.parquet` carrying `maus_id`, `year`, `sensor`, `version`, `pixel_count`, and the spectral bands.

### Task 19: Overlap-Year Sensitivity and Envelopes
**Files:**
- Modify: `src/wa_mine_monitor/trajectories.py`
- Modify: `src/wa_mine_monitor/qa.py`
- Test: `tests/test_qa.py`

**Description:**
Add `core/qa.py` spectral envelope assertions during the build. Ensure sensitivity tests run over the overlap years where sensor counts change.

### Task 20: Validation against Jarrah Huntly Cube
**Description:**
A pure validation task comparing the Tier 1 trajectories extracted from this generic WA-wide pipeline against the curated, bespoke jarrah Huntly cube extracted during the `jarrah-rehab-recovery` project. The metrics must reconcile exactly or within a documented, acceptable float tolerance.

