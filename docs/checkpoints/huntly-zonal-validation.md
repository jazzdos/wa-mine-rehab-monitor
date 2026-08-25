# Huntly Zonal Validation Checkpoint — D13 E5

**Status:** _pending_ — no live run has happened. This checkpoint records
what `validate-huntly` will report once it is run against the real jarrah
data; every figure below is `_pending_` until that run happens. No result
in this document is fabricated or extrapolated from the fixture tests.

## What this gate is

Per the 2026-08-25 owner decision
(`docs/decisions/2026-08-25-e5-engine-parity-rescope.md`, amendment A7 in
`docs/amendments-and-limitations.md`), E5 is an ENGINE test, not a product
test: the monitor's own zonal engine (`huntly_validation.sample_pilot_cube`)
samples jarrah's own pilot-cube composite COGs at jarrah's own site points,
and the result is compared against jarrah's own reference series
(`series_incumbent_w1.parquet`) at the D13 tolerances. Same rasters, same
pixels, same formula — a failure is a real defect in the monitor's zonal
reduction, the only thing this gate can usefully protect.

The command and its module (`src/wa_mine_monitor/huntly_validation.py`,
`validate-huntly` in `src/wa_mine_monitor/cli.py`) are implemented and unit-
and CLI-tested against synthetic fixtures (`tests/test_huntly_validation.py`,
the `huntly`/`statewide`-selected tests in `tests/test_cli.py`). None of
that exercises the real jarrah data — see "Blocked on" below.

## Acceptance figures — _pending_

- **passed:** _pending_
- **n_compared:** _pending_
- **n_sites:** _pending_
- **n_failures:** _pending_
- **Failure reasons and counts (`reference_row_missing` /
  `computability_mismatch` / `value_outside_tolerance`), by metric:**
  _pending_
- **Tolerances used (`spectral_abs`, `fc_abs`, `require_pixel_counts`):**
  _pending_ (the D13 defaults — `1e-6`, `0.1`, and `--require-pixel-counts`
  on — are what a first run should use; `read_reference_cube` selects only
  `HUNTLY_REFERENCE_SCHEMA`'s columns, which the real
  `series_incumbent_w1.parquet` reference is not yet confirmed to carry
  pixel-count columns for, so the on-by-default requirement may need
  `--no-require-pixel-counts` on the first real run — record whichever was
  actually used here once it happens, not before)
- **Output artefact path and manifest:** _pending_
  (`curated/huntly-validation/<date>/validation.json` plus its
  `.manifest.json`)
- **`require_huntly_gate` outcome for `extract-trajectories --scope
  statewide`:** _pending_

## Blocked on

The live run is **owner-run** against the real jarrah data, not something
this task executes:

- **Composites:** `~/data/jarrah-rehab/interim/pilot/composites`
  (`nbart/nbart_<year>.tif`, `fractional_cover/fractional_cover_<year>.tif`).
- **Reference table:**
  `~/data/jarrah-rehab/probe-out/detection_estimand/series_incumbent_w1.parquet`.
- **Reference REPOSITORY** (for the `site_meta.parquet` contract and any
  regeneration of the above): `~/Documents/jarrah-rehab` — per the
  2026-08-25 decision record's path correction,
  `~/Documents/jarrah-rehab-p7` (cited in an earlier draft) does not exist.
- **Site meta:** a `site_meta.parquet` carrying `site_id`, `x_incumbent`,
  `y_incumbent` in EPSG:3577 (`scripts/probes/detection_estimand/
  build_base.py` in the reference repository is the contract this command's
  `--site-meta` column-rename reads).

No result is filled in above until `validate-huntly` has actually been run
against these paths and its output inspected. This checkpoint stays
`_pending_` until then.
