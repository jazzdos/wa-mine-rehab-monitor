# Context for the Batch G QGIS plan review

Owner decisions (chat, 2026-08-30, fixed): Batch G closes QGIS-only —
NO new release packages in `release.PACKAGES`; public export of Tier 1
deferred, D7/site_id question deferred with it. Context folded into the
per-site summary. Output format GeoPackage (not GeoParquet). Approach:
disciplined CLI command + hand-authored QML with drift-guard tests.

On disk, verified this session:
- `export-release` (cli.py:4161), `release.PACKAGES` (one package),
  `export_gate.export_public` — all live and test-pinned; NOT touched
  by this plan.
- Gate pattern to mirror: `build-context-join` (cli.py:8607-8906);
  acceptance verdict at curated/trajectories-acceptance/<date>/
  acceptance.json with extraction_summary_sha256 + parts_digest.
- Eligible register: curated/register/<date>/register.parquet,
  ELIGIBLE_REGISTER_SCHEMA (register.py:1174), trajectory_status has 5
  categories; lon/lat EPSG:4326 floats; no geometry column.
- Trajectories: TRAJECTORY_SCHEMA (trajectories.py:42), WKB EPSG:3577,
  METRICS = (nbr, ndmi, bare_soil, photosynthetic_vegetation,
  non_photosynthetic_vegetation); rows carry shared_footprint_site_count
  and d3_forced_threshold.
- Context join: CONTEXT_JOIN_SCHEMA (context_join.py:68), three-state
  fire vocabulary, FORBIDDEN_NAME_FRAGMENTS.
- Test seeders: tests/test_trajectory_qa.py (_seed_register etc.),
  tests/test_context_join.py (_seed_full_world); cross-file imports
  from tests.* are the established pattern.
- pyogrio 0.13.0 + geopandas 1.1.4 importable in the worktree venv.
- Known-missing: no qgis/ directory, no trajectory-summary artifact.
