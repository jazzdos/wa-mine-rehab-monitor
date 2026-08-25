# Batch E entry: the forced-threshold eligibility path (2026-08-25)

**Trigger.** Batch D closed with `criteria_passed=false`
(`checkpoints/batch-d-result.md`, commit `ea9c2cd`): `spearman_median`
< 0.95 in 25 criterion cells at every candidate support, forced-144
fallback per design doc §8 D3. Downstream,
`register.assign_trajectory_eligibility` rule 3 stamps every one of the
10,910 judged sites `threshold_not_computed` when `criteria_passed` is
false (`register.py:1337`), forces `d3_eligible=False`, and never
evaluates the eligible/insufficient split. Batch E extraction selects on
`trajectory_status == "eligible"`, so with `eligible` empty by
construction the E4 lane extracts nothing. Review finding F1
(`docs/reviews/2026-08-25-batch-e-findings.md`) established that
reopening the lane is a code change, not only a decision, and that no
`forced_threshold` path exists anywhere in src or tests.

**Options considered.** (a) Flip `criteria_passed` to true — forbidden:
that is the post-hoc relaxation the §8 freeze rule ("never relaxed after
seeing results") exists to prevent, and it would falsify the recorded
Batch D result. (b) Hold Batch E; the project record closes at Batch D
with the Tier 1 lane never run. (c) Implement the forced-threshold
eligibility path specified as Task 0 (BLOCKING) of
`docs/plans/2026-08-22-batch-e-e4-e5.md`: a
`forced_threshold: bool = False` argument on
`assign_trajectory_eligibility`, a `d3_forced_threshold` disclosure
column on `D3_ELIGIBILITY_COLUMNS` and `REGISTER_SCHEMA`, and
`--forced-threshold` / `--decision-record` flags on
`apply-d3-threshold`, with `criteria_passed` staying false in the run
manifest.

**Decision.** (c), authorised by the owner 2026-08-25. The basis is that
design doc §8 D3 already pre-registers the fallback: "If nothing through
144 passes, use 144 and label the failed criteria — never relaxed after
seeing results." The D3 threshold selection honoured the first half
(forced-144, labelled, `criteria_passed=false`); rule 3's hard stop at
the eligibility layer goes beyond the pre-registration, which says use
144 and label, not refuse to stamp eligibility. This decision therefore
implements the pre-registered fallback at the eligibility layer; it
relaxes no criterion and changes no recorded result. The L4 disclosure
(`docs/amendments-and-limitations.md`) must travel on every row via
`d3_forced_threshold=true`, into the Batch E checkpoint, the release
manifest, and any rendered surface.

**Consequence.** Task 0 proceeds. At n\* = 144 px the judged population
splits 10,372 `eligible` / 538 `insufficient_pixel_support` over 989
distinct footprints (review F2; regional eligible counts Pilbara 1,115
and Goldfields-Esperance 5,449, so D4's ≥30 Tier 2 gate passes). The
run manifest keeps `criteria_passed=false`; this decision record is the
artefact `--decision-record` points at. Closes open item **O2**.
