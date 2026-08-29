# Huntly Zonal Validation Checkpoint — D13 E5

**Status:** _first live run executed 2026-08-29; verdict `passed: false` for
two identified non-engine reasons; official passing verdict still pending._
Every figure below is from real runs against the real jarrah data, inspected
after the fact. Nothing is extrapolated from fixture tests.

## What this gate is

Per the 2026-08-25 owner decision
(`docs/decisions/2026-08-25-e5-engine-parity-rescope.md`, amendment A7 in
`docs/amendments-and-limitations.md`), E5 is an ENGINE test, not a product
test: the monitor's own zonal engine (`huntly_validation.sample_pilot_cube`)
samples jarrah's own pilot-cube composite COGs at jarrah's own site points,
and the result is compared against jarrah's own reference series at the D13
tolerances. Same rasters, same pixels, same formula — under which a failure
is a real defect in the monitor's zonal reduction, the only thing this gate
can usefully protect. Run 1 below shows the comparison AS DECIDED did not
have that property: the decision named a reference built from different
pixels (window=1), so its failures say nothing about the engine. The
framing holds only for the corrected w3 comparison run 2 performs.

## Run 1 (curated, 2026-08-29): the run as decided, and why it failed

`validate-huntly --date 2026-08-29 --reference-cube
.../series_incumbent_w1.parquet --composites-dir .../composites --site-meta
.../site_meta.parquet --no-require-pixel-counts` — exactly the comparison
the decision record names. Verdict:
`curated/huntly-validation/2026-08-29/validation.json` (with run manifest).

- **passed:** `false`
- **n_compared:** 204,060 (1,074 sites × 38 years × 5 metrics; `ndvi` is
  outside the monitor's metric vocabulary and not compared)
- **n_reference_rows:** 38,714
- **n_failures:** 90,428 — `value_outside_tolerance`: nbr 38,707,
  ndmi 38,706, bare 362, pv 1,168, npv 972; `reference_row_missing`:
  2,098 × 5 metrics; `computability_mismatch`: 23
- **Tolerances used:** `spectral_abs=1e-6`, `fc_abs=0.1`,
  `require_pixel_counts=False` (see "Pixel counts" below)

**Root cause — not an engine defect.** jarrah's `w1`/`w3` suffixes encode
the sampling WINDOW SIZE (`scripts/probes/detection_estimand/build_base.py`:
`key = f"{position}_w{window}"`): `series_incumbent_w1.parquet` is a
single-pixel (window=1) series, not the "3×3 (9 px, 90 m) nanmean" the
decision record calls it. Verified directly on H0001/1988: the reference
row equals the centre pixel exactly on every metric, while the 3×3 nanmean
equals the monitor's extracted value. The decided comparison — window-3
sampling against a window-1 reference — cannot pass at any tolerance, and
the per-site failure magnitudes (median |Δnbr| 1.9e-2, max 2.4e-1, larger
where the window is more heterogeneous) are exactly the signature of that
window mismatch. Correction record:
`docs/decisions/2026-08-29-e5-reference-window-correction.md` (draft,
pending owner authorisation).

## Run 2 (diagnostic, scratch data_root, 2026-08-29): the intended 3×3-vs-3×3

Same command against `series_incumbent_w3.parquet` (window=3 both sides),
written to a scratch `data_root`, never to the curated tree:

- **n_compared:** 204,060 extracted rows accounted for;
  **n_reference_rows:** 39,312, so 196,560 rows had a reference row to
  compare against (the other 1,500 site-years × 5 metrics are the agreed
  no-data rows below — `n_compared` counts accounted-for rows, not
  performed value comparisons)
- **`value_outside_tolerance`: 0. `computability_mismatch`: 0.** Every row
  with a reference value agrees within `1e-6` (nbr/ndmi) and `0.1` (FC).
  The monitor's zonal reduction reproduces jarrah's to tolerance on every
  comparable row of the cube.
- **n_failures:** 7,500 — all `reference_row_missing`: 1,500 site-years
  × 5 metrics, clustered in known gap years (1993, 1999, 2001, 2013, …).
  All 1,500 were re-sampled independently and every band's 3×3 window is
  entirely NaN. jarrah's series contract DROPS a site-year row when every
  metric is NaN; the monitor emits the row as not-computable; both engines
  agree "no data". `compare()` scoring that agreement as failure is a
  harness defect, fixed on `fix/huntly-compare-missing-row-semantics`
  (a not-computable extracted row with no reference row is agreement; a
  computable one remains `reference_row_missing`).

## Pixel counts

D13 E5's exact member/valid pixel-count requirement cannot be enforced
against the real reference: neither `series_incumbent_w1.parquet` nor
`series_incumbent_w3.parquet` carries `n_member_pixels`/`n_valid_pixels`
(schema verified 2026-08-29: `site_id, year, bare, pv, npv, nbr, ndmi,
ndvi`). Both live runs therefore used `--no-require-pixel-counts`, recorded
here as the checkpoint requires. The Batch E plan's blocker-3 note claiming
counts "exist on both sides" described the monitor's sampled side only.

## Official run (authorised 2026-08-29)

The w1 → w3 correction and the pixel-count waiver were authorised by the
owner 2026-08-29 (A9,
`docs/decisions/2026-08-29-e5-reference-window-correction.md`), with run
1's mis-specified verdict deleted and the official run recorded under
2026-08-29. Figures to be recorded here from the official verdict once it
is run and inspected.

- **`require_huntly_gate` outcome for `extract-trajectories --scope
  statewide`:** to be recorded from the official verdict.
