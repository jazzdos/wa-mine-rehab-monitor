# Batch B closeout — seven deferred MINOR findings, D12.3

D12.3 (`docs/decisions/2026-08-16-d9-d12-commit-remote-naming-sequencing.md`,
ruling D12 item 3: "Triage the seven deferred minor findings; close them or
record an explicit non-blocking disposition") requires each of the seven
findings deferred out of earlier review rounds to be triaged: closed with a
pinning test, or deferred with an explicit non-blocking rationale. This
closeout run (batch 0 of the workflow that produced this record) closed six
findings plus a separately-ruled D12.2 disclosure; the workflow batch that
wrote this document (batch B closeout) verified each fix against the tree
and deferred the seventh. Verified 2026-08-16 on branch `closeout/batch-b`,
working tree only (uncommitted; the orchestrator commits per D9/D12).

## Findings table

| # | Finding (verbatim-summarised) | Disposition |
|---|---|---|
| 1 | `crosswalk.build_crosswalk`'s input guards raised bare `ValueError`; the CLI's `except ValueError` around it could not distinguish a genuine input-shape refusal from an unrelated `ValueError` raised deep inside a matching pass (a pandas/shapely internal), so a real defect could be reported as a clean structured refusal. | **FIXED** in this closeout. `crosswalk.CrosswalkInputError` (a `ValueError` subclass, `src/wa_mine_monitor/crosswalk.py`) is now raised by every declared guard (`_assert_target_crs`, `_assert_required_columns`, `_assert_no_null_or_duplicate_site_ids`, `_assert_no_null_or_duplicate_maus_ids`, `_assert_every_site_has_a_usable_location`, `filter_register_for_crosswalk`'s column check); `cli.build_crosswalk_cmd` catches `crosswalk.CrosswalkInputError` specifically, not bare `ValueError`. Column-presence is checked before the CRS check (`gdf.crs` raises bare `AttributeError` on a geometry-less frame) — `build_crosswalk`'s guard order enforces this. Pinned: `tests/test_crosswalk.py::test_crosswalkinputerror_is_a_valueerror_subclass` (line 129) and the required-column tests at lines 111/121; CLI-boundary behaviour pinned by the assertion at `tests/test_crosswalk.py:906` that an unrelated exception is `not isinstance(..., CrosswalkInputError)`. |
| 2 | `crosswalk_counts` added a `row_total` key to its own returned dict after reconciling it, breaking the round-trip invariant `register.reconcile_counts(counts) == counts` for any caller handed the dict a second time (a `low`-confidence site's multiple rows made `row_total` a non-category key mixed into a category total). | **FIXED** in this closeout. `row_total` moved out of `crosswalk_counts` entirely into its own function, `crosswalk.crosswalk_row_total(df)` (`src/wa_mine_monitor/crosswalk.py`); `crosswalk_counts` now carries exactly `CONFIDENCE_LEVELS` plus `register.TOTAL_KEY`. `cli.build_crosswalk_cmd` merges the two into a terminal `disclosed_counts` dict for `crosswalk_counts.json`/manifest/stdout only, never re-fed into `reconcile_counts`. Pinned: `tests/test_crosswalk.py::test_crosswalk_counts_reconciles_directly_with_no_caller_side_filtering` (asserts `register.reconcile_counts(counts) == counts` directly and `"row_total" not in counts`) and `::test_crosswalk_row_total_is_the_row_count_kept_separate_from_confidence_counts` (line 644). |
| 3 | `n_tenements_intersecting` wrote a fabricated `0` for every coordinate-less MINEDEX site, conflating "no tenement intersects this located site" (a genuine computed zero) with "this site could not be located so the count was never computed" — a diagnostic that could not be computed reported as a diagnostic that fired. | **FIXED** in this closeout, under D12.2 (`docs/decisions/2026-08-16-d9-d12-commit-remote-naming-sequencing.md`, ruling D12 item 2). `register.build_register` now initialises the column as `pd.Series(pd.NA, ..., dtype="Int64")` (pandas nullable integer) and overwrites only located rows with a genuine computed integer, including `0`. `REGISTER_SCHEMA` (`src/wa_mine_monitor/register.py`) documents the column as nullable int64 with this exact semantic. A companion disclosure, `register.tenement_count_disclosure(df)`, reports `sites_total` / `n_sites_tenement_count_computed` / `n_sites_tenement_count_zero` / `n_sites_tenement_count_not_computed` as three-way-reconciling counts, wired into `build_register_cmd`'s manifest as `resolved_args["tenement_count_disclosure"]`. Pinned: `tests/test_register.py::test_build_register_n_tenements_intersecting_dtype_is_nullable_integer` (line 894), `::test_register_schema_declares_n_tenements_intersecting_as_nullable_int64` (line 984), `::test_tenement_count_disclosure_reconciles_and_separates_zero_from_not_computed` (line 914), `::test_tenement_count_disclosure_on_a_fully_located_register` (line 942). |
| 4 | `_containment_rows`'s spatial join used `predicate="within"`, a STRICT interior predicate: a site point sitting exactly on a Maus polygon's boundary (an edge or a vertex) fell through to the nearest-match pass instead, reporting `distance_m=0.0` under `match_method="nearest_within_2000m"`/`confidence="medium"` rather than `point_in_polygon`/`high` — `distance_m == 0.0` no longer implied containment. | **FIXED** in this closeout. `_containment_rows` (`src/wa_mine_monitor/crosswalk.py`) now uses `predicate="covered_by"` (the point-side name for the polygon-side `covers`), which is boundary-inclusive — verified directly against geopandas 1.1.4/shapely 2.1.2 per the module's own docstring. `distance_m == 0.0` now always means `point_in_polygon`/`high`. Pinned: `tests/test_crosswalk.py::test_site_inside_polygon_is_point_in_polygon_high_confidence` (line 341), `::test_site_exactly_on_polygon_boundary_edge_is_point_in_polygon_high_confidence` (line 373), `::test_site_exactly_on_polygon_boundary_vertex_is_point_in_polygon_high_confidence` (line 389). |
| 5 | `_count_intersecting_tenements` reduces its spatial-join output with `groupby(level=0)` (index LABEL, not row position); a `located_gdf` with a non-unique index silently merges the rows sharing an index value into one group, and each reports the UNION of both rows' matches as its own count rather than its own count. | **FIXED** in this closeout. `_count_intersecting_tenements` (`src/wa_mine_monitor/register.py`) now refuses up front, naming every duplicated index value, when `located_gdf.index` is not unique. `build_register` (the only CLI-reachable caller) always builds `located_gdf` off a de-duplicated `RangeIndex`, so this never fired through the CLI, but the guard closes the trap for any future caller rather than leaving it to be rediscovered. Pinned: `tests/test_register.py::test_count_intersecting_tenements_raises_on_a_non_unique_index` (line 733), `::test_count_intersecting_tenements_names_the_duplicated_index_values` (line 749). |
| 6 | Two independent copies of the same "scan a directory for `YYYY-MM-DD`-named subdirectories, return the most recent by parsed date" loop existed — `register.latest_snapshot` (over a raw snapshot parent) and `cli._latest_curated_dated_dir` (over a curated-artefact parent) — a duplication risk for a fix landing in only one copy. | **FIXED** in this closeout. The scan is now the single shared function `snapshots.latest_dated_subdir(parent)` (`src/wa_mine_monitor/snapshots.py`), returning `Path | None` and taking no position on `SHA256SUMS.txt` or what a caller should do with "nothing found". `register.latest_snapshot` and `cli._latest_curated_dated_dir` both call it and differ only in the parent path passed and the wording of the exception raised on `None`. Pinned: `tests/test_snapshots.py::test_latest_dated_subdir_returns_the_most_recent_by_parsed_date`, `::test_latest_dated_subdir_ignores_non_date_named_entries`, `::test_latest_dated_subdir_returns_none_when_parent_holds_no_dated_dir`, `::test_latest_dated_subdir_returns_none_when_parent_does_not_exist`, `::test_latest_dated_subdir_selects_a_dated_dir_with_no_sha256sums` (lines 41–70); consumer coverage unchanged and still green: `tests/test_register.py::test_latest_snapshot_*` (lines 1185–1211), `tests/test_cli.py::test_latest_curated_dated_dir_*` (lines 313–367). |
| 7 | `REGISTER_SCHEMA`'s `lon`/`lat` columns versus `export_gate.GEOMETRY_NAME_TOKENS` intent drift (the geometry name-token rule covers `easting`/`northing` point coordinates but not `lon`/`lat`); design doc §4's Tier 0 acceptance criterion "licence fields non-null on every row" is unrepresented in code; `export_gate.export_public` has no caller anywhere in the tree. | **DEFERRED**, non-blocking. See rationale below. Self-defers to Batch G (export/site) and the Tier 0 public-RC lane created by D12 item 6, because `export_public` gains its first caller there. |
| — | D12.2 companion item (not one of the seven, ruled alongside finding 3 under D12 item 2): extract-validation disclosure for current versus ended `ProjectsOwners` relationships — the real 2026-08-14 extract is current-only (every row has a blank `EndDate`), so D8's `owners_at_snapshot` "current owner" filter has had no bite, and nothing pinned or disclosed that property. | **FIXED** in this closeout. Two independent disclosures, both wired: `sources.minedex.validate_minedex_bundles` (`src/wa_mine_monitor/sources/minedex.py`) now returns `n_owner_rows_current`/`n_owner_rows_ended` at fetch time; `register.owner_row_composition(owners_df)` (`src/wa_mine_monitor/register.py`) computes the identical split directly off the `ProjectsOwners.csv` frame `build-register` already holds, returning the fixed key set `OWNER_ROW_COMPOSITION_KEYS` (`owner_rows_total`/`n_owner_rows_current`/`n_owner_rows_ended`), wired into `build_register_cmd`'s manifest as `resolved_args["owner_row_composition"]`. Both reconcile exactly against `ProjectsOwners.csv`'s own row total by construction (an `EndDate`-blank boolean partition). Pinned: `tests/test_register.py::test_owner_row_composition_splits_current_and_ended_rows` (line 439), `::test_owner_row_composition_all_current_on_the_empty_extract_shape` (line 467), `::test_owner_row_composition_requires_an_end_date_column` (line 477), `::test_build_register_cli_discloses_owner_row_composition_on_a_mixed_extract` (line 1416); `tests/sources/test_minedex.py` covers the fetch-time counterpart. |

## Finding 7's deferral rationale, exactly

Finding 7 bundles three observations, none of which is a wired code path
today, and all three sit on the same unwired boundary:

- `export_gate.py`'s own module docstring already states, in its own words,
  that `export_public` "has NO caller anywhere in this tree" and that
  "today this module enforces NOTHING" — verified by `grep -rn
  "export_public" src tests`, which returns only the definition
  (`src/wa_mine_monitor/export_gate.py:294`), its own docstring's
  self-description, and `tests/test_export_gate.py`'s direct unit calls.
  No CLI command calls it.
- `GEOMETRY_NAME_TOKENS = ("geom", "wkt", "wkb", "easting", "northing")`
  (`src/wa_mine_monitor/export_gate.py:131`) does not include `lon`/`lat`,
  while `REGISTER_SCHEMA` (`src/wa_mine_monitor/register.py`) declares both.
  This is live drift only once something calls `export_public` on a frame
  carrying `register.parquet`'s columns — which nothing does yet.
  MINEDEX row-level restriction is presently enforced by the row gate
  (`redistribute_public`), not by the geometry name-token rule, so the drift
  has no export path to bite through today.
- Design doc §4 (`docs/plans/2026-08-15-wa-mine-rehab-monitor-design.md`,
  Tier 0 acceptance line: "row count reconciles against the source's own
  totals; licence fields non-null on every row; export gate proves no
  restricted geometry escapes; immutable snapshot + reconciliation report
  produced") states a "licence fields non-null on every row" criterion that
  has no corresponding check anywhere in the tree — `grep -rn
  "licence_field" src tests` returns nothing.

All three become live only once a Batch G export command
(`export-release`, per the design doc §7 architecture and the module's own
docstring) actually calls `export_public` on a register-shaped frame. Batch
G is where D12 item 6 (`docs/decisions/2026-08-16-d9-d12-commit-remote-naming-sequencing.md`)
places the export/site work and the D5 Pages gate, and the same ruling
splits the D2 public-repository checklist out into a distinct Tier 0
public-RC lane run alongside it. Writing a fix for `GEOMETRY_NAME_TOKENS`,
a licence-fields-non-null check, or an `export_public` caller now, ahead of
that first caller existing, would be building and testing a boundary
against a frame shape nothing yet produces — the finding is real and stays
open, but it is Batch G's / the Tier 0 public-RC lane's to close, not this
closeout's, because that is where `export_public` gains its first caller
and the frame shape it must actually gate becomes concrete rather than
assumed.

## Test verification

Full battery run 2026-08-16 on `closeout/batch-b`, working tree (uncommitted):

```
cd <repo root>
uv run ruff check src tests        # All checks passed!
uv run ruff format --check src tests  # 34 files already formatted
uv run mypy src                    # Success: no issues found in 16 source files
uv run pytest -q                   # 440 passed in 4.66s
```

440 passed, up from the 407 recorded at the D9–D12 ruling point
(`docs/decisions/2026-08-16-d9-d12-commit-remote-naming-sequencing.md`) —
the 33 additional tests are the ones cited against findings 1–6 and the
D12.2 owner-composition item above; every test name cited in the findings
table above was confirmed present at the cited line and passing in this
same run, read directly rather than assumed from the diff.

D12.3 is satisfied: all seven findings are triaged (six closed with a
pinning test each, one explicitly deferred with a non-blocking rationale
naming the batch it defers to), and the D12.2 companion disclosure item is
closed alongside them.

## D12.2 rebuild of the affected artefacts (orchestrator, 2026-08-16)

Per D12.2's rebuild clause, the register and crosswalk were rebuilt from the
already-finalized local snapshots (`--date 2026-08-16`) with the closeout
code, after moving the pre-closeout outputs aside as
`curated/register/2026-08-16.pre-closeout/` and
`curated/crosswalk/2026-08-16.pre-closeout/` (preserved, not deleted; the
build's own immutability refusal fired first and named that remedy).

- Register: `reconciliation: PASS`; every previously recorded count
  identical; two new disclosure blocks in the manifest and stdout, both
  reconciling by direct arithmetic — `tenement_count_disclosure`
  (49,811 computed + 353 not_computed = 50,164; 6,114 of the computed are
  genuine zeros, now distinguishable from the 353 nulls) and
  `owner_row_composition` (8,376 current + 0 ended = 8,376, pinning the
  extract property the D8 semantics rest on).
- Crosswalk: all counts identical to the pre-closeout run — high 11,001 /
  medium 4,076 / low 4,345 / none 27,655, total 47,077. The
  boundary-inclusive containment predicate changed no real row: no site in
  this extract lies exactly on a Maus polygon boundary.
