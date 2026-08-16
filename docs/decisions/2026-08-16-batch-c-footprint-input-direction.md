# Batch C direction — where the per-site Maus footprint input comes from

Date: 2026-08-16
Status: ACCEPTED (codex consult, accepted per session instruction)
Governs: `docs/plans/2026-08-16-batch-c-implementation.md` Tasks 12–15
Basis: D13 §3 C5 (`docs/decisions/2026-08-16-d13-batches-c-g-detailing.md`)

## The question

D13 C5 names **Maus footprints** among the Tier 1 volume estimator's inputs,
and the plan's own pre-build amendment (Finding 1a) requires each site's read
window to be sized from its footprint rather than the fixed 2,010 m constant
the draft used — that constant contradicted the plan's C3 acceptance note
that the MINEDEX point never substitutes for a Tier 1 footprint.

The repo carries no area column anywhere: not in `CROSSWALK_SCHEMA`, not in
`REGISTER_SCHEMA`, not in `sources/maus.py`. Maus is CC-BY-SA, so the
geometry may not enter the repo or any published artefact — only derived
scalars. The scalars therefore had to come from somewhere new.

## Options considered

- **A.** `derive-dea-volume` reads the latest `raw/maus_v2/<date>/` snapshot
  at run time, computes areas in Albers, joins via the crosswalk.
- **B.** Add `maus_footprint_area_m2` to `CROSSWALK_SCHEMA` in Batch B.
- **C.** A separate immutable curated artefact of footprint scalars, with its
  own run manifest, consumed digest-verified by `derive-dea-volume`.

## Decision

**C.** A new module `maus_footprints.py`, a new command
`build-maus-footprint-areas --config --date`, and a new artefact
`curated/maus_footprint_areas/<date>/footprint_areas.parquet` carrying
`maus_id`, `footprint_area_m2`, `footprint_bbox_width_m`,
`footprint_bbox_height_m` — and no geometry.

Rejecting A: `maus_id` is derived from clipped geometry
(`sources/maus.py::_geometry_id`), so "the latest Maus snapshot" at volume
time can carry different ids and different areas than the snapshot the
crosswalk was built from. The join would still succeed on ids that no longer
mean the same polygon. `derive-dea-volume` therefore refuses unless the
crosswalk manifest and the footprint manifest record the SAME Maus
GeoPackage sha256 — a check that is only possible because the scalars have
their own manifest.

Rejecting B: it reopens an accepted Batch B schema, duplicates polygon
properties across match rows, and couples a Batch C estimator input to a
Batch B matching artefact.

Licence: the artefact stays in the Maus CC-BY-SA-4.0 lineage
(`licence.SOURCES["maus_v2"]`); its manifest records
`output_licence="CC-BY-SA-4.0"` and `output_share_alike=true`.

## Window-sizing rule

Area alone cannot size a window — a 9,000 × 1,000 m strip and a 3,000 ×
3,000 m square have equal area and need very different reads. The artefact
therefore carries bounding-box width and height, and the window is sized
from the long span:

```
span_m       = max(footprint_bbox_width_m, footprint_bbox_height_m)
side_px      = max(minimum_side_px, ceil((span_m + 2 * reference_buffer_metres) / pixel_metres) + alignment_pad_px)
window_side_m = pixel_metres * side_px
```

with `WindowPolicy(pixel_metres=30, minimum_side_px=67,
reference_buffer_metres=300, alignment_pad_px=1)`. The old fixed 2,010 m
window survives only as `minimum_side_px` — a floor, not the answer. The
extra pixel covers arbitrary raster-grid alignment. `sqrt(area)` is not used:
it under-covers exactly the elongated footprints that matter most.

## Band/metric selection

An explicit frozen input, not hard-coded band counts:

```python
CollectionSelection(source_id, metric_ids, asset_keys,
                    assumed_bytes_per_pixel, assumed_tile_pixels_per_side)
```

`collection_id` resolves through `SourceSpec`; `asset_keys` must be a
non-empty, duplicate-free subset of `SourceSpec.asset_roles`. The spec says
what every item must CARRY; the selection says what the run intends to
FETCH.

## Asset-metadata discipline

Captured item assets are normalised into a nullable-typed `asset_index`
(`file_size_bytes`, `raster_*_px`, `block_*_px`, `data_type`,
`bytes_per_sample`, `metadata_source`). Nothing is defaulted:

- Missing block dimensions → `expected_range_requests` is **null**, never an
  implicit 4 per window-band and never 512-pixel blocks.
- Missing dtype → `bytes_per_sample` null; bytes-per-pixel falls back to the
  caller's DECLARED assumption and is labelled
  `bytes_per_pixel_source="assumed"`.
- Every absence is counted per collection and reported in the estimate and
  its manifest.

## Upper bound

Priced PER COLLECTION — distinct `(collection, tile_id, year)` at that
collection's own band count and bytes-per-pixel. Collapsing all collections
to `(tile_id, year)` at geomedian pricing silently drops FC and the
overlapping sensors from the bound.

## Consequence for the plan

Two tasks added (12: the module; 13: the CLI), the estimator task rewritten
(14), and `derive-dea-volume` extended with the footprint input and the
Maus-digest equality refusal (15). Task numbering shifted accordingly; the
execution-order section records the new dependency graph.
