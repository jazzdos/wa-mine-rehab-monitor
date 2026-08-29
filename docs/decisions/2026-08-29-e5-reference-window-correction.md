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

**What this correction changes.** Two things, both explicit:

1. The reference file: the decided `--reference-cube` becomes
   `~/data/jarrah-rehab/probe-out/detection_estimand/series_incumbent_w3.parquet`.
   The decision's stated method ("3×3 window") and its named file
   disagreed with each other; the method is the decided substance, the
   filename was the error.
2. The pixel-count requirement: the 2026-08-25 decision kept D13 E5's
   "member and valid pixel counts match exactly", but no real jarrah
   reference variant carries count columns, so the requirement is
   unenforceable as decided. The owner either (a) authorises the
   `--no-require-pixel-counts` waiver for the official run, accepting
   that value agreement at `1e-6` over every comparable row is strong
   but indirect evidence both engines reduced the same pixels, or
   (b) directs regeneration of a counts-bearing reference from the
   jarrah repository first — the stronger option, at the cost of a
   jarrah-side change.

Unchanged and exactly what the corrected comparison implements: the
monitor's own zonal engine, 3×3 window clipped to bounds, mean over
non-NaN members, jarrah's own site points, D13 value tolerances
(`1e-6` NBR/NDMI, 0.1 pp FC), verdict artefact as the sole statewide
unlock.

**Evidence it resolves the failure.** A diagnostic run against w3
(scratch `data_root`, 2026-08-29, figures in the checkpoint): 204,060
extracted rows accounted for, 196,560 with a reference row to compare,
ZERO `value_outside_tolerance`, ZERO `computability_mismatch`.
The only residual failures are the 1,500 fully-masked site-years jarrah's
contract drops from the reference — a `compare()` harness-semantics defect
fixed separately on `fix/huntly-compare-missing-row-semantics`, not a gate
change: values, tolerances, and coverage checking are untouched.

**Also recorded.** The Batch E plan's blocker-3 claim that pixel counts
"exist on both sides" held only for the monitor's side (schema of both
reference variants verified 2026-08-29: no count columns); that is what
makes item 2 above a decision rather than a footnote.

**On authorisation.** This becomes amendment A9 in
`docs/amendments-and-limitations.md`; the owner also chooses how the
official re-run is recorded, given `validate-huntly` refuses same-date
re-runs and `curated/huntly-validation/2026-08-29/` holds run 1's failing
verdict: (a) delete that mis-specified verdict and re-run under
2026-08-29, or (b) leave it and record the official run under a later
date. `require_huntly_gate` reads the latest dated verdict either way.
