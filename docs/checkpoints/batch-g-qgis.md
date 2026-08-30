# Checkpoint: Batch G QGIS-only closure

Status: TBD — populated only after the live `build-trajectory-summary`
run, the full verification battery, and the owner's interactive `.qgz`
save/open are confirmed. Scope per A11
(`docs/decisions/2026-08-30-batch-g-qgis-only-rescope.md`): no release
package, private QGIS project only.

## Live figures

- Build date: TBD
- GeoPackage: `curated/trajectory-summary/<date>/trajectory_summary.gpkg`
  — path: TBD; sha256: TBD
- `n_eligible` (site_summary rows, one per eligible site): TBD
  (expect 10,372, matching E4/context-join's eligible set)
- `n_register_sites` (register_sites rows, all located register sites):
  TBD
- `n_register_sites_unlocated` (register sites with no point geometry,
  disclosed in the run manifest, excluded from the layer): TBD

## Verification battery

`uv run ruff check src tests`, `uv run ruff format --check src tests`,
`uv run mypy src scripts`, `uv run pytest -q -rs`: TBD

## Owner confirmation

- `qgis/wa-mine-monitor.qgz` saved interactively in QGIS following
  `qgis/README.md`: TBD
- `.qgz` opened and layers/styles verified against the curated
  GeoPackage: TBD

## Claim boundary

Outputs are spectral detections, never compliance or performance
findings, never operational rehabilitation dates. The GeoPackage is a
private consumption artifact; it crosses no export boundary and
`export_gate` is not invoked anywhere in this batch.
