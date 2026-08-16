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
   not explain.
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
6. **Replicate persistence is aggregated, pre-registered, and bounded.** Raw
   site-year-support-replicate rows (~10⁹ at real scale) are not persisted.
   `build-d3-inputs` writes two tables:
   `support_inputs.parquet` — one row per site × year × collection × metric ×
   support: `full_value`, `replicate_median_abs_error`,
   `replicate_p90_abs_error`, `n_replicates`, plus identity/stratum/digest
   columns; and `support_spearman.parquet` — one row per site × collection ×
   metric × support × replicate carrying the full-vs-reduced Spearman over
   that site's full-support years. D4's criteria are pre-registered against
   these aggregates: P90 absolute error is the P90 over site-years of the
   per-site-year replicate MEDIAN absolute error; median Spearman is the
   median over the spearman table's rows.
7. **Eligibility support uses the canonical DEA Albers grid, not a per-tile
   read.** `apply-d3-threshold` computes `effective_pixel_support_px` for
   every high-confidence site from Maus geometry against the canonical
   EPSG:3577 30 m grid (origin aligned to the DEA collection-3 96 000 m tile
   lattice), via `pixel_support.build_pixel_support`. Grid identity for
   ACTUAL raster reads in `build-d3-inputs` additionally binds the product
   tile identity per D2.
8. **Live spectral capture is deferred** (Task 16 Step 6): fixture rasters
   prove the chain; the real windowed reads run on luminosity
   (`/mnt/data` scratch, per `docs/checkpoints/batch-c-result.md`) with an
   explicit `--date`, budgeted per 800×800 block, and fill the Batch D
   checkpoint.

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
    rng_free = np.linspace(0.1, 0.9, n)
    bands = {
        "nbart_nir": rng_free,
        "nbart_swir_1": np.full(n, 0.2),
        "nbart_swir_2": np.full(n, 0.1),
    }
    full = d3_inputs.geomedian_metrics(bands)
    nir = rng_free
    assert full["nbr"] == pytest.approx(np.mean((nir - 0.1) / (nir + 0.1)))
    assert full["ndmi"] == pytest.approx(np.mean((nir - 0.2) / (nir + 0.2)))
    reduced = d3_inputs.geomedian_metrics({k: v[:9] for k, v in bands.items()})
    assert reduced["nbr"] != full["nbr"]


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


def test_replicate_aggregation_median_and_p90():
    errors = np.array([abs(x) for x in range(-50, 50)], dtype=float)
    agg = d3_inputs.aggregate_replicate_errors(errors)
    assert agg["replicate_median_abs_error"] == pytest.approx(np.median(errors))
    assert agg["replicate_p90_abs_error"] == pytest.approx(
        np.percentile(errors, 90)
    )
    assert agg["n_replicates"] == 100


def test_spearman_matches_rank_pearson():
    full = pd.Series([0.1, 0.4, 0.2, 0.9, 0.7])
    reduced = pd.Series([0.15, 0.35, 0.25, 0.85, 0.75])
    rho = d3_inputs.spearman(full, reduced)
    expected = full.rank().corr(reduced.rank())
    assert rho == pytest.approx(expected)


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
seed material is pre-registered by the caller (protocol digest + site +
collection + year) and never derived from a clock or process state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

Member = tuple[str, int, int]  # (tile_id, row, col)

#: Geomedian band -> metric formulas (pre-registered; decision 5).
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


def geomedian_metrics(bands: Mapping[str, np.ndarray]) -> dict[str, float]:
    """Spatial mean of the per-pixel index over the given pixel arrays."""
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


def aggregate_replicate_errors(abs_errors: np.ndarray) -> dict[str, float | int]:
    return {
        "replicate_median_abs_error": float(np.median(abs_errors)),
        "replicate_p90_abs_error": float(np.percentile(abs_errors, 90)),
        "n_replicates": int(abs_errors.size),
    }


def spearman(full: pd.Series, reduced: pd.Series) -> float:
    if len(full) < MIN_SPEARMAN_YEARS or len(full) != len(reduced):
        raise D3InputsError(
            f"spearman needs >= {MIN_SPEARMAN_YEARS} paired years, got "
            f"{len(full)} vs {len(reduced)}"
        )
    return float(full.rank().corr(reduced.rank()))
```

Note `test_replicate_aggregation_median_and_p90` passes 100 values, so
`n_replicates` reports the array size — the CLI asserts it equals the
protocol's 100 before persisting.

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_d3_inputs.py -q`
Expected: PASS (9 tests)

---

### Task 11: `d3_inputs.py` — windowed extraction + simulation orchestration

**Files:**
- Modify: `src/wa_mine_monitor/d3_inputs.py`
- Modify: `tests/test_d3_inputs.py`

Adds the raster seam and the per-site simulation driver. Fixture tests
write tiny LOCAL GeoTIFFs with rasterio into `tmp_path` — no network.

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
        grid = d3_inputs.grid_spec_from_dataset(dataset, tile_id="x0y0")
    assert grid.crs == "EPSG:3577"
    assert grid.width == 10 and grid.height == 10
    assert grid.transform[0] == 30.0 and grid.transform[4] == -30.0


def test_read_member_values_windows_only_the_member_bounds(tmp_path):
    import rasterio

    path = tmp_path / "band.tif"
    array = np.arange(100, dtype=np.int16).reshape(10, 10)
    _write_geotiff(path, array)
    members = (("x0y0", 2, 3), ("x0y0", 4, 7))
    with rasterio.open(path) as dataset:
        values = d3_inputs.read_member_values(dataset, members)
    assert values.tolist() == [23, 47]


def test_simulate_site_year_produces_full_and_reduced_rows():
    protocol_digest = "d" * 64
    n = 144
    bands = {
        "nbart_nir": np.linspace(0.2, 0.8, n),
        "nbart_swir_1": np.full(n, 0.2),
        "nbart_swir_2": np.full(n, 0.1),
    }
    members = tuple(("x0y0", r, c) for r in range(12) for c in range(12))
    rows, spearman_series = d3_inputs.simulate_site_year(
        site_id="S1",
        year=2005,
        source_id="dea_gm_ls5t",
        members=members,
        band_values=bands,
        kind="geomedian",
        supports=(9, 16),
        replicates=25,
        protocol_digest=protocol_digest,
    )
    frame = pd.DataFrame(rows)
    assert set(frame["metric_id"]) == {"nbr", "ndmi"}
    assert set(frame["support_px"]) == {9, 16}
    assert (frame["n_replicates"] == 25).all()
    full_nbr = frame[frame["metric_id"] == "nbr"]["full_value"].unique()
    assert len(full_nbr) == 1
    # reduced-value series returned for the spearman stage: one per
    # metric x support x replicate.
    assert set(spearman_series.keys()) == {
        ("nbr", 9), ("nbr", 16), ("ndmi", 9), ("ndmi", 16),
    }
    assert all(len(v) == 25 for v in spearman_series.values())


def test_simulate_site_year_refuses_below_144_support():
    members = tuple(("x0y0", 0, c) for c in range(100))
    with pytest.raises(d3_inputs.D3InputsError, match="144"):
        d3_inputs.simulate_site_year(
            site_id="S1", year=2005, source_id="dea_gm_ls5t",
            members=members,
            band_values={"nbart_nir": np.zeros(100)},
            kind="geomedian", supports=(9,), replicates=5,
            protocol_digest="d" * 64,
        )


def test_simulate_site_year_refuses_null_pixels_in_full_support():
    n = 144
    bands = {
        "nbart_nir": np.linspace(0.2, 0.8, n),
        "nbart_swir_1": np.full(n, 0.2),
        "nbart_swir_2": np.full(n, 0.1),
    }
    bands["nbart_nir"][3] = np.nan
    members = tuple(("x0y0", r, c) for r in range(12) for c in range(12))
    result = d3_inputs.simulate_site_year(
        site_id="S1", year=2005, source_id="dea_gm_ls5t",
        members=members, band_values=bands, kind="geomedian",
        supports=(9,), replicates=5, protocol_digest="d" * 64,
    )
    assert result is None  # not a full-support computable year
```

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_d3_inputs.py -q`
Expected: new tests FAIL with `AttributeError` on `grid_spec_from_dataset`

**Step 3: Implement**

```python
def grid_spec_from_dataset(dataset: "rasterio.DatasetReader", *, tile_id: str) -> pixel_support.GridSpec:
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
    dataset: "rasterio.DatasetReader", members: Sequence[Member]
) -> np.ndarray:
    """One windowed read covering the member bounding box, then index out
    exactly the member pixels, in member order."""
    from rasterio.windows import Window

    rows = [m[1] for m in members]
    cols = [m[2] for m in members]
    row_lo, row_hi = min(rows), max(rows)
    col_lo, col_hi = min(cols), max(cols)
    window = Window(
        col_off=col_lo, row_off=row_lo,
        width=col_hi - col_lo + 1, height=row_hi - row_lo + 1,
    )
    block = dataset.read(1, window=window)
    return np.array([block[r - row_lo, c - col_lo] for _, r, c in members])


def simulate_site_year(
    *,
    site_id: str,
    year: int,
    source_id: str,
    members: Sequence[Member],
    band_values: Mapping[str, np.ndarray],
    kind: str,  # "geomedian" | "fc"
    supports: Sequence[int],
    replicates: int,
    protocol_digest: str,
) -> tuple[list[dict[str, object]], dict[tuple[str, int], list[float]]] | None:
    """Full + reduced metrics for one site-year-collection.

    Returns None when the year is not full-support computable (support
    below 144 is a refusal -- a protocol violation by the caller -- but a
    NULL PIXEL inside the full set is a data property: the year is simply
    not computable at full support, so it contributes nothing).
    """
    distinct = sorted(set(members))
    if len(distinct) < d3_protocol.MIN_FULL_SUPPORT_PX:
        raise D3InputsError(
            f"full support {len(distinct)} is below the frozen minimum "
            f"{d3_protocol.MIN_FULL_SUPPORT_PX} -- caller must not submit "
            "this site"
        )
    stacked = np.vstack([band_values[b] for b in sorted(band_values)])
    valid_mask = ~np.isnan(stacked).any(axis=0)
    if not valid_mask.all():
        return None

    metric_fn = geomedian_metrics if kind == "geomedian" else fc_metrics
    full = metric_fn(band_values)
    member_index = {m: i for i, m in enumerate(distinct)}
    seed_material = f"{protocol_digest}|{site_id}|{source_id}|{year}"

    rows: list[dict[str, object]] = []
    spearman_series: dict[tuple[str, int], list[float]] = {}
    for support in supports:
        per_metric_errors: dict[str, list[float]] = {m: [] for m in full}
        per_metric_reduced: dict[str, list[float]] = {m: [] for m in full}
        for replicate in range(replicates):
            sample = sample_support(
                distinct, support, replicate=replicate, seed_material=seed_material
            )
            indices = [member_index[m] for m in sample]
            reduced = metric_fn(
                {band: values[indices] for band, values in band_values.items()}
            )
            for metric, value in reduced.items():
                per_metric_errors[metric].append(abs(value - full[metric]))
                per_metric_reduced[metric].append(value)
        for metric, errors in per_metric_errors.items():
            aggregate = aggregate_replicate_errors(np.array(errors))
            rows.append(
                {
                    "site_id": site_id,
                    "year": year,
                    "source_id": source_id,
                    "metric_id": metric,
                    "support_px": support,
                    "full_support_px": len(distinct),
                    "valid_support_px": int(valid_mask.sum()),
                    "full_value": full[metric],
                    **aggregate,
                    "protocol_digest": protocol_digest,
                }
            )
            spearman_series[(metric, support)] = per_metric_reduced[metric]
    return rows, spearman_series
```

Imports: `from wa_mine_monitor import d3_protocol, pixel_support`; rasterio
is imported lazily inside the two dataset functions (keeps module import
cheap for the pure-simulation tests).

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_d3_inputs.py -q`
Expected: PASS (14 tests)

---

### Task 12: `build-d3-inputs` CLI

**Files:**
- Modify: `src/wa_mine_monitor/cli.py`
- Modify: `tests/test_cli.py`

The largest command in the batch. Verification chain (each gate individually
refused with the standard JSON shape, in this order):

1. Frozen protocol: `_latest_curated_dated_dir(data_root/"curated"/
   "d3-protocol", label="curated/d3-protocol")`, load `protocol.json`,
   recompute `d3_protocol.protocol_digest(load_protocol(--protocol-config))`
   — refuse on mismatch ("protocol drifted after freezing").
2. Enriched register: latest `curated/register/<date>/`, digest-verified;
   must carry `register.DEA_COVERAGE_COLUMNS` (a bare Batch B register
   refuses, naming `build-dea-coverage`) — same gate `derive-dea-volume`
   uses; read that command's step 1-2 block and mirror it.
3. Crosswalk: latest `curated/crosswalk/<date>/`, digest-verified;
   `tier1_population`.
4. Footprint areas: latest `curated/maus_footprint_areas/<date>/`,
   digest-verified; **Maus sha256 equality gate** between crosswalk
   manifest and footprint-areas manifest — mirror `derive-dea-volume`'s
   refusal wording and rationale.
5. Regions snapshot: `_verify_snapshot_or_refuse(data_root/"raw"/
   "wa_rdc_regions"/<latest>, source_id="wa_rdc_regions",
   required_files=("regions.gpkg",))`; `wa_regions.load_regions`.
6. Maus geometry: `register.latest_snapshot(data_root, "maus_v2")` +
   `_verify_snapshot_or_refuse(..., required_files=("wa_extract.gpkg",))`;
   its sha256 must ALSO equal the crosswalk manifest's Maus digest (the
   compactness scalars must come from the same snapshot the ids came
   from — same drift argument as the footprint gate).
7. Catalogue: the DEA STAC snapshot named by the enriched register's own
   manifest `resolved_args["catalogue_date"]`, `_verify_snapshot_or_refuse`
   + `_load_dea_items` — mirror `derive-dea-volume`.

Then, in order (pre-registered; decision 8 defers the live run):

- Compactness per `maus_id`: read `wa_extract.gpkg`, reproject to
  `crosswalk.TARGET_CRS`, `compactness = 4 * math.pi * area / perimeter**2`
  per polygon (MultiPolygon: area/perimeter of the union as-is);
  `shape_class` via protocol.
- Candidate footprints: tier1 sites joined to footprint stats
  (`join_site_footprints`); region via `assign_regions` on register
  lon/lat points reprojected to TARGET_CRS; commodity via
  `classify_commodity` on the register's raw commodity text.
- Candidate filter (NO raster read yet): effective support ≥ 144 measured
  by `pixel_support.build_pixel_support` against each footprint's
  ACTUAL tile grids (grids read from the catalogue's asset hrefs via
  `d3_inputs.grid_spec_from_dataset`); sites whose geometry returns
  not-computed are disclosed, not silently dropped.
- Extraction over candidates: for each candidate site x full-support year
  (a year with an epoch item in every required asset), read member pixel
  values per band (`read_member_values`), decode via `dea_raster`, and run
  `simulate_site_year`. Count full-support computable years per footprint.
- Adequacy + selection: `stratum_adequacy` + `select_stratum_footprints`
  over the measured `n_full_support_years`. ONLY selected footprints'
  rows are persisted; candidate counts and per-stratum adequacy go in the
  manifest disclosures. (Selection depends on computability alone — no
  metric VALUE feeds selection; that is the D13 acceptance "No accuracy
  result can change sample definitions".)
- Spearman table: for each selected site x collection x metric x support x
  replicate, the Spearman over that site's full-support years between the
  full-value series and the replicate's reduced-value series.

Output: `curated/d3-inputs/<date>/support_inputs.parquet` +
`support_spearman.parquet` + ONE run manifest per table (the second table's
manifest lists the first as an input — mirror how multi-file outputs are
handled elsewhere; if no precedent exists, manifest each independently with
identical inputs). Schemas:

```python
D3_SUPPORT_INPUTS_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string(), nullable=False),
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
        pa.field("replicate_median_abs_error", pa.float64(), nullable=False),
        pa.field("replicate_p90_abs_error", pa.float64(), nullable=False),
        pa.field("n_replicates", pa.int64(), nullable=False),
        pa.field("protocol_digest", pa.string(), nullable=False),
    ]
)

D3_SPEARMAN_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string(), nullable=False),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("metric_id", pa.string(), nullable=False),
        pa.field("support_px", pa.int64(), nullable=False),
        pa.field("replicate", pa.int64(), nullable=False),
        pa.field("spearman", pa.float64(), nullable=False),
        pa.field("n_years", pa.int64(), nullable=False),
        pa.field("protocol_digest", pa.string(), nullable=False),
    ]
)
```

(Declare both in `d3_inputs.py`, not cli.py, next to the row builders.)

**Step 1: Write the failing CLI tests** — fixture chain:

```python
# --- build-d3-inputs CLI command --------------------------------------------
```

Extend `_seed_derive_dea_volume_chain` (read it first) into
`_seed_d3_inputs_chain(tmp_path, monkeypatch)` that ALSO: seeds the RDC
regions snapshot (reuse Task 3's fixture bytes + the real
`fetch-region-boundaries` CLI, monkeypatched fetch seam); freezes the
protocol (`freeze-d3-protocol` via `runner.invoke`); and rewrites the DEA
fixture assets so each item's asset hrefs point at LOCAL GeoTIFFs written
into the snapshot directory with `_write_geotiff`-style content (reuse
Task 11's helper from `tests/test_d3_inputs.py` — import it, do not
duplicate) sized so the fixture Maus polygon covers ≥144 centres. Tests:

```python
def test_build_d3_inputs_end_to_end_over_fixtures(tmp_path, monkeypatch):
    cfg_file = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        ["build-d3-inputs", "--config", str(cfg_file), "--date", "2026-08-18"],
    )
    assert result.exit_code == 0, result.output
    out_dir = tmp_path / "data" / "curated" / "d3-inputs" / "2026-08-18"
    inputs = tables.read_table(out_dir / "support_inputs.parquet")
    assert (inputs["protocol_digest"] == inputs["protocol_digest"].iloc[0]).all()
    assert (inputs["n_replicates"] == 100).all()
    assert set(inputs["support_px"]) <= {9, 16, 25, 36, 49, 64, 100, 144}
    payload = json.loads(result.output)
    assert payload["n_selected_footprints"] >= 1
    assert payload["n_candidate_footprints"] >= payload["n_selected_footprints"]


def test_build_d3_inputs_refuses_drifted_protocol(tmp_path, monkeypatch):
    cfg_file = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    d3_file = tmp_path / "d3.yaml"  # the copy the seed helper froze
    raw = yaml.safe_load(d3_file.read_text())
    raw["d3"]["commodity_token_rules"].append({"group": "other", "tokens": ["x"]})
    d3_file.write_text(yaml.safe_dump(raw))
    result = runner.invoke(
        app,
        ["build-d3-inputs", "--config", str(cfg_file), "--date", "2026-08-18"],
    )
    assert result.exit_code == 1
    assert "drift" in result.output


def test_build_d3_inputs_refuses_bare_batch_b_register(tmp_path, monkeypatch):
    # Seed WITHOUT running build-dea-coverage: latest register lacks the
    # DEA coverage columns.
    ...
    assert result.exit_code == 1
    assert "build-dea-coverage" in result.output


def test_build_d3_inputs_refuses_maus_digest_mismatch(tmp_path, monkeypatch):
    # Re-seed the footprint areas from a DIFFERENT Maus snapshot date, then
    # assert the refusal names the digest gate.
    ...
    assert result.exit_code == 1
    assert "sha256" in result.output
```

The two elided arrange blocks follow the seed-helper composition pattern
`test_cli.py` already uses for `derive-dea-volume`'s refusal tests — read
those tests first and mirror their arrangement rather than inventing one.

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k build_d3_inputs -q`
Expected: FAIL (unknown command)

**Step 3: Implement the command** following the numbered gate order above,
the exemplar body structure (Conventions), and `derive-dea-volume` for the
shared gates. Success echo (keys, exact):

```python
{
    "output_dir": ..., "protocol_digest": ...,
    "n_candidate_footprints": ..., "n_selected_footprints": ...,
    "n_selected_sites": ..., "n_strata_adequate": ...,
    "n_strata_inadequate": ..., "n_site_years_simulated": ...,
    "n_site_years_not_computable": ...,
    "n_sites_support_not_computed": ...,
    "region_ambiguity": {...}, "manifest_paths": [...],
}
```

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k build_d3_inputs -q`
Expected: PASS (4 tests)

**Step 5: Run the full battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: clean, full suite green.

---

### Task 13: `d3_threshold.py` — threshold evaluation (D4 pure core)

**Files:**
- Create: `src/wa_mine_monitor/d3_threshold.py`
- Create: `tests/test_d3_threshold.py`

Pure evaluation over the Task 12 tables. The criteria are the frozen
protocol's (`REQUIRED_CRITERIA`, Task 4): per stratum × metric × support —

- geomedian metrics (nbr, ndmi): P90 across site-years of
  `replicate_p90_abs_error` ≤ **0.03** (index units);
- FC metrics: same statistic ≤ **5.0** (percentage points);
- Spearman: median across site × replicate of `spearman` ≥ **0.95**;
- computable fraction: site-years contributing at that support ÷
  site-years contributing at full support ≥ **0.90**.

A support PASSES when every adequate stratum passes every criterion for
every metric of both kinds. `n_star` = smallest passing support. If no
support ≤ 144 passes: `criteria_passed=false`, `n_star=144` (fall back to
the full-support minimum — D13 §4 "the threshold never relaxes below the
frozen floor"). `nominal_area_m2 = 900 * n_star`.

**Step 1: Write the failing tests**

```python
# tests/test_d3_threshold.py
"""D3 threshold evaluation (D13 Batch D task D4)."""

import pandas as pd
import pytest

from wa_mine_monitor import d3_threshold

def _inputs_frame(*, p90_by_support):
    rows = []
    for support, p90 in p90_by_support.items():
        for year in (2000, 2001, 2002):
            rows.append(
                {
                    "site_id": "S1", "maus_id": "M1",
                    "region": "pilbara", "commodity_group": "iron_ore",
                    "shape_class": "compact", "year": year,
                    "source_id": "dea_gm_ls5t", "metric_id": "nbr",
                    "support_px": support, "full_support_px": 200,
                    "valid_support_px": 200, "full_value": 0.5,
                    "replicate_median_abs_error": p90 / 2,
                    "replicate_p90_abs_error": p90,
                    "n_replicates": 100, "protocol_digest": "d" * 64,
                }
            )
    return pd.DataFrame(rows)


def _spearman_frame(*, rho_by_support):
    rows = []
    for support, rho in rho_by_support.items():
        for replicate in range(5):
            rows.append(
                {
                    "site_id": "S1", "source_id": "dea_gm_ls5t",
                    "metric_id": "nbr", "support_px": support,
                    "replicate": replicate, "spearman": rho,
                    "n_years": 3, "protocol_digest": "d" * 64,
                }
            )
    return pd.DataFrame(rows)


def test_smallest_passing_support_wins():
    inputs = _inputs_frame(p90_by_support={9: 0.08, 16: 0.02, 25: 0.01, 144: 0.0})
    spearman = _spearman_frame(rho_by_support={9: 0.80, 16: 0.99, 25: 0.99, 144: 1.0})
    result = d3_threshold.evaluate_threshold(
        inputs, spearman, adequate_strata=[("pilbara", "iron_ore", "compact")]
    )
    assert result.criteria_passed is True
    assert result.n_star == 16
    assert result.nominal_area_m2 == 900 * 16


def test_no_passing_support_falls_back_to_144():
    inputs = _inputs_frame(p90_by_support={9: 0.5, 144: 0.5})
    spearman = _spearman_frame(rho_by_support={9: 0.1, 144: 0.1})
    result = d3_threshold.evaluate_threshold(
        inputs, spearman, adequate_strata=[("pilbara", "iron_ore", "compact")]
    )
    assert result.criteria_passed is False
    assert result.n_star == 144


def test_fc_metrics_use_percentage_point_tolerance():
    inputs = _inputs_frame(p90_by_support={16: 4.0, 144: 0.0})
    inputs["metric_id"] = "bare_soil"
    inputs["source_id"] = "dea_fc_pc"
    spearman = _spearman_frame(rho_by_support={16: 0.99, 144: 1.0})
    spearman["metric_id"] = "bare_soil"
    spearman["source_id"] = "dea_fc_pc"
    result = d3_threshold.evaluate_threshold(
        inputs, spearman, adequate_strata=[("pilbara", "iron_ore", "compact")]
    )
    # 4.0 pp is within the 5 pp tolerance -> 16 passes; the same number
    # against the geomedian 0.03 tolerance would fail.
    assert result.n_star == 16


def test_computable_fraction_gate_fails_a_support():
    inputs = _inputs_frame(p90_by_support={16: 0.01, 144: 0.0})
    # Drop 2 of 3 years at support 16: fraction 1/3 < 0.90.
    mask = (inputs["support_px"] == 16) & (inputs["year"] > 2000)
    inputs = inputs[~mask]
    spearman = _spearman_frame(rho_by_support={16: 0.99, 144: 1.0})
    result = d3_threshold.evaluate_threshold(
        inputs, spearman, adequate_strata=[("pilbara", "iron_ore", "compact")]
    )
    assert result.n_star == 144
    assert result.criteria_passed is False


def test_inadequate_strata_are_excluded_from_the_gate():
    inputs = _inputs_frame(p90_by_support={16: 0.01, 144: 0.0})
    bad = _inputs_frame(p90_by_support={16: 0.9, 144: 0.0})
    bad["region"] = "other_wa"
    spearman = _spearman_frame(rho_by_support={16: 0.99, 144: 1.0})
    result = d3_threshold.evaluate_threshold(
        pd.concat([inputs, bad], ignore_index=True),
        spearman,
        adequate_strata=[("pilbara", "iron_ore", "compact")],  # other_wa NOT adequate
    )
    assert result.n_star == 16


def test_mixed_protocol_digest_is_refused():
    inputs = _inputs_frame(p90_by_support={16: 0.01, 144: 0.0})
    inputs.loc[0, "protocol_digest"] = "e" * 64
    spearman = _spearman_frame(rho_by_support={16: 0.99, 144: 1.0})
    with pytest.raises(d3_threshold.D3ThresholdError, match="digest"):
        d3_threshold.evaluate_threshold(
            inputs, spearman, adequate_strata=[("pilbara", "iron_ore", "compact")]
        )


def test_per_support_detail_is_returned_for_the_report():
    inputs = _inputs_frame(p90_by_support={9: 0.08, 16: 0.02, 144: 0.0})
    spearman = _spearman_frame(rho_by_support={9: 0.80, 16: 0.99, 144: 1.0})
    result = d3_threshold.evaluate_threshold(
        inputs, spearman, adequate_strata=[("pilbara", "iron_ore", "compact")]
    )
    detail = {d["support_px"]: d for d in result.per_support}
    assert detail[9]["passed"] is False
    assert detail[16]["passed"] is True
    assert "criteria" in detail[9]
```

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_d3_threshold.py -q`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement**

```python
# src/wa_mine_monitor/d3_threshold.py
"""D3 threshold evaluation (D13 Batch D task D4).

Evaluates the frozen accuracy criteria over the Task-D3 simulation tables
and returns the smallest passing effective-pixel support. The criteria
values live in the frozen protocol; this module hard-codes the SAME frozen
numbers and the CLI cross-checks them against the loaded protocol before
calling in -- two sources must agree or the run refuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from wa_mine_monitor import d3_inputs, d3_protocol

GEOMEDIAN_P90_TOLERANCE = 0.03
FC_P90_TOLERANCE_PP = 5.0
SPEARMAN_MIN_MEDIAN = 0.95
COMPUTABLE_FRACTION_MIN = 0.90

_FC_METRICS = frozenset(d3_inputs.FC_METRIC_ASSETS)
_STRATUM_KEYS = ["region", "commodity_group", "shape_class"]


class D3ThresholdError(ValueError):
    """Threshold evaluation input violated the frozen protocol -- refused."""


@dataclass(frozen=True)
class ThresholdResult:
    n_star: int
    criteria_passed: bool
    nominal_area_m2: int
    protocol_digest: str
    per_support: tuple[dict[str, object], ...] = field(default=())


def _single_digest(*frames: pd.DataFrame) -> str:
    digests = set()
    for frame in frames:
        digests.update(frame["protocol_digest"].unique())
    if len(digests) != 1:
        raise D3ThresholdError(
            f"expected one protocol digest across inputs, found {len(digests)}"
        )
    return digests.pop()


def _support_criteria(
    inputs: pd.DataFrame,
    spearman: pd.DataFrame,
    support: int,
    stratum: tuple[str, str, str],
) -> dict[str, object]:
    sel = inputs[
        (inputs["support_px"] == support)
        & (inputs[_STRATUM_KEYS].apply(tuple, axis=1) == stratum)
    ]
    full = inputs[
        (inputs["support_px"] == d3_protocol.MIN_FULL_SUPPORT_PX)
        & (inputs[_STRATUM_KEYS].apply(tuple, axis=1) == stratum)
    ]
    criteria: dict[str, object] = {}
    passed = True
    for metric_id, group in sel.groupby("metric_id"):
        tolerance = (
            FC_P90_TOLERANCE_PP
            if metric_id in _FC_METRICS
            else GEOMEDIAN_P90_TOLERANCE
        )
        p90 = float(group["replicate_p90_abs_error"].quantile(0.9))
        ok = p90 <= tolerance
        criteria[f"p90_abs_error:{metric_id}"] = {"value": p90, "passed": ok}
        passed = passed and ok

        sp = spearman[
            (spearman["support_px"] == support)
            & (spearman["metric_id"] == metric_id)
            & (spearman["site_id"].isin(group["site_id"].unique()))
        ]
        rho = float(sp["spearman"].median()) if len(sp) else float("nan")
        ok = len(sp) > 0 and rho >= SPEARMAN_MIN_MEDIAN
        criteria[f"spearman_median:{metric_id}"] = {"value": rho, "passed": ok}
        passed = passed and ok

    n_full_site_years = len(
        full[["site_id", "source_id", "year"]].drop_duplicates()
    )
    n_here = len(sel[["site_id", "source_id", "year"]].drop_duplicates())
    fraction = n_here / n_full_site_years if n_full_site_years else 0.0
    ok = fraction >= COMPUTABLE_FRACTION_MIN
    criteria["computable_fraction"] = {"value": fraction, "passed": ok}
    passed = passed and ok

    criteria["passed"] = passed
    return criteria


def evaluate_threshold(
    inputs: pd.DataFrame,
    spearman: pd.DataFrame,
    *,
    adequate_strata: list[tuple[str, str, str]],
) -> ThresholdResult:
    digest = _single_digest(inputs, spearman)
    if not adequate_strata:
        raise D3ThresholdError("no adequate strata -- nothing to evaluate")

    per_support: list[dict[str, object]] = []
    n_star: int | None = None
    for support in d3_protocol.REQUIRED_SUPPORTS:
        stratum_results = {
            "/".join(s): _support_criteria(inputs, spearman, support, s)
            for s in adequate_strata
        }
        passed = all(bool(r["passed"]) for r in stratum_results.values())
        per_support.append(
            {"support_px": support, "passed": passed, "criteria": stratum_results}
        )
        if passed and n_star is None:
            n_star = support

    criteria_passed = n_star is not None
    if n_star is None:
        n_star = d3_protocol.MIN_FULL_SUPPORT_PX
    return ThresholdResult(
        n_star=n_star,
        criteria_passed=criteria_passed,
        nominal_area_m2=900 * n_star,
        protocol_digest=digest,
        per_support=tuple(per_support),
    )
```

Note the P90-of-P90s statistic: each row already carries the per-site-year
`replicate_p90_abs_error`; the stratum criterion takes the 0.9 quantile of
those across site-years. That is the D13 §4 reading ("P90 across the
sampled footprint-years of the replicate P90 absolute error"). If the
implementing agent reads D13 §4 differently, STOP and escalate rather than
pick silently — the statistic is frozen with the protocol.

Also note support 144 itself always "passes" trivially in the loop
(errors are 0 by construction). That is correct — 144 IS the fallback —
but `criteria_passed` must reflect whether a REDUCED support passed; if
the loop finds only 144, treat it as no reduced support passing:

```python
    criteria_passed = n_star is not None and n_star < d3_protocol.MIN_FULL_SUPPORT_PX
    if not criteria_passed:
        n_star = d3_protocol.MIN_FULL_SUPPORT_PX
```

Use this refined block, not the simpler one above, and keep both tests
(`test_no_passing_support_falls_back_to_144` covers it; add an assertion
to `test_smallest_passing_support_wins` that 144 alone would set
`criteria_passed` False if you find the distinction untested).

**Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_d3_threshold.py -q`
Expected: PASS (7 tests)

---

### Task 14: `derive-d3-threshold` CLI

**Files:**
- Modify: `src/wa_mine_monitor/cli.py`
- Modify: `tests/test_cli.py`

**Command:** `derive-d3-threshold --config ... --date ...`

Gates, in order: (1) frozen protocol loaded + digest recomputed from
`--protocol-config` (same gate as `build-d3-inputs`); (2) latest
`curated/d3-inputs/<date>/` digest-verified via its manifests; (3) BOTH
tables' `protocol_digest` column must equal the frozen digest (refuse
"inputs built under a different protocol"); (4) cross-check the module's
hard-coded criteria constants against the loaded protocol's
`REQUIRED_CRITERIA` — refuse on any mismatch ("criteria drift between
d3_threshold module and frozen protocol").

Adequate strata are recomputed from the inputs table itself: group by
(region, commodity_group, shape_class), count footprints with ≥10
full-support years (rows at support 144, distinct maus_id with ≥10
distinct years), keep strata meeting `Adequacy` — the SAME
`stratum_adequacy` call Task 12 used, so the report can never claim
adequacy the inputs don't support.

Output: `reports/d3-threshold/<date>/threshold.json` (mirror
`derive-dea-volume`'s reports layout) containing the full
`ThresholdResult` serialized (n_star, criteria_passed, nominal_area_m2,
protocol_digest, per_support detail), plus `adequate_strata`,
`inadequate_strata` (with their counts), and the input table paths +
digests. Manifest inputs: both parquet files + protocol.json. Success
echo: `{"output_path", "n_star", "criteria_passed", "nominal_area_m2",
"n_strata_adequate", "n_strata_inadequate", "manifest_path"}`.

**Step 1: Write the failing tests**

```python
def test_derive_d3_threshold_end_to_end(tmp_path, monkeypatch):
    cfg_file = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    result = runner.invoke(
        app, ["build-d3-inputs", "--config", str(cfg_file), "--date", "2026-08-18"]
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        ["derive-d3-threshold", "--config", str(cfg_file), "--date", "2026-08-19"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["n_star"] in {9, 16, 25, 36, 49, 64, 100, 144}
    report = json.loads(
        (tmp_path / "data" / "reports" / "d3-threshold" / "2026-08-19"
         / "threshold.json").read_text()
    )
    assert report["nominal_area_m2"] == 900 * payload["n_star"]
    assert report["protocol_digest"] == payload_digest_from_freeze  # bind via seed helper


def test_derive_d3_threshold_refuses_digest_mismatch(tmp_path, monkeypatch):
    # Build inputs, then re-freeze a MODIFIED protocol so the frozen digest
    # no longer matches the tables' protocol_digest column.
    ...
    assert result.exit_code == 1
    assert "different protocol" in result.output


def test_derive_d3_threshold_refuses_missing_inputs(tmp_path, monkeypatch):
    cfg_file = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        ["derive-d3-threshold", "--config", str(cfg_file), "--date", "2026-08-19"],
    )
    assert result.exit_code == 1
    assert "d3-inputs" in result.output
```

(`payload_digest_from_freeze`: have `_seed_d3_inputs_chain` return the
frozen digest alongside the config path — adjust the Task 12 tests'
unpacking accordingly, or return a small named tuple.)

**Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -k derive_d3_threshold -q`
Expected: FAIL (unknown command)

**Step 3: Implement the command** per the gate order and output contract
above.

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

**Part A — schema.** Add to `register.py`:

```python
D3_ELIGIBILITY_COLUMNS = (
    "effective_pixel_support_px",  # int64, nullable (null = not computed)
    "d3_threshold_px",             # int64, non-null (the applied n_star)
    "d3_eligible",                 # bool, non-null
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

plus `ELIGIBLE_REGISTER_SCHEMA` = `ENRICHED_REGISTER_SCHEMA` + the four
fields (`effective_pixel_support_px` nullable int64; others non-null;
follow how `ENRICHED_REGISTER_SCHEMA` extends the base — read it first).

Status assignment rules (exactly one per site, first match wins):
1. site not in `tier1_population` high-confidence crosswalk →
   `crosswalk_not_high_confidence`;
2. crosswalked but footprint geometry missing/invalid (support
   not-computed, `None` from `build_pixel_support`) →
   `no_usable_footprint`;
3. support computed but `< d3_threshold_px` →
   `insufficient_pixel_support`;
4. threshold report has `criteria_passed` False → every otherwise-eligible
   site gets `threshold_not_computed` (the 144 fallback is applied as the
   threshold but flagged — D13: eligibility under an unvalidated
   threshold is disclosed, not silently granted). `d3_eligible` is True
   ONLY for `trajectory_status == "eligible"`.
5. otherwise → `eligible`.

Register tests (in `tests/test_register.py`): schema field
count/nullability; a table with a status outside `_TRAJECTORY_STATUSES`
fails validation; `d3_eligible` True with a non-eligible status fails
validation (add a consistency check to the existing validate function —
read how `ENRICHED_REGISTER_SCHEMA` validation hooks in first).

**Part B — CLI.** `apply-d3-threshold --config ... --date ...`:

Gates: enriched register (with coverage columns, digest-verified) +
crosswalk + footprint areas (Maus digest equality, as Task 12 gates 2–4)
+ latest `reports/d3-threshold/<date>/threshold.json` digest-verified +
its `protocol_digest` equals the frozen protocol's. Per-site support
comes from the `build-d3-inputs` support computation — but Task 12 only
persisted SELECTED footprints. Persist per-site support in Task 12 as a
third table `site_support.parquet` (site_id, maus_id, effective_pixel
_support_px nullable, support_not_computed_reason nullable string,
protocol_digest) — go back and add it to Task 12's outputs, schema next
to the other two, one more assertion in the end-to-end test. This command
then joins register × site_support × threshold.

Output: `curated/register/<date>/register.parquet` under
`ELIGIBLE_REGISTER_SCHEMA` (a NEW dated register version — same
convention as `build-dea-coverage`, distinct date). Success echo:
`{"output_path", "d3_threshold_px", "criteria_passed", "n_eligible",
"n_by_status": {...}, "rows": ..., "manifest_path"}` with rows-in =
rows-out asserted.

**Step 1: Write the failing tests**

```python
def test_apply_d3_threshold_assigns_every_site_exactly_one_status(tmp_path, monkeypatch):
    # chain: seed -> build-d3-inputs -> derive-d3-threshold -> apply
    ...
    out = tables.read_table(register_path)
    assert len(out) == n_register_rows
    assert set(out["trajectory_status"]) <= set(register._TRAJECTORY_STATUSES)
    assert (out["d3_eligible"] == (out["trajectory_status"] == "eligible")).all()
    payload = json.loads(result.output)
    assert sum(payload["n_by_status"].values()) == len(out)


def test_apply_d3_threshold_flags_unvalidated_threshold(tmp_path, monkeypatch):
    # Arrange a threshold report with criteria_passed false (edit the seed
    # fixture values so no reduced support passes), then assert every
    # otherwise-eligible site carries threshold_not_computed and
    # d3_eligible is False everywhere.
    ...


def test_apply_d3_threshold_refuses_protocol_mismatch(tmp_path, monkeypatch):
    ...
    assert result.exit_code == 1
    assert "protocol" in result.output
```

**Step 2: Run to verify they fail** — `uv run pytest tests/test_cli.py -k
apply_d3_threshold -q` → FAIL (unknown command); register tests FAIL on
missing schema.

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
run of the full chain (`fetch-region-boundaries` → `freeze-d3-protocol` →
`build-d3-inputs` → `derive-d3-threshold` → `apply-d3-threshold`) in one
test module, then assertions mapped one-to-one to D13 §4's Batch D
acceptance criteria (read lines 274–483 and quote each criterion in the
test's docstring):

```python
class TestBatchDAcceptance:
    """Each test quotes the D13 SS4 criterion it verifies."""

    def test_protocol_frozen_before_any_spectral_read(...):
        # manifest timestamps: protocol.json manifest precedes the
        # d3-inputs manifests in the chain fixture; the d3-inputs manifest
        # RECORDS the protocol digest as an input.

    def test_no_accuracy_result_can_change_sample_definitions(...):
        # selection inputs (adequacy counts, selected maus_ids) recorded in
        # the d3-inputs manifest depend only on computability counts;
        # rebuild with perturbed band VALUES (same nulls) -> identical
        # selection, different metric values.

    def test_every_register_row_has_exactly_one_trajectory_status(...):

    def test_determinism_same_inputs_same_outputs(...):
        # run build-d3-inputs twice into two dates; parquet bytes of
        # support_inputs equal after dropping the date-dependent manifest.

    def test_refusals_are_structured_json(...):
        # each refusal exercised above emitted {"refusal": ...} on stdout.
```

Fill in real bodies — the sketches name the intent; the arrange code
reuses `_seed_d3_inputs_chain`. Also verify the perturbed-values rebuild
uses a distinct `--date` (existing-output refusal otherwise).

**Step 2: Run** `uv run pytest tests/test_batch_d_acceptance.py -q` →
grow them red-to-green individually if any fail; all must pass without
touching src (they exercise already-built behaviour — a failure here is a
Task 1–15 bug: run `kit:debugging` before fixing).

**Step 3: Checkpoint skeleton** — `docs/checkpoints/batch-d-result.md`
mirroring `batch-c-result.md`'s structure: status line (fixture suite
green, live run PENDING), `_pending_` fields for the live run (frozen
protocol digest; regions fetch date + gpkg sha256; candidate/selected
footprint counts per stratum; site-years simulated / not computable;
n_star, criteria_passed, per-criterion margins; eligibility counts by
trajectory_status), and a "Live run" section stating: extraction executes
on luminosity (`/mnt/data`, per batch-c-result.md host decision),
windowed streaming reads budgeted per 800×800 block, run deferred to a
human-reviewed session — record the four-command chain with explicit
`--date` flags, mirroring the Batch C handoff format.

**Step 4: Full battery**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: all clean, full suite green (554 pre-batch tests + all new).

**Step 5 (DEFERRED — live run):** Not part of this build. The live chain
runs on luminosity in a later human-reviewed session:

```
uv run wa-mine-monitor fetch-region-boundaries --config config/<cfg>.yaml --date <YYYY-MM-DD>
uv run wa-mine-monitor freeze-d3-protocol      --config config/<cfg>.yaml --date <YYYY-MM-DD>
uv run wa-mine-monitor build-d3-inputs         --config config/<cfg>.yaml --date <YYYY-MM-DD>
uv run wa-mine-monitor derive-d3-threshold     --config config/<cfg>.yaml --date <YYYY-MM-DD>
uv run wa-mine-monitor apply-d3-threshold      --config config/<cfg>.yaml --date <YYYY-MM-DD>
```

then fill the checkpoint's `_pending_` fields. Watch items: the frozen
protocol digest must be committed BEFORE `build-d3-inputs` runs live;
`build-d3-inputs` is the batch's big network step (~hundreds of GB of
block-granular COG reads) — confirm `/mnt/data` free space ≥ 600 GB
first, and if the DPIRD-020 download URL has moved, re-verify the licence
page before re-pinning.

---

## Execution notes

- Build in a worktree via `kit:build-flow`; `kit:verify` then
  `kit:finish-branch` before calling the work done.
- Tasks 1–10 have no ordering constraints beyond their stated imports;
  Tasks 11–16 are strictly sequential.
- The full battery command appears in Tasks 12 and 16; run it at least at
  those two points and always before finishing.
