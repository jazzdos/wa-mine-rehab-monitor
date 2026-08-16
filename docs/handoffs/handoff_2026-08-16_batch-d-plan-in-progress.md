# Handoff — Batch D plan in progress (paused mid-draft)

Date: 2026-08-16, paused on Jarrod's "pause soon" while drafting the plan.
Branch: `main`. Committed this session: `5945f0a` (Batch C checkpoint filled
from the live run), `bbae7f4` (COG block-layout verification). Nothing
pushed.

## Where things stand

1. **Batch C is fully closed.** The deferred live capture ran: catalogue
   snapshot `raw/dea_stac/2026-08-16/` (34,492 items, verify ok=180),
   footprint areas `curated/maus_footprint_areas/2026-08-16/` (1,753 rows),
   enriched register `curated/register/2026-08-17/` (dated +1 day because
   2026-08-16 holds the Batch B source register — disclosed in checkpoint),
   volume report `reports/dea-volume/2026-08-16/` (117 tiles, 597.1 GB
   windowed, 3.30 TB upper bound). All `_pending_` checkpoint fields filled:
   `docs/checkpoints/batch-c-result.md`.
2. **Execution host decided and verified**: luminosity `/mnt/data` (7.3 TB
   disk, 1.7 TB free, checked over Tailscale `jarrod@100.116.95.13`).
   MacBook Air excluded (35 GiB free). Whole-tile staging excluded
   everywhere. Jarrod offers ~1 TB more via a partition on the same 8 TB
   HDD if free space shrinks — recorded as fallback in the checkpoint.
   A venv exists at `~/venvs/cogcheck` on luminosity with rasterio 1.5.1
   (used for the block-layout check: all four collections are 3,200×3,200
   COGs, 800×800 deflate blocks, overviews 2–32).
3. **Batch D plan is MID-DRAFT** at
   `docs/plans/2026-08-16-batch-d-implementation.md`: header, 8 design
   decisions, conventions, and Tasks 1–3 (DPIRD-020 licence pin,
   `sources/wa_regions.py`, `fetch-region-boundaries` CLI) are complete.
   Tasks 4–16 remain (outline below).

## Standing instructions from Jarrod this session

- **Codex must receive a SELF-CONTAINED review artifact**: when the plan is
  done, assemble ONE document bundling the full plan + D13 §4 text + the
  Batch C live-run facts + the module-API surface (below), and give codex
  that, not file pointers.
- No bulk download on luminosity is required yet (established: the ~600 GB
  extraction is streaming reads gated on D1/D2 code that doesn't exist).

## Remaining plan tasks (4–16 outline, follow Batch C plan granularity)

- **Task 4**: `config/d3.yaml` + `d3_protocol.py` core — frozen constants
  (supports `9,16,25,36,49,64,100,144`; regions `pilbara`/
  `goldfields_esperance`/`other_wa`; commodity groups ×6; Polsby–Popper
  classes elongated `<0.20` / intermediate `0.20–<0.50` / compact `≥0.50`;
  adequacy = ≥10 footprints with ≥10 full-support computable years;
  select-30-by-stable-hash rule; 100 replicates; D4 criteria constants),
  `load_protocol` + canonical-JSON sha256 digest.
- **Task 5**: `d3_protocol` classification — `classify_commodity` (ordered
  token map, non-empty unmatched → `other`, null/empty → refusal),
  `shape_class`, `assign_region` (covered_by over DPIRD-020 in EPSG:3577;
  point in NO region → refusal, `other_wa` only for points in a non-named
  RDC region).
- **Task 6**: `d3_protocol` selection — stratum adequacy, 10–29 → all,
  ≥30 → 30 by `sha256(maus_id)` stable hash; stable under input reorder.
- **Task 7**: `freeze-d3-protocol` CLI → `curated/d3-protocol/<date>/
  protocol.json` + manifest; refuse overwrite/digest alteration;
  `build-d3-inputs` later refuses config-vs-frozen digest mismatch.
- **Task 8**: `pixel_support.py` (D2) — `GridSpec` (crs, affine 6-tuple,
  width, height, tile_id), `build_pixel_support(geometry, grid)` →
  `PixelSupport` (grid identity, member indices, effective_pixel_support_px,
  assignment digest). Pixel-CENTRE membership, 30 m grid; refuse CRS
  mismatch, shifted grid (origin not on 30 m lattice), rotated grid
  (shear terms non-zero); empty support = computed 0; missing/invalid
  geometry = not-computed (not zero). Tests: exact 9/16/144-centre
  polygons, boundary centres.
- **Task 9**: `pyproject.toml` rasterio pin + `dea_raster.py` with ONLY
  `decode_geomedian` (−999→null, ÷10,000) and `decode_fc` (255→null,
  values >100 RETAINED and counted). These are Batch E's declared rules
  (D13 lines 511–512); Batch E extends this module.
- **Task 10**: `d3_inputs.py` pure core — `sample_support(member_indices,
  n, replicate, seed_material)` deterministic AND nested (rank pixels by
  sha256 of seed_material:replicate:index; prefix property gives nesting);
  no repeated pixel; refuse full support <144. Metric computation from
  decoded arrays per design decision 5; replicate aggregation per decision
  6 (median/P90 abs error per site-year-metric-support; Spearman per
  site-collection-metric-support-replicate over full-support years).
- **Task 11**: `d3_inputs.py` extraction orchestration — windowed reads via
  rasterio from asset hrefs in the catalogue snapshot item index
  (fixture tests use local GeoTIFF hrefs in tmp_path; NO network).
- **Task 12**: `build-d3-inputs` CLI → `curated/d3-inputs/<date>/
  support_inputs.parquet` + `support_spearman.parquet` + manifest. Verifies:
  frozen protocol digest matches config; enriched register is DEA-enriched;
  crosswalk tier1; footprint areas digest == crosswalk Maus digest (same
  gate as derive-dea-volume); regions snapshot verified; Maus geometry read
  in-CLI for compactness (decision 3).
- **Task 13**: `d3_threshold.py` (D4) — `evaluate_threshold(inputs,
  protocol) -> ThresholdResult`; per support × stratum: P90 abs NBR ≤0.03,
  P90 abs NDMI ≤0.03, P90 abs FC ≤5 pp, median Spearman ≥0.95, computable
  site-year fraction ≥0.90, every adequately-sampled stratum passes;
  smallest passing support wins; none → 144 with `criteria_passed=false` +
  failed list; `nominal_area_m2 = 900 * n_star`. Tests: each criterion
  fails independently; failing stratum blocks; sparse strata disclosed not
  pooled.
- **Task 14**: `derive-d3-threshold` CLI → `curated/d3-threshold/<date>/
  threshold.json` + manifest.
- **Task 15**: D5 register application — `register.py` columns
  `effective_pixel_support_px` (int64 nullable), `d3_threshold_px`,
  `d3_eligible` (bool nullable), `trajectory_status` (eligible /
  no_usable_footprint / crosswalk_not_high_confidence /
  insufficient_pixel_support / threshold_not_computed); `apply-d3-threshold
  --date` CLI; support for ALL sites from canonical Albers grid (decision
  7); status counts reconcile to register total; refusal on
  missing/altered/unaccepted threshold manifest.
- **Task 16**: `tests/test_batch_d_acceptance.py` + `docs/checkpoints/
  batch-d-result.md` skeleton + full battery + DEFERRED live run
  (fetch-region-boundaries → freeze-d3-protocol → build-d3-inputs →
  derive-d3-threshold → apply-d3-threshold, explicit --date, spectral
  reads ON LUMINOSITY per checkpoint host decision).

## Module-API facts gathered by scouts (verified against source this session)

- `register.py`: `REGISTER_SCHEMA`/`ENRICHED_REGISTER_SCHEMA` are plain
  `pa.schema` literals; `ENRICHED = REGISTER_SCHEMA + [pa.field(c,
  pa.int64(), nullable=True) for c in DEA_COVERAGE_COLUMNS]` where
  `DEA_COVERAGE_COLUMNS = tuple(dea_coverage.DEA_EPOCH_COLUMN_BY_SOURCE
  .values())`. `latest_snapshot(root, source_id)` raises
  `NoSnapshotFoundError`; `enrich_register_with_dea_coverage` refuses
  row loss/reorder/set-mismatch via `RegisterEnrichmentError(ValueError)`.
  Register `commodity` column is RAW MINEDEX `Commodities` free text.
- `tables.py`: `write_table(df, path, schema)` refuses missing/extra
  columns, reorders to schema, `read_table(path)`.
- `manifests.py`: `write_run_manifest(output, inputs, config, git_state, *,
  argv=None, resolved_args=None, timestamp=None, package_versions=None)`;
  raises FileNotFoundError if output absent, FileExistsError on differing
  provenance; no-ops (returns existing) on identical provenance.
  `MANIFEST_SUFFIX = ".run_manifest.json"`. `root_relative_path(path, *,
  config) -> (reduced, root_name)` where root_name ∈ {"data_root",
  "unrooted"}. `preflight_manifest_conflict(output, *, config, git_state,
  argv=None, package_versions=None) -> str | None`.
- `cli.py` seams: `_load_config_or_exit`, `_collect_git_state_disclosing_
  gaps(_REPO_ROOT)`, `_latest_curated_dated_dir(base, *, label)`,
  `_verify_snapshot_or_refuse(dir, *, source_id, required_files=())`,
  `_digest_verified_manifest(artefact_path)`, `_write_table_or_refuse(df,
  path, schema, *, payload=None)`, `_refuse_if_curated_output_already_
  exists(path, *, config, git_state)`, `_load_dea_items(catalogue_dir)`,
  `DateOption` (module-level typer.Option with `_validate_snapshot_date`
  callback). Every command opens: resolved = _load_config_or_exit;
  resolved_config = resolved.model_dump(mode="json"); git_state = ...;
  data_root = resolved.run.data_root. Exemplar command:
  `build-maus-footprint-areas` (cli.py:2367–2537) — locate → verify →
  compute → refuse-existing → ALL manifest ingredients → write → manifest
  (FileExistsError guard) → success JSON echo.
- `tests/test_cli.py` helpers: `_init_git_repo(tmp_path)`;
  `_write_monitor_config(tmp_path)` (plain function, returns cfg_file);
  `_seed_curated_register(data_root, date_str)`;
  `_seed_dea_catalogue_snapshot(cfg_file, catalogue_date, monkeypatch)`
  (invokes real fetch CLI with `_FakeCatalogueClient(_dea_fixture_pages())`
  monkeypatched over `wa_mine_monitor.cli.new_dea_client`);
  `_seed_derive_dea_volume_chain(tmp_path, monkeypatch)`. Standard triple:
  init git repo, monkeypatch `_REPO_ROOT` to tmp_path, write config.
  Refusals asserted as exit_code 1 + `"refusal" in result.output`.
- `crosswalk.py`: `TARGET_CRS = "EPSG:3577"`; high confidence =
  point_in_polygon; `tier1_population(df)` = confidence=="high";
  `CROSSWALK_SCHEMA` has no geometry.
- `maus_footprints.py`: `MAUS_FOOTPRINT_STATS_SCHEMA` (maus_id,
  footprint_area_m2, bbox width/height, all non-nullable);
  `derive_footprint_stats` refuses non-TARGET_CRS input;
  `join_site_footprints(high_conf_crosswalk, stats)` → 5 join columns.
- `dea_volume.py`: `WindowPolicy(pixel_metres=30, minimum_side_px=67,
  reference_buffer_metres=300, alignment_pad_px=1)`; `_window_for(span,
  policy) -> (px, "floor"|"footprint")`.
- `config.py`: `ProjectConfig(extra="allow"){run: RunConfig{data_root
  (expanduser), redistribute_public}, sources: SourcesConfig{minedex_
  public_export_blocked=True}}`; `load_config` = yaml.safe_load +
  model_validate.
- `sources/maus.py`: `_geometry_id` = sha256(wkb)[:12] over CLIPPED
  geometry; `clip_to_wa` adds `maus_id` string dtype.
- `licence.py`: `SOURCES: dict[str, SourceLicence]` frozen dataclass
  (source_id, title, source_url, licence_id, licence_url,
  attribution_text, redistribute_public, notes) — all fields required
  non-placeholder (swept by `test_every_source_has_required_fields`).
- Batch E decode rules (D13): line 511 `decode_geomedian` −999→null,
  ÷10,000; line 512 `decode_fc` 255→null, >100 retained; line 527
  out-of-range FC preserved AND counted; sensor overlaps kept as separate
  product-variant rows (lines 601, 682–686).
- "D4" in D13 line 300 = design-doc ruling D4 (docs/plans/2026-08-15-
  wa-mine-rehab-monitor-design.md §8 lines 251–260), NOT Batch D task D4.
  No boundary dataset was pinned anywhere before this session; the plan
  pins **DPIRD-020 Regional Development Commission Boundaries**
  (catalogue.data.wa.gov.au/dataset/regional-development-commission-
  boundaries, CC-BY-4.0, GDA94/EPSG:4283, GeoPackage via
  data-downloads.slip.wa.gov.au/DPIRD-020/Geopackage, WFS available;
  verified live this session). No commodity-group mapping exists anywhere;
  plan decision 2 declares it in `config/d3.yaml`.

## Resume procedure

1. Read the plan draft (`docs/plans/2026-08-16-batch-d-implementation.md`)
   — decisions + Tasks 1–3 are done; delete the trailing "DRAFT STATUS"
   section once Tasks 4–16 are written.
2. Write Tasks 4–16 per the outline above at Batch C granularity (complete
   code, failing-test-first, exact run commands).
3. Assemble the SELF-CONTAINED codex review package (plan + D13 §4 +
   Batch C live-run facts + API facts above) and run the codex plan attack
   (codex-consult skill, detached).
4. Apply amendments, then execute via kit:build-flow in a worktree
   (kit:git-worktrees), then kit:verify + kit:finish-branch.
5. Task 16's live spectral step stays deferred to a human-reviewed run on
   luminosity.
