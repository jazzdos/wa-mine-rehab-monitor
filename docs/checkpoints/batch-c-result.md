# Batch C result — DEA catalogue, epoch coverage, volume re-derivation

Status: PENDING LIVE RUN. The fixture acceptance suite passes
(`tests/test_batch_c_acceptance.py`); the figures below are filled by the
live run and are empty until it happens.

## Live run record

- Fetch date (`--date` of `fetch-dea-catalogue`): _pending_
- Collection extent dates (temporal extent read from each captured
  `collection.json` — NOT the fetch date): _pending_
- Product version (`odc:dataset_version` from captured items): _pending_
- Per-collection live item counts (must all be non-zero): _pending_
- Snapshot verify counts (ok/bad/missing): _pending_
- Coverage disclosures (all four collections; computed + zero +
  not_computed reconciled against register rows): _pending_
- Footprint scalars: n footprints, min/median/max area, how many sites size
  at the declared floor window vs. their own footprint: _pending_
- Volume estimate: eligible sites, distinct footprints, distinct tiles and
  per-collection tile-years, windowed-read bytes (per collection),
  upper-bound bytes (per collection), scratch space, expected range requests
  (or null with its disclosure counts): _pending_
- Asset-metadata completeness: observed vs. missing `file:size`, block size
  and dtype per collection — and, for every figure that fell back to a
  declared assumption, which assumption and why: _pending_
- Provisional figures replaced (comparison): 367 tiles / 350 GB / 2.3 TB
  vs. measured: _pending_

## Gates

- The enriched register remains INTERNAL (D7 closed; manifest records
  `minedex_public_export_blocked: true`).
- The volume report selects the execution host from measured scratch-space
  need, not a fixed machine assumption — decision recorded here after the
  live run.
