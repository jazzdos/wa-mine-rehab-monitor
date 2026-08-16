# D13 — Batches C–G detailing and reuse adjudication

Date: 2026-08-16  
Status: proposed director ruling for orchestrator review

## 1. Scope and governing constraints

This ruling executes D12 item 4. It does not amend D1–D12.

The private implementation sequence remains **C → D → E → F → G**. Batch H remains conditional under D4. The Tier 0 public-RC lane may run in parallel but independently gates the public repository flip. Batch G retains export, site, and D5 Pages work; it does not inherit the D2 public-repository checklist.

The fixed claim boundary applies to every batch: descriptive spectral monitoring only. Outputs must not state or imply rehabilitation dates, compliance findings, recovery or equivalence findings, or performance findings about named owners or operators. Spectral onsets remain detection years or intervals, with “cause not determined” until fire and climate context are displayed. The binding sources are `docs/plans/2026-08-15-wa-mine-rehab-monitor-design.md`, `docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md`, and `docs/decisions/2026-08-16-d9-d12-commit-remote-naming-sequencing.md`.

D7 is resolved fail-closed, not unresolved: MINEDEX redistribution remains prohibited. Internal MINEDEX-derived records may support processing, but no public repository, release payload, or generated site may contain MINEDEX-derived row-level records, coordinates, ownership fields, crosswalk membership, or raw licence evidence.

The accepted register semantics in `src/wa_mine_monitor/register.py` are binding for all new counts:

- Null means not computed.
- Zero means the computation ran and returned zero.
- Every nullable count receives a manifest disclosure reconciling computed, zero, and not-computed populations.
- D8’s `owners_at_snapshot` terminology remains; owners are never relabelled as operators.

All tasks below use fixture-first TDD. Tests may not make live network calls. Every operational command writes an immutable artifact and run-manifest sidecar, refuses overwrite, records input digests and source versions, and uses declared Arrow schemas. Each task runs its named tests followed by:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest -q
```

Dataplatform references below are paths relative to the dataplatform repository. The reuse assessment is `docs/research/dataplatform-reuse-assessment_2026-08-16.md` in its source repository.

## 2. Dependency and gate summary

| Work lane | Licence dependency | D3 dependency | Public-RC dependency |
|---|---|---|---|
| Batch C | DEA collection licences must remain exact-resource CC-BY-4.0; MINEDEX-derived coverage stays internal under D7 | None | None |
| Batch D | Maus-derived simulation artifacts are CC-BY-SA-4.0; MINEDEX-linked samples stay internal | Produces the binding D3 result | None |
| Batch E | DEA inputs are CC-BY-4.0; Maus geometry and derived trajectories require a separate CC-BY-SA-4.0 package; D7 blocks public release of MINEDEX-selected rows | Requires accepted D3 result | None for private extraction |
| Batch F | DBCA-060 mirror use is conditional on mirror provenance and licence-evidence adjudication; SILO credentials remain secret | Requires Batch E trajectories for the final context join | None for private processing |
| Batch G | Export gates must enforce D7 and the Maus ShareAlike package boundary | Requires accepted D3 and Tier 1 | Public-RC does not authorize Pages; D5 independently gates Pages |
| Tier 0 public-RC | D7 exclusion is mandatory; only licence-clean tenements and Maus fallback artifacts may ship | None | This lane is the public-flip gate |

No D12 sequencing constraint is unsatisfiable. D5 Pages publication cannot pass while the proposed public site contains MINEDEX-derived selection or row-level information. Batch G may finish its private implementation with the Pages gate recorded as failed; the failure must not be waived or treated as a Batch G failure to preserve sequence.

---

## 3. Batch C — DEA catalogue, epoch coverage, and volume re-derivation

### Batch ruling

**Decision: adapt and build.** Adapt the dataplatform HTTP primitives after closing their transport and rate-policy gaps; build the monitor-specific DEA catalogue, coverage, register enrichment, and volume estimator. Do not port a dataplatform schema, driver, or backfill framework.

### Reuse adjudication

- **`core/http.py` and `map_concurrent`: adapt.** Their bounded ordered execution, injectable sleep, 429 handling, and deterministic exception propagation are useful, but the source implementation does not retry connection/timeout exceptions, ignores HTTP-date `Retry-After`, and leaves aggregate concurrency limits to callers.
- **AdapterSpec/REGISTRY pattern: adapt only the declarative contract.** A frozen source specification is useful now that the monitor has more than five sources, but the dataplatform drivers are coupled to DuckLake, Polars, AWST, and the canonical observations table.
- **Dataplatform backfill driver: decline for Batch C.** STAC catalogue snapshots and coverage indexes are immutable batch artifacts, not replace-window observation loads.

### Ordered tasks

#### C1. Adapt the bounded HTTP client

**Files**

- Create `src/wa_mine_monitor/http.py`.
- Create `tests/test_http.py`.

**Interfaces**

- `HttpClient.get()`, `get_json()`, `get_text()`, and `get_bytes()`.
- `map_concurrent(fn, items, *, max_workers, tolerate_errors=False)`.
- `RetryPolicy` containing attempts, timeout, backoff cap, `Retry-After` cap, retryable exceptions, and source-level concurrency.

**Tests to add**

- Numeric and HTTP-date `Retry-After`.
- Retry of `requests.ConnectionError` and `requests.Timeout`.
- Immediate refusal for non-429 4xx responses.
- Exhausted-attempt error without URL query secrets.
- Ordered serial/parallel parity.
- Bounded worker count.
- Deterministic lowest-index exception.
- `max_workers=1` inline execution.
- A completeness-sensitive caller refusing `tolerate_errors=True`.

**Acceptance**

- STAC callers can inject sessions and sleep functions.
- Errors and manifests never contain credential-bearing query values.
- Each source declares aggregate concurrency and rate policy; `map_concurrent` does not silently invent one.

#### C2. Build the pinned DEA STAC catalogue

**Files**

- Create `src/wa_mine_monitor/sources/dea.py`.
- Create `src/wa_mine_monitor/source_catalogue.py`.
- Modify `src/wa_mine_monitor/licence.py`.
- Modify `src/wa_mine_monitor/cli.py`.
- Create `tests/sources/test_dea.py`.
- Create synthetic STAC fixtures under `tests/fixtures/dea/`.

**Interfaces and fields**

- Frozen `SourceSpec(source_id, collection_id, cadence, region_scope, licence_state, asset_roles)`.
- `DEA_COLLECTIONS` pins:
  - `ga_ls5t_gm_cyear_3`
  - `ga_ls7e_gm_cyear_3`
  - `ga_ls8cls9c_gm_cyear_3`
  - `ga_ls_fc_pc_cyear_3`
- `fetch-dea-catalogue --date YYYY-MM-DD`.
- Snapshot metadata records `collection_id`, STAC URL, reported item count, temporal extent, `license`, required assets, fetch date, and response digest.

**Tests to add**

- Exact verified collection names.
- Rejection of the known empty-stub naming pattern.
- Rejection of zero or null item counts.
- Rejection of a collection licence inconsistent with `licence.SOURCES`.
- Rejection of missing required assets or temporal extent.
- Pagination with duplicate item IDs.
- Snapshot overwrite refusal and manifest redaction.

**Acceptance**

- Every accepted collection has a positive unique item count.
- Collection JSON and paged item metadata are retained in an immutable snapshot.
- Duplicate item IDs fail reconciliation rather than inflating coverage.
- Pytest uses only committed synthetic fixtures.

#### C3. Build the DEA epoch-coverage index

**Files**

- Create `src/wa_mine_monitor/dea_coverage.py`.
- Create `tests/test_dea_coverage.py`.

**Interfaces and schema**

- `build_item_index(items)` produces collection, item ID, year, geometry, asset identity, product version, and tile identity.
- `count_site_epochs(register, item_index)` counts distinct years having at least one intersecting item.
- Add nullable `int64` register fields:
  - `n_dea_gm_ls5t_epochs`
  - `n_dea_gm_ls7e_epochs`
  - `n_dea_gm_ls8cls9c_epochs`
  - `n_dea_fc_pc_epochs`
- Add per-collection manifest disclosures:
  - `n_sites_coverage_computed`
  - `n_sites_coverage_zero`
  - `n_sites_coverage_not_computed`
  - `n_distinct_items`
  - `n_duplicate_items_refused`

**Tests to add**

- A coordinate-less row produces null for all four counts.
- A located row with no intersecting item produces a genuine zero.
- Multiple tiles in one year count as one epoch.
- Duplicate item IDs do not double-count.
- Overlapping sensor collections remain separate.
- Computed plus not-computed reconciles to register rows.
- Counts survive declared Arrow-schema write/read.

**Acceptance**

- The computation uses the internal MINEDEX point only for the Tier 0 coverage diagnostic; it does not define or substitute a Tier 1 footprint.
- The existing `n_tenements_intersecting` nullable semantics remain unchanged.
- Every count is traceable to a captured catalogue snapshot.

#### C4. Enrich the versioned register

**Files**

- Modify `src/wa_mine_monitor/register.py`.
- Modify `src/wa_mine_monitor/cli.py`.
- Modify `tests/test_register.py`.
- Modify `tests/test_cli.py`.

**Interface**

- Add `build-dea-coverage --date YYYY-MM-DD --catalogue-date YYYY-MM-DD`.
- Write a new `curated/register/<date>/register.parquet`; never mutate the accepted Batch B artifact.
- Manifest fields:
  - `source_register_manifest`
  - `source_catalogue_manifest`
  - `dea_coverage_disclosure`
  - `minedex_public_export_blocked`
  - `register_rows_before`
  - `register_rows_after`

**Tests to add**

- Refusal when the source register or catalogue manifest fails digest verification.
- Refusal on row loss, row gain, or row reordering.
- Preservation of existing register columns and nullable dtypes.
- D7 blocked state carried unchanged.
- Curated-output overwrite refusal.

**Acceptance**

- Register row identity and order remain byte-stable apart from the four appended fields and associated Parquet metadata.
- Before/after row totals are equal.
- The enriched register remains internal while D7 is closed.

#### C5. Re-derive the Tier 1 volume estimate

**Files**

- Create `src/wa_mine_monitor/dea_volume.py`.
- Modify `src/wa_mine_monitor/cli.py`.
- Create `tests/test_dea_volume.py`.

**Interface and output**

- Add `derive-dea-volume --date YYYY-MM-DD`.
- Inputs are the real high-confidence crosswalk population, Maus footprints, the captured STAC index, asset block metadata, year ranges, and metric/band selections.
- Write `reports/dea-volume/<date>/estimate.json` with:
  - eligible and unmatched site counts
  - distinct footprints and tiles
  - site-year windows
  - per-collection epochs
  - requested bands
  - estimated compressed bytes
  - conservative upper-bound bytes
  - expected range requests
  - scratch-space requirement
  - formulas and assumptions
  - source-manifest digests

**Tests to add**

- Shared tiles are not counted repeatedly as full downloads when windowed reads are planned.
- Sensor-overlap years are counted for every required sensitivity variant.
- Null coverage does not become zero.
- Unit conversion and upper-bound arithmetic.
- Population reconciliation against high-confidence crosswalk rows.
- The earlier full-WA figures are comparison fields only, never computational constants.

**Acceptance**

- The report replaces the provisional 367-tile, 350 GB, and 2.3 TB figures for planning.
- The selected execution host and scratch-space requirement are justified from measured output, not a fixed machine assumption.
- No public export is produced.

#### C6. Run and record Batch C acceptance

**Files**

- Create `tests/test_batch_c_acceptance.py`.
- Create `docs/checkpoints/batch-c-result.md`.

**Tests to add**

- Fixture-driven end-to-end catalogue → coverage → enriched register → volume report.
- Manifest-chain and reconciliation assertions.
- A blocked public export of the enriched register.

**Acceptance**

- Live catalogue item counts are non-zero.
- All snapshots and manifests digest-verify.
- All four coverage disclosures reconcile.
- The real-register volume report is recorded.
- The checkpoint distinguishes fetch date, collection extent date, and product version.

### Batch dependencies and gates

Batch C starts only from the accepted Batch B register semantics recorded in `docs/checkpoints/tier0-result.md` and `docs/checkpoints/batch-b-closeout.md`. DEA licences must be re-read from the captured collection JSON. D7 does not block private coverage calculation but blocks public distribution of the enriched MINEDEX register. Batch C has no dependency on D3 or the public-RC lane.

---

## 4. Batch D — D3 effective-pixel-support threshold

### Batch ruling

**Decision: build.** The dataplatform has no threshold derivation equivalent. Build a monitor-specific, pre-registered simulation using EPSG:3577 pixel centres and the exact D3 criteria. Batch D may introduce only the raster-window primitives required for the simulation; Batch E generalizes production zonal extraction after `n*` is fixed.

### Reuse adjudication

- **Dataplatform CRS model: decline.** EPSG:4326-canonical storage and project-at-query-time behavior do not satisfy D3’s fixed 30 m EPSG:3577 pixel grid.
- **Dataplatform observations schema: decline.** It cannot represent band, composite, sensor, mask, and support provenance.
- **Constructor discipline: adapt.** Use one declared constructor for threshold rows and derived partition fields, without adopting the observations columns.
- **Claim that the dataplatform has no raster handling: decline as stated.** It has raster-style zonal code, but no STAC catalogue or D3 implementation; Batch D remains a monitor build.

### Ordered tasks

#### D1. Freeze the simulation protocol before reading spectral results

**Files**

- Create `config/d3.yaml`.
- Create `src/wa_mine_monitor/d3_protocol.py`.
- Create `tests/test_d3_protocol.py`.

**Protocol**

- Supports: `9, 16, 25, 36, 49, 64, 100, 144`.
- Regions: `pilbara`, `goldfields_esperance`, and `other_wa`, using the same pinned official boundaries required by D4.
- Commodity groups: `iron_ore`, `gold`, `bauxite_alumina`, `nickel`, `mineral_sands`, and `other`.
- Shape classes by Polsby–Popper compactness:
  - `elongated`: `<0.20`
  - `intermediate`: `0.20–<0.50`
  - `compact`: `≥0.50`
- An adequately sampled stratum contains at least 10 independent high-confidence footprints with at least 10 full-support computable years each.
- Use all footprints where a stratum has 10–29; otherwise select 30 by a stable hash of `maus_id`.
- Run 100 deterministic replicates per site-year-support.
- The configuration digest is written before metric extraction.

**Tests to add**

- Exact support set and immutable criteria.
- Stable stratum assignment and selection under reordered inputs.
- Boundary behavior for shape classes.
- Refusal when a region, commodity, or shape value is unclassified.
- Refusal to overwrite or alter a frozen protocol digest.

**Acceptance**

- No accuracy result can change sample definitions or criteria.
- Every adequately sampled stratum is included.
- Sparse strata are reported as not adequately sampled, not silently pooled.

#### D2. Build EPSG:3577 pixel-support assignments

**Files**

- Create `src/wa_mine_monitor/pixel_support.py`.
- Create `tests/test_pixel_support.py`.
- Modify `pyproject.toml` to add the pinned raster-window dependency required by the implementation.

**Interfaces and fields**

- `build_pixel_support(geometry, grid) -> PixelSupport`.
- `PixelSupport` records grid identity, member indices, `effective_pixel_support_px`, and assignment digest.
- Pixel-centre membership on the 30 m grid; partial-pixel weighting and `all_touched` are prohibited.

**Tests to add**

- Exact 9-, 16-, and 144-centre synthetic polygons.
- Boundary-centre behavior.
- CRS mismatch refusal.
- Shifted-grid and rotated-grid refusal.
- Empty support returns a computed zero, not null.
- Missing or invalid geometry returns not-computed, not zero.
- Stable assignment digest.

**Acceptance**

- Grid CRS, affine transform, width, height, and product tile identity participate in the assignment identity.
- Effective support is a measured pixel count, not area divided by 900.

#### D3. Build deterministic reduced-support simulation inputs

**Files**

- Create `src/wa_mine_monitor/d3_inputs.py`.
- Create `tests/test_d3_inputs.py`.

**Interfaces and output**

- Read only the large high-confidence footprints selected by the frozen protocol.
- Construct full-support annual NBR, NDMI, and FC metric vectors.
- `sample_support(member_indices, n, replicate, seed_material)` samples without replacement and is nested and deterministic.
- Write `curated/d3-inputs/<date>/support_inputs.parquet` with site, year, collection, full support, valid support, metric, full value, protocol digest, and input-manifest digests.

**Tests to add**

- Stable samples under process and input reordering.
- No repeated pixel within a replicate.
- Exact requested support when available.
- Refusal when full effective support is below 144.
- Nodata and FC out-of-range values follow Batch E’s declared rules.
- Mismatched protocol and input digests are refused.

**Acceptance**

- This task is a bounded simulation-input builder, not the production Tier 1 extractor.
- Sensor overlap variants remain separate.
- Maus and MINEDEX-linked artifacts remain internal.

#### D4. Evaluate the never-relaxed D3 criteria

**Files**

- Create `src/wa_mine_monitor/d3_threshold.py`.
- Create `tests/test_d3_threshold.py`.

**Interfaces and result fields**

- `evaluate_threshold(inputs, protocol) -> ThresholdResult`.
- Record for every support and stratum:
  - P90 absolute NBR error
  - P90 absolute NDMI error
  - P90 absolute FC error in percentage points
  - median full-versus-reduced Spearman correlation
  - computable site-year fraction
  - sample counts
  - pass/fail per criterion
- Select the smallest support meeting:
  - NBR P90 absolute error ≤0.03
  - NDMI P90 absolute error ≤0.03
  - FC P90 absolute error ≤5 percentage points
  - median Spearman ≥0.95
  - computable site-year fraction ≥0.90
  - every adequately sampled stratum passes
- If none passes, select 144 and set `criteria_passed=false` with all failed criteria listed.

**Tests to add**

- Each individual criterion can fail independently.
- Smallest passing support is selected.
- A failing stratum prevents selection.
- Sparse strata are disclosed but do not masquerade as adequate.
- No-passing-support behavior selects 144 without altering thresholds.
- Nominal area is exactly `900 * n_star`.

**Acceptance**

- Write `curated/d3-threshold/<date>/threshold.json` and manifest.
- `n_star`, `nominal_area_m2`, `criteria_passed`, and failure details are immutable inputs to Batch E.

#### D5. Apply D3 eligibility to the internal register

**Files**

- Modify `src/wa_mine_monitor/register.py`.
- Modify `src/wa_mine_monitor/cli.py`.
- Modify `tests/test_register.py`.
- Modify `tests/test_cli.py`.

**Fields**

- `effective_pixel_support_px`: nullable `int64`.
- `d3_threshold_px`: nullable `int64`.
- `d3_eligible`: nullable `bool`.
- `trajectory_status`, with:
  - `eligible`
  - `no_usable_footprint`
  - `crosswalk_not_high_confidence`
  - `insufficient_pixel_support`
  - `threshold_not_computed`

**Tests to add**

- Unmatched and unusable footprints receive `no_usable_footprint`.
- Low/medium confidence never become eligible.
- Computed support below `n*` is a genuine ineligible result.
- Missing support remains not computed.
- Status counts reconcile to the register total.
- Refusal when the threshold manifest is missing, altered, or not accepted.

**Acceptance**

- Add `apply-d3-threshold --date YYYY-MM-DD`.
- The manifest records status counts, computed/zero/not-computed support counts, threshold digest, and whether D3 criteria passed.
- A forced 144 result retains the failed-criteria disclosure.

#### D6. Run and record Batch D acceptance

**Files**

- Create `tests/test_batch_d_acceptance.py`.
- Create `docs/checkpoints/batch-d-result.md`.

**Tests to add**

- Fixture-driven protocol → support → simulation → threshold → eligibility chain.
- Post-result configuration mutation refusal.
- Complete status and stratum reconciliation.

**Acceptance**

- The checkpoint publishes no MINEDEX row-level data.
- The accepted `n*` and protocol digest are the only values Batch E may consume.
- D3 thresholds are not relaxed after results.

### Batch dependencies and gates

Batch D requires Batch C’s accepted catalogue and real-volume inputs. Maus is the sole footprint under D1. D7 permits private matching and simulation but prohibits public row-level sample artifacts. Batch D produces the D3 gate and does not depend on the public-RC lane.

---

## 5. Batch E — Tier 1 trajectory extraction

### Batch ruling

**Decision: adapt and build.** Adapt the dataplatform zonal API and backfill state model after correcting cache identity and CRS assumptions; build monitor-specific DEA window reads, spectral metrics, schemas, overlap sensitivity, Huntly validation, and statewide extraction.

### Reuse adjudication

- **`core/zonal.py`: adapt.** Retain pixel-centre assignment, stacked-array support, memoisation, and omission of zero-valid reductions, but replace its bounds-only polygon cache signature and make D3 support semantics explicit.
- **Dataplatform backfill windows: adapt.** Port idempotent partition coverage, explicit load outcomes, and empty-write refusal to Parquet partitions; do not port DuckLake or observations-table code.
- **`core/qa.py`: adapt.** Translate sentinel, envelope, drift, and synthetic-fixture checks to raster-product and trajectory schemas.
- **Bounded campaign wrapper: adapt.** Use its resource-limit pattern for statewide extraction, with a tested refusal when the selected execution path cannot enforce the declared memory ceiling.
- **Dataplatform observations schema: decline.** Tier 1 keeps a raster-native site × year × metric schema.

### Ordered tasks

#### E1. Build DEA window and value decoding

**Files**

- Create `src/wa_mine_monitor/dea_raster.py`.
- Create `tests/test_dea_raster.py`.

**Interfaces**

- `read_asset_window(asset, geometry, grid)`.
- `decode_geomedian(values)` maps −999 to null and divides valid values by 10,000.
- `decode_fc(values)` maps 255 to null and retains values above 100.
- Read only the intersecting COG window; full-tile download is prohibited.

**Tests to add**

- Exact window bounds and CRS transformation.
- Range-read behavior using a local COG fixture.
- Geomedian nodata and scale.
- FC nodata and measured values above 100 without clipping.
- Missing bands and changed block metadata.
- Read failures produce not-computable reasons rather than zero metrics.

**Acceptance**

- Every returned window records collection, item, asset, product version, grid, nodata, scale, and source-manifest digest.
- Out-of-documented-range FC values are preserved and counted.

#### E2. Adapt the memoised zonal engine

**Files**

- Create `src/wa_mine_monitor/zonal.py`.
- Create `tests/test_zonal.py`.

**Interfaces**

- `AssignmentKey` includes CRS, affine transform, dimensions, geometry digest, and membership convention.
- `build_assignment(grid, polygons, cache)`.
- `zonal_stats(values, assignment, valid, stat)` returns value, member count, and valid count.

**Tests to add**

- Two different polygons with identical bounds never share an assignment.
- Geometry-order independence.
- Fixed-grid cache reuse across years.
- Cache separation for changed transform, CRS, or footprint.
- 2-D and stacked-array parity.
- Zero member pixels versus zero valid pixels.
- D3 `effective_pixel_support_px` parity.
- Huntly-origin arithmetic fixture parity.

**Acceptance**

- The bounds-only cache identity in dataplatform `core/zonal.py` is not copied.
- The engine uses the exact D3 pixel-centre convention.
- A zero-valid region is represented as not computable, not omitted without a reason.

#### E3. Build spectral metric and trajectory schemas

**Files**

- Create `src/wa_mine_monitor/trajectories.py`.
- Create `src/wa_mine_monitor/spectral_metrics.py`.
- Create `tests/test_trajectories.py`.
- Create `tests/test_spectral_metrics.py`.

**Schema fields**

- `site_id`
- `maus_id`
- `year`
- `metric`
- `value`
- `sensor`
- `collection_id`
- `item_id`
- `product_version`
- `geomad_count`
- `n_member_pixels`
- `n_valid_pixels`
- `effective_pixel_support_px`
- `computable`
- `not_computable_reason`
- `value_out_of_documented_range`
- `transition_adjacent`
- `source_snapshot_date`
- `geometry` in EPSG:3577 for the internal/Maus-derived package

**Tests to add**

- NBR and NDMI formula fixtures.
- Zero denominator produces not-computable.
- FC percentile mapping without clipping.
- `geomad_count` is null for FC, never fabricated as zero.
- Nullable booleans and integers survive Parquet round trips.
- Metric vocabulary and geometry CRS are enforced.

**Acceptance**

- Output is site × year × metric × product variant, so sensor overlaps are preserved rather than overwritten.
- Geometry and all derived rows are classified as Maus-derived and package-bound to CC-BY-SA-4.0.

#### E4. Build resumable partition extraction

**Files**

- Create `src/wa_mine_monitor/trajectory_extract.py`.
- Modify `src/wa_mine_monitor/cli.py`.
- Create `tests/test_trajectory_extract.py`.
- Modify `tests/test_cli.py`.

**Interfaces and manifests**

- Add `extract-trajectories`.
- Partition by `collection_id/year`.
- `PartitionResult(existing, inserted, refused_empty, not_computable)` replaces the dataplatform `LoadResult` for immutable Parquet.
- Coverage is read from verified partitions and manifests, not an unverified progress file.

**Tests to add**

- Completed partitions are skipped.
- `--force` writes a new versioned output rather than mutating an old partition.
- Empty replacement is refused.
- Partial failure cannot finalize a batch manifest.
- Serial and concurrent extraction are identical.
- Only `trajectory_status=eligible` rows enter extraction.

**Acceptance**

- Every partition independently verifies and reconciles.
- MINEDEX IDs and crosswalk membership remain internal.
- The command refuses statewide mode until the Huntly gate passes.

#### E5. Validate against the Huntly cube first

**Files**

- Create `src/wa_mine_monitor/huntly_validation.py`.
- Create `tests/test_huntly_validation.py`.
- Create `docs/checkpoints/huntly-zonal-validation.md`.

**Gate**

- NBR and NDMI absolute difference ≤`1e-6`.
- FC absolute difference ≤`0.1` percentage points.
- Member and valid pixel counts match exactly.
- Computable/not-computable classifications match exactly.

**Tests to add**

- Passing comparison.
- Failure for each metric tolerance.
- Failure on count or status mismatch.
- Statewide extraction refusal without an accepted checkpoint digest.
- Checkpoint/input digest mismatch.

**Acceptance**

- Validation runs against the declared Huntly reference cube before any statewide extraction.
- Any failed metric keeps statewide mode blocked.

#### E6. Run sensor-overlap and transition sensitivity

**Files**

- Create `src/wa_mine_monitor/sensor_sensitivity.py`.
- Create `tests/test_sensor_sensitivity.py`.

**Output fields**

- `site_id`, `year`, `metric`
- `collection_a`, `collection_b`
- `value_a`, `value_b`, `delta`
- `count_a`, `count_b`
- `transition_adjacent`
- `computable_pair`
- `not_computable_reason`

**Tests to add**

- Every overlapping product pair is retained.
- Missing one side is not a zero delta.
- Transition-adjacent years are flagged.
- Counts and denominators accompany every summary rate.
- Sensor priority cannot remove a sensitivity row.

**Acceptance**

- Write `curated/sensor-sensitivity/<date>/sensitivity.parquet` and a reconciled report.
- The production trajectory does not conceal overlap-year disagreement.

#### E7. Run statewide extraction and acceptance

**Files**

- Create `src/wa_mine_monitor/trajectory_qa.py`.
- Create `tests/test_trajectory_qa.py`.
- Create `tests/test_batch_e_acceptance.py`.
- Create `docs/checkpoints/batch-e-result.md`.

**Tests to add**

- NBR/NDMI envelope checks.
- FC nodata and out-of-range disclosure.
- Synthetic/test identifiers cannot enter product partitions.
- Expected and observed site-year-product counts reconcile.
- Resource-ceiling enforcement path.
- Complete manifest chain from Batch C catalogue and Batch D threshold.

**Acceptance**

- Huntly gate passes.
- Every eligible site is present or has an explicit not-computable reason.
- Per-site-year product, sensor, version, GeoMAD count, support, and valid count are preserved.
- Overlap sensitivity is complete.
- Tier 1 remains private pending Batch G export adjudication.

### Batch dependencies and gates

Batch E requires the accepted D3 `n*`, protocol digest, and eligibility register. DEA inputs remain CC-BY-4.0. All geometry and trajectories over Maus footprints enter the separate CC-BY-SA-4.0 lineage. D7 blocks public release of any rows whose selection or identity derives from MINEDEX, even when the spectral values and Maus geometry have otherwise permissive licences.

---

## 6. Batch F — DBCA-060 fire context and SILO climate context

### Batch ruling

**Decision: conditional adapt and build.** Adapt the ArcGIS client and build the fire/climate context modules. The ArcGIS Online mirror route is not adopted now. It becomes an approved acquisition route only after the evidence task below passes; otherwise the monitor must use a validated authoritative local DBCA-060 snapshot.

### Reuse adjudication

- **ArcGIS REST client and truncation-refusing pager: adapt.** Preserve epoch-millisecond conversion, field-specific sentinel handling, continued paging while `exceededTransferLimit` is true, and fail-closed reconciliation; add object-ID equality, duplicate detection, and service-schema validation.
- **ArcGIS Online DBCA-060 mirror route: conditional adapt.** The route is declined for product ingestion until authoritative-versus-mirror identity, publisher authority, licence, and freshness evidence pass. Dataplatform `adapters/dbca_burns.py` itself says authoritative comparison is required before published redistribution.
- **ERA5-Land/SILO adapter template: adapt.** Reuse monthly-window and precipitation-accumulation lessons, not its CDS, NetCDF, virtual-station, or AOI implementation.
- **DFES and FIRMS recency-gap adapters: decline for v1.** They are outside Batch F’s approved DBCA-060 plus SILO scope and require separate licence, retention, and geometry adjudication.
- **Already-landed reference/KCGM data: decline.** No Batch F consumer requires the demographic or emissions layers, and source inspection does not prove live-lake inventory or publishability.
- **SW-WA AOI logic: decline.** Statewide processing must not inherit `config.AOI_BBOX` or `config.in_aoi`.

### Ordered tasks

#### F1. Adjudicate DBCA-060 mirror provenance and licence evidence

**Files**

- Create `src/wa_mine_monitor/sources/dbca_evidence.py`.
- Create `tests/sources/test_dbca_evidence.py`.
- Create `docs/evidence/dbca-060-mirror-adjudication.md`.

**Required evidence**

- Authoritative DBCA-060 package, metadata, and licence files with digests.
- Authoritative resource URL, release/version date, schema, CRS, and feature identity.
- ArcGIS item owner/publisher identity, item metadata, service metadata, licence text, modification date, capabilities, object-ID field, maximum record count, and layer schema.
- Evidence that the mirror publisher is authorized to redistribute the dataset and that the grant permits downstream republication.
- Reproducible authoritative-versus-mirror comparison of:
  - object-ID or stable-feature identity
  - feature count and duplicate count
  - schema and field domains
  - geometry digests after canonical normalization
  - attribute values
  - event dates
  - spatial extent
  - release/version dates
- A freshness, withdrawal, and failure policy that preserves the last verified authoritative snapshot.

**Tests to add**

- Missing evidence fails closed.
- Count-equal but ID-different sources fail.
- Schema, geometry, attribute, extent, or version mismatch fails.
- Unknown publisher authority or downstream grant fails.
- Evidence digest mutation fails.
- PASS and FAIL adjudications are immutable.

**Acceptance**

- `mirror_route_allowed=true` only when all evidence categories pass.
- A discrepancy may be accepted only if it is an explicitly documented, reproducible transformation that does not change dataset identity or licence scope.
- Until PASS, the mirror may not feed Batch F product artifacts; the authoritative staged-file route remains the only allowed input.

#### F2. Adapt the ArcGIS client and complete pager

**Files**

- Create `src/wa_mine_monitor/sources/arcgis.py`.
- Create `tests/sources/test_arcgis.py`.

**Interfaces**

- `ArcGISLayerMetadata`.
- `ArcGISPager.fetch_ids()`.
- `ArcGISPager.fetch_features()`.
- `ArcGISPager.reconcile(expected_ids, features)`.
- Field-specific date and sentinel normalizers supplied by the source adapter.

**Tests to add**

- HTTP-200 ArcGIS error envelopes.
- Short pages with `exceededTransferLimit=true`.
- Empty terminal pages.
- Duplicate, missing, and unexpected IDs.
- Count-equal but set-unequal failure.
- Epoch milliseconds before and after 1970.
- `999` retained for `fih_fire_type` but nulled only in declared missing-value fields.
- Capability or schema drift refusal.

**Acceptance**

- Returned feature IDs equal the uncapped ID set exactly.
- No partial result may replace or supersede a verified snapshot.
- Source-specific semantics are not embedded in the generic pager.

#### F3. Acquire and validate DBCA-060

**Files**

- Create `src/wa_mine_monitor/sources/dbca.py`.
- Modify `src/wa_mine_monitor/licence.py`.
- Modify `src/wa_mine_monitor/cli.py`.
- Create `tests/sources/test_dbca.py`.

**Interfaces and metadata**

- Add `fetch-dbca-fire --mode authoritative|mirror`.
- Mirror mode requires the accepted adjudication digest.
- Snapshot metadata records mode, source/version, CRS, schema digest, feature IDs/count, temporal coverage, spatial coverage, licence, attribution, and evidence digest.

**Tests to add**

- Mirror-mode refusal without accepted evidence.
- Authoritative staged-file validation.
- Atomic finalization.
- Feature-ID reconciliation.
- CRS and required-field validation.
- Changed edition/date produces a new snapshot rather than overwrite.

**Acceptance**

- Acquisition mode is explicit in every manifest.
- Mirror and authoritative snapshots are never represented as interchangeable without the adjudication record.

#### F4. Build the three-state fire-context join

**Files**

- Create `src/wa_mine_monitor/fire_context.py`.
- Create `tests/test_fire_context.py`.

**Schema fields**

- `site_id`
- `maus_id`
- `year`
- `fire_status`: `recorded`, `not_recorded`, or `unknown`
- `fire_record_count`
- `fire_source_version`
- `fire_coverage_status`
- `fire_snapshot_date`
- `not_computable_reason`

**Tests to add**

- Intersecting recorded fire produces `recorded`.
- A validated covered year with no intersecting record produces `not_recorded`.
- Year outside declared source coverage produces `unknown`.
- Incomplete/unadjudicated source produces `unknown`.
- Missing footprint or invalid geometry produces `unknown`.
- Multiple fire records are counted without changing the three-state meaning.
- “Not recorded” is never emitted from an unverified absence.

**Acceptance**

- DBCA-060 is described as recorded fire overlap only.
- No output treats `not_recorded` as a known-negative fire label.
- Status counts reconcile for every site-year.

#### F5. Build SILO rainfall context

**Files**

- Create `src/wa_mine_monitor/sources/silo.py`.
- Create `src/wa_mine_monitor/climate_context.py`.
- Modify `src/wa_mine_monitor/secrets.py`.
- Modify `src/wa_mine_monitor/cli.py`.
- Create `tests/sources/test_silo.py`.
- Create `tests/test_climate_context.py`.

**Schema fields**

- `site_id`
- `maus_id`
- `year`
- `silo_cell_id`
- `annual_rainfall_mm`
- `rain_days_ge_1mm`
- `rainfall_anomaly_mm`
- `rainfall_baseline_start_year=1991`
- `rainfall_baseline_end_year=2020`
- `climate_status`
- `not_computable_reason`
- source version and snapshot date

**Tests to add**

- Credential redaction.
- Monthly-window completeness.
- Leap-year and missing-day handling.
- Annual totals and ≥1 mm rain-day counts.
- Fixed 1991–2020 anomaly.
- Missing baseline or incomplete year produces unknown/not-computable.
- Statewide region checks reject inherited SW-WA AOI clipping.

**Acceptance**

- SILO credentials never enter manifests, logs, or fixtures.
- Climate context is aligned by Maus footprint/cell and year.
- No missing rainfall value becomes zero.

#### F6. Join context to trajectories and record acceptance

**Files**

- Create `src/wa_mine_monitor/context_join.py`.
- Create `tests/test_context_join.py`.
- Create `tests/test_batch_f_acceptance.py`.
- Create `docs/checkpoints/batch-f-result.md`.

**Tests to add**

- One context record per Tier 1 site-year.
- Fire and climate missingness remain independent.
- Trajectory rows are not dropped because context is unknown.
- Rendering contract requires climate and fire context beside any onset interpretation.
- “Cause not determined” remains when either required context is absent.

**Acceptance**

- Trajectory, fire, and climate counts reconcile.
- Source versions and coverage limitations are carried forward.
- No causal fire or climate attribution is generated.
- The checkpoint records whether the mirror remained declined or passed its evidence gate.

### Batch dependencies and gates

Batch F follows accepted Batch E extraction. The ArcGIS mirror evidence is a hard precondition, not documentation after use. DBCA-060’s current `open` entry in `src/wa_mine_monitor/licence.py` does not establish the mirror publisher’s authority. SILO access requires a secret account credential and a captured exact-resource licence record. The public-RC lane does not waive either condition.

---

## 7. Batch G — export, releases, static site, and D5 Pages gate

### Batch ruling

**Decision: adapt and build.** Adapt the existing fail-closed export boundary and the dataplatform QA/operational patterns; build package-aware exports, versioned releases, MapLibre/PMTiles site generation, and a machine-readable D5 gate. The D2 public-repository checklist remains outside this batch.

### Reuse adjudication

- **Three-state licence enum and static conformance test: adapt through the public-RC foundation.** Batch G consumes that model, but runtime export enforcement remains mandatory; static scans alone do not protect a release.
- **Dataplatform QA: adapt.** Apply envelope, sentinel, drift, and synthetic-fixture checks to release tables and site payloads.
- **Deployment-bijection and timer patterns: adapt only where scheduled refresh is introduced.** Every production source must have either a refresh path or a documented exemption.
- **MCP lake reader: decline.** Its lexical `WITH`/`EXPLAIN` guard is not a safe read-only boundary and is not required for a static site.
- **Dataplatform generic run/backfill drivers: decline.** Batch G exports immutable release artifacts and site bundles rather than lake windows.

### Ordered tasks

#### G1. Build package-aware export enforcement

**Files**

- Modify `src/wa_mine_monitor/export_gate.py`.
- Create `src/wa_mine_monitor/releases.py`.
- Modify `src/wa_mine_monitor/cli.py`.
- Modify `tests/test_export_gate.py`.
- Create `tests/test_releases.py`.

**Interfaces and fields**

- `ReleasePackageSpec(package_id, package_licence, allowed_source_ids, forbidden_fields, allow_geometry, claim_boundary)`.
- `export-release --package PACKAGE_ID --version VERSION`.
- Every row or partition carries `lineage_source_ids` and `licence_state`.
- Package classes:
  - licence-clean CC-BY release
  - Maus-derived CC-BY-SA-4.0 release
  - internal-only artifact

**Tests to add**

- `gated_internal` and `research_only` inputs refuse public export.
- Missing or unknown lineage refuses.
- A Maus-derived scalar or geometry refuses a non-ShareAlike package.
- The Maus package includes attribution, source link, modification statement, and CC-BY-SA-4.0 assignment.
- MINEDEX lineage or MINEDEX-derived selection refuses every public package while D7 is closed.
- Row filtering is prohibited; a mixed package fails as a whole.
- Geometry-name and geometry-value detection remain active.

**Acceptance**

- `src/wa_mine_monitor/export_gate.py` becomes wired to the only release command.
- ShareAlike is enforced by lineage, not by geometry naming alone.
- No scalar-field carve-out is asserted.
- The current Tier 1 output remains blocked from public export if its selection derives from MINEDEX.

#### G2. Build versioned release manifests and reconciliation

**Files**

- Create `src/wa_mine_monitor/release_manifest.py`.
- Create `tests/test_release_manifest.py`.

**Manifest fields**

- package and version
- output licence
- attribution blocks
- claim-boundary text
- source and evidence digests
- schema and row counts
- partitions
- geometry presence/CRS
- dropped or transformed fields
- data-current-as-at date
- refresh policy
- D7 audit outcome
- D3 and Huntly checkpoint digests

**Tests to add**

- Semantic-version validation.
- Immutable version refusal.
- Row/partition reconciliation.
- Manifest/output digest mismatch.
- Missing attribution or modification notice.
- D7 audit failure.
- Restricted/raw geometry scan.

**Acceptance**

- Release artifacts and manifests reconcile independently of the site.
- GeoParquet files remain outside the Pages artifact.

#### G3. Build the static site data contract

**Files**

- Create `src/wa_mine_monitor/site_data.py`.
- Create `src/wa_mine_monitor/site.py`.
- Modify `src/wa_mine_monitor/cli.py`.
- Create `tests/test_site_data.py`.
- Create `tests/test_site.py`.

**Public fields and language**

- The first product reference includes: “Descriptive spectral change chronologies; not a compliance or performance assessment.”
- Detection labels use year or interval only.
- Onsets display “cause not determined” until fire and climate context is present.
- No owner/operator rankings, comparative scores, best/worst sorting, or unqualified red/green status.
- D8 owner terminology is used only on an internal preview; D7 prevents it from entering a public build.

**Tests to add**

- Required claim-boundary string.
- Prohibited compliance/performance phrases.
- No precise onset date.
- No “operator” substitution for owners.
- Context and uncertainty rendering.
- D7-restricted field and identifier scan.
- Table/map/site count reconciliation.

**Acceptance**

- Public-mode generation refuses any MINEDEX-derived row, identifier, coordinate, owner, crosswalk, or selection.
- Private preview output is ignored by Git and cannot satisfy the public Pages gate.

#### G4. Build MapLibre and PMTiles assets

**Files**

- Create `site/package.json`.
- Create `site/assets/app.js`.
- Create `site/assets/styles.css`.
- Create `site/templates/index.html`.
- Create `site/templates/site.html`.
- Create `tests/test_site_assets.py`.

**Tests to add**

- Deterministic PMTiles generation.
- Range-request compatibility in a local preview.
- Map/table counts match release manifests.
- Keyboard navigation and focus order.
- Mobile viewport behavior.
- Broken internal and external links.
- No raw or restricted geometry in the site artifact.

**Acceptance**

- Map styles describe spectral detections and context without evaluative operator styling.
- PMTiles and site tables are generated only from an accepted public release package.
- The site remains static and contains no credentials or private API endpoints.

#### G5. Build the D5 Pages gate

**Files**

- Create `src/wa_mine_monitor/pages_gate.py`.
- Create `scripts/check_pages_artifact.py`.
- Create `tests/test_pages_gate.py`.
- Create `docs/checkpoints/pages-release-candidate.md`.

**Gate fields**

- `tier0_acceptance`
- `tier1_acceptance`
- `release_site_reconciliation`
- `attribution_rendered`
- `uncertainty_rendered`
- `artifact_bytes`
- `artifact_under_800_mib`
- `pmtiles_range_requests`
- `mobile_checks`
- `keyboard_checks`
- `broken_link_checks`
- `restricted_geometry_scan`
- `d7_public_payload_audit`
- `accepted`

**Tests to add**

- Every individual failed condition keeps `accepted=false`.
- Artifact equality at and above the 800 MiB boundary.
- GeoParquet mistakenly included in Pages artifact.
- Missing attribution or uncertainty.
- Restricted geometry or MINEDEX-derived content.
- PMTiles preview failure.

**Acceptance**

- Pages deployment occurs only when all D5 fields pass.
- Public-RC success alone does not set any D5 field.
- While D7 blocks the Tier 1 public payload, the checkpoint records the exact failed condition and Pages remains undeployed.

#### G6. Run and record Batch G acceptance

**Files**

- Create `tests/test_batch_g_acceptance.py`.
- Create `docs/checkpoints/batch-g-result.md`.

**Tests to add**

- Internal Tier 1 → refused public export under D7.
- Licence-clean synthetic package → versioned release → site → D5 gate.
- Full release/site reconciliation.
- Prohibited-claim scan.

**Acceptance**

- Export and site machinery are operational and fail closed.
- A failed D5 gate is an accepted, recorded outcome when the public Tier 1 payload is legally unavailable.
- The D2 public-repository checklist is not duplicated here.

### Batch dependencies and gates

Batch G follows Batch F and requires accepted Tier 0, D3, Huntly, Tier 1, and context checkpoints. It consumes the public-RC licence-state foundation but does not inherit public-RC approval. D7 currently blocks a public site or release whose row selection identifies the internal MINEDEX monitoring frame. D5 remains binding and cannot be waived.

---

## 8. Tier 0 public-RC lane — independent D2 public-flip gate

### Lane ruling

**Decision: adapt and build.** Adapt the three-state licence and evidence-verifier concepts; build a monitor-specific public-safe fallback release, complete payload audits, and record the D2/D10 gate. This lane may proceed alongside Batches C–G and has no authority over the private C→G sequence or D5 Pages.

### Reuse adjudication

- **Three-state `LicenceState`: adapt.** Adopt the `public`, `gated_internal`, and `research_only` meanings and default-deny behavior; integrate them with the existing exact-resource registry and runtime export gate.
- **Static licence conformance test: adapt.** Retain the conformance objective, but discover every source use and require explicit, tested exemptions for computed or mixed row states; do not copy dataplatform’s partial module map.
- **`bin/verify_provenance.py`: adapt.** Reuse ledger traversal, saved-extract discipline, digest anchoring, and non-zero exit behavior; replace the `econ/` paths and digit-only matching with source identity, digest, context, units, and claim checks.
- **AdapterSpec/REGISTRY: adapt.** Lift a small frozen source contract and catalogue query; decline `run.py` and `backfill.py`.
- **Reference/KCGM and shared-lake inventory: decline.** They are not required for the Tier 0 fallback and cannot be accepted from unverified live-lake claims.
- **MCP reader: decline.** It is outside the lane and does not provide an adequate read-only security boundary.

### Ordered tasks

#### P1. Add three-state licence governance and complete conformance checks

**Files**

- Modify `src/wa_mine_monitor/licence.py`.
- Modify `src/wa_mine_monitor/export_gate.py`.
- Create `tests/test_licence_conformance.py`.
- Modify `tests/test_licence.py`.
- Modify `tests/test_export_gate.py`.

**Fields and behavior**

- `LicenceState.PUBLIC`
- `LicenceState.GATED_INTERNAL`
- `LicenceState.RESEARCH_ONLY`
- `SourceLicence.licence_state`
- Default state is `GATED_INTERNAL`.
- MINEDEX is `GATED_INTERNAL`.
- DEA, DMIRS-003, Maus, and other public sources remain public only with exact-resource evidence.
- `RESEARCH_ONLY` can never enter internal reporting intended for later publication.

**Tests to add**

- Every registry entry has a valid state and exact-resource evidence fields.
- “UNVERIFIED”, conflict, or missing licence evidence cannot map to public.
- Every literal source use agrees with its registry state.
- Computed/mixed exemptions are enumerated, justified, and name real sources.
- Enum-to-boolean mapping is fail-closed.
- Static conformance and runtime export refusal both execute.

**Acceptance**

- The static test cannot silently skip a newly registered source.
- Runtime export remains the controlling gate.
- `docs/licensing-matrix.md` is generated or checked against the authoritative registry without drift.

#### P2. Converge provenance and evidence-digest verification

**Files**

- Create `src/wa_mine_monitor/evidence.py`.
- Create `bin/verify_evidence.py`.
- Create `evidence/provenance.yaml`.
- Create `tests/test_evidence.py`.
- Create `tests/test_evidence_conformance.py`.

**Ledger fields**

- `source_id`
- `resource_url`
- `snapshot_date`
- `licence_state`
- `evidence_files`
- expected SHA-256 digests
- claim identifier
- cited context and units
- verification status
- delegated verifier, if any
- offline-runnable flag

**Tests to add**

- Missing or malformed ledger.
- Unknown status.
- Missing, changed, or out-of-root evidence file.
- Correct number appearing in the wrong context or units.
- Source identity mismatch.
- Failed delegated verifier.
- Index-only verifier is disclosed and cannot satisfy a required public gate.
- Full manifest counts and non-zero failure exit.

**Acceptance**

- `verify-evidence` proves file digests, source identity, cited context, and units; mere occurrence of matching digits is insufficient.
- Raw evidence remains outside the committed repository where D9 requires it; the committed ledger carries safe digests and relative identifiers only.
- D7’s adjudicated MINEDEX evidence verifies as closed, never as permission.

#### P3. Build the public-safe Tier 0 fallback release

**Files**

- Create `src/wa_mine_monitor/public_rc.py`.
- Modify `src/wa_mine_monitor/cli.py`.
- Create `tests/test_public_rc.py`.

**Artifacts**

- `tier0-tenements`: a DMIRS-003 CC-BY-4.0 package containing audited tenement identifiers, status, snapshot date, source/licence fields, and permitted geometry.
- `tier0-maus-wa`: a separate Maus-derived CC-BY-SA-4.0 package containing `maus_id`, WA geometry, snapshot date, source link, attribution, and modification statement.
- No crosswalk, MINEDEX identifier, MINEDEX point, MINEDEX site selection, MINEDEX owner, or MINEDEX-derived row.

**Tests to add**

- Exact field allowlists for both packages.
- MINEDEX and crosswalk lineage refusal.
- Separate package licences and attribution.
- No Maus scalar/geometry carve-out into the CC-BY package.
- Row, geometry, and manifest reconciliation.
- Rebuild produces a new immutable version.

**Acceptance**

- Add `build-tier0-public-rc --version VERSION`.
- The README and release notes call these licence-clean reference-layer fallbacks, not a public MINEDEX site register.
- No MINEDEX-derived aggregate is included unless it has a separate recorded D7 payload clearance.

#### P4. Audit the repository and release payload

**Files**

- Create `scripts/audit_public_tree.py`.
- Create `scripts/audit_release_payload.py`.
- Create `tests/test_public_audits.py`.

**Tests to add**

- Raw DASC archives, shapefile sidecars, Parquet snapshots, evidence bundles, credentials, local paths, generated private site assets, and MINEDEX row-level fixtures are detected.
- Synthetic licence-clean fixtures remain permitted.
- WKT, WKB, GeoJSON, and renamed geometry detection.
- MINEDEX field-name, identifier, and lineage detection.
- Audit output itself redacts local paths and secrets.

**Acceptance**

- Both staged-tree and release-payload audits return zero findings.
- Full-history secret scanning is separately required; a clean working tree scan is insufficient.

#### P5. Complete public wording and attribution evidence

**Files**

- Modify `README.md`.
- Modify `docs/licensing-matrix.md`.
- Create `tests/test_public_wording.py`.
- Create `tests/test_attribution_rendering.py`.

**Tests to add**

- Exact D11 claim-boundary sentence at first product reference.
- D8 owner terminology.
- Clear distinction between the internal MINEDEX frame and public fallback layers.
- No implication that MINEDEX-derived rows are distributed.
- All fallback attributions and modification notices render.

**Acceptance**

- README, release notes, licensing matrix, and generated attribution blocks agree with the registry and D7.
- No compliance, performance, recovery, or equivalence claim appears.

#### P6. Execute and record the D2/D10 public-flip checklist

**Files**

- Create `docs/checkpoints/tier0-public-rc.md`.
- Create `tests/test_public_rc_checkpoint.py`.

**Checkpoint fields**

- `d7_exclusion_passed`
- `fallback_release_passed`
- `licensing_matrix_reconciled`
- `attribution_tests_passed`
- `permitted_fixture_passed`
- `prohibited_fixture_passed`
- `staged_tree_audit_passed`
- `release_payload_audit_passed`
- `full_history_secret_scan_passed`
- `private_ci_green`
- `actions_logs_reviewed`
- `readme_claim_boundary_passed`
- `private_snapshot_verification_passed`
- `reconciliation_report_committed`
- `public_aggregate_clearances`
- `public_flip_authorized`

**Tests to add**

- Every absent, false, or null condition keeps authorization false.
- D7 exclusion cannot be replaced by an assertion of permission.
- Private CI without reviewed logs is insufficient.
- Public aggregate clearance must enumerate every aggregate actually shipped.
- Checkpoint evidence digests must match.

**Acceptance**

- Repository visibility changes only after every field passes and the checkpoint is committed.
- This authorization applies to the repository and Tier 0 fallback release only.
- It does not authorize Batch G Pages deployment or any MINEDEX-derived release.

### Lane dependencies and gates

The lane depends on D6’s accepted DASC snapshot provenance, D7’s closed adjudication, D8 terminology, and the accepted Tier 0 reconciliation. It does not depend on Batches C–G and may run in parallel. Failure leaves the repository private without blocking private spectral work.

---

## 9. Consolidated non-transfer rulings

- **EPSG:4326-canonical lake CRS: decline.** Retain source CRS assertions, EPSG:3577 for DEA analysis, EPSG:7844 for DASC source validation, and explicitly transformed output CRS.
- **Dataplatform observations columns: decline.** Build register, threshold, trajectory, context, release, and evidence schemas native to this monitor; adapt only the single-constructor and declared-schema discipline.
- **Dataplatform STAC/compositing implementation: decline because none exists.** Build the DEA STAC client and raster reader; adapt only the verified zonal primitive.
- **Health economics, US adapters, DWER scrapers, and health/outcomes modules: decline.** They have no approved monitor consumer.
- **SW-WA AOI constants and `config.in_aoi`: decline.** All lifted source code must accept an explicit statewide region or footprint.
- **Generic DuckLake drivers and canonical table: decline.** The monitor remains DuckDB over versioned partitioned GeoParquet; no task may write to dataplatform `observations`.
- **Attached-lake reference data and live row-count claims: decline without a separate inventory and licence adjudication.**
- **MCP read-only SQL guard: decline.** It is unnecessary and does not safely prove read-only execution.
- **Dataplatform operational wrappers: adapt selectively.** Resource ceilings, locking, and explicit intentional-skip outcomes may be reused only with platform-specific enforcement tests.
- **DFES/FIRMS recency layers: decline for v1.** Record them as future candidates without expanding Batch F.
- **Dataplatform raster absence claim: decline as overbroad.** `core/zonal.py` and raster-style adapters exist, but no reusable DEA STAC or production trajectory implementation exists.

## 10. Completion and sequencing ruling

D13 authorizes implementation planning and execution in the following order:

1. Batch C.
2. Batch D.
3. Batch E.
4. Batch F.
5. Batch G.

The Tier 0 public-RC lane may proceed at any point after its Tier 0 inputs are available. Its success does not reorder or authorize later batches. Batch H remains conditional under D4 and is not detailed here.

Each batch closes only with its named checkpoint, manifest reconciliation, targeted tests, and full quality battery. A failed conditional publication gate is recorded as failed; it is never relaxed, silently bypassed, or converted into a compliance or performance conclusion.
---

Editorial note (orchestrator, not director): this ruling was produced by a
detached codex CLI consultation executing D12 item 4, launched 2026-08-16
from the jarrah-line session with the monitor repo as read-only working
directory; the prompt and raw answer are preserved outside the repo in the
session scratchpad. The body above is verbatim except this note. Orchestrator
review performed before recording: no absolute local paths, usernames, or
home directories appear (D9 item 4 scan clean); every existing file the
ruling cites was verified present in the tree; the ruling stays within
D1–D12 as it states. One reading hazard, flagged not amended: the ordered
task identifiers inside Batch D (D1–D6) collide with the director ruling
numbers D1–D12 — a Batch D task citation should always be written as
"Batch D task D4", never bare "D4".
