# D3: footprints outside every RDC polygon (2026-08-21)

**Trigger.** First live `build-d3-inputs` (luminosity, Batch D live run,
Maus 2026-08-16 extract, DPIRD-020 snapshot 2026-08-21) refused:
"region is unclassified for site(s): [20 maus_ids] -- point(s) covered by
no RDC polygon". All 20 footprints are Perth-metropolitan quarries and
sand pits (Kwinana/Postans/Baldivis x9, Neerabup/Nowergup x2,
Gnangara/Bullsbrook x2, Upper Swan x2, Red Hill/Herne Hill x5), 4.8–23.9 km
outside the nearest RDC polygon (Peel or Wheatbelt). DPIRD-020 covers the
nine Regional Development Commission areas and excludes the Perth
metropolitan area by construction, so the refusal was the Batch D rule
(plan decision 1: "a point covered by NO RDC polygon is a refusal")
operating as written on a population the rule did not anticipate.

**Options considered.** (a) Exclude uncovered footprints from D3
derivation with disclosure; (b) classify uncovered onshore points as
`other_wa`; (c) add the Perth metropolitan boundary as a fourth stratum.
(b) and (c) change the frozen 2026-08-18 protocol's region semantics and
would require a new protocol lineage. (b) also violates the existing rule
that `other_wa` is a positive classification.

**Decision.** (a). `build-d3-inputs` partitions Tier-1 footprint
representative points with the same `covered_by` predicate as
`assign_regions`; uncovered footprints are removed before stratification,
carry `support_not_computed_reason = OUTSIDE_RDC_REGIONS_REASON`,
`region = null`, `selected = false` in `footprint_support.parquet`, and are
listed in the run manifest under
`region_ambiguity.footprints_outside_rdc_regions` with count
`n_footprints_outside_rdc_regions`. If more than `MAX_UNCOVERED_FRACTION`
(5%) of Tier-1 footprints with usable Maus geometry are uncovered, the
command refuses before writing anything, naming the ids, because that
pattern indicates a wrong or partial region snapshot rather than
metro-area sites. The denominator (`n_for_ceiling` in `cli.py`, `len
(footprint_geometry)`) is the Tier-1 population AFTER the missing/empty/
invalid-geometry exclusions above but BEFORE the later, stricter
`n_candidate_footprints` count (support >= 144px and >= 1 epoch year) is
derived -- the two are different populations and must not be read as
interchangeable. `assign_regions` keeps its refusal as the backstop. The
frozen protocol digest is unchanged: this is input scoping, not a
protocol parameter. An earlier draft of this record cited "20 of 1,753
Maus footprints (1.1%)" as headroom evidence against the 5% ceiling;
1,753 is the total `curated/maus_footprint_areas` count, not
`n_for_ceiling`, and the Trigger's refusal happened before
`footprint_support.parquet` was ever written, so no run has yet disclosed
the correct denominator. The live fraction against `n_for_ceiling` must
be read from a completed `build-d3-inputs` run before the 5% headroom is
treated as confirmed.

**Consequence for D3.** The D3 threshold is derived from footprints inside
the nine RDC regions only. Perth-metro footprints are not in any stratum
and receive no region-stratified threshold. Concretely: an excluded
footprint carries no `effective_pixel_support_px` in
`footprint_support.parquet` (`support_not_computed_reason =
OUTSIDE_RDC_REGIONS_REASON`), so at the register end
`register.assign_trajectory_eligibility`'s rule 1 stamps its site
`trajectory_status = no_usable_footprint` with `d3_eligible` NULL -- the
same status a site with missing/invalid Maus geometry receives, even
though the excluded site's own Maus geometry may be perfectly valid. The
two causes are not distinguishable from `trajectory_status` alone;
`footprint_support.parquet["support_not_computed_reason"]` is where a
downstream reader must look to separate "excluded as outside every RDC
polygon" from "geometry missing or invalid" (register.py's rule-1
docstring names both causes explicitly). Live count at 2026-08-16: 20
footprints excluded outside every RDC polygon.
