# SILO gridded rainfall feed — design

Date: 2026-08-26. Status: approved by owner (this session), pre-plan.
Scope: the climate half of Batch F (D13 §5 task F5) — acquisition and
derivation. Fire context (F4) and the trajectory context join (F6) are
untouched. Authority: this design defers to
`docs/decisions/2026-08-16-d13-batches-c-g-detailing.md` on schema and
claim boundary; deviations from D13 are listed at the end and go to a
decision record before implementation ships.

## Owner decisions taken in this session

1. **Ingestion lives in this repo** (`sources/silo.py` + CLI), not in
   env-health. Investigated first: env-health holds no SILO data, no
   adapter, and no credential — SILO was evaluated there and killed
   ("REDUNDANT-KILL",
   `env-health/dataplatform/research/envhealth-finding_silo-agcd-rainfall.md`)
   in favour of ERA5-Land, whose AOI is not statewide. Feeding from
   env-health's lake would also conflict with the recorded D13 ruling
   declining dataplatform schema/CRS/storage.
2. **Product: SILO gridded, anonymous route.** The gridded product is an
   AWS open-data bucket (`s3://silo-open-data`, ap-southeast-2), CC BY
   4.0, no account. O7 ("no SILO account exists") is closed by this
   fact, not by registration. The account-gated framing in the current
   `licence.py` entry applies to the point/Data-Drill API, which this
   project does not use.
3. **Storage: `<data_root>/raw/silo/<date>/`** — the existing
   per-project convention (`snapshots.py` already names SILO). A
   machine-level per-source store was considered and declined: `~/data`
   is organised per-project, and cross-project sharing already works by
   explicit dated path into another project's root (the Maus v2
   precedent). Other projects may read this snapshot the same way.
4. **Fetch approach A: whole annual NetCDF files.**
   `Official/annual/daily_rain/<year>.daily_rain.nc`, one object per
   year (~410 MB), byte-verifiable, per-year resumable, re-derivable
   offline. Streaming range-reads (no local copy) and daily GeoTIFFs
   were declined: weaker provenance / ~14,600 objects respectively.
5. **No download without explicit owner approval.** The owner is on a
   metered connection. `fetch-silo` is owner-run only; no test,
   implementation step, or CI path touches the network.

## Why daily grids

`rain_days_ge_1mm` (D13 F5 schema) needs daily values; annual totals
alone cannot produce it. The anomaly baseline is fixed 1991–2020, and
climate context must cover all trajectory years, so the fetch spans
1987 → current: ~40 files, ~16 GB one-time, `daily_rain` only. No other
variable is fetched (YAGNI; D13 names rainfall fields only).

## Components

### 1. `fetch-silo` (CLI, mirrors `fetch-tenements`)

Options: `--date` (snapshot date, caller-supplied, house rule), 
`--start-year` / `--end-year` (default 1987 → current year), `--config`,
`--dry-run`.

- `--dry-run` prints the object list and total expected bytes and exits
  without network I/O — the metered-connection guard.
- Gates, in order: `_refuse_if_snapshot_already_finalized` before any
  I/O; per-year download over anonymous HTTPS; per-file validation
  (NetCDF opens, `daily_rain` variable present, expected 0.05° grid and
  extent) before finalize; `write_snapshot_metadata`;
  `finalize_snapshot` + `verify_snapshot`.
- Per-year loop so an interrupted fetch resumes (files already present
  and valid are skipped pre-finalize; the finalized snapshot is
  immutable as usual).
- One `SourceAsset` per fetched file (uri = object URL, sha256, 
  `snapshot_date` from `--date`, licence fields from `licence.SOURCES
  ["silo"]`), recorded via `write_run_manifest` beside `SHA256SUMS.txt`.

### 2. `sources/silo.py`

- URL construction for the bucket layout; NetCDF validation.
- Grid indexing: cell containing a point; `silo_cell_id` encodes the
  cell-centre coordinates (self-describing, e.g. `-32.700_115.675`).
- Metric math per cell-year: `annual_rainfall_mm` = sum of daily rain;
  `rain_days_ge_1mm` = count(daily ≥ 1.0 mm);
  `rainfall_anomaly_mm` = annual − mean of that cell's 1991–2020
  annuals. Missing daily values propagate to
  `climate_status`/`not_computable_reason`; never zero-filled (D13
  acceptance: "No missing rainfall value becomes zero").

### 3. `build-climate-context` (CLI)

- Inputs: verified `raw/silo/<date>/` snapshot
  (`_verify_snapshot_or_refuse`), the eligible register and Maus
  footprints (curated inputs, digest-verified).
- Spatial method: each Maus footprint maps to the 0.05° cell containing
  its **centroid**. Footprints are nearly all far smaller than a 5 km
  cell; the method is recorded in the decision record and on the row via
  `silo_cell_id`.
- Output: `curated/climate-context/<date>/climate_context.parquet` with
  the D13 F5 schema (`site_id`, `maus_id`, `year`, `silo_cell_id`,
  `annual_rainfall_mm`, `rain_days_ge_1mm`, `rainfall_anomaly_mm`,
  `rainfall_baseline_start_year=1991`, `rainfall_baseline_end_year=2020`,
  `climate_status`, `not_computable_reason`, source version, snapshot
  date) + run manifest with input `SourceAsset` sha256s.
- Refusals: existing output; snapshot missing any 1991–2020 baseline
  year (fail closed — never a silently narrower baseline); snapshot
  missing a requested trajectory year.

### 4. Records

- `licence.py` SILO entry amended: `licence_id` CC BY 4.0 (gridded,
  anonymous), licence_url updated, attribution string kept,
  `redistribute_public=True` unchanged; `docs/licensing-matrix.md` row
  updated to match.
- `docs/decisions/2026-08-26-silo-gridded-feed.md`: closes O7; records
  decisions 1–5 above, centroid-cell method, and the D13 deviation
  below. `docs/amendments-and-limitations.md` O7 updated to point at it.

## Claim boundary

Unchanged and restated: climate context is displayed beside
trajectories; "cause not determined" stands until both fire and climate
context exist (F6); no causal attribution is ever generated. This
design produces context rows only.

## Error handling

House style throughout: structured JSON refusals + `typer.Exit(1)`;
fail-closed on unverifiable snapshots, missing years, malformed
NetCDF; missing data becomes `climate_status`/`not_computable_reason`,
never a default value.

## Testing

Fixture-first, zero network: tiny synthetic NetCDFs (few cells × few
days) generated inside tests. Coverage: metric math (leap years,
missing values, baseline edges), grid indexing at cell boundaries,
`--dry-run` output, every refusal path, schema conformance of the
output parquet. Battery: ruff check / format / mypy / pytest, CI order.

## Deviation from D13 (to be recorded, not silently applied)

D13 F5 planned `secrets.py` changes and credential-redaction tests on
the assumption SILO access is account-gated. The gridded route is
anonymous: there is no credential, so those items are objectless and
dropped. If a future need for the point API arises, the credential
machinery comes back with it.
