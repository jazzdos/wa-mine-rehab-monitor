# WA Mine Rehabilitation Spectral Monitor — design

Date: 2026-08-15, revised same day per codex director review (verdict
REVISE; architecture and descriptive-claim boundary approved;
implementation authorised on the private scaffold; the eight required
revisions below are folded in and bind before anything publishes).
Jarrod delegated design decisions to codex consultation (2026-08-15);
codex's D1–D5 rulings appear in §8 as decisions taken.

## 1. What this is

A public monitor of surface change at **MINEDEX sites in the monitoring
frame** — not "every WA mine site": for each site in the frame, an
annual spectral trajectory (Fractional Cover bare/pv/npv percentiles and
geomedian-derived NBR/NDMI from Landsat via Digital Earth Australia), a
spectral disturbance chronology and a spectral revegetation chronology —
published as an open static web map with per-site pages and versioned
GeoParquet data releases.

Claim scope is fixed: **descriptive chronologies only.** Every onset is
a spectral detection labelled as one; annual data supports a **detection
year or interval, never an event date**, and no precise onset date is
ever displayed. No operational rehabilitation date, no compliance
finding, no recovery/equivalence verdict, no operator performance
statement, no operator league tables, no comparative scores, no
best/worst sorting, no unqualified red/green status styling. Site pages
carry "cause not determined" language for onsets until climate/context
covariates are shown beside them. Operator attributes are displayed only
as "operator recorded in the MINEDEX snapshot dated <D>" — never applied
retrospectively to a whole trajectory. The jarrah event-type separation
(`t_clear` / `t_rehab` / `t_spectral_onset`) carries over unchanged.

Owner decisions, fixed: remote-sensing depth and WA relevance are the
priorities; cloud/Terraform is out of scope; staging is inventory-first;
output is a static site + map + GeoParquet; home is a new public repo.
Future candidates recorded, not in scope: SW fire severity/fire-age
atlas, WA clearing monitor, wheatbelt salinity trends.

## 2. Novelty position

Prior-art audit 2026-08-15 (jarrah repo,
`docs/research/wa-statewide-mine-monitor-prior-art_2026-08-15.md`):
**not found as a whole; partially exists in components**, five
populations searched, each with a demonstrated positive control.
Acknowledged neighbours on the published site: DEA "Tracking
rehabilitation of mines" notebook (the method's direct ancestor,
credited); "From mining to recovery" (EIA Review 122, 2027; coal-only);
NSW Mine Rehabilitation Portal / SEED; QLD SLATS; Decipher/DecipherGreen
(closed commercial). Red flags on record: GA productisation, CRC
TiME/CRC-SmartSat roadmaps, the coal group extending coverage.

## 3. Population and site frame

The frame is defined by MINEDEX records, which describe mines **and
mineral deposits** (projects, prospects, operating sites,
care-and-maintenance, closed). Tier 0 publishes: explicit inclusion
statuses, duplicate/project grouping rules, the snapshot date, and
counts per category. All claims use "MINEDEX sites in the monitoring
frame".

**MINEDEX–Maus crosswalk (deterministic specification, Tier 0
deliverable).** Handles one-to-many and many-to-one matches, point
displacement, adjacent operations and shared infrastructure. Every
match row stores: match method, distance, confidence class and
manual-review status. Low-confidence matches remain Tier 0 only and
never enter the Tier 1 trajectory population.

## 4. Staging

### Tier 0 — statewide register (first release)

One row per in-frame MINEDEX record: identity (site id, name,
commodity, status, operator-as-at-snapshot), location, tenure linkage
(DMIRS-003), crosswalk result, and per-site DEA epoch-coverage counts.
Released as a versioned data release from the public repo (not Pages).
Acceptance: row count reconciles against the source's own totals;
licence fields non-null on every row; export gate proves no restricted
geometry escapes; immutable snapshot + reconciliation report produced.

### Tier 1 — statewide trajectories over a fixed 2019-observed mask

**Estimand, stated:** measurement over the Maus v2 mining-land-use
extent as interpreted for 2019 (producer accuracy for the mine class
78.9% per the validation paper). Post-2019 expansion and land
rehabilitated and omitted from the 2019 mask are **outside this
estimand**; the fixed mask creates temporal look-ahead and survivor
exposure that the site and the data dictionary state plainly.
Population: high-confidence crosswalk matches passing the D3 threshold
only; unmatched sites stay in Tier 0 with
`trajectory_status = no_usable_footprint`; tenements and buffers are
never substituted into the same trajectory population.
Output: site × year × metric GeoParquet, declared Arrow schemas, with
per-site-year sensor/product/version and GeoMAD `count` preserved.

### Tier 2 — chronology deep-dive (not a launch dependency)

Region selected AFTER Tier 1 by the pre-registered quality ranking in
§8 D4. Full custom composites via the ported jarrah engine, onset
machinery under P12-style calibration gates (known-negative strata,
recall floors, three-count diagnostics), pre-registered in this repo's
own LEARNINGS.md before launch. If neither candidate region passes the
hard gates, Tier 2 does not run.

## 5. Data layer

Probed 2026-08-15 (jarrah repo,
`docs/research/dea-probe-geomedian-fc-percentile_2026-08-15.md`),
verdict GO-WITH-CAVEATS, live-verified against the DEA STAC:

- **Annual geomedians**: `ga_ls5t_gm_cyear_3`, `ga_ls7e_gm_cyear_3`,
  `ga_ls8cls9c_gm_cyear_3` — continuous 1986–2025, all six NBART bands
  (int16, nodata −999, ÷10000) → NBR/NDMI/NDVI computable per epoch.
  Geomedian pixels are synthetic (GeoMAD); the product moves from
  per-sensor to combined Landsat 8/9 from 2022.
- **FC annual percentiles**: `ga_ls_fc_pc_cyear_3`, 1987–2025;
  bs/pv/npv × 10th/50th/90th percentiles (uint8, nodata 255).
- Access: `https://explorer.dea.ga.gov.au/stac`, public, keyless,
  anonymous S3, NOT requester-pays. CC-BY-4.0 read from each
  collection JSON's own `license` field. EPSG:3577, 96 km tiles,
  geometry-based STAC search confirmed.
- Volume (live ceiling, to be re-derived against the real Tier 0
  register before Tier 1 is sized): full-WA bbox touches 367 tiles;
  worst-case backfill ≈350 GB (FC median) to ≈2.3 TB (6-band
  geomedian); windowed S3 reads ~0.1 s/window, single-machine viable.

Caveats encoded in code, not remembered:

1. The commonly documented names (`ga_ls5t_nbart_gm_cyear_3` etc.)
   resolve HTTP 200 as **empty stubs** (0 items, null extent). The
   catalogue module pins the verified names and asserts non-zero item
   count at discovery.
2. FC percentile values exceed the documented 100 ceiling in real
   tiles (measured `pv_pc_50` 0–118). No silent clipping;
   out-of-range handling declared and counted.
3. Geomedian collections overlap (1999–2011, 2013–2021). Temporal
   quality control goes beyond a priority rule: per-site-year
   sensor/product/version and GeoMAD `count` are persisted; overlap
   years get a sensitivity run under EACH available sensor;
   sensor-transition discontinuities are tested and
   transition-adjacent breakpoints flagged.
4. Per-scene FC (`ga_ls_fc_3`) is UTM zone-native — targeted per-site
   fallback only.

Context layers:

- **DBCA-060 fire history** (on disk statewide, 4.6 GB): used to
  identify **recorded fire overlap** only. The record is incomplete
  statewide, and its own metadata says it can include burns in
  mining-rehabilitation areas (the jarrah snapshot measured zero `MR`
  records — both facts carried). Every site-year gets
  `fire_status ∈ {recorded, not_recorded, unknown}`; "not recorded" is
  NEVER a known-negative fire label, and absence from the record never
  establishes absence of fire.
- **Climate covariates** (SILO, fetcher exists): rainfall context
  displayed beside trajectories before any revegetation-onset
  interpretation appears — annual composites respond to rainfall,
  drought, flooding, dust and phenology, and fire alone is not
  sufficient context.
- Hansen GFC (CC-BY-4.0, both credit strings) as a Tier 1 cross-check;
  GLO-30 if terrain context is needed.

## 6. Source licences (measured 2026-08-15, primary records)

| Source | Measured licence | Status |
|---|---|---|
| DEA geomedian ×3 + FC percentiles | CC-BY-4.0 (collection JSON `license` field) | clean |
| DMIRS-003 Mining Tenements | CC-BY-4.0 (Data WA `license_id: cc-by`) | clean |
| **DMIRS-001 MINEDEX** | **CONFLICT**: Data WA `license_id: cc-nc` (CC-BY-NC-4.0) vs DASC blanket "unless otherwise noted" CC-BY-4.0 — and the catalogue label is such a note | **fail-closed, see rule** |
| Maus et al. v2 | CC-BY-SA-4.0 (PANGAEA) | usable; ShareAlike handled per §8 D1 |
| DBCA-060 | open (jarrah-verified) | context only |
| Hansen GFC | CC-BY-4.0 conditional on both credit strings | clean with strings |
| SILO | open with account | clean |

**MINEDEX binding rule (codex):** ingest the exact DASC download; its
bundled metadata/landing capture must EXPLICITLY place that resource
under CC-BY-4.0 with no contrary notice, and the captured evidence is
stored with the snapshot. Otherwise MINEDEX-derived public exports are
blocked (the written-clarification route is unavailable — standing
owner directive against external data-request emails), and the public
register falls back to tenements (CC-BY) + Maus (CC-BY-SA) with
MINEDEX attributes internal-only. The licence gate tests the exact
resource, never infers from the agency.

**Licensing matrix (repo deliverable):** MIT for original code; copied
jarrah code retains its compatible licence with origin-commit headers;
every data artefact carries its own licence assignment — source, exact
resource URL, snapshot date, licence version, attribution text,
transformation notice, redistribution decision. No single
repository-wide data licence. The Maus-derived Tier 1 package
(WA extract, crosswalk, trajectories over the Maus mask) publishes as a
separate package under **CC-BY-SA-4.0** with attribution, source link
and modification statement — ShareAlike applied conservatively to the
whole package, no scalar-field carve-outs asserted.

## 7. Architecture

New repo, Python package `wa_mine_monitor`, `uv`-managed; `ruff`,
`mypy`, `pytest`; one GitHub Actions test workflow (same shape as
jarrah's).

Ported verbatim (clean copies, origin-commit headers): `provenance.py`,
`secrets.py`, `reporting/manifests.py`, `reporting/export.py` (licence
gate), declared-Arrow-schema parquet writing, `s3_access.py`, quantile
convention, `fetch_proj_grids.py`, workflow watchdog. Ported with
adaptation: STAC catalogue module (new collections + stub-collection
assertion), zonal-reduction primitives, ArcGIS/SLIP paging +
reconciliation pattern, dated-snapshot raw layout, compositing engine
(Tier 2 only). Not carried: jarrah domain modules, equivalence
estimand, matched-reference machinery.

CLI subcommands, each writing artefact + run-manifest sidecar:
`fetch-minedex`, `fetch-tenements`, `fetch-maus-extract`,
`build-register` (Tier 0), `build-crosswalk`, `derive-threshold` (D3),
`extract-trajectories` (Tier 1), `build-chronologies` (Tier 2),
`export-release`, `build-site`. Warehouse: DuckDB over partitioned
GeoParquet, local. Site: static MapLibre + PMTiles + per-site pages.

Compute: MacBook Air for Tier 0; bulk windowed reads and Tier 2 on the
RTX 3070 box per machine policy; luminosity as warehouse host if volume
demands. Every public release pins immutable inputs, product versions,
a refresh policy and a visible "data current as at" date.

## 8. Decisions taken by codex (director rulings, 2026-08-15)

- **D1 footprint**: Maus v2 polygons are the SOLE Tier 1 measurement
  footprint for v1. High-confidence crosswalk matches only; no
  tenement/buffer substitution; no spectrally derived footprints in v1
  (separate calibration problem + circular-selection risk). Maus
  package publishes separately under CC-BY-SA-4.0 (§6).
- **D2 naming/publication**: repo `wa-mine-rehab-monitor`, package
  `wa_mine_monitor`, product title "WA Mine Rehabilitation Spectral
  Monitor", short label "WA Mine Rehab Monitor". Remote created
  PRIVATE immediately; goes public at the Tier 0 release candidate
  only after ALL of: MINEDEX licence resolved; licensing matrix
  committed; attribution rendering tested; licence gate tested with
  permitted AND prohibited fixtures; raw/bulk artefacts excluded;
  full-history secret scan clean; CI green; README states the claim
  boundary; immutable Tier 0 snapshot + reconciliation report; Actions
  logs reviewed (they become public with the repo).
- **D3 inclusion threshold**: derived from effective pixel support and
  trajectory stability, not area or compute budget. Pixel-centre
  rasterisation on the 30 m grid; simulate supports
  {9,16,25,36,49,64,100,144} px on large high-confidence footprints
  with deterministic spatial resampling; select smallest n* meeting:
  P90 |err| ≤0.03 (NBR, NDMI), P90 |err| ≤5 pp (FC), median full-vs-
  reduced Spearman ≥0.95, ≥90% site-years computable, criteria met in
  every adequately sampled stratum (region × commodity × shape). If
  nothing through 144 passes, use 144 and label the failed criteria —
  never relaxed after seeing results. Report 900·n* m² only as nominal
  area equivalent.
- **D4 Tier 2 region**: chosen after Tier 1 by pre-registered ranking
  over the official Pilbara and Goldfields–Esperance boundaries. Hard
  gates: ≥30 high-confidence D3-eligible sites; sufficient independent
  positive AND negative calibration cases; compute within local
  budget. Ranking: 35% median computable-year fraction, 20% P10
  computable-year fraction, 20% high-confidence crosswalk share, 15%
  inverse sensor-transition discontinuity, 10% log eligible-site
  count. Never scored: spectral change magnitude, apparent-onset
  count, operator identity, narrative value. Neither passes → Tier 2
  does not run.
- **D5 Pages publication**: Pages ships only with the accepted Tier 1
  release (Tier 0 releases via the repo + versioned data release).
  Preconditions: Tier 0+1 acceptance green; tables reconcile with site
  and map; attributions and uncertainty labels render; Pages artifact
  <800 MiB (GitHub limit 1 GB, soft 100 GB/mo bandwidth; GeoParquet
  releases stay OUTSIDE the Pages artifact); PMTiles range requests
  verified in deployed preview; mobile/keyboard/broken-link checks;
  no restricted or raw geometry in the deployment artifact.

## 9. Governance carried over

Licence gate fail-closed at the export boundary; run manifests on every
artefact; secret scrubbing; dated snapshots with SHA256SUMS; sentinel
discipline; three-count diagnostics (agreed/disagreed/not-computable);
denominators beside every rate; count reconciliation before any total
is trusted; suite passes under bare `uv run pytest -q`. Sourcing rule:
any claim about a named operator beyond what the DMIRS record itself
states requires the primary document. A LEARNINGS.md + pre-registration
guard is created in this repo before any Tier 2 calibration-gated run.

## 10. Testing and acceptance

Tier 0 per §4. Tier 1: the zonal engine is validated on the jarrah
pilot cube FIRST — it must reproduce the known Huntly trajectories
within declared tolerance before touching statewide data; per-site
epoch coverage reported as counts, never a flag alone; D3 thresholds
never relaxed post hoc. Tier 2: P12-style pre-registered gates. TDD
throughout via the build-flow chain.
