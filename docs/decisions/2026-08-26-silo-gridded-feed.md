# SILO gridded feed: anonymous route, in-repo ingestion (2026-08-26)

**Trigger.** Batch F task F5 (D13 §5) needs a SILO rainfall feed to
build climate context beside spectral trajectories. Open item O7
recorded that no SILO account or snapshot existed on either data root.

## Owner decisions

1. **Ingestion lives in this repo** (`sources/silo.py` + CLI), not in
   env-health. Investigated first: env-health holds no SILO data, no
   adapter, and no credential — SILO was evaluated there and killed
   ("REDUNDANT-KILL",
   `env-health/dataplatform/research/envhealth-finding_silo-agcd-rainfall.md`)
   in favour of ERA5-Land, whose AOI is not statewide. Feeding from
   env-health's lake would also conflict with the recorded D13 ruling
   declining dataplatform schema/CRS/storage.
2. **Product: SILO gridded, anonymous route.** The gridded product is
   an AWS open-data bucket (`s3://silo-open-data`, ap-southeast-2), CC
   BY 4.0, no account. O7 closes on this fact, not on registration: the
   account-gated framing in the prior `licence.py` entry applied to the
   point/Data-Drill API, which this project does not use.
3. **Storage: `<data_root>/raw/silo/<date>/`** — the existing
   per-project convention (`snapshots.py` already names SILO). A
   machine-level per-source store was considered and declined: `~/data`
   is organised per-project, and cross-project sharing already works by
   explicit dated path into another project's root (the Maus v2
   precedent). Other projects may read this snapshot the same way.
4. **Fetch approach: whole annual NetCDF files.**
   `Official/annual/daily_rain/<year>.daily_rain.nc`, one object per
   year, byte-verifiable, per-year resumable, re-derivable offline.
   Streaming range-reads (no local copy) and daily GeoTIFFs were
   declined: weaker provenance / far more objects respectively.
5. **No download without explicit owner approval.** The owner is on a
   metered connection. `fetch-silo` is owner-run only; no test,
   implementation step, or CI path touches the network.

## Centroid-cell method

Each Maus footprint maps to the 0.05° (~5 km) SILO grid cell containing
its **centroid**, not an area-weighted blend across cells. Maus
footprints are nearly all far smaller than a 5 km cell, so a
centroid-cell lookup and an area-weighted mean would agree for the
overwhelming majority of sites; the simpler method is used and the
choice is recorded here rather than silently assumed. The centroid is
taken in EPSG:3577 (equal-area, matching the footprint CRS) and
reprojected to the grid's geographic CRS for the cell lookup. The
chosen cell is recorded on every output row as `silo_cell_id`, so a
reader can check which cell fed which site without re-deriving the
lookup.

## D13 F5 deviation

D13 F5 planned `secrets.py` changes and credential-redaction tests on
the assumption SILO access is account-gated. The gridded route is
anonymous: there is no credential, so those items are objectless and
are dropped from this batch. The point/Data-Drill API remains unused.
If a future need for it arises, the credential machinery returns with
it.

Carried with the same reasoning: `http.py`'s module docstring, which
referenced a SILO API key in describing why exhausted-attempt error
messages strip query strings, has been corrected — the redaction is
unconditional because the client is shared across sources that do
carry a credential, not because SILO itself has one.

## Fail-closed data semantics

- `rain_days_ge_1mm` counts days where the daily value is `>= 1.0 mm`
  (inclusive at the threshold).
- A missing daily value makes that cell-year `not_computable`; it is
  never treated as zero rainfall.
- An incomplete 1991–2020 baseline (any missing year) refuses rather
  than producing a mean over a shorter window.
- A snapshot missing a requested trajectory year is refused at
  validation.
- A grid that does not cover WA is refused at validation.

## Consequence

Closes open item **O7** in `docs/amendments-and-limitations.md`: the
gridded product is anonymous, so no account or credential is needed on
this route.
