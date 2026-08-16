# Handoff — Batch C merged, next session's goal

Date: 2026-08-16
Branch state: `main` at `6fa1193`, PR #1 merged (`0fce684`), pushed.
Worktree `../wa-mine-rehab-monitor-batch-c` still exists (branch
`feature/batch-c-dea-catalogue`) — clean it up with `kit:worktree-cleanup`
when you no longer want it.

## What landed

Batch C (D13 §3), 16 tasks, all merged:

- `http.py` — bounded client: `RetryPolicy`, `get`/`get_json`/`get_text`/
  `get_bytes`, `map_concurrent` (`tolerate_errors` defaults to False).
- `source_catalogue.py` — frozen `SourceSpec`, four pinned DEA collections,
  `spec_for_collection` / `spec_for_source`.
- `sources/dea.py` — STAC validation and paged fetch.
- `dea_coverage.py` — `build_item_index`, `build_asset_index`,
  `count_site_epochs`.
- `maus_footprints.py` — per-`maus_id` footprint scalars, no geometry.
- `dea_volume.py` — pure Tier 1 estimator under a declared `WindowPolicy`.
- CLI: `fetch-dea-catalogue`, `build-maus-footprint-areas`,
  `build-dea-coverage`, `derive-dea-volume`.
- `register.ENRICHED_REGISTER_SCHEMA`, `licence.licence_for_collection`.

Verified at merge: 554 tests pass, ruff check + format clean, mypy clean
(22 source files). No test touches the network.

## The goal for the next session

**Run the deferred live Batch C capture and fill the checkpoint.** This is
Task 16 Step 5 of `docs/plans/2026-08-16-batch-c-implementation.md`, held
back deliberately because it is the batch's only live-network step. Nothing
else in Batch C is outstanding.

Chain, all with an explicit `--date` (never from the clock):

```
uv run wa-mine-monitor fetch-dea-catalogue      --config config/<cfg>.yaml --date <YYYY-MM-DD>
uv run wa-mine-monitor build-maus-footprint-areas --config config/<cfg>.yaml --date <YYYY-MM-DD>
uv run wa-mine-monitor build-dea-coverage       --config config/<cfg>.yaml --date <YYYY-MM-DD>
uv run wa-mine-monitor derive-dea-volume        --config config/<cfg>.yaml --date <YYYY-MM-DD>
```

Then fill the `_pending_` fields in `docs/checkpoints/batch-c-result.md`:
fetch date, per-collection temporal extent read from each captured
`collection.json` (NOT the fetch date), `odc:dataset_version` from the
captured items, per-collection live item counts (all must be non-zero),
and the re-derived volume figures that replace the provisional 367-tile /
350 GB / 2.3 TB planning numbers.

Two things to watch on the live run:

- The four pinned collections must all return non-zero items. A zero count
  means the collection has gone the way of the `*_nbart_gm_cyear_3` stubs
  and the pin needs re-adjudicating, not working around.
- `derive-dea-volume` refuses unless the crosswalk manifest and the
  footprint-areas manifest record the same Maus GeoPackage sha256. If it
  refuses, rebuild the footprint areas from the same Maus snapshot the
  crosswalk was built from — do not relax the check. Rationale:
  `docs/decisions/2026-08-16-batch-c-footprint-input-direction.md`.

## After that

Batch D — the D3 effective-pixel-support threshold, `docs/decisions/
2026-08-16-d13-batches-c-g-detailing.md` §4 (lines 274–483). It **requires
Batch C's accepted catalogue and real-volume inputs**, so the live run
above gates it. First task is D1: freeze the simulation protocol before
reading any spectral result — `config/d3.yaml`,
`src/wa_mine_monitor/d3_protocol.py`, `tests/test_d3_protocol.py`, with
supports `9, 16, 25, 36, 49, 64, 100, 144` over regions `pilbara`,
`goldfields_esperance`, `other_wa`.

## Pointers

| What | Where |
|---|---|
| Governing spec | `docs/decisions/2026-08-16-d13-batches-c-g-detailing.md` (Batch C §3 lines 49–272; Batch D §4 lines 274–483) |
| Batch C plan | `docs/plans/2026-08-16-batch-c-implementation.md` (Task 16 at line 4326) |
| Footprint direction | `docs/decisions/2026-08-16-batch-c-footprint-input-direction.md` |
| Checkpoint to fill | `docs/checkpoints/batch-c-result.md` |
| Merged PR | https://github.com/jazzdos/wa-mine-rehab-monitor/pull/1 |

## Open items not part of Batch C

`docs/handoffs/handoff_2026-08-16_tier0-build-and-d6-d8-rework.md` has
uncommitted edits from before the Batch C session. They are untouched.
