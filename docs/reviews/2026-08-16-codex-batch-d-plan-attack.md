I reviewed the supplied package inline because all source material was already present in the prompt. I did not inspect repository files.

1. **Blocker — Design decision 6 vs. Task 13, Step 3 (`_support_criteria`)**  
   The frozen statistic is contradictory. Design decision 6 defines the error criterion as the P90 across site-years of each site-year’s replicate **median** absolute error. Task 13 evaluates the P90 of `replicate_p90_abs_error`, and its note explicitly endorses P90-of-P90s. These can produce materially different `n_star` values. The plan must choose one before any spectral read and align the schema, tests, and report wording.

2. **Blocker — Task 13, Steps 1 and 3 (`evaluate_threshold`)**  
   Missing metrics can silently pass. `_support_criteria` iterates only over metrics present in `sel`; it never requires NBR, NDMI, and all three FC metrics. The supplied unit tests contain only NBR and nevertheless expect a passing threshold, contradicting D4’s requirement that every metric of both kinds pass.

3. **Blocker — Tasks 11–13, computable-fraction protocol**  
   The proposed data flow makes the computable-site-year fraction identically 1.0. `simulate_site_year` discards any year with a null anywhere in full support; every reduced sample is then a subset of an entirely valid full set, and rows are emitted for every support. Task 13’s failing-fraction test manufactures missing rows that Task 11 cannot produce. The plan needs a definition of reduced-support computability that the simulator can actually generate.

4. **Blocker — Task 12, extraction before adequacy/selection**  
   The plan reads all candidate footprints and only afterward applies adequacy and stable-hash selection. D13 D3 says to read only the large high-confidence footprints selected by the frozen protocol. The plan also creates a circular dependency: selection requires ten computable years, while computability is established through the spectral reads selection was supposed to precede. A pre-value availability/validity phase or an expressly revised protocol is required.

5. **Blocker — Task 11, Step 3 (`simulate_site_year`)**  
   Member-to-value alignment is not invariant to input ordering. `members` is converted to `sorted(set(members))`, but each `band_values` array remains in the caller’s original member order. `member_index` then indexes the arrays as though they had been reordered. Raster values will be assigned to the wrong pixels whenever the incoming member order is not already sorted; the tests use sorted input and miss this.

6. **Blocker — Tasks 8, 11, and 12, multi-tile footprints**  
   The data model and reader do not implement multi-tile support. `PixelSupport` contains one grid, while `Member` contains a tile ID; `read_member_values` accepts one dataset and ignores every member’s tile ID. A large footprint spanning tiles can therefore be truncated or have indices from another tile read against the wrong raster. The plan needs an explicit union of per-tile assignments and tile-indexed dataset reads.

7. **Blocker — Task 4, Step 4 (`load_protocol`)**  
   Several D13-frozen values are asserted only against the repository’s default YAML, not validated by the loader. A different file can freeze altered shape thresholds, adequacy thresholds, or selection limits because `load_protocol` checks none of them. Tests should mutate each frozen field and require refusal.

8. **Blocker — Tasks 4, 5, 6, 7, and 12, protocol identity**  
   The protocol digest does not bind many sample-defining algorithms: boundary tie resolution, compactness computation, stable-hash construction, seed construction, sampling algorithm, metric formulas, decoding, asset selection, or full-year rules. The freeze manifest records git state, but downstream commands do not require their git state or implementation identity to match the freeze. Code can therefore change sample definitions without changing `protocol_digest`.

9. **Blocker — Task 7 and downstream “latest protocol” gates**  
   Immutability is enforced only within one dated output path. An altered commodity map or other allowed configuration can be frozen under a later date, after results have been inspected, and then become the “latest” protocol used by a rebuilt chain. This does not enforce “No accuracy result can change sample definitions.” The plan needs a single accepted protocol lineage or an explicit supersession rule that cannot be driven by observed results.

10. **Blocker — Tasks 6, 12, and 13, statistical unit**  
    Selection is by unique `maus_id`, but simulation and threshold aggregation are by `site_id`. Batch C has 11,001 eligible sites linked to only 1,252 footprints, so the same footprint can contribute repeated identical footprint-year measurements through multiple sites. That destroys the stated “independent footprints” unit and weights heavily linked footprints more strongly in P90 and Spearman calculations.

11. **Blocker — Tasks 5, 6, and 12, footprint stratum identity**  
    Region and commodity are assigned from register sites, while selection is by footprint. One `maus_id` linked to multiple sites can therefore belong to multiple commodity or region strata and be selected multiple times. The plan does not define how a footprint receives exactly one stratum, despite adequacy being defined in independent footprints.

12. **Blocker — Task 14, adequate/inadequate stratum reconstruction**  
    Only selected footprints’ simulation rows are persisted. Recomputing adequacy from `support_inputs.parquet` can recover selected adequate strata, but it cannot recover sparse or rejected strata or their counts. `site_support.parquet` also lacks stratum and full-support-year fields. The requested `inadequate_strata` report cannot be generated from the declared inputs.

13. **Blocker — Task 12 output schema vs. D13 D3**  
    D13 requires `support_inputs.parquet` to carry input-manifest digests. The proposed schema carries only `protocol_digest`; it has no catalogue, Maus, crosswalk, region, grid-assignment, or source-manifest digest. This is a direct provenance-schema omission.

14. **Blocker — Task 15, Part B gates**  
    `apply-d3-threshold` consumes `site_support.parquet`, but its gate list does not locate and digest-verify that file or its manifest, nor require its protocol digest to match the threshold. An altered or unrelated support table could determine eligibility.

15. **Blocker — Task 13/14 output location**  
    D13 D4 requires `curated/d3-threshold/<date>/threshold.json`. Task 14 instead writes `reports/d3-threshold/<date>/threshold.json`, and Task 15 gates that reports path. Batch E is specified to consume the immutable D3 gate; changing its namespace without amending the governing decision creates an incompatible interface.

16. **Blocker — Task 15, Part A schema**  
    D13 specifies nullable `d3_threshold_px` and nullable `d3_eligible`. The plan makes both non-nullable. This is a direct schema contradiction and removes the ability to represent genuinely uncomputed eligibility in the fields themselves.

17. **Major — Task 15, Part A status ordering**  
    D13’s tests require unmatched and unusable footprints to receive `no_usable_footprint`. The plan’s first rule assigns every site outside the high-confidence population—including unmatched sites—`crosswalk_not_high_confidence`. It must distinguish unmatched from matched low/medium-confidence sites.

18. **Major — Task 15, Part B treatment of an unaccepted threshold**  
    D13 calls for refusal when the threshold is missing, altered, or “not accepted,” but the plan applies a fallback 144 threshold when `criteria_passed=false` and writes `threshold_not_computed` statuses. The governing text also requires retaining forced-144 disclosures, so the intended behavior needs an explicit ruling: refuse the command, or produce a fully ineligible register.

19. **Major — Design decision 7 vs. Tasks 12 and 15**  
    Design decision 7 says eligibility support is measured on the canonical DEA Albers grid, independent of a particular tile read. Task 15 instead reuses support computed in Task 12 from actual product tile grids and asset hrefs. The two methods can differ through tile availability, extent, or grid selection; the plan must use one authoritative eligibility assignment.

20. **Major — Task 8, Step 3 (`_validate_grid`)**  
    The validator does not require EPSG:3577. A geometry and grid both labelled EPSG:4326 pass if their affine happens to use numeric 30-unit pixels. It also checks only divisibility by 30, not alignment to the declared DEA 96,000 m lattice or consistency between `tile_id` and transform.

21. **Major — Task 8 tests**  
    D13 explicitly requires an exact 144-centre test, but none is present. The file contains ten test functions while Step 4 claims eleven tests. This leaves the full-support boundary central to D3 untested.

22. **Major — Task 2, Step 3 (`load_regions`)**  
    The module promises usable geometries but validates none of: null geometry, empty geometry, invalid geometry, non-polygon geometry, or missing CRS. It also claims to require exactly one usable name column but silently chooses the first when several candidates exist. These failures can alter region assignment without refusal.

23. **Major — Task 5, Step 3 (`assign_regions`)**  
    Every multi-polygon match is treated as an ambiguous boundary and resolved lexicographically. A point in an erroneous polygon overlap is indistinguishable from a true shared-boundary point, allowing a corrupt boundary extract to be silently classified. The loader should validate non-overlapping interiors or the assignment should refuse interior overlaps.

24. **Major — Task 6, Step 3 (`stratum_adequacy`)**  
    Only strata observed in the input frame are returned. Zero-count combinations from the frozen 3×6×3 stratum space disappear entirely, contradicting the requirement that sparse strata be reported rather than silently omitted.

25. **Major — Task 12, adequacy calculation**  
    `n_full_support_years` is not defined by collection or metric. A footprint might have ten FC years but fewer than ten valid years for one geomedian sensor, NBR, or NDMI. The plan must specify whether adequacy requires ten years for every required collection/metric, a union across collections, or another pre-registered rule.

26. **Major — Task 12, catalogue-to-raster orchestration**  
    The plan does not define how an item is chosen for a footprint tile and year, how multi-tile assets are mosaicked, how required band hrefs are matched, how duplicate items are refused, or how grids are compared across bands. “An epoch item in every required asset” is insufficient to prevent accidental cross-year, cross-tile, or cross-product joins.

27. **Major — Tasks 10–13, finite-value handling**  
    `geomedian_metrics` can produce NaN or infinity when `nir + swir` is zero. The validity mask checks only whether decoded bands are NaN, not whether derived metrics are finite. Constant annual vectors also produce NaN Spearman values. The plan provides no refusal or disclosure rule before writing non-nullable float fields and evaluating thresholds.

28. **Major — Task 13, Step 3, collection variants**  
    Threshold evaluation groups error rows only by `metric_id` and filters Spearman by metric and site, combining LS5, LS7, and LS8/9 variants. This permits a well-performing sensor variant to mask a failing one, despite D13 requiring sensor-overlap variants to remain separate. The gate needs an explicit collection-level rule.

29. **Major — Task 13, Step 3, required report content**  
    D4 requires sample counts for every support, stratum, and criterion. The proposed `criteria` entries contain values and pass flags only; no footprint-year count, replicate count, Spearman count, or computable numerator/denominator is recorded. Failure details are therefore not auditable.

30. **Major — Task 13 API**  
    The governing interface is `evaluate_threshold(inputs, protocol)`. The plan implements `evaluate_threshold(inputs, spearman, adequate_strata=...)` and hard-codes criteria independently. If the interface is intentionally revised, the decision must be amended; otherwise the implementation is against the quoted surface.

31. **Major — Task 13 statistical definitions**  
    Pandas’ default quantile interpolation and NaN-skipping median behavior are not pre-registered. With small strata, interpolation choice can change pass/fail at the threshold. Quantile method, tie behavior, finite-value requirements, and missing-correlation treatment need explicit protocol rules.

32. **Major — Task 12 and Task 15 step ordering**  
    `site_support.parquet` is introduced retroactively in Task 15 by instructing the implementer to “go back” and change Task 12 after Task 12’s tests and full battery have supposedly passed. This breaks the stated strict sequencing and TDD checkpoints. It should be designed and tested as part of Task 12 initially.

33. **Major — Task 12 fixture test**  
    The end-to-end test expects at least one selected footprint, but selection requires at least ten independent footprints with at least ten computable years each. The fixture instructions mention only making “the fixture Maus polygon” large enough; they do not require ten distinct footprints or ten annual items. The stated green expectation is unsupported.

34. **Major — Tasks 7, 12, and 14, multi-output atomicity**  
    Directly writing an artifact and then its manifest can leave an unmanifested artifact if manifest creation fails. Task 12 magnifies this across two, later three, tables with separate manifests and no completion marker or rollback. This violates the plan’s own requirement that artifact and manifest land together or fail together.

35. **Major — Task 12 spectral provenance**  
    The actual COG bytes are read live from asset hrefs, but the plan does not require content checksums, ETags, immutable asset identifiers, or a captured response identity for those bytes. A digest-verified STAC JSON proves which URL was listed, not which content was served during extraction. Reproduction can silently change if an href is replaced in place.

36. **Major — Task 16 acceptance test for value perturbation**  
    Rewriting fixture GeoTIFFs while claiming “same inputs” either alters a finalized snapshot or demonstrates that the spectral assets were never covered by its checksums. A valid test needs two independently finalized, provenance-complete input snapshots and should assert identical selections under equal null masks, not mutate an already verified source in place.

37. **Major — Task 12 refusal timing**  
    Existing-output refusal occurs after the full computation under the declared locate→verify→compute→refuse pattern. For a hundreds-of-gigabytes live operation, an accidental repeat performs the entire network extraction before reporting that the destination exists. A cheap preflight conflict check should occur before raster access, followed by the final provenance check before writing.

38. **Major — Task 16 live-run resource gate**  
    The ≥600 GB free-space check is derived from the naive window-byte estimate, while Batch C established that actual transfer is block-granular and can range toward 3.30 TB. Conversely, true streaming does not require free disk equal to transferred bytes. The plan needs an explicit caching/staging policy and a disk-space formula based on maximum retained blocks, not transfer volume.

39. **Minor — Task 4 digest test**  
    `safe_load` followed by `safe_dump(..., sort_keys=False)` does not actually reorder keys, so the test does not prove key-order independence. Construct a recursively reordered mapping before serialization.

40. **Minor — Task 5 (`shape_class`)**  
    The docstring says compactness must be in `(0, 1+eps)`, but the function accepts any positive finite number. Values materially above 1 indicate invalid geometry or computation and should be refused.

41. **Minor — Task 3 structured refusals**  
    Boundary parsing catches only `RegionExtractError` and `OSError`. GeoPackage readers can raise other data-source/driver exceptions, which would escape the required JSON refusal format. The fetch, filesystem-write, finalization, and manifest failure paths also need structured handling.

42. **Minor — Task 16 checkpoint instructions**  
    The text calls the displayed sequence a “four-command chain,” but it contains five commands. It also says the frozen protocol digest must be “committed” before extraction without specifying whether this means committing `config/d3.yaml`, recording `protocol.json`, or requiring a clean git commit whose identity matches the freeze manifest.