# Batch D — D3 Effective-Pixel-Support Threshold Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Derive the pre-registered D3 effective-pixel-support threshold `n*`
(D13 §4, tasks D1–D6): freeze a simulation protocol before any spectral value
is read, build EPSG:3577 pixel-support primitives, run a deterministic
reduced-support simulation over large high-confidence footprints, evaluate the
never-relaxed criteria, and stamp eligibility onto the internal register.

**Architecture:** Five new pure modules (`d3_protocol`, `pixel_support`,
`dea_raster`, `d3_inputs`, `d3_threshold`) feed five new CLI commands
(`fetch-region-boundaries`, `freeze-d3-protocol`, `build-d3-inputs`,
`derive-d3-threshold`, `apply-d3-threshold`), each following the
locate → verify → compute → refuse-existing-output → manifest-ingredients-
before-write → write pattern `build-maus-footprint-areas` established. The
protocol is frozen as its own digest-verifiable curated artefact BEFORE the
inputs builder may run; the inputs builder refuses a digest mismatch. The one
live-network step (spectral windowed reads on the real data root) is deferred
exactly as Batch C's Task 16 Step 5 was, and runs on luminosity per the
Batch C checkpoint host decision.

**Tech Stack:** Python 3.12, pandas/geopandas/pyarrow (existing), shapely
(existing, via geopandas), **rasterio (new pinned dependency — the D2
"raster-window" dependency)**, typer CLI, pytest. No test touches the network.

---

## Design decisions (pre-registered; the module is authoritative over quotes)

1. **Region boundaries pinned to DPIRD-020.** "The official Pilbara and
   Goldfields–Esperance boundaries" (design doc §8, ruling D4) are pinned to
   the data.wa.gov.au dataset **Regional Development Commission Boundaries
   (DPIRD-020)** — the nine regions defined under the Regional Development
   Commissions Act 1993, publisher DPIRD via SLIP, licence **CC-BY-4.0**,
   source CRS GDA94 (EPSG:4283). Snapshot lands at
   `raw/wa_rdc_regions/<date>/regions.gpkg` via a new `fetch-region-boundaries`
   command. Region strata: `pilbara` = the Pilbara RDC polygon,
   `goldfields_esperance` = the Goldfields-Esperance RDC polygon, `other_wa` =
   covered by any OTHER RDC polygon. A register point covered by NO RDC
   polygon is a refusal (unclassified), not `other_wa` — `other_wa` is a
   positive classification, never a fallback for a point the boundaries do
   not explain. Amended 2026-08-21: such footprints are excluded from D3
   derivation with disclosure (`n_footprints_outside_rdc_regions`), bounded by
   a 5% ceiling; see docs/decisions/2026-08-21-d3-outside-rdc-exclusion.md.
2. **Commodity grouping is a declared token map, not inference.**
   `config/d3.yaml` carries an ordered, case-insensitive token→group map from
   raw MINEDEX `commodity` free text to the six D13 groups. First matching
   rule wins; a non-empty commodity string matching no rule is `other`
   (a positive catch-all classification); a null/empty/whitespace commodity
   is a refusal (unclassified). The map is part of the frozen protocol digest.
3. **Shape class comes from Maus geometry read in the CLI, not a new column
   on Batch C's artefact.** `maus_footprint_areas` is immutable and carries
   no perimeter. Polsby–Popper compactness (`4πA/P²`) is computed from
   `wa_extract.gpkg` geometry inside `build-d3-inputs` (the same
   read-geometry-in-the-CLI seam `build-maus-footprint-areas` uses), in
   `crosswalk.TARGET_CRS`; only the scalar compactness and its class are
   persisted. Geometry never enters a curated Batch D artefact.
4. **`dea_raster.py` is created in Batch D with only the two decode rules
   Batch E declares** (D13 lines 511–512): `decode_geomedian` maps −999→null
   and divides by 10,000; `decode_fc` maps 255→null and RETAINS values above
   100 (counted, never clipped). Batch E extends this module; Batch D does
   not fork or duplicate the rules.
5. **Metric set and formulas (pre-registered).** Per collection selections
   mirror `dea_volume` metric ids. Geomedian collections (ls5t/ls7e/ls8cls9c):
   `nbr = (nir − swir2)/(nir + swir2)`, `ndmi = (nir − swir1)/(nir + swir1)`
   computed per pixel from decoded bands, then averaged over the support
   pixel set. FC (`ga_ls_fc_pc_cyear_3`): metrics `bare_soil`,
   `photosynthetic_vegetation`, `non_photosynthetic_vegetation` are the
   spatial means of decoded `bs_pc_50`, `pv_pc_50`, `npv_pc_50` (median-
   percentile assets; the 10/90 assets are not simulation metrics). A
   site-year metric is computable only when every contributing band pixel in
   the sampled set is non-null; nulls reduce `valid_support_px` and a
   full-support year requires `valid_support_px == effective_pixel_support_px
   ≥ 144`.
6. **Replicate persistence is bounded but exact, and the stratum statistic
   is frozen.** Raw replicate rows are not persisted as ~10⁹ scalars;
   instead `support_inputs.parquet` carries one row per **footprint**
   (`maus_id`) × year × collection × metric × support with `full_value`,
   `replicate_abs_errors` (a `list<float64>` of the 100 per-replicate
   absolute errors, sorted ascending), `n_replicates`, plus
   identity/stratum/digest columns (~10⁵–10⁶ rows, each ~800 bytes of
   list payload — bounded). `support_spearman.parquet` is one row per
   footprint × collection × metric × support × replicate. Frozen stratum
   statistics (resolving the D13 §4 "P90 absolute error" ambiguity, chosen
   BEFORE any spectral read): **P90 absolute error = `numpy.percentile`
   (`method="linear"`) over the POOLED per-replicate absolute errors across
   all of the stratum's footprint-years** (exactly recoverable from the
   persisted lists); **median Spearman = `numpy.median` over the stratum's
   spearman rows**. All statistics run on finite values only, with the
   finite/total counts recorded; an EMPTY value set fails the criterion, it
   never passes vacuously.
7. **One authoritative support measurement.** `effective_pixel_support_px`
   is computed once, in `build-d3-inputs`, from Maus geometry against the
   ACTUAL product tile grids (per-tile assignments unioned, D2 tile
   identity bound; multi-tile footprints sum distinct members across
   tiles). It is persisted per footprint in `footprint_support.parquet`
   and `apply-d3-threshold` consumes that table — it never recomputes
   support on a synthetic grid. A site's support is its linked
   high-confidence footprint's support.
8. **Live spectral capture is deferred** (Task 16 Step 6): fixture rasters
   prove the chain; the real windowed reads run on luminosity
   (`/mnt/data` scratch, per `docs/checkpoints/batch-c-result.md`) with an
   explicit `--date`, budgeted per 800×800 block, and fill the Batch D
   checkpoint.
9. **The statistical unit is the footprint, not the register site.** Batch
   C measured 11,001 eligible sites linked to only 1,252 footprints; using
   `site_id` as the unit would replicate identical footprint-year
   measurements and destroy the D13 "independent footprints" adequacy
   unit. All simulation, adequacy, selection, and threshold statistics key
   on `maus_id` with each footprint appearing exactly once. Sites re-enter
   only at `apply-d3-threshold`, where each site inherits its linked
   footprint's support.
10. **Footprint stratum identity is single-valued and pre-registered.** A
   footprint linked to several sites could otherwise claim several strata.
   Rules: region = `assign_regions` on the footprint geometry's
   `representative_point()` (geometry-based, not site-based); commodity
   group = the modal group over the footprint's linked high-confidence
   sites' classified groups, ties broken by lexicographically smallest
   group name, with tie counts disclosed in the manifest; shape class from
   the footprint's own geometry. Exactly one stratum per footprint.
11. **Two-phase extraction; no accuracy result precedes selection.**
   Phase A (validity pass) reads candidate footprints' member pixels ONLY
   to establish per-footprint-year-collection computability booleans — a
   year is full-support computable for a collection iff every member pixel
   is non-null after decode in every required band and, for geomedian
   metrics, no member has a zero metric denominator. No metric aggregate
   is computed, logged, or persisted in Phase A. Adequacy counts a
   footprint-year toward the ≥10 requirement iff FC is computable AND at
   least one geomedian collection is computable that year (sensor variants
   still evaluated separately downstream). Adequacy + stable-hash
   selection then run on those counts alone; Phase B reads values only for
   SELECTED footprints. This resolves the D13 "read only the selected
   footprints" circularity: selection depends on computability, which
   Phase A establishes without observing accuracy.
12. **Computable footprint-year fraction is a data-completeness gate.**
   Per stratum × collection: full-support-computable footprint-years ÷
   epoch-covered footprint-years among selected footprints. It is
   identical across supports by construction (every reduced sample is a
   subset of a fully valid set) and is reported at every support for the
   record; it gates the threshold's evidentiary base, not a per-support
   property.
13. **Protocol lineage is single and procedures are digest-bound.**
   `freeze-d3-protocol` refuses if ANY dated protocol snapshot already
   exists; every downstream command refuses if more than one exists.
   Superseding a frozen protocol requires human deletion recorded in a
   decision doc. `protocol.json` embeds a `procedures` block — literal
   frozen texts of the boundary tie rule, commodity mode rule, compactness
   formula, stable-hash rule, replicate seed template, sampling rank rule,
   metric formulas, decode rules, full-support-year rule, item-selection
   rule, and quantile method — all inside the digest; each consuming
   module cross-checks its own constants against the loaded protocol and
   refuses on drift.
14. **Threshold artefact path and acceptance semantics.** The threshold
   lands at `curated/d3-threshold/<date>/threshold.json` (D13 D4; NOT
   `reports/`). `apply-d3-threshold` refuses when the artefact is missing,
   digest-altered, or protocol-mismatched. A `criteria_passed=false`
   artefact IS accepted: the forced 144 threshold is applied, every
   otherwise-eligible site gets `trajectory_status="threshold_not_computed"`
   and `d3_eligible=false`, and the failed-criteria disclosure is carried
   into the register manifest (D13: "a forced 144 result retains the
   failed-criteria disclosure").
15. **Multi-file outputs land atomically.** Every dated curated output
   with more than one file is assembled in a sibling `.tmp` directory and
   renamed into place only after ALL tables and manifests are written
   (the snapshot-finalize pattern); a mid-write failure leaves no partial
   dated directory. The existing-output check runs BEFORE any raster
   access in `build-d3-inputs` (preflight) and again immediately before
   the rename.
16. **Spectral response identity is recorded.** Every asset href read in
   Phase A/B records its HTTP `ETag` and `Last-Modified` (when served)
   into `extraction_assets.parquet` beside the output tables; Phase B
   refuses if an asset's ETag differs from Phase A's. A digest-verified
   STAC snapshot proves which URL was listed, not which bytes were served
   — this table is the served-content record.
17. **Live-run disk formula.** Streaming reads with a bounded block cache
   (default 50 GB, config-overridable) — the disk requirement is the
   cache bound, NOT the transfer volume. The transfer budget is disclosed
   as 597 GB–3.30 TB block-granular (Batch C measured 800×800 deflate
   blocks).

## Conventions binding every task

- Where this plan quotes an existing helper (`_latest_curated_dated_dir`,
  `_verify_snapshot_or_refuse`, `_digest_verified_manifest`,
  `_write_table_or_refuse`, `_refuse_if_curated_output_already_exists`,
  `manifests.root_relative_path`, `DateOption`), the MODULE is authoritative,
  not the plan — read it before calling.
- Every CLI refusal is `typer.echo(json.dumps({"refusal": ..., ...}, indent=2,
  sort_keys=True))` then `raise typer.Exit(1)` (` from None` when suppressing
  a caught exception).
- Every curated output: compute EVERY manifest ingredient BEFORE the artefact
  write; artefact and manifest land together or fail together.
- Schemas are plain `pyarrow.Schema` literals; tables go through
  `tables.write_table` via `_write_table_or_refuse`.
- Tests use the `test_cli.py` arrange helpers (`_init_git_repo`,
  `_write_monitor_config`, `_seed_curated_register`,
  `_seed_dea_catalogue_snapshot`, `_FakeCatalogueClient`) — import/extend,
  never duplicate. No test touches the network.
- Nothing in this batch commits, publishes, or exports. All Batch D artefacts
  stay under `data_root`, internal, per D7. Maus-derived simulation artefacts
  are CC-BY-SA-4.0 lineage; MINEDEX-linked samples stay internal.

---

### Task 1: Pin the DPIRD-020 licence record

**Files:**
- Modify: `src/wa_mine_monitor/licence.py` (add one `SOURCES` entry)
- Modify: `tests/test_licence.py`

**Step 1: Write the failing test**

Append to `tests/test_licence.py`:

```python
def test_wa_rdc_regions_licence_is_pinned_cc_by():
    record = licence.SOURCES["wa_rdc_regions"]
    assert record.licence_id == "CC-BY-4.0"
    assert record.redistribute_public is True
    assert "DPIRD-020" in record.title
    assert "catalogue.data.wa.gov.au" in record.source_url
```

**Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_licence.py::test_wa_rdc_regions_licence_is_pinned_cc_by -q`
Expected: FAIL with `KeyError: 'wa_rdc_regions'`

**Step 3: Add the entry to `SOURCES` in `licence.py`**

Insert alongside the existing entries (dict order: keep alphabetical-ish
grouping with the other non-DEA sources):

```python
    "wa_rdc_regions": SourceLicence(
        source_id="wa_rdc_regions",
        title="WA Regional Development Commission Boundaries (DPIRD-020)",
        source_url=(
            "https://catalogue.data.wa.gov.au/dataset/"
            "regional-development-commission-boundaries"
        ),
        licence_id="CC-BY-4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_text=(
            "Contains Regional Development Commission Boundaries (DPIRD-020) "
            "data © Department of Primary Industries and Regional Development "
            "(WA), licensed under CC-BY-4.0."
        ),
        redistribute_public=True,
        notes=(
            "The nine regions defined under the Regional Development "
            "Commissions Act 1993, pinned as the design doc D4's 'official "
            "Pilbara and Goldfields-Esperance boundaries'. Licence read from "
            "the Data WA catalogue record for DPIRD-020 (CC-BY-4.0), "
            "source CRS GDA94 (EPSG:4283), download via SLIP "
            "data-downloads.slip.wa.gov.au. Pinned 2026-08-16."
        ),
    ),
```

**Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_licence.py -q`
Expected: PASS (whole file — the existing
`test_every_source_has_required_fields` sweep must also still pass, which is
why every field above is non-placeholder).

---

### Task 2: `sources/wa_regions.py` — validate the boundary extract

**Files:**
- Create: `src/wa_mine_monitor/sources/wa_regions.py`
- Create: `tests/test_wa_regions.py`

The module mirrors `sources/maus.py`'s job: validate a downloaded
GeoPackage and normalise it into the frame the protocol needs. The three
strata names are NOT decided here — this module only guarantees the two
named RDC regions exist and every feature has a usable name and geometry.

**Step 1: Write the failing tests**

```python
# tests/test_wa_regions.py
"""DPIRD-020 regional-boundary extract validation (D13 Batch D task D1)."""

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from wa_mine_monitor.sources import wa_regions


def _regions_gdf(names):
    geoms = [
        Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)])
        for i in range(len(names))
    ]
    return gpd.GeoDataFrame(
        {"dpird_region_name": list(names)}, geometry=geoms, crs="EPSG:4283"
    )


def _write_gpkg(tmp_path, gdf) -> Path:
    path = tmp_path / "regions.gpkg"
    gdf.to_file(path, driver="GPKG")
    return path


def test_load_regions_returns_named_frame_in_target_crs(tmp_path):
    path = _write_gpkg(
        tmp_path, _regions_gdf(["Pilbara", "Goldfields-Esperance", "Kimberley"])
    )
    out = wa_regions.load_regions(path)
    assert list(out.columns) == ["region_name", "geometry"]
    assert str(out.crs) == "EPSG:3577"
    assert set(out["region_name"]) == {
        "Pilbara", "Goldfields-Esperance", "Kimberley",
    }


def test_load_regions_refuses_when_a_required_region_is_missing(tmp_path):
    path = _write_gpkg(tmp_path, _regions_gdf(["Pilbara", "Kimberley"]))
    with pytest.raises(wa_regions.RegionExtractError, match="Goldfields-Esperance"):
        wa_regions.load_regions(path)


def test_load_regions_refuses_null_or_duplicate_region_names(tmp_path):
    gdf = _regions_gdf(["Pilbara", "Goldfields-Esperance", "Pilbara"])
    with pytest.raises(wa_regions.RegionExtractError, match="duplicate"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf))
    gdf2 = _regions_gdf(["Pilbara", "Goldfields-Esperance", None])
    with pytest.raises(wa_regions.RegionExtractError, match="null"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf2))


def test_load_regions_refuses_missing_name_column(tmp_path):
    gdf = _regions_gdf(["Pilbara", "Goldfields-Esperance"]).rename(
        columns={"dpird_region_name": "unexpected"}
    )
    with pytest.raises(wa_regions.RegionExtractError, match="name column"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf))
```

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_wa_regions.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named
'wa_mine_monitor.sources.wa_regions'`

**Step 3: Implement the module**

```python
# src/wa_mine_monitor/sources/wa_regions.py
"""DPIRD-020 Regional Development Commission boundary extract.

Validates and normalises the downloaded GeoPackage: exactly one usable
region-name column, non-null unique names, both protocol-required regions
present, reprojected to `crosswalk.TARGET_CRS` so downstream point-in-
polygon runs in the same equal-area CRS as every other spatial join in
this project. The D3 strata (`pilbara`/`goldfields_esperance`/`other_wa`)
are assigned by `d3_protocol`, not here.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd

from wa_mine_monitor import crosswalk

#: Region-name column candidates, in preference order. DPIRD-020's SLIP
#: exports have carried both spellings; the loader requires exactly one of
#: them to be present.
_NAME_COLUMNS: tuple[str, ...] = ("dpird_region_name", "region_name", "name")

#: The two regions the D3 protocol names as their own strata. Their absence
#: means the wrong dataset (or a truncated download) -- refuse, never guess.
REQUIRED_REGIONS: tuple[str, ...] = ("Pilbara", "Goldfields-Esperance")


class RegionExtractError(ValueError):
    """The boundary extract cannot support the D3 protocol -- refused."""


def load_regions(path: Path) -> gpd.GeoDataFrame:
    """Read, validate and normalise the DPIRD-020 GeoPackage."""
    gdf = gpd.read_file(path)
    name_column = next((c for c in _NAME_COLUMNS if c in gdf.columns), None)
    if name_column is None:
        raise RegionExtractError(
            f"no usable region name column in {sorted(gdf.columns)}; "
            f"expected one of {_NAME_COLUMNS}"
        )
    names = gdf[name_column]
    if names.isna().any():
        raise RegionExtractError("null region name in boundary extract")
    if names.duplicated().any():
        duplicated = sorted(names[names.duplicated()].unique())
        raise RegionExtractError(f"duplicate region names: {duplicated}")
    missing = [r for r in REQUIRED_REGIONS if r not in set(names)]
    if missing:
        raise RegionExtractError(
            f"required regions absent from extract: {missing}"
        )
    out = gdf[[name_column, "geometry"]].rename(
        columns={name_column: "region_name"}
    )
    return out.to_crs(crosswalk.TARGET_CRS).reset_index(drop=True)
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_wa_regions.py -q`
Expected: PASS (4 tests)

**Amendments (codex review 2026-08-16, binding):**

- `load_regions` must additionally REFUSE (RegionExtractError): null, empty,
  or shapely-invalid geometries; non-(Multi)Polygon geometry types; a layer
  with no CRS; and MORE THAN ONE of the `_NAME_COLUMNS` candidates present
  (ambiguity is a refusal, not first-wins). Add one test per refusal (5 new
  tests) plus a passing-fixture assertion that exactly one name column
  exists.
- `load_regions` must validate that region polygon INTERIORS do not overlap
  (pairwise `overlaps` / interior intersection area > 0 → refusal naming
  both regions); boundary touching is allowed. Add a two-overlapping-squares
  refusal test. This makes Task 5's ambiguous-boundary rule sound: with
  non-overlapping interiors, a multi-match can only be a shared boundary.

---

### Task 3: `fetch-region-boundaries` CLI

**Files:**
- Modify: `src/wa_mine_monitor/cli.py`
- Modify: `tests/test_cli.py`

Downloads the pinned DPIRD-020 GeoPackage into an immutable dated snapshot
`raw/wa_rdc_regions/<date>/regions.gpkg`, validates it with
`wa_regions.load_regions`, finalizes the snapshot (SHA256SUMS) and writes
the run manifest — the same shape `fetch-maus-extract` uses. The download
URL is a module constant so the manifest's `resolved_args` records it.

**Step 1: Write the failing tests**

Append to `tests/test_cli.py` (reuse `_init_git_repo`,
`_write_monitor_config`; add one seed helper):

```python
# --- fetch-region-boundaries CLI command ------------------------------------


def _rdc_fixture_gpkg_bytes() -> bytes:
    """Nine-region synthetic DPIRD-020 stand-in as GeoPackage bytes."""
    import io

    names = [
        "Pilbara", "Goldfields-Esperance", "Kimberley", "Gascoyne",
        "Mid West", "Wheatbelt", "Peel", "South West", "Great Southern",
    ]
    gdf = gpd.GeoDataFrame(
        {"dpird_region_name": names},
        geometry=[
            Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)])
            for i in range(len(names))
        ],
        crs="EPSG:4283",
    )
    buffer = io.BytesIO()
    gdf.to_file(buffer, driver="GPKG")
    return buffer.getvalue()


def test_fetch_region_boundaries_writes_finalized_snapshot(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    payload = _rdc_fixture_gpkg_bytes()
    monkeypatch.setattr(
        "wa_mine_monitor.cli._fetch_region_boundaries_bytes", lambda: payload
    )
    result = runner.invoke(
        app,
        ["fetch-region-boundaries", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert result.exit_code == 0, result.output
    snapshot_dir = tmp_path / "data" / "raw" / "wa_rdc_regions" / "2026-08-16"
    assert (snapshot_dir / "regions.gpkg").exists()
    assert (snapshot_dir / "SHA256SUMS.txt").exists()
    manifest = json.loads(
        (snapshot_dir / "SHA256SUMS.txt.run_manifest.json").read_text()
    )
    assert manifest["resolved_args"]["source_url"]
    payload_out = json.loads(result.output)
    assert payload_out["region_count"] == 9


def test_fetch_region_boundaries_refuses_extract_missing_required_region(
    tmp_path, monkeypatch
):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    import io

    gdf = gpd.GeoDataFrame(
        {"dpird_region_name": ["Pilbara", "Kimberley"]},
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
        ],
        crs="EPSG:4283",
    )
    buffer = io.BytesIO()
    gdf.to_file(buffer, driver="GPKG")
    monkeypatch.setattr(
        "wa_mine_monitor.cli._fetch_region_boundaries_bytes",
        lambda: buffer.getvalue(),
    )
    result = runner.invoke(
        app,
        ["fetch-region-boundaries", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert result.exit_code == 1
    assert "refusal" in result.output
    assert not (
        tmp_path / "data" / "raw" / "wa_rdc_regions" / "2026-08-16" / "SHA256SUMS.txt"
    ).exists()
```

Note: if `gpd.GeoDataFrame.to_file` on a `BytesIO` is unsupported by the
pinned pyogrio, write to a `tmp_path` file and read its bytes instead — the
test's contract is "the CLI receives GeoPackage BYTES from the fetch seam".

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k fetch_region_boundaries -q`
Expected: FAIL with exit code 2 output (unknown command
`fetch-region-boundaries`) — assert failure on `result.exit_code`

**Step 3: Implement the command in `cli.py`**

Module-level constants + fetch seam (near the other source constants):

```python
#: DPIRD-020 GeoPackage download, pinned 2026-08-16 (Data WA catalogue
#: record `regional-development-commission-boundaries`, licence CC-BY-4.0).
_RDC_REGIONS_DOWNLOAD_URL = (
    "https://data-downloads.slip.wa.gov.au/DPIRD-020/Geopackage"
)


def _fetch_region_boundaries_bytes() -> bytes:
    """Download the pinned DPIRD-020 GeoPackage (network seam, monkeypatchable)."""
    client = http.new_client()
    return client.get_bytes(_RDC_REGIONS_DOWNLOAD_URL)
```

(Read `http.py` for the actual constructor/name — `new_dea_client` exists for
DEA; reuse the same bounded-client construction for this host. If `http.py`
exposes only `new_dea_client`, add a generic `new_client()` there mirroring
it, with the same `RetryPolicy` defaults, plus a matching unit test in
`tests/test_http.py`.)

Command body (follow the `fetch-maus-extract` snapshot pattern exactly):

```python
@app.command("fetch-region-boundaries")
def cmd_fetch_region_boundaries(
    config: Path = ConfigOption,
    date: str = DateOption,
) -> None:
    """Capture the pinned DPIRD-020 RDC boundaries into a dated snapshot.

    Downloads the GeoPackage, validates it with
    `wa_regions.load_regions` (both protocol regions present, non-null
    unique names) BEFORE finalization, and writes an immutable snapshot at
    `<data_root>/raw/wa_rdc_regions/<date>/` with one run manifest. A
    failed validation refuses the WHOLE snapshot -- a boundary set that
    cannot classify every site would poison every stratum downstream.
    """
    resolved = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)

    snapshot_dir = resolved.run.data_root / "raw" / "wa_rdc_regions" / date
    sums_path = snapshot_dir / snapshots.SHA256SUMS_FILENAME
    if sums_path.exists():
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{sums_path} already exists -- this snapshot was "
                        "already captured and is immutable. Choose a "
                        "different --date to capture again."
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    try:
        payload = _fetch_region_boundaries_bytes()
    except http.HttpError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    gpkg_path = snapshot_dir / "regions.gpkg"
    gpkg_path.write_bytes(payload)

    try:
        regions = wa_regions.load_regions(gpkg_path)
    except (wa_regions.RegionExtractError, OSError) as exc:
        gpkg_path.unlink(missing_ok=True)
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    # Finalize + manifest: mirror fetch-maus-extract (metadata.txt,
    # SHA256SUMS.txt via snapshots.finalize_snapshot, then one run manifest
    # on the SHA256SUMS file with a single SourceAsset input).
    ...

    typer.echo(
        json.dumps(
            {
                "snapshot_dir": str(snapshot_dir),
                "region_count": int(len(regions)),
                "regions": sorted(regions["region_name"].tolist()),
                "manifest_path": str(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
```

The elided finalize/manifest block is NOT license to improvise: copy the
exact sequence from `fetch-maus-extract` (same `snapshots.finalize_snapshot`
call, same `SourceAsset(uri=_RDC_REGIONS_DOWNLOAD_URL,
sha256=sha256_file(gpkg_path), licence="CC-BY-4.0",
redistribute_public=True, snapshot_date=...)`, same
`resolved_args={"date": date, "source_url": _RDC_REGIONS_DOWNLOAD_URL,
"region_count": ...}`), because that command already encodes the
finalize-once/refuse-partial discipline these snapshots require.

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k fetch_region_boundaries -q`
Expected: PASS (2 tests)

**Amendments (codex review 2026-08-16, binding):**

- The CLI body's parse step must catch not only `RegionExtractError` and
  `OSError` but ANY exception raised by the GeoPackage read (driver/data-
  source errors vary by GDAL build): wrap the load in
  `except Exception as exc:` → structured JSON refusal naming the snapshot
  path. Fetch, write, finalize, and manifest failures likewise emit the
  structured refusal shape, never a bare traceback. Extend the corrupt-
  bytes test to assert JSON refusal output.

---


### Task 4: `config/d3.yaml` + `d3_protocol.py` core (constants, load, digest)

**Files:**
- Create: `config/d3.yaml`
- Create: `src/wa_mine_monitor/d3_protocol.py`
- Create: `tests/test_d3_protocol.py`

**Step 1: Write the failing tests**

```python
# tests/test_d3_protocol.py
"""D3 simulation protocol: frozen before any spectral result (D13 task D1)."""

from pathlib import Path

import pytest
import yaml

from wa_mine_monitor import d3_protocol

_CONFIG = Path(__file__).resolve().parents[1] / "config" / "d3.yaml"


def test_support_set_is_exactly_the_d13_set():
    protocol = d3_protocol.load_protocol(_CONFIG)
    assert protocol.supports == (9, 16, 25, 36, 49, 64, 100, 144)


def test_criteria_are_the_immutable_d13_values():
    protocol = d3_protocol.load_protocol(_CONFIG)
    assert protocol.criteria.nbr_p90_abs_error_max == 0.03
    assert protocol.criteria.ndmi_p90_abs_error_max == 0.03
    assert protocol.criteria.fc_p90_abs_error_pp_max == 5.0
    assert protocol.criteria.spearman_median_min == 0.95
    assert protocol.criteria.computable_site_year_fraction_min == 0.90
    assert protocol.replicates == 100
    assert protocol.adequacy.min_footprints == 10
    assert protocol.adequacy.min_full_support_years == 10
    assert protocol.selection.use_all_below == 30
    assert protocol.selection.select_n == 30


def test_load_refuses_a_drifted_support_set(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["supports"] = [9, 16, 25]
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="supports"):
        d3_protocol.load_protocol(drifted)


def test_load_refuses_a_relaxed_criterion(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["criteria"]["nbr_p90_abs_error_max"] = 0.05
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="criteria"):
        d3_protocol.load_protocol(drifted)


def test_digest_is_stable_and_key-order-independent(tmp_path):
    protocol = d3_protocol.load_protocol(_CONFIG)
    digest_one = d3_protocol.protocol_digest(protocol)
    # Re-serialise the YAML with keys sorted differently: same protocol,
    # same digest -- the digest binds CONTENT, not file bytes.
    raw = yaml.safe_load(_CONFIG.read_text())
    reordered = tmp_path / "d3.yaml"
    reordered.write_text(yaml.safe_dump(raw, sort_keys=False))
    assert d3_protocol.protocol_digest(d3_protocol.load_protocol(reordered)) == digest_one
    assert len(digest_one) == 64
```

(Rename `test_digest_is_stable_and_key-order-independent` to a valid Python
identifier — `test_digest_is_stable_and_key_order_independent` — when writing
the file; the hyphenated form above is a plan typo guard, not code.)

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_d3_protocol.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named
'wa_mine_monitor.d3_protocol'`

**Step 3: Write `config/d3.yaml`**

```yaml
# D3 effective-pixel-support simulation protocol (D13 Batch D task D1).
# FROZEN: freeze-d3-protocol writes this content's digest into
# curated/d3-protocol/<date>/ BEFORE any spectral value is read, and
# build-d3-inputs refuses to run against a config whose digest differs.
# No accuracy result may change anything in this file.
d3:
  supports: [9, 16, 25, 36, 49, 64, 100, 144]
  regions: [pilbara, goldfields_esperance, other_wa]
  region_source_names:
    pilbara: "Pilbara"
    goldfields_esperance: "Goldfields-Esperance"
  commodity_groups: [iron_ore, gold, bauxite_alumina, nickel, mineral_sands, other]
  # Ordered, case-insensitive substring rules over the register's raw
  # MINEDEX `commodity` text. First matching rule wins; a non-empty value
  # matching nothing is `other`; null/empty is a refusal (unclassified).
  commodity_token_rules:
    - group: iron_ore
      tokens: ["iron"]
    - group: bauxite_alumina
      tokens: ["bauxite", "alumina", "aluminium"]
    - group: nickel
      tokens: ["nickel"]
    - group: mineral_sands
      tokens: ["mineral sands", "heavy mineral", "ilmenite", "rutile", "zircon", "leucoxene", "monazite", "garnet"]
    - group: gold
      tokens: ["gold"]
  shape_classes:
    elongated_below: 0.20
    compact_at_least: 0.50
  adequacy:
    min_footprints: 10
    min_full_support_years: 10
  selection:
    use_all_below: 30
    select_n: 30
  replicates: 100
  criteria:
    nbr_p90_abs_error_max: 0.03
    ndmi_p90_abs_error_max: 0.03
    fc_p90_abs_error_pp_max: 5.0
    spearman_median_min: 0.95
    computable_site_year_fraction_min: 0.90
```

Rule-order note (pre-registered): `iron_ore`, `bauxite_alumina`, `nickel`
and `mineral_sands` precede `gold` so that polymetallic strings like
"Gold, Nickel" classify by their FIRST-listed rule match in RULE order, not
string order — the ordering is part of the frozen digest, so it cannot be
tuned after results exist.

**Step 4: Implement `d3_protocol.py` (core)**

```python
# src/wa_mine_monitor/d3_protocol.py
"""D3 simulation protocol: frozen constants, loading, and digest (D13 D1).

The module pins the D13-immutable values as constants; `load_protocol`
refuses any config that drifts from them. The YAML is therefore not a
tuning surface -- it is the human-readable declaration whose canonical-JSON
sha256 (`protocol_digest`) is written to the frozen protocol artefact
before metric extraction, and checked by every downstream command.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_SUPPORTS: tuple[int, ...] = (9, 16, 25, 36, 49, 64, 100, 144)
REQUIRED_REGIONS: tuple[str, ...] = ("pilbara", "goldfields_esperance", "other_wa")
REQUIRED_COMMODITY_GROUPS: tuple[str, ...] = (
    "iron_ore", "gold", "bauxite_alumina", "nickel", "mineral_sands", "other",
)
REQUIRED_CRITERIA: dict[str, float] = {
    "nbr_p90_abs_error_max": 0.03,
    "ndmi_p90_abs_error_max": 0.03,
    "fc_p90_abs_error_pp_max": 5.0,
    "spearman_median_min": 0.95,
    "computable_site_year_fraction_min": 0.90,
}
REQUIRED_REPLICATES = 100
MIN_FULL_SUPPORT_PX = 144


class D3ProtocolError(ValueError):
    """The protocol config drifts from the D13-frozen values -- refused."""


@dataclass(frozen=True)
class Criteria:
    nbr_p90_abs_error_max: float
    ndmi_p90_abs_error_max: float
    fc_p90_abs_error_pp_max: float
    spearman_median_min: float
    computable_site_year_fraction_min: float


@dataclass(frozen=True)
class Adequacy:
    min_footprints: int
    min_full_support_years: int


@dataclass(frozen=True)
class Selection:
    use_all_below: int
    select_n: int


@dataclass(frozen=True)
class ShapeClasses:
    elongated_below: float
    compact_at_least: float


@dataclass(frozen=True)
class CommodityRule:
    group: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class D3Protocol:
    supports: tuple[int, ...]
    regions: tuple[str, ...]
    region_source_names: tuple[tuple[str, str], ...]
    commodity_groups: tuple[str, ...]
    commodity_token_rules: tuple[CommodityRule, ...]
    shape_classes: ShapeClasses
    adequacy: Adequacy
    selection: Selection
    replicates: int
    criteria: Criteria


def load_protocol(path: Path) -> D3Protocol:
    """Load and validate the protocol YAML against the frozen constants."""
    raw = yaml.safe_load(Path(path).read_text())
    try:
        d3 = raw["d3"]
        protocol = D3Protocol(
            supports=tuple(d3["supports"]),
            regions=tuple(d3["regions"]),
            region_source_names=tuple(sorted(d3["region_source_names"].items())),
            commodity_groups=tuple(d3["commodity_groups"]),
            commodity_token_rules=tuple(
                CommodityRule(group=r["group"], tokens=tuple(r["tokens"]))
                for r in d3["commodity_token_rules"]
            ),
            shape_classes=ShapeClasses(**d3["shape_classes"]),
            adequacy=Adequacy(**d3["adequacy"]),
            selection=Selection(**d3["selection"]),
            replicates=int(d3["replicates"]),
            criteria=Criteria(**d3["criteria"]),
        )
    except (KeyError, TypeError) as exc:
        raise D3ProtocolError(f"malformed d3 protocol config: {exc}") from exc
    if protocol.supports != REQUIRED_SUPPORTS:
        raise D3ProtocolError(
            f"supports {protocol.supports} != frozen {REQUIRED_SUPPORTS}"
        )
    if protocol.regions != REQUIRED_REGIONS:
        raise D3ProtocolError(f"regions {protocol.regions} != frozen {REQUIRED_REGIONS}")
    if protocol.commodity_groups != REQUIRED_COMMODITY_GROUPS:
        raise D3ProtocolError(
            f"commodity groups {protocol.commodity_groups} != frozen "
            f"{REQUIRED_COMMODITY_GROUPS}"
        )
    for name, value in REQUIRED_CRITERIA.items():
        if getattr(protocol.criteria, name) != value:
            raise D3ProtocolError(
                f"criteria.{name}={getattr(protocol.criteria, name)} != frozen {value}"
            )
    if protocol.replicates != REQUIRED_REPLICATES:
        raise D3ProtocolError(
            f"replicates {protocol.replicates} != frozen {REQUIRED_REPLICATES}"
        )
    rule_groups = {rule.group for rule in protocol.commodity_token_rules}
    unknown = rule_groups - set(protocol.commodity_groups)
    if unknown:
        raise D3ProtocolError(f"token rules name unknown groups: {sorted(unknown)}")
    return protocol


def _canonical(value: object) -> object:
    if isinstance(value, tuple):
        return [_canonical(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: _canonical(getattr(value, name))
            for name in sorted(value.__dataclass_fields__)
        }
    return value


def protocol_digest(protocol: D3Protocol) -> str:
    """sha256 of the protocol's canonical JSON -- binds content, not bytes."""
    payload = json.dumps(_canonical(protocol), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

**Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_d3_protocol.py -q`
Expected: PASS (5 tests)

**Amendments (codex review 2026-08-16, binding):**

- `load_protocol` must refuse DRIFT OF EVERY FROZEN FIELD, not just the
  support set: shape-class thresholds (0.20/0.50), adequacy minimums
  (10 footprints / 10 years), selection sizes (10–29 all / 30 by hash),
  replicates (100), regions, commodity groups, criteria values, and
  MIN_FULL_SUPPORT_PX. Add one mutation-refusal test per field group
  (parametrize: mutate one YAML field → `load_protocol` raises).
- `config/d3.yaml` and the frozen protocol gain a `procedures` block per
  design decision 13 — literal strings naming each frozen rule. It is part
  of the canonical digest. `load_protocol` requires all procedure keys
  present.
- The digest key-order test as sketched (`safe_load` → `safe_dump(...,
  sort_keys=False)`) does not reorder keys. Build the reordered mapping
  explicitly (recursively reverse each dict's insertion order) before
  serialization, then assert digest equality.
- Rename `test_digest_is_stable_and_key-order-independent` →
  `test_digest_is_stable_and_key_order_independent` (hyphen is not a valid
  identifier).

---

### Task 5: `d3_protocol.py` classification (commodity, shape, region)

**Files:**
- Modify: `src/wa_mine_monitor/d3_protocol.py`
- Modify: `tests/test_d3_protocol.py`

**Step 1: Write the failing tests**

```python
def _protocol():
    return d3_protocol.load_protocol(_CONFIG)


def test_classify_commodity_first_rule_wins_and_other_is_catch_all():
    protocol = _protocol()
    assert d3_protocol.classify_commodity("IRON ORE - Hematite", protocol) == "iron_ore"
    assert d3_protocol.classify_commodity("Gold, Nickel", protocol) == "nickel"
    assert d3_protocol.classify_commodity("Zircon; Rutile", protocol) == "mineral_sands"
    assert d3_protocol.classify_commodity("Coal", protocol) == "other"


def test_classify_commodity_refuses_null_or_blank():
    protocol = _protocol()
    for bad in (None, "", "   "):
        with pytest.raises(d3_protocol.D3ProtocolError, match="unclassified"):
            d3_protocol.classify_commodity(bad, protocol)


def test_shape_class_boundaries_match_d13():
    protocol = _protocol()
    assert d3_protocol.shape_class(0.19, protocol) == "elongated"
    assert d3_protocol.shape_class(0.20, protocol) == "intermediate"
    assert d3_protocol.shape_class(0.49, protocol) == "intermediate"
    assert d3_protocol.shape_class(0.50, protocol) == "compact"
    assert d3_protocol.shape_class(1.0, protocol) == "compact"


def test_shape_class_refuses_non_finite_or_non_positive():
    protocol = _protocol()
    for bad in (0.0, -0.1, float("nan")):
        with pytest.raises(d3_protocol.D3ProtocolError, match="compactness"):
            d3_protocol.shape_class(bad, protocol)


def test_assign_regions_names_strata_and_refuses_uncovered_points():
    import geopandas as gpd
    from shapely.geometry import Point, Polygon

    protocol = _protocol()
    regions = gpd.GeoDataFrame(
        {"region_name": ["Pilbara", "Goldfields-Esperance", "Kimberley"]},
        geometry=[
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            Polygon([(10, 0), (20, 0), (20, 10), (10, 10)]),
            Polygon([(20, 0), (30, 0), (30, 10), (20, 10)]),
        ],
        crs="EPSG:3577",
    )
    points = gpd.GeoDataFrame(
        {"site_id": ["S1", "S2", "S3"]},
        geometry=[Point(5, 5), Point(15, 5), Point(25, 5)],
        crs="EPSG:3577",
    )
    assigned, disclosure = d3_protocol.assign_regions(points, regions, protocol)
    assert assigned.tolist() == ["pilbara", "goldfields_esperance", "other_wa"]
    assert disclosure["n_ambiguous_boundary_points"] == 0

    outside = gpd.GeoDataFrame(
        {"site_id": ["S4"]}, geometry=[Point(99, 99)], crs="EPSG:3577"
    )
    with pytest.raises(d3_protocol.D3ProtocolError, match="S4"):
        d3_protocol.assign_regions(outside, regions, protocol)


def test_assign_regions_boundary_point_resolves_deterministically():
    import geopandas as gpd
    from shapely.geometry import Point, Polygon

    protocol = _protocol()
    regions = gpd.GeoDataFrame(
        {"region_name": ["Pilbara", "Goldfields-Esperance"]},
        geometry=[
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            Polygon([(10, 0), (20, 0), (20, 10), (10, 10)]),
        ],
        crs="EPSG:3577",
    )
    on_border = gpd.GeoDataFrame(
        {"site_id": ["S1"]}, geometry=[Point(10, 5)], crs="EPSG:3577"
    )
    assigned, disclosure = d3_protocol.assign_regions(on_border, regions, protocol)
    # Lexicographically smallest source region name wins:
    # "Goldfields-Esperance" < "Pilbara".
    assert assigned.tolist() == ["goldfields_esperance"]
    assert disclosure["n_ambiguous_boundary_points"] == 1
```

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_d3_protocol.py -q`
Expected: the new tests FAIL with `AttributeError: module
'wa_mine_monitor.d3_protocol' has no attribute 'classify_commodity'`; the
Task 4 tests still PASS.

**Step 3: Implement**

Append to `d3_protocol.py`:

```python
def classify_commodity(raw: str | None, protocol: D3Protocol) -> str:
    """Map raw MINEDEX commodity text to a frozen group. Refuses blank."""
    if raw is None or not str(raw).strip():
        raise D3ProtocolError(
            "commodity is unclassified: null or blank raw value refused"
        )
    lowered = str(raw).lower()
    for rule in protocol.commodity_token_rules:
        if any(token in lowered for token in rule.tokens):
            return rule.group
    return "other"


def shape_class(compactness: float, protocol: D3Protocol) -> str:
    """Polsby-Popper class. Compactness must be finite and in (0, 1+eps)."""
    if not math.isfinite(compactness) or compactness <= 0.0:
        raise D3ProtocolError(f"compactness {compactness} is unclassifiable")
    if compactness < protocol.shape_classes.elongated_below:
        return "elongated"
    if compactness < protocol.shape_classes.compact_at_least:
        return "intermediate"
    return "compact"


def assign_regions(
    points: "gpd.GeoDataFrame",
    regions: "gpd.GeoDataFrame",
    protocol: D3Protocol,
) -> tuple["pd.Series", dict[str, int]]:
    """Assign each point a region stratum by covered_by membership.

    `other_wa` is a POSITIVE classification (covered by a non-named RDC
    region); a point covered by no region refuses, naming its site_ids. A
    point on a shared boundary (covered by 2+) resolves to the
    lexicographically smallest source region name, and is counted in the
    disclosure -- deterministic, pre-registered, never value-dependent.
    """
    if str(points.crs) != str(regions.crs):
        raise D3ProtocolError(
            f"points CRS {points.crs} != regions CRS {regions.crs}"
        )
    named = dict(protocol.region_source_names)  # stratum -> source name
    source_to_stratum = {v: k for k, v in named.items()}
    joined = gpd.sjoin(
        points[["site_id", "geometry"]],
        regions[["region_name", "geometry"]],
        how="left",
        predicate="covered_by",
    )
    matches = joined.groupby("site_id", sort=False)["region_name"].agg(list)
    uncovered = [
        site
        for site, names in matches.items()
        if not names or all(name is None or name != name for name in names)
        or names == [None]
    ]
    # (pandas leaves a single NaN for unmatched left rows; normalise first)
    ...
    n_ambiguous = 0
    assigned: list[str] = []
    for site_id in points["site_id"]:
        names = [n for n in matches[site_id] if isinstance(n, str)]
        if not names:
            raise D3ProtocolError(
                f"region is unclassified for site(s): ['{site_id}'] -- point "
                "covered by no RDC polygon"
            )
        if len(names) > 1:
            n_ambiguous += 1
        chosen = sorted(names)[0]
        assigned.append(source_to_stratum.get(chosen, "other_wa"))
    return (
        pd.Series(assigned, index=points.index, name="region"),
        {"n_ambiguous_boundary_points": n_ambiguous},
    )
```

The elided normalisation: collect ALL uncovered site_ids first and refuse
once naming the full sorted list (the test matches on "S4"); do not refuse
one-at-a-time. Imports (`math`, `geopandas as gpd`, `pandas as pd`) go to
the module header; keep `gpd`/`pd` as real imports, not TYPE_CHECKING —
this module now genuinely depends on them.

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_d3_protocol.py -q`
Expected: PASS (11 tests)

**Amendments (codex review 2026-08-16, binding):**

- `shape_class` must refuse (raise) compactness outside `(0, 1 + 1e-9]` or
  non-finite — a Polsby–Popper value above 1 indicates invalid geometry or
  computation, never a legitimate class. Add tests for 1.5, `inf`, `nan`,
  0, and negative inputs.
- `assign_regions` is also used on footprint `representative_point()`
  geometries (design decision 10), not only register site points — the
  docstring and one test must cover a polygon-derived point input.
- With Task 2's non-overlap validation in force, the multi-match rule's
  docstring must state its precondition: interiors are disjoint, so a
  multi-match IS a shared boundary; the lexicographic tie-break plus
  disclosure count stands.

---

### Task 6: `d3_protocol.py` selection (adequacy + stable hash)

**Files:**
- Modify: `src/wa_mine_monitor/d3_protocol.py`
- Modify: `tests/test_d3_protocol.py`

**Step 1: Write the failing tests**

```python
def _footprint_frame(rows):
    import pandas as pd

    return pd.DataFrame(
        rows, columns=["maus_id", "region", "commodity_group", "shape_class", "n_full_support_years"]
    )


def test_adequacy_and_selection_use_all_when_10_to_29():
    protocol = _protocol()
    rows = [
        (f"m{i:03d}", "pilbara", "iron_ore", "compact", 12) for i in range(15)
    ]
    selected = d3_protocol.select_stratum_footprints(_footprint_frame(rows), protocol)
    stratum = ("pilbara", "iron_ore", "compact")
    assert stratum in selected
    assert len(selected[stratum]) == 15


def test_selection_caps_at_30_by_stable_hash_of_maus_id():
    protocol = _protocol()
    rows = [
        (f"m{i:03d}", "pilbara", "gold", "compact", 12) for i in range(40)
    ]
    frame = _footprint_frame(rows)
    selected = d3_protocol.select_stratum_footprints(frame, protocol)
    stratum = ("pilbara", "gold", "compact")
    assert len(selected[stratum]) == 30
    import hashlib

    expected = sorted(
        (row[0] for row in rows),
        key=lambda m: hashlib.sha256(m.encode("utf-8")).hexdigest(),
    )[:30]
    assert selected[stratum] == tuple(sorted(expected))


def test_selection_is_stable_under_row_reorder():
    protocol = _protocol()
    rows = [
        (f"m{i:03d}", "pilbara", "gold", "compact", 12) for i in range(40)
    ]
    forward = d3_protocol.select_stratum_footprints(_footprint_frame(rows), protocol)
    backward = d3_protocol.select_stratum_footprints(
        _footprint_frame(list(reversed(rows))), protocol
    )
    assert forward == backward


def test_sparse_stratum_is_reported_not_selected():
    protocol = _protocol()
    rows = [(f"m{i:03d}", "pilbara", "nickel", "compact", 12) for i in range(9)]
    selected = d3_protocol.select_stratum_footprints(_footprint_frame(rows), protocol)
    assert ("pilbara", "nickel", "compact") not in selected
    adequacy = d3_protocol.stratum_adequacy(_footprint_frame(rows), protocol)
    assert adequacy[("pilbara", "nickel", "compact")] == {
        "n_footprints_meeting_years": 9,
        "adequate": False,
    }


def test_footprints_below_min_years_do_not_count_toward_adequacy():
    protocol = _protocol()
    rows = [(f"m{i:03d}", "pilbara", "nickel", "compact", 12) for i in range(9)]
    rows += [("m_low", "pilbara", "nickel", "compact", 9)]
    adequacy = d3_protocol.stratum_adequacy(_footprint_frame(rows), protocol)
    assert adequacy[("pilbara", "nickel", "compact")]["adequate"] is False
```

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_d3_protocol.py -q`
Expected: new tests FAIL with `AttributeError` on
`select_stratum_footprints`; earlier tests PASS.

**Step 3: Implement**

```python
Stratum = tuple[str, str, str]  # (region, commodity_group, shape_class)


def stratum_adequacy(
    footprints: pd.DataFrame, protocol: D3Protocol
) -> dict[Stratum, dict[str, object]]:
    """Per-stratum adequacy: >=10 footprints with >=10 full-support years."""
    eligible = footprints[
        footprints["n_full_support_years"] >= protocol.adequacy.min_full_support_years
    ]
    counts = eligible.groupby(
        ["region", "commodity_group", "shape_class"], sort=True
    )["maus_id"].nunique()
    out: dict[Stratum, dict[str, object]] = {}
    all_strata = footprints.groupby(
        ["region", "commodity_group", "shape_class"], sort=True
    ).groups
    for stratum in all_strata:
        n = int(counts.get(stratum, 0))
        out[stratum] = {
            "n_footprints_meeting_years": n,
            "adequate": n >= protocol.adequacy.min_footprints,
        }
    return out


def _stable_hash(maus_id: str) -> str:
    return hashlib.sha256(maus_id.encode("utf-8")).hexdigest()


def select_stratum_footprints(
    footprints: pd.DataFrame, protocol: D3Protocol
) -> dict[Stratum, tuple[str, ...]]:
    """Select simulation footprints per adequate stratum (D13 D1).

    10-29 qualifying footprints: use all. 30+: the 30 smallest by
    sha256(maus_id) hex. Returned tuples are sorted by maus_id so equality
    is order-insensitive; selection itself depends only on the hash order,
    never on input row order.
    """
    adequacy = stratum_adequacy(footprints, protocol)
    eligible = footprints[
        footprints["n_full_support_years"] >= protocol.adequacy.min_full_support_years
    ]
    selected: dict[Stratum, tuple[str, ...]] = {}
    for stratum, info in adequacy.items():
        if not info["adequate"]:
            continue
        members = sorted(
            eligible[
                (eligible["region"] == stratum[0])
                & (eligible["commodity_group"] == stratum[1])
                & (eligible["shape_class"] == stratum[2])
            ]["maus_id"].unique()
        )
        if len(members) >= protocol.selection.use_all_below:
            members = sorted(
                sorted(members, key=_stable_hash)[: protocol.selection.select_n]
            )
        selected[stratum] = tuple(members)
    return selected
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_d3_protocol.py -q`
Expected: PASS (16 tests)

**Amendments (codex review 2026-08-16, binding):**

- `stratum_adequacy` must return the FULL frozen 3×6×3 stratum space
  (54 rows), zero-count strata included with `adequate=False` — sparse
  strata are reported, never silently omitted (D13 D1 acceptance). Add a
  test asserting 54 rows and that an empty input frame still yields 54
  inadequate strata.
- Adequacy consumes FOOTPRINT rows (`maus_id` unit, design decision 9):
  rename any `site_id` in this task's signatures/tests to `maus_id`.

---

### Task 7: `freeze-d3-protocol` CLI

**Files:**
- Modify: `src/wa_mine_monitor/cli.py`
- Modify: `tests/test_cli.py`

Writes `curated/d3-protocol/<date>/protocol.json` — the canonical protocol
content plus its digest — with a run manifest whose single `SourceAsset`
input is `config/d3.yaml` itself (sha256 of the file). Refuses an existing
output; a re-run against an ALTERED d3.yaml therefore cannot silently
replace the frozen digest (existing-output refusal fires first, and the
manifest would refuse on differing provenance anyway).

**Step 1: Write the failing tests**

```python
# --- freeze-d3-protocol CLI command -----------------------------------------


def _write_d3_config(tmp_path) -> Path:
    import shutil

    src = Path(__file__).resolve().parents[1] / "config" / "d3.yaml"
    dst = tmp_path / "d3.yaml"
    shutil.copy(src, dst)
    return dst


def test_freeze_d3_protocol_writes_digest_artifact(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    d3_file = _write_d3_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "freeze-d3-protocol",
            "--config", str(cfg_file),
            "--protocol-config", str(d3_file),
            "--date", "2026-08-16",
        ],
    )
    assert result.exit_code == 0, result.output
    out_dir = tmp_path / "data" / "curated" / "d3-protocol" / "2026-08-16"
    frozen = json.loads((out_dir / "protocol.json").read_text())
    from wa_mine_monitor import d3_protocol

    expected = d3_protocol.protocol_digest(d3_protocol.load_protocol(d3_file))
    assert frozen["protocol_digest"] == expected
    manifest = json.loads(
        (out_dir / "protocol.json.run_manifest.json").read_text()
    )
    assert manifest["resolved_args"]["protocol_digest"] == expected


def test_freeze_d3_protocol_refuses_existing_output(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    d3_file = _write_d3_config(tmp_path)
    argv = [
        "freeze-d3-protocol",
        "--config", str(cfg_file),
        "--protocol-config", str(d3_file),
        "--date", "2026-08-16",
    ]
    assert runner.invoke(app, argv).exit_code == 0
    result = runner.invoke(app, argv)
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_freeze_d3_protocol_refuses_drifted_config(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    d3_file = _write_d3_config(tmp_path)
    raw = yaml.safe_load(d3_file.read_text())
    raw["d3"]["replicates"] = 5
    d3_file.write_text(yaml.safe_dump(raw))
    result = runner.invoke(
        app,
        [
            "freeze-d3-protocol",
            "--config", str(cfg_file),
            "--protocol-config", str(d3_file),
            "--date", "2026-08-16",
        ],
    )
    assert result.exit_code == 1
    assert "refusal" in result.output
```

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k freeze_d3_protocol -q`
Expected: FAIL (unknown command)

**Step 3: Implement the command**

```python
@app.command("freeze-d3-protocol")
def cmd_freeze_d3_protocol(
    config: Path = ConfigOption,
    protocol_config: Path = typer.Option(
        Path("config/d3.yaml"),
        "--protocol-config",
        help="Path to the D3 protocol YAML to freeze.",
    ),
    date: str = DateOption,
) -> None:
    """Freeze the D3 simulation protocol BEFORE any spectral value is read.

    Writes `curated/d3-protocol/<date>/protocol.json` -- the canonical
    protocol content and its sha256 digest -- so `build-d3-inputs` can
    refuse a config that drifted after freezing (D13: "The configuration
    digest is written before metric extraction"; "No accuracy result can
    change sample definitions or criteria").
    """
    resolved = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)

    try:
        protocol = d3_protocol.load_protocol(protocol_config)
    except (d3_protocol.D3ProtocolError, OSError, yaml.YAMLError) as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    digest = d3_protocol.protocol_digest(protocol)

    output_dir = resolved.run.data_root / "curated" / "d3-protocol" / date
    output_path = output_dir / "protocol.json"
    _refuse_if_curated_output_already_exists(
        output_path, config=resolved_config, git_state=git_state
    )

    protocol_source_sha = sha256_file(protocol_config)
    source_path, source_root = manifests.root_relative_path(
        protocol_config, config=resolved_config
    )
    input_assets = [
        SourceAsset(
            uri=str(protocol_config),
            sha256=protocol_source_sha,
            collection=None,
            snapshot_date=None,
            licence=None,
            redistribute_public=False,
        )
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "protocol": d3_protocol._canonical(protocol),
                "protocol_digest": digest,
            },
            indent=2,
            sort_keys=True,
        )
    )
    try:
        manifests.write_run_manifest(
            output=output_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                "protocol_config": source_path,
                "protocol_config_root": source_root,
                "protocol_digest": digest,
            },
        )
    except FileExistsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {
                "output_path": str(output_path),
                "protocol_digest": digest,
            },
            indent=2,
            sort_keys=True,
        )
    )
```

`d3_protocol._canonical` being private but used by the CLI is a smell —
promote it: rename to `canonical_protocol(protocol) -> dict` as a public
function in Task 4's module (and have `protocol_digest` call it). Check
`SourceAsset`'s actual required fields (`provenance.py`) before writing the
literal — the plan's field list mirrors `build-dea-coverage`'s usage; if
`licence=None` is not accepted, use the licence id string of the repo's own
config (`"MIT"` is wrong — prefer omitting optional fields per the model).

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k freeze_d3_protocol -q`
Expected: PASS (3 tests)

**Amendments (codex review 2026-08-16, binding):**

- **Single lineage (design decision 13):** the command REFUSES if any dated
  directory already exists under `curated/d3-protocol/` — not just the
  same date. Refusal names the existing snapshot and states supersession
  requires a recorded human decision. Add a test freezing 2026-08-18 then
  refusing 2026-08-19.
- The frozen `protocol.json` includes the `procedures` block; the digest
  covers it.
- **Atomic finalize (design decision 15):** write `protocol.json` + its
  manifest into `<dir>.tmp` and `os.replace` the directory into place
  after both files exist. The existing-output check runs before the write
  AND the rename re-checks. Apply the same pattern anywhere this plan
  writes a multi-file dated output.

---

### Task 8: `pixel_support.py` — EPSG:3577 pixel-support assignments (D2)

**Files:**
- Create: `src/wa_mine_monitor/pixel_support.py`
- Create: `tests/test_pixel_support.py`

**Step 1: Write the failing tests**

```python
# tests/test_pixel_support.py
"""EPSG:3577 pixel-CENTRE support assignments (D13 Batch D task D2).

Partial-pixel weighting and all_touched are prohibited: membership is
"pixel centre covered by the polygon", nothing else. Effective support is
a measured centre count, never area/900.
"""

import pytest
from shapely.geometry import Polygon

from wa_mine_monitor import pixel_support

# A grid whose origin sits on the 30 m lattice: 10x10 pixels, top-left
# corner at (0, 300), pixel size 30 x -30 (north-up).
GRID = pixel_support.GridSpec(
    crs="EPSG:3577",
    transform=(30.0, 0.0, 0.0, 0.0, -30.0, 300.0),
    width=10,
    height=10,
    tile_id="x0y0",
)


def _square(x0: float, y0: float, side: float) -> Polygon:
    return Polygon([(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)])


def test_exact_nine_centre_square():
    # Covers centres of cols 0-2, rows 7-9 (y in [0, 90]): 3x3 = 9.
    result = pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", GRID)
    assert result.effective_pixel_support_px == 9
    assert len(result.member_indices) == 9


def test_exact_sixteen_centre_square():
    result = pixel_support.build_pixel_support(_square(0, 0, 120), "EPSG:3577", GRID)
    assert result.effective_pixel_support_px == 16


def test_boundary_centre_is_a_member():
    # Polygon edge passing exactly through a centre: covered_by includes
    # the boundary, so the centre at (15, 15) belongs to a polygon whose
    # edge is x in [15, 45], y in [15, 45].
    polygon = Polygon([(15, 15), (45, 15), (45, 45), (15, 45)])
    result = pixel_support.build_pixel_support(polygon, "EPSG:3577", GRID)
    assert result.effective_pixel_support_px == 4  # centres 15,45 x 15,45


def test_effective_support_is_not_area_over_900():
    # A thin sliver with area 900*4 m^2 that covers NO pixel centre.
    sliver = Polygon([(1, 0), (2, 0), (2, 3600), (1, 3600)])
    result = pixel_support.build_pixel_support(sliver, "EPSG:3577", GRID)
    assert result.effective_pixel_support_px == 0  # computed zero, not null


def test_crs_mismatch_refused():
    with pytest.raises(pixel_support.PixelSupportError, match="CRS"):
        pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:4326", GRID)


def test_shifted_grid_refused():
    shifted = pixel_support.GridSpec(
        crs="EPSG:3577",
        transform=(30.0, 0.0, 7.0, 0.0, -30.0, 300.0),
        width=10, height=10, tile_id="x0y0",
    )
    with pytest.raises(pixel_support.PixelSupportError, match="lattice"):
        pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", shifted)


def test_rotated_grid_refused():
    rotated = pixel_support.GridSpec(
        crs="EPSG:3577",
        transform=(30.0, 0.5, 0.0, 0.5, -30.0, 300.0),
        width=10, height=10, tile_id="x0y0",
    )
    with pytest.raises(pixel_support.PixelSupportError, match="rotat"):
        pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", rotated)


def test_wrong_pixel_size_refused():
    coarse = pixel_support.GridSpec(
        crs="EPSG:3577",
        transform=(60.0, 0.0, 0.0, 0.0, -60.0, 600.0),
        width=10, height=10, tile_id="x0y0",
    )
    with pytest.raises(pixel_support.PixelSupportError, match="30"):
        pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", coarse)


def test_missing_or_invalid_geometry_is_not_computed_not_zero():
    assert pixel_support.build_pixel_support(None, "EPSG:3577", GRID) is None
    bowtie = Polygon([(0, 0), (30, 30), (30, 0), (0, 30)])
    assert pixel_support.build_pixel_support(bowtie, "EPSG:3577", GRID) is None


def test_assignment_digest_binds_grid_identity_and_members():
    a = pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", GRID)
    b = pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", GRID)
    assert a.assignment_digest == b.assignment_digest
    other_tile = pixel_support.GridSpec(
        crs=GRID.crs, transform=GRID.transform,
        width=GRID.width, height=GRID.height, tile_id="x1y0",
    )
    c = pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", other_tile)
    assert c.assignment_digest != a.assignment_digest
```

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_pixel_support.py -q`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement**

```python
# src/wa_mine_monitor/pixel_support.py
"""Pixel-CENTRE support on the fixed 30 m EPSG:3577 grid (D13 task D2).

Membership is exact: a pixel belongs to a footprint when its CENTRE is
covered by the polygon (boundary inclusive). Partial-pixel weighting and
all_touched are prohibited by the D3 protocol. The assignment identity
binds grid CRS, affine transform, width, height and product tile identity,
so the same polygon on a different tile is a DIFFERENT assignment.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
import shapely

PIXEL_METRES = 30.0


class PixelSupportError(ValueError):
    """The grid cannot carry a D3 pixel-support assignment -- refused."""


@dataclass(frozen=True)
class GridSpec:
    """Grid identity: CRS, GDAL-order affine (a,b,c,d,e,f), size, tile."""

    crs: str
    transform: tuple[float, float, float, float, float, float]
    width: int
    height: int
    tile_id: str


@dataclass(frozen=True)
class PixelSupport:
    grid: GridSpec
    member_indices: tuple[tuple[int, int], ...]  # (row, col), sorted
    effective_pixel_support_px: int
    assignment_digest: str


def _validate_grid(grid: GridSpec) -> None:
    a, b, c, d, e, f = grid.transform
    if b != 0.0 or d != 0.0:
        raise PixelSupportError(
            f"rotated/sheared grid refused: transform {grid.transform}"
        )
    if a != PIXEL_METRES or e != -PIXEL_METRES:
        raise PixelSupportError(
            f"pixel size ({a}, {e}) != required (30.0, -30.0)"
        )
    if c % PIXEL_METRES != 0.0 or f % PIXEL_METRES != 0.0:
        raise PixelSupportError(
            f"grid origin ({c}, {f}) is off the 30 m lattice -- shifted "
            "grid refused"
        )


def build_pixel_support(
    geometry: shapely.Geometry | None,
    geometry_crs: str,
    grid: GridSpec,
) -> PixelSupport | None:
    """Assign member pixel centres. Returns None (NOT computed) for a
    missing or invalid geometry; returns a computed 0-member assignment for
    a valid geometry covering no centre."""
    _validate_grid(grid)
    if geometry_crs != grid.crs:
        raise PixelSupportError(
            f"geometry CRS {geometry_crs} != grid CRS {grid.crs}"
        )
    if geometry is None or geometry.is_empty or not geometry.is_valid:
        return None

    a, _, c, _, e, f = grid.transform
    minx, miny, maxx, maxy = geometry.bounds
    col_lo = max(0, int(np.floor((minx - c) / a - 0.5)))
    col_hi = min(grid.width - 1, int(np.ceil((maxx - c) / a - 0.5)))
    row_lo = max(0, int(np.floor((maxy - f) / e - 0.5)))
    row_hi = min(grid.height - 1, int(np.ceil((miny - f) / e - 0.5)))
    members: list[tuple[int, int]] = []
    if col_lo <= col_hi and row_lo <= row_hi:
        cols = np.arange(col_lo, col_hi + 1)
        rows = np.arange(row_lo, row_hi + 1)
        col_grid, row_grid = np.meshgrid(cols, rows)
        xs = c + (col_grid + 0.5) * a
        ys = f + (row_grid + 0.5) * e
        centres = shapely.points(xs.ravel(), ys.ravel())
        covered = shapely.covered_by(centres, geometry)
        members = sorted(
            (int(r), int(col))
            for r, col, hit in zip(
                row_grid.ravel(), col_grid.ravel(), covered, strict=True
            )
            if hit
        )
    digest_payload = json.dumps(
        {
            "crs": grid.crs,
            "transform": list(grid.transform),
            "width": grid.width,
            "height": grid.height,
            "tile_id": grid.tile_id,
            "members": members,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return PixelSupport(
        grid=grid,
        member_indices=tuple(members),
        effective_pixel_support_px=len(members),
        assignment_digest=hashlib.sha256(digest_payload.encode("utf-8")).hexdigest(),
    )
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pixel_support.py -q`
Expected: PASS (11 tests). If `test_exact_nine_centre_square` disagrees on
the count, hand-check the centre coordinates (15, 45, 75 in both axes for a
90 m square at origin) before touching the implementation — the TEST
encodes the definition.

**Amendments (codex review 2026-08-16, binding):**

- `_validate_grid` must additionally refuse: `crs != crosswalk.TARGET_CRS`
  (exactly "EPSG:3577" — a 30-unit EPSG:4326 grid must not pass); and,
  when `tile_id` matches `^x(-?\d+)y(-?\d+)$`, an origin inconsistent
  with the DEA collection-3 tiling (origin_x == x_index * 96_000,
  origin_y == (y_index + 1) * 96_000 on the 3 200 × 30 m layout —
  VERIFY the exact index convention against one captured item's actual
  transform before hard-coding signs, and freeze what the data shows).
  Non-matching tile_id formats skip the lattice check (fixture grids) but
  still require EPSG:3577 and the 30 m lattice.
- D13 D2 requires an exact **144-centre** test; add
  `test_exact_144_centre_square` (12×12 polygon → support 144). With the
  refusal additions the test count rises — state the true count in Step 4
  after writing them, and make Step 4's expected count match the file.

---

### Task 9: rasterio pin + `dea_raster.py` decode rules

**Files:**
- Modify: `pyproject.toml`
- Create: `src/wa_mine_monitor/dea_raster.py`
- Create: `tests/test_dea_raster.py`

**Step 1: Add the pinned dependency**

In `pyproject.toml` `[project] dependencies`, add (match the existing pin
style used for geopandas/pyarrow):

```toml
    "rasterio>=1.5,<2",
```

Run: `uv lock && uv sync` — expected: lockfile updates, install succeeds.

**Step 2: Write the failing tests**

```python
# tests/test_dea_raster.py
"""DEA raster decode rules -- Batch E's declared rules, built in Batch D.

D13 lines 511-512: geomedian -999 -> null, valid values / 10_000; FC 255 ->
null, values above 100 RETAINED (measured, counted, never clipped).
"""

import numpy as np

from wa_mine_monitor import dea_raster


def test_decode_geomedian_nodata_and_scale():
    raw = np.array([[-999, 0], [5000, 10000]], dtype=np.int16)
    decoded = dea_raster.decode_geomedian(raw)
    assert np.isnan(decoded[0, 0])
    assert decoded[0, 1] == 0.0
    assert decoded[1, 0] == 0.5
    assert decoded[1, 1] == 1.0


def test_decode_fc_nodata_and_out_of_range_retained_and_counted():
    raw = np.array([[255, 42], [101, 120]], dtype=np.uint8)
    decoded, n_out_of_range = dea_raster.decode_fc(raw)
    assert np.isnan(decoded[0, 0])
    assert decoded[0, 1] == 42.0
    assert decoded[1, 0] == 101.0  # retained, not clipped
    assert decoded[1, 1] == 120.0
    assert n_out_of_range == 2


def test_decode_functions_do_not_mutate_input():
    raw = np.array([-999, 100], dtype=np.int16)
    dea_raster.decode_geomedian(raw)
    assert raw.tolist() == [-999, 100]
```

**Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_dea_raster.py -q`
Expected: FAIL with `ModuleNotFoundError`

**Step 4: Implement**

```python
# src/wa_mine_monitor/dea_raster.py
"""DEA collection-3 decode rules (D13 Batch E task E1, built for Batch D).

Batch D needs exactly two rules for the D3 simulation; Batch E EXTENDS
this module rather than re-declaring them. Rules are D13-frozen:
geomedian nodata is -999 and valid values scale by 1/10_000; FC nodata is
255 and values above 100 are real measurements -- retained and counted,
never clipped.
"""

from __future__ import annotations

import numpy as np

GEOMEDIAN_NODATA = -999
GEOMEDIAN_SCALE = 10_000.0
FC_NODATA = 255
FC_DOCUMENTED_MAX = 100.0


def decode_geomedian(values: np.ndarray) -> np.ndarray:
    """-999 -> NaN; everything else / 10_000. Returns float64, input unchanged."""
    out = values.astype(np.float64, copy=True)
    out[values == GEOMEDIAN_NODATA] = np.nan
    return out / GEOMEDIAN_SCALE


def decode_fc(values: np.ndarray) -> tuple[np.ndarray, int]:
    """255 -> NaN; >100 retained and counted. Returns (float64, n_out_of_range)."""
    out = values.astype(np.float64, copy=True)
    out[values == FC_NODATA] = np.nan
    n_out_of_range = int(np.sum(out > FC_DOCUMENTED_MAX))
    return out, n_out_of_range
```

**Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_dea_raster.py -q`
Expected: PASS (3 tests)

---

### Task 10: `d3_inputs.py` — deterministic sampling, metrics, aggregation

**Files:**
- Create: `src/wa_mine_monitor/d3_inputs.py`
- Create: `tests/test_d3_inputs.py`

The statistical unit is the FOOTPRINT (`maus_id`, design decision 9); no
`site_id` appears in this module or its tables.

**Step 1: Write the failing tests**

```python
# tests/test_d3_inputs.py
"""Deterministic reduced-support simulation inputs (D13 Batch D task D3)."""

import numpy as np
import pandas as pd
import pytest

from wa_mine_monitor import d3_inputs

MEMBERS = tuple(("x0y0", r, c) for r in range(12) for c in range(12))  # 144


def test_sample_support_is_deterministic_and_input_order_free():
    a = d3_inputs.sample_support(MEMBERS, 16, replicate=3, seed_material="seed")
    b = d3_inputs.sample_support(
        tuple(reversed(MEMBERS)), 16, replicate=3, seed_material="seed"
    )
    assert a == b
    assert len(a) == 16


def test_sample_support_is_nested_across_supports():
    small = d3_inputs.sample_support(MEMBERS, 9, replicate=7, seed_material="seed")
    large = d3_inputs.sample_support(MEMBERS, 100, replicate=7, seed_material="seed")
    assert set(small) <= set(large)


def test_sample_support_has_no_repeats_and_varies_by_replicate():
    one = d3_inputs.sample_support(MEMBERS, 64, replicate=0, seed_material="seed")
    two = d3_inputs.sample_support(MEMBERS, 64, replicate=1, seed_material="seed")
    assert len(set(one)) == 64
    assert one != two


def test_sample_support_refuses_more_than_available():
    with pytest.raises(d3_inputs.D3InputsError, match="requested"):
        d3_inputs.sample_support(MEMBERS[:10], 16, replicate=0, seed_material="s")


def test_geomedian_metrics_full_vs_reduced():
    n = 144
    nir = np.linspace(0.1, 0.9, n)
    bands = {
        "nbart_nir": nir,
        "nbart_swir_1": np.full(n, 0.2),
        "nbart_swir_2": np.full(n, 0.1),
    }
    full = d3_inputs.geomedian_metrics(bands)
    assert full["nbr"] == pytest.approx(np.mean((nir - 0.1) / (nir + 0.1)))
    assert full["ndmi"] == pytest.approx(np.mean((nir - 0.2) / (nir + 0.2)))
    reduced = d3_inputs.geomedian_metrics({k: v[:9] for k, v in bands.items()})
    assert reduced["nbr"] != full["nbr"]


def test_geomedian_validity_rejects_zero_denominator():
    # nir = -swir2 at one pixel -> nbr denominator zero -> pixel invalid.
    bands = {
        "nbart_nir": np.array([0.5, -0.1]),
        "nbart_swir_1": np.array([0.2, 0.2]),
        "nbart_swir_2": np.array([0.1, 0.1]),
    }
    valid = d3_inputs.geomedian_valid_mask(bands)
    assert valid.tolist() == [True, False]


def test_fc_metrics_are_means_of_the_median_percentile_assets():
    values = {
        "bs_pc_50": np.array([10.0, 20.0]),
        "pv_pc_50": np.array([30.0, 50.0]),
        "npv_pc_50": np.array([5.0, 15.0]),
    }
    metrics = d3_inputs.fc_metrics(values)
    assert metrics == {
        "bare_soil": 15.0,
        "photosynthetic_vegetation": 40.0,
        "non_photosynthetic_vegetation": 10.0,
    }


def test_spearman_matches_rank_pearson():
    full = pd.Series([0.1, 0.4, 0.2, 0.9, 0.7])
    reduced = pd.Series([0.15, 0.35, 0.25, 0.85, 0.75])
    rho = d3_inputs.spearman(full, reduced)
    expected = full.rank().corr(reduced.rank())
    assert rho == pytest.approx(expected)


def test_spearman_returns_none_for_constant_series():
    # A constant series has undefined rank correlation: not-computable,
    # disclosed by the caller -- never silently 0 or NaN in a table.
    assert d3_inputs.spearman(pd.Series([1.0, 1.0, 1.0]), pd.Series([1.0, 2.0, 3.0])) is None


def test_spearman_refuses_fewer_than_min_years():
    with pytest.raises(d3_inputs.D3InputsError, match="years"):
        d3_inputs.spearman(pd.Series([1.0]), pd.Series([2.0]))
```

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_d3_inputs.py -q`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement**

```python
# src/wa_mine_monitor/d3_inputs.py
"""Deterministic reduced-support simulation inputs (D13 Batch D task D3).

Sampling is WITHOUT replacement, deterministic, nested and input-order
free: members are ranked by sha256("{seed_material}|{replicate}|{member}")
and a sample of size n is the first n of that ranking, so the n=9 sample
is always a prefix-subset of the n=100 sample for the same replicate. The
seed material is pre-registered by the caller (protocol digest + maus_id +
collection + year) and never derived from a clock or process state.

The statistical unit throughout is the footprint (maus_id); register
sites never enter this module.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

Member = tuple[str, int, int]  # (tile_id, row, col)

#: Geomedian metric -> (numerator-plus band, numerator-minus band).
GEOMEDIAN_METRIC_BANDS: dict[str, tuple[str, str]] = {
    "nbr": ("nbart_nir", "nbart_swir_2"),
    "ndmi": ("nbart_nir", "nbart_swir_1"),
}
FC_METRIC_ASSETS: dict[str, str] = {
    "bare_soil": "bs_pc_50",
    "photosynthetic_vegetation": "pv_pc_50",
    "non_photosynthetic_vegetation": "npv_pc_50",
}
MIN_SPEARMAN_YEARS = 2


class D3InputsError(ValueError):
    """Simulation-input construction violated the frozen protocol -- refused."""


def _rank_key(member: Member, replicate: int, seed_material: str) -> str:
    token = f"{seed_material}|{replicate}|{member[0]},{member[1]},{member[2]}"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sample_support(
    members: Sequence[Member], n: int, *, replicate: int, seed_material: str
) -> tuple[Member, ...]:
    distinct = sorted(set(members))
    if n > len(distinct):
        raise D3InputsError(
            f"requested support {n} exceeds available {len(distinct)} members"
        )
    ranked = sorted(distinct, key=lambda m: _rank_key(m, replicate, seed_material))
    return tuple(ranked[:n])


def geomedian_valid_mask(bands: Mapping[str, np.ndarray]) -> np.ndarray:
    """Pixel validity for geomedian metrics: every band finite AND every
    metric denominator nonzero (design decision 11)."""
    stacked = np.vstack([bands[b] for b in sorted(bands)])
    valid = np.isfinite(stacked).all(axis=0)
    for plus, minus in GEOMEDIAN_METRIC_BANDS.values():
        valid &= (bands[plus] + bands[minus]) != 0
    return valid


def fc_valid_mask(values: Mapping[str, np.ndarray]) -> np.ndarray:
    stacked = np.vstack([values[a] for a in sorted(values)])
    return np.isfinite(stacked).all(axis=0)


def geomedian_metrics(bands: Mapping[str, np.ndarray]) -> dict[str, float]:
    """Spatial mean of the per-pixel index over the given pixel arrays.
    Caller guarantees validity (geomedian_valid_mask all-True)."""
    out: dict[str, float] = {}
    for metric, (plus, minus) in GEOMEDIAN_METRIC_BANDS.items():
        numerator = bands[plus] - bands[minus]
        denominator = bands[plus] + bands[minus]
        out[metric] = float(np.mean(numerator / denominator))
    return out


def fc_metrics(values: Mapping[str, np.ndarray]) -> dict[str, float]:
    return {
        metric: float(np.mean(values[asset]))
        for metric, asset in FC_METRIC_ASSETS.items()
    }


def spearman(full: pd.Series, reduced: pd.Series) -> float | None:
    if len(full) < MIN_SPEARMAN_YEARS or len(full) != len(reduced):
        raise D3InputsError(
            f"spearman needs >= {MIN_SPEARMAN_YEARS} paired years, got "
            f"{len(full)} vs {len(reduced)}"
        )
    if full.nunique() < 2 or reduced.nunique() < 2:
        return None  # undefined for a constant series -- caller discloses
    return float(full.rank().corr(reduced.rank()))
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_d3_inputs.py -q`
Expected: PASS (10 tests)

---

### Task 11: `d3_inputs.py` — tile-aware reads + two-phase simulation

**Files:**
- Modify: `src/wa_mine_monitor/d3_inputs.py`
- Modify: `tests/test_d3_inputs.py`

Implements the raster seam and the per-footprint driver under design
decision 11 (Phase A computability, Phase B values). Fixture tests write
tiny LOCAL GeoTIFFs with rasterio into `tmp_path` — no network.

**Step 1: Write the failing tests**

```python
def _write_geotiff(path, array, *, origin=(0.0, 300.0), nodata=None):
    import rasterio
    from rasterio.transform import from_origin

    height, width = array.shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=array.dtype,
        crs="EPSG:3577",
        transform=from_origin(origin[0], origin[1], 30, 30),
        nodata=nodata,
    ) as dst:
        dst.write(array, 1)


def test_grid_spec_from_dataset_reads_identity(tmp_path):
    import rasterio

    path = tmp_path / "band.tif"
    _write_geotiff(path, np.zeros((10, 10), dtype=np.int16))
    with rasterio.open(path) as dataset:
        grid = d3_inputs.grid_spec_from_dataset(dataset, tile_id="fixture-a")
    assert grid.crs == "EPSG:3577"
    assert grid.width == 10 and grid.height == 10
    assert grid.transform[0] == 30.0 and grid.transform[4] == -30.0


def test_read_member_values_multi_tile_in_canonical_order(tmp_path):
    import rasterio

    a = tmp_path / "a.tif"
    b = tmp_path / "b.tif"
    _write_geotiff(a, np.arange(100, dtype=np.int16).reshape(10, 10))
    _write_geotiff(b, (np.arange(100, dtype=np.int16) + 1000).reshape(10, 10))
    members = (("tile-a", 2, 3), ("tile-b", 0, 1), ("tile-a", 4, 7))
    with rasterio.open(a) as da, rasterio.open(b) as db:
        values = d3_inputs.read_member_values(
            {"tile-a": da, "tile-b": db}, members
        )
    # canonical member order is sorted(set(members)):
    # (tile-a,2,3)=23, (tile-a,4,7)=47, (tile-b,0,1)=1001
    assert values.tolist() == [23, 47, 1001]


def test_read_member_values_refuses_unknown_tile(tmp_path):
    import rasterio

    a = tmp_path / "a.tif"
    _write_geotiff(a, np.zeros((10, 10), dtype=np.int16))
    with rasterio.open(a) as da:
        with pytest.raises(d3_inputs.D3InputsError, match="tile"):
            d3_inputs.read_member_values({"tile-a": da}, (("tile-b", 0, 0),))


def _bands(n):
    return {
        "nbart_nir": np.linspace(0.2, 0.8, n),
        "nbart_swir_1": np.full(n, 0.2),
        "nbart_swir_2": np.full(n, 0.1),
    }


def test_simulate_footprint_year_produces_rows_and_series():
    members = tuple(sorted(("x0y0", r, c) for r in range(12) for c in range(12)))
    result = d3_inputs.simulate_footprint_year(
        maus_id="M1",
        year=2005,
        source_id="dea_gm_ls5t",
        members=members,
        band_values=_bands(144),
        kind="geomedian",
        supports=(9, 16),
        replicates=25,
        protocol_digest="d" * 64,
    )
    assert result is not None
    rows, reduced_series = result
    frame = pd.DataFrame(rows)
    assert set(frame["metric_id"]) == {"nbr", "ndmi"}
    assert set(frame["support_px"]) == {9, 16}
    assert (frame["n_replicates"] == 25).all()
    # errors persisted as the full sorted replicate list (decision 6)
    lengths = frame["replicate_abs_errors"].map(len)
    assert (lengths == 25).all()
    assert frame["replicate_abs_errors"].map(
        lambda v: v == sorted(v)
    ).all()
    assert set(reduced_series.keys()) == {
        ("nbr", 9), ("nbr", 16), ("ndmi", 9), ("ndmi", 16),
    }
    assert all(len(v) == 25 for v in reduced_series.values())


def test_simulate_footprint_year_requires_canonical_member_order():
    members = tuple(("x0y0", r, c) for r in range(12) for c in range(12))
    shuffled = members[1:] + members[:1]
    with pytest.raises(d3_inputs.D3InputsError, match="sorted"):
        d3_inputs.simulate_footprint_year(
            maus_id="M1", year=2005, source_id="dea_gm_ls5t",
            members=shuffled, band_values=_bands(144), kind="geomedian",
            supports=(9,), replicates=5, protocol_digest="d" * 64,
        )


def test_simulate_footprint_year_refuses_below_144_support():
    members = tuple(sorted(("x0y0", 0, c) for c in range(100)))
    with pytest.raises(d3_inputs.D3InputsError, match="144"):
        d3_inputs.simulate_footprint_year(
            maus_id="M1", year=2005, source_id="dea_gm_ls5t",
            members=members, band_values=_bands(100), kind="geomedian",
            supports=(9,), replicates=5, protocol_digest="d" * 64,
        )


def test_simulate_footprint_year_invalid_pixel_returns_none():
    members = tuple(sorted(("x0y0", r, c) for r in range(12) for c in range(12)))
    bands = _bands(144)
    bands["nbart_nir"][3] = np.nan
    result = d3_inputs.simulate_footprint_year(
        maus_id="M1", year=2005, source_id="dea_gm_ls5t",
        members=members, band_values=bands, kind="geomedian",
        supports=(9,), replicates=5, protocol_digest="d" * 64,
    )
    assert result is None  # not full-support computable


def test_year_computable_matches_simulate_none_result():
    bands = _bands(144)
    assert d3_inputs.year_computable(bands, kind="geomedian") is True
    bands["nbart_nir"][3] = np.nan
    assert d3_inputs.year_computable(bands, kind="geomedian") is False
```

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_d3_inputs.py -q`
Expected: new tests FAIL with `AttributeError`

**Step 3: Implement**

```python
def grid_spec_from_dataset(
    dataset: "rasterio.DatasetReader", *, tile_id: str
) -> pixel_support.GridSpec:
    """Grid identity read from the ACTUAL raster -- D2's tile binding."""
    t = dataset.transform
    return pixel_support.GridSpec(
        crs=str(dataset.crs),
        transform=(t.a, t.b, t.c, t.d, t.e, t.f),
        width=int(dataset.width),
        height=int(dataset.height),
        tile_id=tile_id,
    )


def read_member_values(
    datasets: Mapping[str, "rasterio.DatasetReader"],
    members: Sequence[Member],
) -> np.ndarray:
    """Member pixel values in CANONICAL order (sorted(set(members))),
    grouped per tile: one windowed read per tile covering that tile's
    member bounding box. Refuses a member whose tile has no dataset."""
    from rasterio.windows import Window

    canonical = sorted(set(members))
    missing = {m[0] for m in canonical} - set(datasets)
    if missing:
        raise D3InputsError(f"no dataset for tile(s): {sorted(missing)}")
    out = np.empty(len(canonical), dtype=np.float64)
    by_tile: dict[str, list[int]] = {}
    for i, m in enumerate(canonical):
        by_tile.setdefault(m[0], []).append(i)
    for tile_id, positions in by_tile.items():
        rows = [canonical[i][1] for i in positions]
        cols = [canonical[i][2] for i in positions]
        row_lo, col_lo = min(rows), min(cols)
        window = Window(
            col_off=col_lo, row_off=row_lo,
            width=max(cols) - col_lo + 1, height=max(rows) - row_lo + 1,
        )
        block = datasets[tile_id].read(1, window=window)
        for i in positions:
            _, r, c = canonical[i]
            out[i] = block[r - row_lo, c - col_lo]
    return out


def _require_canonical(members: Sequence[Member]) -> tuple[Member, ...]:
    canonical = tuple(members)
    if list(canonical) != sorted(set(canonical)):
        raise D3InputsError(
            "members must be sorted and duplicate-free (canonical order); "
            "band_values arrays are positionally aligned to that order"
        )
    return canonical


def year_computable(band_values: Mapping[str, np.ndarray], *, kind: str) -> bool:
    """Phase A computability: every member pixel valid (design decision 11)."""
    mask = (
        geomedian_valid_mask(band_values)
        if kind == "geomedian"
        else fc_valid_mask(band_values)
    )
    return bool(mask.all())


def simulate_footprint_year(
    *,
    maus_id: str,
    year: int,
    source_id: str,
    members: Sequence[Member],
    band_values: Mapping[str, np.ndarray],
    kind: str,  # "geomedian" | "fc"
    supports: Sequence[int],
    replicates: int,
    protocol_digest: str,
) -> tuple[list[dict[str, object]], dict[tuple[str, int], list[float]]] | None:
    """Full + reduced metrics for one footprint-year-collection (Phase B).

    `members` MUST be canonical (sorted, unique) and `band_values` arrays
    MUST be positionally aligned to it -- refused otherwise, because a
    silent misalignment assigns raster values to the wrong pixels.
    Support below 144 is a caller error (refused); an invalid pixel is a
    data property (year not computable -> None).
    """
    canonical = _require_canonical(members)
    if len(canonical) < d3_protocol.MIN_FULL_SUPPORT_PX:
        raise D3InputsError(
            f"full support {len(canonical)} is below the frozen minimum "
            f"{d3_protocol.MIN_FULL_SUPPORT_PX} -- caller must not submit"
        )
    for band, values in band_values.items():
        if len(values) != len(canonical):
            raise D3InputsError(
                f"band {band} has {len(values)} values for "
                f"{len(canonical)} members -- misaligned input"
            )
    if not year_computable(band_values, kind=kind):
        return None

    metric_fn = geomedian_metrics if kind == "geomedian" else fc_metrics
    full = metric_fn(band_values)
    member_index = {m: i for i, m in enumerate(canonical)}
    seed_material = f"{protocol_digest}|{maus_id}|{source_id}|{year}"

    rows: list[dict[str, object]] = []
    reduced_series: dict[tuple[str, int], list[float]] = {}
    for support in supports:
        per_metric_errors: dict[str, list[float]] = {m: [] for m in full}
        per_metric_reduced: dict[str, list[float]] = {m: [] for m in full}
        for replicate in range(replicates):
            sample = sample_support(
                canonical, support, replicate=replicate,
                seed_material=seed_material,
            )
            indices = [member_index[m] for m in sample]
            reduced = metric_fn(
                {band: values[indices] for band, values in band_values.items()}
            )
            for metric, value in reduced.items():
                per_metric_errors[metric].append(abs(value - full[metric]))
                per_metric_reduced[metric].append(value)
        for metric, errors in per_metric_errors.items():
            rows.append(
                {
                    "maus_id": maus_id,
                    "year": year,
                    "source_id": source_id,
                    "metric_id": metric,
                    "support_px": support,
                    "full_support_px": len(canonical),
                    "valid_support_px": len(canonical),
                    "full_value": full[metric],
                    "replicate_abs_errors": sorted(errors),
                    "n_replicates": replicates,
                    "protocol_digest": protocol_digest,
                }
            )
            reduced_series[(metric, support)] = per_metric_reduced[metric]
    return rows, reduced_series
```

Imports: `from wa_mine_monitor import d3_protocol, pixel_support`; rasterio
is imported lazily inside the dataset functions.

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_d3_inputs.py -q`
Expected: PASS (19 tests)

---

### Task 12: `build-d3-inputs` CLI

**Files:**
- Modify: `src/wa_mine_monitor/cli.py`
- Modify: `src/wa_mine_monitor/d3_inputs.py` (schemas + orchestration helpers)
- Modify: `tests/test_cli.py`

The largest command in the batch. **Gate order** (each individually refused
with the standard JSON shape):

0. **Preflight existing-output check** (design decision 17/15): refuse if
   `curated/d3-inputs/<date>/` exists, BEFORE any snapshot or raster access.
1. Frozen protocol: exactly ONE dated dir under `curated/d3-protocol/`
   (more than one → refusal per design decision 13), digest-verified;
   recompute `protocol_digest(load_protocol(--protocol-config))` — refuse
   mismatch ("protocol drifted after freezing"). Cross-check every module
   constant this command relies on (`d3_inputs.GEOMEDIAN_METRIC_BANDS`,
   `FC_METRIC_ASSETS`, decode constants, hash/seed rule strings) against
   the protocol's `procedures` block — refuse drift.
2. Enriched register: latest `curated/register/<date>/`, digest-verified;
   must carry `register.DEA_COVERAGE_COLUMNS` (bare Batch B register
   refuses, naming `build-dea-coverage`) — mirror `derive-dea-volume`.
3. Crosswalk: latest, digest-verified; `tier1_population`.
4. Footprint areas: latest, digest-verified; Maus sha256 equality gate
   between crosswalk and footprint-areas manifests — mirror
   `derive-dea-volume`'s wording.
5. Regions snapshot: `_verify_snapshot_or_refuse(..., source_id=
   "wa_rdc_regions", required_files=("regions.gpkg",))`; `load_regions`.
6. Maus geometry snapshot: sha256 must ALSO equal the crosswalk manifest's
   Maus digest (compactness must come from the snapshot the ids came from).
7. Catalogue: the DEA STAC snapshot named by the enriched register's
   manifest `resolved_args["catalogue_date"]`, verified + `_load_dea_items`.

**Computation order** (pre-registered; live run deferred by decision 8):

- **Footprint strata** (decisions 9–10): per `maus_id` in
  `tier1_population`: compactness + `shape_class` from Maus geometry in
  `crosswalk.TARGET_CRS`; region from `assign_regions` on
  `representative_point()`; commodity group = modal group over linked
  high-confidence sites (`classify_commodity` on register text), ties →
  lexicographically smallest, tie count disclosed.
- **Support** (decision 7): per footprint, `build_pixel_support` against
  each intersecting tile's ACTUAL grid (grids via
  `grid_spec_from_dataset` from the catalogue item assets; item selection
  rule below), members unioned across tiles;
  `effective_pixel_support_px` = distinct member count. Geometry
  missing/invalid → support not-computed with a reason, disclosed.
- **Item selection rule (frozen in `procedures`):** for each (collection,
  tile, year) exactly one item must exist in the item index — zero means
  the year is not epoch-covered for that tile; more than one is a refusal
  (duplicate item). Band hrefs are matched by the frozen asset keys per
  collection (geomedian: `nbart_nir`, `nbart_swir_1`, `nbart_swir_2`;
  FC: `bs_pc_50`, `pv_pc_50`, `npv_pc_50`). All bands of an item must
  report identical grid identity (refuse otherwise). No mosaicking:
  members are read per tile from that tile's own item (Task 11 reader).
- **Phase A (validity):** for candidate footprints (support ≥ 144 and
  epoch coverage > 0), read member values per year and evaluate ONLY
  `year_computable` per collection; record per-asset `ETag`/
  `Last-Modified` response headers (decision 16). A footprint-year counts
  toward adequacy iff FC computable AND ≥1 geomedian collection
  computable (decision 11).
- **Adequacy + selection:** `stratum_adequacy` (full 54-stratum space) +
  `select_stratum_footprints` over Phase A counts. No metric value exists
  yet — selection cannot depend on accuracy.
- **Phase B (values):** for SELECTED footprints only, re-read member
  values (refusing if any asset ETag changed since Phase A), decode via
  `dea_raster`, run `simulate_footprint_year` per computable year ×
  collection.
- **Spearman:** per selected footprint × collection × metric × support ×
  replicate over that footprint's computable years; `spearman()` returning
  `None` (constant series) drops the row and increments a disclosed
  counter.

**Outputs** — assembled in `<dir>.tmp`, atomically renamed (decision 15),
four tables each with a run manifest sharing identical inputs:

- `support_inputs.parquet` (`D3_SUPPORT_INPUTS_SCHEMA`)
- `support_spearman.parquet` (`D3_SPEARMAN_SCHEMA`)
- `footprint_support.parquet` (`D3_FOOTPRINT_SUPPORT_SCHEMA`)
- `stratum_summary.parquet` (`D3_STRATUM_SUMMARY_SCHEMA`)
- `extraction_assets.parquet` (`D3_EXTRACTION_ASSETS_SCHEMA`)

Schemas (declare in `d3_inputs.py`):

```python
_INPUT_DIGEST_FIELD = pa.field("input_manifest_digests", pa.string(), nullable=False)
# canonical JSON: {"catalogue": sha, "register": sha, "crosswalk": sha,
#  "footprint_areas": sha, "maus": sha, "regions": sha, "protocol": sha}
# identical on every row of every table (D13 D3: input-manifest digests).

D3_SUPPORT_INPUTS_SCHEMA = pa.schema([
    pa.field("maus_id", pa.string(), nullable=False),
    pa.field("region", pa.string(), nullable=False),
    pa.field("commodity_group", pa.string(), nullable=False),
    pa.field("shape_class", pa.string(), nullable=False),
    pa.field("year", pa.int64(), nullable=False),
    pa.field("source_id", pa.string(), nullable=False),
    pa.field("metric_id", pa.string(), nullable=False),
    pa.field("support_px", pa.int64(), nullable=False),
    pa.field("full_support_px", pa.int64(), nullable=False),
    pa.field("valid_support_px", pa.int64(), nullable=False),
    pa.field("full_value", pa.float64(), nullable=False),
    pa.field("replicate_abs_errors", pa.list_(pa.float64()), nullable=False),
    pa.field("n_replicates", pa.int64(), nullable=False),
    pa.field("protocol_digest", pa.string(), nullable=False),
    _INPUT_DIGEST_FIELD,
])

D3_SPEARMAN_SCHEMA = pa.schema([
    pa.field("maus_id", pa.string(), nullable=False),
    pa.field("source_id", pa.string(), nullable=False),
    pa.field("metric_id", pa.string(), nullable=False),
    pa.field("support_px", pa.int64(), nullable=False),
    pa.field("replicate", pa.int64(), nullable=False),
    pa.field("spearman", pa.float64(), nullable=False),
    pa.field("n_years", pa.int64(), nullable=False),
    pa.field("protocol_digest", pa.string(), nullable=False),
    _INPUT_DIGEST_FIELD,
])

D3_FOOTPRINT_SUPPORT_SCHEMA = pa.schema([
    pa.field("maus_id", pa.string(), nullable=False),
    pa.field("region", pa.string(), nullable=True),
    pa.field("commodity_group", pa.string(), nullable=True),
    pa.field("shape_class", pa.string(), nullable=True),
    pa.field("effective_pixel_support_px", pa.int64(), nullable=True),
    pa.field("support_not_computed_reason", pa.string(), nullable=True),
    pa.field("n_epoch_covered_years", pa.int64(), nullable=False),
    pa.field("n_full_support_years", pa.int64(), nullable=False),
    pa.field("candidate", pa.bool_(), nullable=False),
    pa.field("selected", pa.bool_(), nullable=False),
    pa.field("protocol_digest", pa.string(), nullable=False),
    _INPUT_DIGEST_FIELD,
])

D3_STRATUM_SUMMARY_SCHEMA = pa.schema([
    pa.field("region", pa.string(), nullable=False),
    pa.field("commodity_group", pa.string(), nullable=False),
    pa.field("shape_class", pa.string(), nullable=False),
    pa.field("n_footprints", pa.int64(), nullable=False),
    pa.field("n_adequate_footprints", pa.int64(), nullable=False),
    pa.field("adequate", pa.bool_(), nullable=False),
    pa.field("n_selected", pa.int64(), nullable=False),
    pa.field("protocol_digest", pa.string(), nullable=False),
    _INPUT_DIGEST_FIELD,
])  # exactly 54 rows, zero-count strata included

D3_EXTRACTION_ASSETS_SCHEMA = pa.schema([
    pa.field("source_id", pa.string(), nullable=False),
    pa.field("tile_id", pa.string(), nullable=False),
    pa.field("year", pa.int64(), nullable=False),
    pa.field("asset_key", pa.string(), nullable=False),
    pa.field("href", pa.string(), nullable=False),
    pa.field("etag", pa.string(), nullable=True),
    pa.field("last_modified", pa.string(), nullable=True),
    pa.field("phase", pa.string(), nullable=False),  # "a" | "b"
])
```

**Step 1: Write the failing CLI tests** — extend
`_seed_derive_dea_volume_chain` (read it first) into
`_seed_d3_inputs_chain(tmp_path, monkeypatch)` returning a named tuple
`(cfg_file, protocol_digest, d3_yaml_path)`. It must ALSO: seed the RDC
regions snapshot (Task 3's fetch seam); freeze the protocol via
`runner.invoke`; and rewrite the DEA fixture so asset hrefs point at LOCAL
GeoTIFFs (`file://` or plain paths — verify what rasterio accepts for
local paths and use that) written with Task 11's `_write_geotiff`
(imported, not duplicated). **The fixture must contain at least 10
distinct Maus footprints, each covering ≥144 pixel centres, each with
≥10 fixture years of items in FC and one geomedian collection** —
selection requires 10 adequate footprints; a single-footprint fixture
cannot go green. Keep rasters tiny (e.g. 20×20) and years synthetic.
Tests:

```python
def test_build_d3_inputs_end_to_end_over_fixtures(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    result = runner.invoke(
        app, ["build-d3-inputs", "--config", str(seed.cfg_file), "--date", "2026-08-18"]
    )
    assert result.exit_code == 0, result.output
    out_dir = tmp_path / "data" / "curated" / "d3-inputs" / "2026-08-18"
    inputs = tables.read_table(out_dir / "support_inputs.parquet")
    assert (inputs["protocol_digest"] == seed.protocol_digest).all()
    assert (inputs["n_replicates"] == 100).all()
    assert set(inputs["support_px"]) <= {9, 16, 25, 36, 49, 64, 100, 144}
    summary = tables.read_table(out_dir / "stratum_summary.parquet")
    assert len(summary) == 54
    support = tables.read_table(out_dir / "footprint_support.parquet")
    assert support["selected"].sum() >= 10
    payload = json.loads(result.output)
    assert payload["n_selected_footprints"] >= 10
    assert payload["n_candidate_footprints"] >= payload["n_selected_footprints"]


def test_build_d3_inputs_refuses_existing_output_before_any_read(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    (tmp_path / "data" / "curated" / "d3-inputs" / "2026-08-18").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(  # any raster open records a call
        "rasterio.open", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError)
    )
    result = runner.invoke(
        app, ["build-d3-inputs", "--config", str(seed.cfg_file), "--date", "2026-08-18"]
    )
    assert result.exit_code == 1
    assert calls == []  # preflight refused before raster access


def test_build_d3_inputs_refuses_drifted_protocol(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    raw = yaml.safe_load(seed.d3_yaml_path.read_text())
    raw["d3"]["commodity_token_rules"].append({"group": "other", "tokens": ["x"]})
    seed.d3_yaml_path.write_text(yaml.safe_dump(raw))
    result = runner.invoke(
        app, ["build-d3-inputs", "--config", str(seed.cfg_file), "--date", "2026-08-18"]
    )
    assert result.exit_code == 1
    assert "drift" in result.output


def test_build_d3_inputs_refuses_bare_batch_b_register(tmp_path, monkeypatch):
    # Seed WITHOUT build-dea-coverage: latest register lacks coverage columns.
    ...
    assert result.exit_code == 1
    assert "build-dea-coverage" in result.output


def test_build_d3_inputs_refuses_maus_digest_mismatch(tmp_path, monkeypatch):
    # Footprint areas re-seeded from a DIFFERENT Maus snapshot date.
    ...
    assert result.exit_code == 1
    assert "sha256" in result.output


def test_build_d3_inputs_refuses_second_frozen_protocol(tmp_path, monkeypatch):
    # Manually create a second dated dir under curated/d3-protocol/.
    ...
    assert result.exit_code == 1
    assert "protocol" in result.output
```

The elided arrange blocks mirror `derive-dea-volume`'s refusal tests —
read those first.

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k build_d3_inputs -q`
Expected: FAIL (unknown command)

**Step 3: Implement** per the gate + computation order above. Success echo
(keys, exact):

```python
{
    "output_dir": ..., "protocol_digest": ...,
    "n_candidate_footprints": ..., "n_selected_footprints": ...,
    "n_strata_adequate": ..., "n_strata_inadequate": ...,
    "n_footprint_years_simulated": ..., "n_footprint_years_not_computable": ...,
    "n_footprints_support_not_computed": ...,
    "n_spearman_not_computable": ...,
    "region_ambiguity": {...}, "commodity_ties": {...},
    "manifest_paths": [...],
}
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k build_d3_inputs -q`
Expected: PASS (6 tests)

**Step 5: Run the full battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean, full suite green.

---

### Task 13: `d3_threshold.py` — threshold evaluation (D4 pure core)

**Files:**
- Create: `src/wa_mine_monitor/d3_threshold.py`
- Create: `tests/test_d3_threshold.py`

Public surface per D13 D4: **`evaluate_threshold(inputs, protocol) ->
ThresholdResult`** where `inputs` is a small container of the Task 12
tables and `protocol` is the loaded frozen `D3Protocol` (criteria come
FROM the protocol; the module hard-codes nothing the protocol carries —
the earlier hard-coded-constants sketch is superseded).

Frozen statistics (design decision 6): P90 = `numpy.percentile(pooled
per-replicate absolute errors across the stratum's footprint-years, 90,
method="linear")`; median Spearman = `numpy.median` over rows; computable
fraction per decision 12 (from `footprint_support` counts). Rules:

- Evaluation is per **stratum × collection × metric**: geomedian sensor
  variants (ls5t/ls7e/ls8cls9c) are evaluated SEPARATELY and each
  (collection, metric) cell with data must pass — a strong sensor cannot
  mask a weak one (D13 "sensor overlap variants remain separate").
- The REQUIRED metric set per collection kind must be present wherever
  that collection has rows (geomedian: nbr AND ndmi; FC: all three) —
  a missing metric is a refusal, not a silent pass.
- Every criterion cell records value, pass flag, AND sample counts
  (n_footprint_years, n_error_values, n_spearman_rows, fraction
  numerator/denominator) — D4 requires auditable failure details.
- Statistics run on finite values; an empty or all-non-finite cell FAILS
  that criterion (never passes vacuously) and records n=0.
- `criteria_passed` is True only when a REDUCED support (< 144) passes;
  otherwise `n_star=144`, `criteria_passed=False`, failed criteria listed.
- Mixed `protocol_digest` across input rows, or a digest differing from
  `protocol_digest(protocol)`, is a refusal.

**Step 1: Write the failing tests** — build small frames directly (helper
constructors `_inputs_frame`/`_spearman_frame`/`_support_frame` giving one
adequate stratum, parameterized per-support error lists and rhos):

- `test_smallest_passing_support_wins` — errors pass at 16 not 9 → n_star
  16, `criteria_passed True`, `nominal_area_m2 == 900*16`.
- `test_each_criterion_can_fail_independently` — parametrized: p90-error /
  spearman / fraction each individually sink support 16 → n_star 144.
- `test_no_passing_support_falls_back_to_144` — `criteria_passed False`,
  failed criteria listed in `result.failed_criteria`.
- `test_only_144_passing_is_not_criteria_passed` — all reduced supports
  fail, 144 trivially fine → `criteria_passed False`.
- `test_fc_uses_percentage_point_tolerance` — 4.0 pp passes FC, would fail
  geomedian.
- `test_sensor_variants_evaluated_separately` — ls5t passes, ls7e fails at
  16 → 16 fails overall.
- `test_missing_required_metric_is_refused` — geomedian rows with only nbr
  → `D3ThresholdError`.
- `test_inadequate_strata_excluded` — failing rows in a stratum absent
  from `stratum_summary.adequate` don't sink the support.
- `test_empty_cell_fails_not_passes` — a support with zero spearman rows
  for one metric fails that criterion with n=0 recorded.
- `test_mixed_protocol_digest_is_refused`.
- `test_per_support_detail_records_counts` — every criteria cell carries
  the count fields.

**Step 2: Run to verify they fail** — `uv run pytest
tests/test_d3_threshold.py -q` → `ModuleNotFoundError`.

**Step 3: Implement.** Container + result:

```python
@dataclass(frozen=True)
class ThresholdInputs:
    support_inputs: pd.DataFrame
    support_spearman: pd.DataFrame
    footprint_support: pd.DataFrame
    stratum_summary: pd.DataFrame


@dataclass(frozen=True)
class ThresholdResult:
    n_star: int
    criteria_passed: bool
    nominal_area_m2: int
    protocol_digest: str
    per_support: tuple[dict[str, object], ...]
    failed_criteria: tuple[str, ...]
```

`evaluate_threshold(inputs: ThresholdInputs, protocol: d3_protocol.D3Protocol)`:
adequate strata = `stratum_summary[stratum_summary.adequate]` rows;
criteria values from `protocol.criteria`; loop supports ascending; per
stratum × collection × metric compute pooled P90 (explode the
`replicate_abs_errors` lists with `numpy.concatenate`), spearman median,
computable fraction (`n_full_support_years` ÷ `n_epoch_covered_years`
summed over the stratum's SELECTED footprints); every cell dict carries
`{"value", "passed", "n_footprint_years", "n_error_values",
"n_spearman_rows", "fraction_numerator", "fraction_denominator"}`; refuse
missing required metrics; `n_star` = smallest passing support **strictly
below 144**, else 144 with `criteria_passed=False` and `failed_criteria`
naming every failing `stratum/collection/metric/criterion` at the best
support. Cross-check module tolerance interpretation: tolerances come
from `protocol.criteria` — if D13 §4's criterion values and the loaded
protocol disagree, `load_protocol` already refused (Task 4 amendment); do
not re-declare constants here.

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_d3_threshold.py -q`
Expected: PASS (11+ tests; state the true count)

---

### Task 14: `derive-d3-threshold` CLI

**Files:**
- Modify: `src/wa_mine_monitor/cli.py`
- Modify: `tests/test_cli.py`

**Command:** `derive-d3-threshold --config ... --date ...`

Gates, in order: (1) single frozen protocol, digest recomputed from
`--protocol-config` (as Task 12 gate 1); (2) latest
`curated/d3-inputs/<date>/` with ALL FIVE tables digest-verified via
their manifests; (3) every table's `protocol_digest` equals the frozen
digest (refuse "inputs built under a different protocol"); (4) the
tables' `input_manifest_digests` values are identical across tables
(refuse a mixed input set).

Adequacy comes from `stratum_summary.parquet` directly (persisted by
Task 12 over the full 54-stratum space) — the report includes both
`adequate_strata` and `inadequate_strata` WITH their counts, recoverable
because the summary table persists every stratum.

Output (design decision 14): **`curated/d3-threshold/<date>/
threshold.json`** — atomic finalize with its manifest — containing the
serialized `ThresholdResult` (n_star, criteria_passed, nominal_area_m2,
protocol_digest, per_support detail with counts, failed_criteria),
`adequate_strata`, `inadequate_strata`, and the input table paths +
digests. Manifest inputs: all five parquet files + protocol.json.
Success echo: `{"output_path", "n_star", "criteria_passed",
"nominal_area_m2", "n_strata_adequate", "n_strata_inadequate",
"manifest_path"}`.

**Step 1: Write the failing tests**

```python
def test_derive_d3_threshold_end_to_end(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    result = runner.invoke(
        app, ["build-d3-inputs", "--config", str(seed.cfg_file), "--date", "2026-08-18"]
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app, ["derive-d3-threshold", "--config", str(seed.cfg_file), "--date", "2026-08-19"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["n_star"] in {9, 16, 25, 36, 49, 64, 100, 144}
    report = json.loads(
        (tmp_path / "data" / "curated" / "d3-threshold" / "2026-08-19"
         / "threshold.json").read_text()
    )
    assert report["nominal_area_m2"] == 900 * payload["n_star"]
    assert report["protocol_digest"] == seed.protocol_digest
    assert len(report["adequate_strata"]) + len(report["inadequate_strata"]) == 54


def test_derive_d3_threshold_refuses_digest_mismatch(tmp_path, monkeypatch):
    # Build inputs, delete the frozen protocol dir, re-freeze a MODIFIED
    # protocol under a new date -> frozen digest no longer matches tables.
    ...
    assert result.exit_code == 1
    assert "different protocol" in result.output


def test_derive_d3_threshold_refuses_missing_inputs(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    result = runner.invoke(
        app, ["derive-d3-threshold", "--config", str(seed.cfg_file), "--date", "2026-08-19"]
    )
    assert result.exit_code == 1
    assert "d3-inputs" in result.output
```

**Step 2: Run to verify they fail** — unknown command.

**Step 3: Implement** per the gates and output contract.

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k derive_d3_threshold -q`
Expected: PASS (3 tests)

---

### Task 15: register D5 columns + `apply-d3-threshold` CLI

**Files:**
- Modify: `src/wa_mine_monitor/register.py`
- Modify: `src/wa_mine_monitor/cli.py`
- Modify: `tests/test_register.py`
- Modify: `tests/test_cli.py`

**Part A — schema.** Add to `register.py` (nullability per D13 D5 —
`d3_threshold_px` and `d3_eligible` are NULLABLE):

```python
D3_ELIGIBILITY_COLUMNS = (
    "effective_pixel_support_px",  # int64, nullable (null = not computed)
    "d3_threshold_px",             # int64, nullable
    "d3_eligible",                 # bool, nullable
    "trajectory_status",           # string, non-null, one of _TRAJECTORY_STATUSES
)

_TRAJECTORY_STATUSES = (
    "eligible",
    "no_usable_footprint",
    "crosswalk_not_high_confidence",
    "insufficient_pixel_support",
    "threshold_not_computed",
)
```

`ELIGIBLE_REGISTER_SCHEMA` = `ENRICHED_REGISTER_SCHEMA` + the four fields
(follow how `ENRICHED_REGISTER_SCHEMA` extends the base — read it first).

**Status assignment (exactly one per site, first match wins; D13 D5 test
"unmatched and unusable footprints receive no_usable_footprint"):**

1. site matched to NO Maus footprint at all, OR matched to a footprint
   whose support is not-computed (missing/invalid geometry) →
   `no_usable_footprint`;
2. site matched but not in the HIGH-confidence `tier1_population` →
   `crosswalk_not_high_confidence`;
3. threshold artefact has `criteria_passed=False` → every site that would
   otherwise be judged → `threshold_not_computed`, `d3_eligible=False`,
   `d3_threshold_px=144` (forced value applied and disclosed, decision 14);
4. computed support < `n_star` → `insufficient_pixel_support`,
   `d3_eligible=False`;
5. otherwise → `eligible`, `d3_eligible=True`.

`d3_eligible` is True ONLY for `eligible`; it is False for statuses 3–4
and NULL for statuses 1–2 (no judgement was possible — the nullable
fields exist to represent exactly this). `d3_threshold_px` is the applied
`n_star` on every judged row (3–5) and NULL on 1–2.

Register tests: schema field count/nullability; unknown status fails
validation; `d3_eligible=True` with non-eligible status fails validation;
null `d3_eligible` with status 3–5 fails validation.

**Part B — CLI.** `apply-d3-threshold --config ... --date ...`:

Gates: enriched register (coverage columns, digest-verified) + crosswalk
(digest-verified) + latest `curated/d3-threshold/<date>/threshold.json`
digest-verified with `protocol_digest` equal to the single frozen
protocol's + **latest `curated/d3-inputs/<date>/footprint_support.parquet`
digest-verified via its manifest, its `protocol_digest` matching the
threshold's** (the support table is a gated input like any other — an
unverified support table must not determine eligibility). Refuse when the
threshold artefact is missing or altered; a `criteria_passed=False`
artefact is applied per decision 14, never refused.

Join: register sites × crosswalk (all confidence tiers, to distinguish
rule 1 from rule 2) × footprint_support on `maus_id`. Output: a NEW dated
`curated/register/<date>/register.parquet` under
`ELIGIBLE_REGISTER_SCHEMA` (distinct date, as `build-dea-coverage`).
Manifest records status counts, computed/zero/not-computed support
counts, threshold digest, `criteria_passed`, and (when False) the
failed-criteria disclosure copied from the threshold artefact. Success
echo: `{"output_path", "d3_threshold_px", "criteria_passed",
"n_eligible", "n_by_status": {...}, "rows", "manifest_path"}`; rows-in ==
rows-out asserted.

**Step 1: Write the failing tests**

```python
def test_apply_d3_threshold_assigns_every_site_exactly_one_status(tmp_path, monkeypatch):
    # chain: seed -> build-d3-inputs -> derive-d3-threshold -> apply
    ...
    out = tables.read_table(register_path)
    assert len(out) == n_register_rows
    assert set(out["trajectory_status"]) <= set(register._TRAJECTORY_STATUSES)
    eligible_mask = out["trajectory_status"] == "eligible"
    assert (out.loc[eligible_mask, "d3_eligible"] == True).all()  # noqa: E712
    assert not out.loc[~eligible_mask, "d3_eligible"].fillna(False).any()
    payload = json.loads(result.output)
    assert sum(payload["n_by_status"].values()) == len(out)


def test_apply_d3_threshold_unmatched_site_is_no_usable_footprint(tmp_path, monkeypatch):
    # A register site absent from the crosswalk entirely.
    ...
    assert row["trajectory_status"] == "no_usable_footprint"
    assert pd.isna(row["d3_eligible"]) and pd.isna(row["d3_threshold_px"])


def test_apply_d3_threshold_forced_144_discloses(tmp_path, monkeypatch):
    # Fixture values tuned so no reduced support passes ->
    # criteria_passed False; command still succeeds; all judged sites
    # threshold_not_computed with d3_threshold_px == 144; manifest carries
    # failed-criteria disclosure.
    ...


def test_apply_d3_threshold_refuses_unverified_support_table(tmp_path, monkeypatch):
    # Corrupt footprint_support.parquet after its manifest is written.
    ...
    assert result.exit_code == 1


def test_apply_d3_threshold_refuses_protocol_mismatch(tmp_path, monkeypatch):
    ...
    assert result.exit_code == 1
    assert "protocol" in result.output
```

**Step 2: Run to verify they fail** — unknown command / missing schema.

**Step 3: Implement** Part A then Part B.

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_register.py tests/test_cli.py -k "d3 or register" -q`
Expected: PASS

---

### Task 16: acceptance suite, checkpoint, full battery, deferred live run

**Files:**
- Create: `tests/test_batch_d_acceptance.py`
- Create: `docs/checkpoints/batch-d-result.md`

**Step 1: Write the acceptance tests** — one fixture-driven end-to-end
chain (`fetch-region-boundaries` → `freeze-d3-protocol` →
`build-d3-inputs` → `derive-d3-threshold` → `apply-d3-threshold`), then
assertions mapped one-to-one to D13 §4's acceptance criteria (quote each
criterion in the docstring):

- `test_protocol_frozen_before_any_spectral_read` — the d3-inputs
  manifest records the protocol digest as an input; freezing after
  results is impossible (single-lineage refusal exercised).
- `test_no_accuracy_result_can_change_sample_definitions` — build TWO
  independently seeded, independently finalized fixture chains whose
  spectral VALUES differ but whose null masks are identical (two separate
  `_seed_d3_inputs_chain` invocations parameterized by a value offset —
  never mutate a finalized snapshot in place); assert identical
  `footprint_support.selected` sets and identical `stratum_summary`, with
  different `full_value`s.
- `test_every_register_row_has_exactly_one_trajectory_status`.
- `test_sparse_strata_disclosed_not_pooled` — stratum_summary always has
  54 rows; inadequate strata appear in the threshold report.
- `test_determinism_same_inputs_same_outputs` — run `build-d3-inputs`
  twice into two dates from the SAME seed; table contents equal
  (compare DataFrames, not file bytes — manifests differ by date).
- `test_refusals_are_structured_json` — each refusal exercised above
  emitted `{"refusal": ...}` on stdout.

**Step 2: Run** `uv run pytest tests/test_batch_d_acceptance.py -q` —
all must pass without touching src (a failure here is a Task 1–15 bug:
run `kit:debugging` before fixing).

**Step 3: Checkpoint skeleton** — `docs/checkpoints/batch-d-result.md`
mirroring `batch-c-result.md`: status line (fixture suite green, live run
PENDING), `_pending_` fields (frozen protocol digest; regions fetch date
+ gpkg sha256; candidate/selected footprint counts per stratum;
footprint-years simulated / not computable; n_star, criteria_passed,
per-criterion margins with counts; eligibility counts by
trajectory_status), and a "Live run" section stating: execution on
luminosity (`/mnt/data`, per batch-c-result.md), streaming reads with the
bounded block cache (design decision 17) — **disk requirement = cache
bound (default 50 GB), transfer budget 597 GB–3.30 TB block-granular**,
run deferred to a human-reviewed session.

**Step 4: Full battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: all clean, full suite green (554 pre-batch tests + all new).

**Step 5 (DEFERRED — live run):** Not part of this build. The live
five-command chain runs on luminosity in a later human-reviewed session:

```
uv run wa-mine-monitor fetch-region-boundaries --config config/<cfg>.yaml --date <YYYY-MM-DD>
uv run wa-mine-monitor freeze-d3-protocol      --config config/<cfg>.yaml --date <YYYY-MM-DD>
uv run wa-mine-monitor build-d3-inputs         --config config/<cfg>.yaml --date <YYYY-MM-DD>
uv run wa-mine-monitor derive-d3-threshold     --config config/<cfg>.yaml --date <YYYY-MM-DD>
uv run wa-mine-monitor apply-d3-threshold      --config config/<cfg>.yaml --date <YYYY-MM-DD>
```

then fill the checkpoint. Watch items: the frozen `protocol.json`, its
run manifest, and `config/d3.yaml` must be **git-committed (clean tree,
commit recorded in the freeze manifest's git state) BEFORE
`build-d3-inputs` runs live** — "committed" means exactly that;
`build-d3-inputs` is the batch's big network step — confirm `/mnt/data`
free space ≥ the configured block-cache bound plus output headroom
(NOT ≥ transfer volume; decision 17); if the DPIRD-020 download URL has
moved, re-verify the licence page before re-pinning.

---

## Codex review adjudication (2026-08-16)

The plan was attacked pre-build by codex against a self-contained package
(plan + D13 §4 + Batch C facts + module API surface): 16 blockers, 22
majors, 4 minors. ALL findings were accepted and applied in place —
design decisions 6–7 rewritten, decisions 9–17 added, amendment blocks
appended to Tasks 2–8, Tasks 10–16 rewritten. Spec-interpretation rulings
(frozen before any spectral read): pooled-P90 statistic (decision 6),
computable fraction as data-completeness (decision 12), two-phase
extraction (decision 11), footprint unit + stratum identity (decisions
9–10), single protocol lineage + procedures digest (decision 13),
threshold path/nullability/API corrected to D13's letter (decision 14,
Tasks 13–15). Raw findings: `docs/reviews/2026-08-16-codex-batch-d-plan-attack.md`.

## Execution notes

- Build in a worktree via `kit:build-flow`; `kit:verify` then
  `kit:finish-branch` before calling the work done.
- Tasks 1–10 have no ordering constraints beyond their stated imports;
  Tasks 11–16 are strictly sequential.
- The full battery command appears in Tasks 12 and 16; run it at least at
  those two points and always before finishing.
- Where an amendment block contradicts its task's earlier text, the
  amendment is binding.
