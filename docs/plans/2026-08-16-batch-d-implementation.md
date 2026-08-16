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

---

## DRAFT STATUS — PLAN INCOMPLETE, DO NOT EXECUTE

Tasks 1–3 above are fully drafted. Tasks 4–16 are outlined in
`docs/handoffs/handoff_2026-08-16_batch-d-plan-in-progress.md` together with
the module-API research needed to write them. Resume there. The plan must be
completed, then attacked by codex as a SELF-CONTAINED review package (Jarrod's
standing instruction this session), before kit:build-flow may run.
