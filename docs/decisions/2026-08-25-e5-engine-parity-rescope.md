# E5: the Huntly gate compares engine parity on the pilot cube (2026-08-25)

**Trigger.** Measurement for review finding F6
(`docs/reviews/2026-08-25-batch-e-findings.md`; reproduce with
`diag_batch_e_readiness.py --check huntly`): exactly one Maus footprint
(`a6ddd34a1d67`, effective pixel support 411,895 px, ~370 km²) covers
the jarrah Huntly plots; the jarrah reference series is a 3×3 (9 px,
90 m) `nanmean` around each plot centroid; 206 of 1,074 jarrah plots
fall inside the footprint and all 13 high-confidence MINEDEX matches
resolve to the same single footprint mean. Under the E4/E5 draft plan's
product reading — monitor footprint means from DEA compared to the
jarrah reference — D13 E5's `1e-6` NBR/NDMI gate is unpassable at any
parameter setting, and no `--site-map` can make the comparison
one-to-one. E4 statewide extraction is gated on E5 passing, so the
draft reading is a hard deadlock independent of the Batch D outcome.
D13's E5 text (`2026-08-16-d13-batches-c-g-detailing.md:635-661`) never
names the comparison target; the only concrete statement of intent is
design doc §10: "the zonal engine is validated on the jarrah pilot cube
FIRST — it must reproduce the known Huntly trajectories within declared
tolerance before touching statewide data."

**Options considered.** (a) Keep the product reading and loosen the
tolerance — rejected: no tolerance makes a 9-pixel plot mean and a
411,895-pixel footprint mean commensurable, so the gate would test
nothing. (b) Re-scope to the design-§10 reading: E5 is an engine test
on the pilot cube, not a product test against DEA. (c) Defer — leaves
E4 deadlocked and the zonal engine unvalidated.

**Decision.** (b), authorised by the owner 2026-08-25. E5 runs the
monitor's own zonal engine over jarrah's annual composite COGs
(`~/data/jarrah-rehab/interim/pilot/composites/nbart/nbart_<year>.tif`
and `.../fractional_cover/fractional_cover_<year>.tif`; EPSG:3577,
30 m, NaN-masked, bands read by description name) at jarrah's site
points, 3×3 window clipped to bounds, mean over non-NaN members, and
compares against
`~/data/jarrah-rehab/probe-out/detection_estimand/series_incumbent_w1.parquet`.
Same rasters, same pixels, same formula, so the tolerances are correct
by construction and a failure is a real defect in the monitor's zonal
reduction — the only thing this gate can usefully protect. Unchanged:
the tolerances (NBR/NDMI ≤ 1e-6, FC ≤ 0.1 pp, member and valid pixel
counts match exactly, computable/not-computable classifications match
exactly), the requirement that validation precedes any statewide
extraction, and the verdict artefact under
`curated/huntly-validation/<date>/` as the sole unlock for
`extract-trajectories` statewide mode. The plan deltas are already
written into `docs/plans/2026-08-22-batch-e-e4-e5.md` ("E5: what the
Huntly gate compares", Task 8's
`sample_pilot_cube(composites_dir, sites, *, window=3)`, `--site-map`
and `--fc-reference-scale` deleted, `--composites-dir`, `--site-meta`,
`--window` added). Path correction carried with the re-scope: the
reference repository is `~/Documents/jarrah-rehab`;
`~/Documents/jarrah-rehab-p7`, cited in an earlier draft, does not
exist.

**Consequence.** This changes what a pre-registered acceptance gate
compares and is therefore amendment **A7** in
`docs/amendments-and-limitations.md`. E5 becomes runnable immediately:
it touches no DEA data, needs no Batch D artefact, and does not wait on
the forced-threshold decision. Open item **O3**'s "gate as drafted
cannot pass" component is resolved; O3 stays open until a passing
verdict artefact exists.
