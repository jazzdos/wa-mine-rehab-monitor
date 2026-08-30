# Context for diff review — feature/dbca-fire-context (2026-08-29)

Decisions already fixed by the owner (do not relitigate):
- ArcGIS mirror route DECLINED permanently for v1; D13 F1 dissolved
  (docs/decisions/2026-08-29-dbca-mirror-declined.md, A10).
- F4 coverage window frozen [1937, snapshot_year - 1].
- Three-state fire_status {recorded, not_recorded, unknown};
  not_recorded is NEVER a known-negative (L18, claim boundary).
- Un-crosswalked eligible site / maus_id absent from Maus snapshot =
  integrity refusals (climate-context precedent); unknown/no_footprint
  only for empty-or-invalid footprint geometry.
- fih_fire_type normalised UPPER(TRIM()) before the {WF,PB,999}
  tripwire — the real GDA94 file carries one raw lowercase `wf`.

What is on disk: authoritative DBCA-060 package at
~/data/jarrah-rehab/raw/dbca-060/2026-07-20/ (2.1 GB gpkg, zips,
SHA256SUMS.txt covering only the zips, metadata.txt). Live staging
(fetch-dbca-fire) and build (build-fire-context) run post-merge; no
test touches the real file or the network.

Verification already done: full battery green (956 passed, ruff,
format, mypy). Focus on what tests cannot catch.
