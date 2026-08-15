# D6–D8: DASC acquisition route, MINEDEX licence adjudication, owners field

Date: 2026-08-16. Decision authority: delegated director (codex CLI ruling,
detached run, transcript preserved in the session scratchpad; rulings
reproduced verbatim in §4 below). Context: the Tier 0 live acceptance run
(Task 11) found the pinned SLIP direct-download URLs auth-gated, and the
replacement route surfaced new licence evidence.

## 1. Measured facts the rulings rest on (2026-08-16, direct download)

- `https://data-downloads.slip.wa.gov.au/DMIRS-003/Geopackage` and
  `.../DMIRS-001/Geopackage` return a Landgate SSO login page
  (`sso.slip.wa.gov.au`), not data. The project's validation gate refused the
  HTML payload; no snapshot was finalized.
- DMIRS DASC (`dasc.dmirs.wa.gov.au`) serves statewide GDA2020 product
  bundles unauthenticated at `/Download/File/<id>`, discovered via
  `/Home/GetHierarchy?productAlias=<alias>&parentFolderId=729`:
  - MINEDEX (updated 2026-08-14): id 3978 ESRI Shapefile zip (Minedex.shp,
    50,164 point sites, EPSG:7844); id 3981 CSV database zip (Sites.csv
    50,164 rows, ProjectsOwners.csv 8,376 rows, SiteTenements.csv,
    SiteProduction.csv, ResourceEstimates.csv, others); ids 3979/3980/3982
    TAB/FGDB/KML.
  - Tenements Current (updated 2026-08-14): id 2056 ESRI Shapefile zip
    (CurrentTenements.shp, 30,456 features, EPSG:7844, HOLDER1..9,
    TENSTATUS, FMT_TENID); ids 2053/2054/2055/2057/2058 in other formats.
- Every DASC bundle contains `Licence_CCBY4.pdf` (file dated 2025-12-23),
  operative text verbatim: "© State of Western Australia (Department of
  Mines, Petroleum and Exploration) 2026. With the exception of the Western
  Australia's Coat of Arms of State and other logos, and where otherwise
  noted, these data are provided under a Creative Commons Attribution 4.0
  International Licence. http://creativecommons.org/licenses/by/4.0/legalcode"
- Data WA CKAN `package_show` for `minedex-dmirs-001` (read 2026-08-16)
  still says `license_id: cc-nc`, "Creative Commons Attribution
  Non-Commercial 4.0"; for `mining-tenements-dmirs-003` it says `cc-by`.
- SLIP Public Services ArcGIS REST layers (MINEDEX layer 0, Mining
  Tenements layer 3) are public with pagination (maxRecordCount 10,000) but
  carry truncated attribute sets and, for MINEDEX, no owner/operator field.
- No MINEDEX spatial product (SHP/FGDB/KML/REST) carries an operator or
  owner column. `ProjectsOwners.csv` maps ProjectCode → OwnerName with
  HoldingPct/StartDate/EndDate; in the 2026-08-14 extract all 8,376 rows
  have an empty EndDate (current owners only). 4,145 of 4,922 projects in
  Sites.csv have a current owner; 5,822 of 50,164 sites carry no
  ProjectCode; 353 sites have null Latitude/Longitude.
- Sites.csv stage counts reconcile against the total: Shut 20,578 +
  Undeveloped 17,717 + Proposed 4,727 + Operating 4,717 + Care and
  Maintenance 2,189 + Under Development 236 = 50,164.

## 2. Rulings (operative summary)

- **D6 — adopt DASC statewide bundles as the primary acquisition route.**
  fetch-minedex downloads ids 3978 and 3981; fetch-tenements downloads id
  2056. Each zip is preserved byte-for-byte in the dated raw snapshot;
  validation reads members directly; no capture-time conversion. The two
  MINEDEX bundles finalize atomically — a failure, inconsistent extract
  date, or incompatible ProjectCode population in either bundle leaves the
  entire snapshot unfinalized. Pin URLs and expected product identity
  (filenames, required members, CRS, schemas) — a numeric DASC id alone is
  not sufficient identity protection. ArcGIS REST is recorded as a
  documented, non-automatic fallback requiring its own acquisition mode and
  acceptance run.
- **D7 — MINEDEX public redistribution stays closed.** The bundled PDF is
  explicit evidence of a CC-BY-4.0 grant, but `contrary_notice: false`
  cannot be recorded while Data WA — an official government catalogue, not
  third-party commentary — labels the same dataset CC-BY-NC-4.0, and DASC's
  own grant is qualified by "where otherwise noted". Adjudication recorded
  as: `explicit_grant: "CC-BY-4.0"`, `contrary_notice: true`,
  `adjudicated: true`, decision "licence conflict; redistribution closed",
  `evidence_files` = the byte-identical `Licence_CCBY4.pdf` plus a captured
  Data WA metadata record, both in `SHA256SUMS.txt`. Consequently
  `minedex_redistribution_allowed` remains False and MINEDEX-derived rows
  remain excluded from public register exports.
- **D8 — `operator_at_snapshot` becomes `owners_at_snapshot` (plural).**
  Derived from current ProjectsOwners records joined through
  Sites.ProjectCode; owners are never labelled operators. Canonical
  representation `Owner A (60%); Owner B (40%)`: aggregate current owners
  before joining so one site stays one register row; sort by normalized
  OwnerName with OwnerCode tie-break; preserve stated percentages without
  inferring missing shares; render a missing holding as
  `Owner Name (holding not stated)`; null is reserved for sites with no
  resolvable current owner. The manifest counts at least: missing
  ProjectCode, unmatched ProjectCode, no current owner, multiple current
  owners, missing holding percentage, duplicate current-owner
  relationships.

## 3. Consequential project decisions taken under the rulings

- `STAGE_TO_INCLUSION` (`src/wa_mine_monitor/register.py`) is UNCHANGED by
  this batch and still carries the old fixture vocabulary (Operating / Care
  and Maintenance / Closed / Deposit / Prospect). It WILL BE updated by a
  follow-on register-rework task to the measured MINEDEX stage vocabulary
  this ruling's §1 establishes: Operating → operating; Care and Maintenance
  → care_and_maintenance; Shut → closed; Undeveloped → deposit; Proposed →
  prospect; Under Development → other. Until that task lands, the two
  measured stages that together account for ~77% of the 50,164-site
  population -- `Shut` and `Undeveloped` -- are absent from the mapping and
  fall to `other` rather than `closed`/`deposit`. `stage` is preserved
  verbatim in the register beside `inclusion_status`, so the eventual
  mapping is a recomputable view, not a data loss, and unknown stages are
  never dropped in the meantime -- they land in `other`, not silently
  excluded. `register.py`'s current build-register command is in any case
  blocked against a DASC-route snapshot until that same follow-on task lands
  (see the note below).
- **`build-register`/`build-crosswalk` are blocked against a DASC-route
  snapshot, disclosed here rather than left for an operator to discover as
  an unexplained refusal.** `cli.build_register_cmd` (unchanged this batch)
  still hardcodes `required_files=("minedex.gpkg",)`/`("tenements.gpkg",)`
  and reads `snapshot_dir/"minedex.gpkg"`/`"tenements.gpkg"` directly -- the
  filenames the superseded SLIP GeoPackage route produced. D6's snapshots
  carry `minedex_gda2020_shp.zip`/`minedex_gda2020_csv.zip`/
  `tenements_current_gda2020_shp.zip` instead, so `_verify_snapshot_or_
  refuse` refuses cleanly (structured JSON, not a crash) against any
  snapshot fetched via `fetch-tenements`/`fetch-minedex` as they stand after
  this batch. Task 11's acceptance chain (`build-register` ->
  `build-crosswalk`) cannot complete end to end until the same follow-on
  register-rework task rewires `build_register_cmd` to the DASC zip layout
  and the STAGE_TO_INCLUSION update above lands alongside it.
- The superseded SLIP direct-download URLs remain recorded in
  `docs/licensing-matrix.md` history as auth-gated (measured 2026-08-16),
  not removed silently.

## 4. Ruling text, verbatim

The codex ruling is preserved unabridged below.

### D6 — Acquisition route

**Ruling: Adopt DASC statewide bundles as the primary acquisition route.**

Approve:

- MINEDEX: DASC IDs `3978` (SHP ZIP) and `3981` (CSV database ZIP).
- Current Tenements: DASC ID `2056` (SHP ZIP).
- Preserve each ZIP byte-for-byte in the dated raw snapshot.
- Read members directly for validation; perform no capture-time conversion.
- Finalize the two MINEDEX bundles atomically. A failure, inconsistent
  extract date, or incompatible ProjectCode population in either bundle
  leaves the entire snapshot unfinalized.
- Pin the URLs and expected product identity, filenames, required members,
  CRS, extract dates, and schemas. A numeric DASC ID alone is not
  sufficient identity protection.
- Record ArcGIS REST as a documented, non-automatic fallback. Using it
  requires a separately identified acquisition mode and acceptance run
  because its schema and paging provenance differ.

Data WA directs users to DASC for "other spatial formats" while marking the
SLIP GeoPackage resources login-required. DASC states that its ZIP files
contain the dataset, metadata, and licence statement, and identifies
GDA2020 as the continuing output datum. The public REST service confirms
the alternative's 10,000-record limit and pagination support.
[MINEDEX catalogue](https://catalogue.data.wa.gov.au/dataset/minedex-dmirs-001),
[Tenements catalogue](https://catalogue.data.wa.gov.au/en/dataset/mining-tenements-dmirs-003),
[DASC](https://dasc.dmirs.wa.gov.au/),
[MINEDEX REST layer](https://public-services.slip.wa.gov.au/public/rest/services/SLIP_Public_Services/Industry_and_Mining_WFS/MapServer/0).

The exact file IDs and bundle contents were not independently exposed by
the text-accessible public pages; those particulars rely on the verified
direct downloads reported for this session.

### D7 — MINEDEX licence adjudication

**Ruling: Keep MINEDEX public redistribution closed.**

The bundled PDF constitutes explicit evidence of a CC-BY-4.0 grant, but
`contrary_notice=false` cannot be recorded while the official Data WA
record identifies the same DMIRS-001 dataset as CC-BY-NC-4.0. Data WA is an
official government catalogue that identifies the publishing department and
resource; it is not sufficiently remote from the licensor to dismiss its
resource-specific licence label as third-party commentary. DASC's own
language is qualified by "unless otherwise noted."
[Data WA presently states CC-BY-NC-4.0](https://catalogue.data.wa.gov.au/dataset/minedex-dmirs-001),
while [DASC states its general CC-BY-4.0 position with the exception](https://dasc.dmirs.wa.gov.au/).

Record the adjudication as:

- `explicit_grant: "CC-BY-4.0"`
- `contrary_notice: true`
- `adjudicated: true`
- decision/status: licence conflict; redistribution closed
- `evidence_files`: the byte-identical extracted `Licence_CCBY4.pdf` and a
  captured Data WA metadata record, both included in `SHA256SUMS.txt`

Therefore `minedex_redistribution_allowed` remains false and
MINEDEX-derived rows remain excluded from public register exports. This
follows the existing fail-closed contract in `docs/licensing-matrix.md` and
`src/wa_mine_monitor/licence.py`.

### D8 — Owner field

**Ruling: Adopt option (a), amended to `owners_at_snapshot` in the
plural.**

Replace `operator_at_snapshot` with `owners_at_snapshot`, derived from
current `ProjectsOwners.csv` records joined through
`Sites.csv.ProjectCode`. Do not label owners as operators. The public
spatial layer's published field list has `proj_code` but no owner or
operator field, while the MINEDEX data dictionary defines `ProjectsOwners`
as project ownership and says an EndDate denotes a relationship that is no
longer current.
[REST field schema](https://public-services.slip.wa.gov.au/public/rest/services/SLIP_Public_Services/Industry_and_Mining_WFS/MapServer/0),
[MINEDEX data dictionary](https://warsydprdstadasc.blob.core.windows.net/downloads/Metadata_Statements/XML/MINEDEX_Database_DataDictionary_GDA2020.pdf).

Use this canonical representation:

`Owner A (60%); Owner B (40%)`

Aggregate current owners before joining so one site remains one register
row. Sort by normalized `OwnerName`, with `OwnerCode` as the tie-breaker.
Preserve stated percentages without inferring missing shares; render a
missing value as `Owner Name (holding not stated)`. Null is reserved for
sites with no resolvable current owner.

The manifest must count at least missing ProjectCode, unmatched
ProjectCode, no current owner, multiple current owners, missing holding
percentage, and duplicate current-owner relationships. This corrects the
unsupported semantics previously documented in `register.py`.
