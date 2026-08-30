# Private QGIS project

The private consumption surface for the Tier 1 product (Batch G,
re-scoped: `docs/decisions/2026-08-25-public-web-page-descope.md`,
`docs/decisions/2026-08-30-batch-g-qgis-only-rescope.md`). Nothing in
this directory or the data it displays crosses the public export
boundary.

## Claim boundary

The project title and every print layout footer must carry, verbatim:

> No operational rehabilitation date, no compliance finding, no
> recovery/equivalence verdict, no operator performance statement, no
> operator league tables, no comparative scores, no best/worst sorting,
> no unqualified red/green status styling.

Styling rules that enforce it: no red/green status styling anywhere;
`trajectory_status` is a processing status, not a performance verdict;
fire/climate context is displayed beside trajectories with cause not
determined.

## Data root

Set a QGIS project variable `data_root` (Project ▸ Properties ▸
Variables) pointing at this machine's data root. Layer sources below
are written against that variable so the project is not machine-pinned.

## Layers, load order

1. **RDC boundaries** — the raw DPIRD-020 snapshot under
   `<data_root>/raw/wa_rdc_regions/<date>/` (reference outline only).
   Apply `styles/rdc_boundaries.qml` if present, else a no-fill outline.
2. **register_sites** — layer `register_sites` of
   `<data_root>/curated/trajectory-summary/<date>/trajectory_summary.gpkg`.
   Apply `styles/register_sites.qml` (categorised on
   `trajectory_status`; all five categories, colorblind-safe, no
   red/green semantics).
3. **site_summary** — layer `site_summary` of the same GeoPackage.
   Apply `styles/site_summary.qml`:
   - dashed orange outline = site judged under the forced-144
     threshold (`d3_forced_threshold`, L4 disclosure);
   - label "shared with N−1 other sites" where
     `shared_footprint_site_count > 1` (L17 disclosure);
   - sensor overlap at a metric's latest year leaves
     `<metric>_latest` NULL with `<metric>_latest_collections > 1` —
     never attribute a value to a site whose collections disagree.

`register_sites` omits sites with no register location (they cannot be
points); the omitted count is in the GeoPackage's run manifest
(`n_register_sites_unlocated`). `d3_forced_threshold` on that layer is
1/0/NULL — NULL means the site was never judged.

## Saving the project

Save as `qgis/wa-mine-monitor.qgz` (QGIS ≥ 3.34) after: setting the
`data_root` variable, loading the three layers, applying the styles,
and pasting the claim-boundary sentence into the project title and any
layout footer.

## Refresh

New curated date → re-run `wa-mine-monitor build-trajectory-summary`,
then re-point the two GeoPackage layers at the new dated directory.
The gpkg is immutable per date; never edit one in place.
