# E5: the reference series is w3, not w1 (2026-08-29) — DRAFT, pending owner authorisation

**Status: DRAFT.** This corrects what the pre-registered E5 gate compares,
so it binds only once the owner authorises it; until then the 2026-08-25
rescope decision stands as written and the gate stays failing.

**Trigger.** First live `validate-huntly` run (2026-08-29, verdict at
`curated/huntly-validation/2026-08-29/validation.json`): 38,707 of 38,714
nbr comparisons outside `1e-6`, median |Δ| 1.9e-2. Investigation
(recorded in `docs/checkpoints/huntly-zonal-validation.md`) shows the
2026-08-25 rescope decision contains a factual error: it names
`series_incumbent_w1.parquet` as "a 3×3 (9 px, 90 m) nanmean around each
plot centroid". jarrah's `w<N>` suffix is the sampling window size
(`scripts/probes/detection_estimand/build_base.py` iterates
`WINDOWS` into `key = f"{position}_w{window}"`), so `w1` is a
SINGLE-PIXEL series. Verified on live data: every metric of the w1
reference row equals the centre pixel exactly, and the w3 row equals the
monitor's 3×3 nanmean.

**What this correction changes.** One token in the decided comparison: the
default `--reference-cube` becomes
`~/data/jarrah-rehab/probe-out/detection_estimand/series_incumbent_w3.parquet`.
Everything else in the 2026-08-25 decision is untouched and is exactly what
the corrected comparison implements: the monitor's own zonal engine, 3×3
window clipped to bounds, mean over non-NaN members, jarrah's own site
points, D13 tolerances (`1e-6` NBR/NDMI, 0.1 pp FC), verdict artefact as
the sole statewide unlock. The decision's stated method ("3×3 window")
and its named file disagreed with each other; the method is the decided
substance, the filename was the error.

**Evidence it resolves the failure.** A diagnostic run against w3
(scratch `data_root`, 2026-08-29, figures in the checkpoint): 204,060
comparisons, ZERO `value_outside_tolerance`, ZERO `computability_mismatch`.
The only residual failures are the 1,500 fully-masked site-years jarrah's
contract drops from the reference — a `compare()` harness-semantics defect
fixed separately on `fix/huntly-compare-missing-row-semantics`, not a gate
change: values, tolerances, and coverage checking are untouched.

**Also recorded.** Both real reference variants carry no
`n_member_pixels`/`n_valid_pixels` columns, so the D13 exact pixel-count
requirement is unenforceable against them; live runs use
`--no-require-pixel-counts` (the CLI's documented honest-refusal default
otherwise). The Batch E plan's blocker-3 claim that counts "exist on both
sides" held only for the monitor's side.

**On authorisation.** This becomes amendment A9 in
`docs/amendments-and-limitations.md`; the owner also chooses how the
official re-run is recorded, given `validate-huntly` refuses same-date
re-runs and `curated/huntly-validation/2026-08-29/` holds run 1's failing
verdict: (a) delete that mis-specified verdict and re-run under
2026-08-29, or (b) leave it and record the official run under a later
date. `require_huntly_gate` reads the latest dated verdict either way.
