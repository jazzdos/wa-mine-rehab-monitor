# Checkpoint: Batch G QGIS-only closure

Status: live run and battery complete (2026-08-30); awaiting the
owner's interactive `.qgz` save/open confirmation. Scope per A11
(`docs/decisions/2026-08-30-batch-g-qgis-only-rescope.md`): no release
package, private QGIS project only.

## Live figures

- Build date: 2026-08-30
- GeoPackage: `curated/trajectory-summary/2026-08-30/trajectory_summary.gpkg`
  (8.5 MB) — sha256:
  `ee3b33299a11fe88f1ac3e68a66c866afaeb8ad854cac73bb9b2851cf77e0ed3`
- `n_eligible` (site_summary rows, one per eligible site): 10,372 —
  matches E4/context-join's eligible set exactly
- `n_register_sites` (register rows consumed): 50,164; the
  `register_sites` layer carries the 49,811 located sites
- `n_register_sites_unlocated` (register sites with no point geometry,
  disclosed in the run manifest, excluded from the layer): 353

## Verification battery

`uv run ruff check src tests`, `uv run ruff format --check src tests`,
`uv run mypy src scripts`, `uv run pytest -q -rs`: all green on merged
main (2026-08-30): 1237 passed, 0 failed; ruff check clean; format
clean; mypy clean (51 files).

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
