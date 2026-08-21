# Batch D Live Run + Batch E (E3) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute Part C of this plan. Parts A and B are operator steps (git decision, live run on luminosity) and are NOT build-flow work — execute them by hand in the order given.

**Goal:** Clear the uncommitted working tree, run the Batch D live `n*` derivation on luminosity to unblock the Batch E gate, and build D13 task E3 (trajectory row schema + spectral metric functions) — the only Batch E code that does not depend on the live `n*`.

**Architecture:** Part A is a one-time git decision. Part B stages the 492 MB Mac data root and a repo clone onto luminosity (`/mnt/data`, 1.6 TB free) and runs the frozen five-command chain, then fills `docs/checkpoints/batch-d-result.md`. Part C adds `spectral_metrics.py` (thin wrappers over the already-tested `d3_inputs` metric formulas and `dea_raster` decoders, returning per-site-year metric rows with explicit not-computable reasons) and `trajectories.py` (the D13 E3 pyarrow schema, metric vocabulary, and a validated Parquet writer). E4+ (`extract-trajectories` CLI, Huntly gate) is a follow-on plan written after the live `n*` exists.

**Tech Stack:** Python 3.12, uv, typer, pyarrow, pandas, numpy, shapely, rasterio 1.5, pytest, ruff (line-length 100), mypy.

**Authority:** `docs/decisions/2026-08-16-d13-batches-c-g-detailing.md` §5 (Batch E, E1–E7) is the frozen design. It names the CLI `extract-trajectories`, not `build-trajectory-extract`. The 46-line draft `docs/plans/2026-08-18-batch-e-implementation.md` (Tasks 17–20) and the untracked `patch_cli_trajectories.py` both diverge from D13 and are superseded by this plan (Task 17 — `core/zonal.py` port — is already committed as `90a4f50` and stands).

---

## Findings that shaped this plan (2026-08-21)

- Mac data root `~/data/wa-mine-monitor` (492 MB) holds: `curated/d3-protocol/2026-08-18` (frozen), `curated/register/2026-08-17`, `curated/crosswalk/2026-08-16`, `curated/maus_footprint_areas/2026-08-16`, `raw/dea_stac/2026-08-16`, `raw/maus_v2/2026-08-16`. Missing: region boundaries, `curated/d3-inputs`, `curated/d3-threshold`.
- Mac has 25 GB free, below the 50 GB block-cache bound in the Batch D plan (design decision 17). The Batch C checkpoint already excluded the Mac by measurement. Live run host is luminosity.
- luminosity (`jarrod@192.168.1.75`): Python 3.12.3, 4 cores, 15 GB RAM, `/mnt/data` 1.6 TB free. No repo clone, no `uv`, no data root yet.
- `build-d3-inputs` is the network step: 597 GB–3.30 TB block-granular transfer. No GDAL block-cache env is configured anywhere in the repo; Part B sets it explicitly.
- `apply-d3-threshold` adds `effective_pixel_support_px`, `d3_threshold_px`, `d3_eligible`, `trajectory_status` (`register.py:1122-1138`); extraction input = rows with `trajectory_status == "eligible"`.
- Footprint geometry lives only in `raw/maus_v2/<date>/wa_extract.gpkg` (EPSG:4326, `maus_id`), joined to sites via `curated/crosswalk`. The patch script used `maus-v2` (wrong dir name) and invented `dea_coverage.build_item_index` (does not exist) — two reasons to discard it.
- Metric formulas and decoders already exist and are tested: `d3_inputs.geomedian_metrics`/`fc_metrics`/`geomedian_valid_mask`/`fc_valid_mask` (`d3_inputs.py:66-94`), `dea_raster.decode_geomedian`/`decode_fc` (`dea_raster.py:20-31`). E3 reuses them; it does not re-implement formulas.

---

## Part A — Working tree decision (Jarrod, ~5 min)

Uncommitted state in the main checkout:
- `src/wa_mine_monitor/core/zonal.py`, `tests/core/test_zonal.py` — black-style reformat only, 680 tests pass.
- `patch_cli_trajectories.py` — untracked stub that regex-appends a half-written command to `cli.py`. Never run (no `build-trajectory-extract` in `cli.py`).

Recommended:

```
cd ~/Documents/wa-mine-rehab-monitor
uv run ruff format --check src tests && uv run pytest tests/core/test_zonal.py -q
git add src/wa_mine_monitor/core/zonal.py tests/core/test_zonal.py
git commit -m "style(zonal): ruff-format Task 17 port (no behaviour change)"
rm patch_cli_trajectories.py
rm .git/claude-escalation.md
git push origin main   # origin/main is at 1d66196 (Batch C); Batch D + Task 17 are local-only. B1 clones from GitHub, so this push must happen first.
```

Expected: `ruff format --check` prints nothing (exit 0); zonal tests pass; tree clean after the `rm`s; `git status -sb` shows `main` not ahead of `origin/main`.

---

## Part B — Batch D live run on luminosity (operator runbook)

Precondition from the Batch D plan (Task 16 Step 5): `config/d3.yaml`, the frozen `protocol.json`, and its manifest must be git-committed with a clean tree before `build-d3-inputs` runs. Part A satisfies the clean tree; `main` at `90a4f50` already contains `config/d3.yaml`.

### B1. Stage repo and data root

On the Mac:

```
rsync -av --exclude '*.pre-closeout' ~/data/wa-mine-monitor/ jarrod@192.168.1.75:/mnt/data/wa-mine-monitor/
ssh jarrod@192.168.1.75 'curl -LsSf https://astral.sh/uv/install.sh | sh && \
  git clone https://github.com/jazzdos/wa-mine-rehab-monitor.git ~/wa-mine-rehab-monitor && \
  cd ~/wa-mine-rehab-monitor && ~/.local/bin/uv sync && ~/.local/bin/uv run pytest -q -x'
```

Expected: rsync transfers ~490 MB; `uv sync` resolves Python 3.12 deps; pytest reports `680 passed` (plus the zonal CRS warning seen on the Mac).

### B2. Point the config at `/mnt/data`

On luminosity, create `config/luminosity.yaml` (do NOT edit `base.yaml`; the manifest records the resolved config):

```yaml
run:
  data_root: "/mnt/data/wa-mine-monitor"
  redistribute_public: false
sources:
  minedex_public_export_blocked: true
```

Copy the values of any other keys present in `config/base.yaml` verbatim. Verify:

```
uv run wa-mine-monitor config-check --config config/luminosity.yaml
```

Expected: JSON with `data_root: /mnt/data/wa-mine-monitor`, exit 0.

### B3. Disk and cache bound check

```
df -h /mnt/data
export GDAL_CACHEMAX=4096            # MB, in-process block cache (15 GB RAM host)
export CPL_VSIL_CURL_CACHE_SIZE=53687091200   # 50 GB, decision 17 bound
export GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR
export GDAL_HTTP_MAX_RETRY=5 GDAL_HTTP_RETRY_DELAY=5
```

Expected: free space ≥ 50 GB cache bound + output headroom (it is 1.6 TB). Record `df` output for the checkpoint.

### B4. Run the frozen five-command chain

Run inside `tmux` (the network step is hours). Use one date for the whole chain, `D=$(date +%F)`:

```
uv run wa-mine-monitor fetch-region-boundaries --config config/luminosity.yaml --date $D
uv run wa-mine-monitor freeze-d3-protocol      --config config/luminosity.yaml --date $D --protocol-config config/d3.yaml
```

Expected for `freeze-d3-protocol`: REFUSAL with `{"refusal": ...single lineage...}` because `curated/d3-protocol/2026-08-18` already exists (rsynced from the Mac). That refusal is correct — keep the 2026-08-18 freeze; do not create a second dated dir. If instead it succeeds, stop: the rsync missed `curated/d3-protocol` and the chain must not proceed with two lineages.

```
uv run wa-mine-monitor build-d3-inputs    --config config/luminosity.yaml --date $D --protocol-config config/d3.yaml 2>&1 | tee /mnt/data/wa-mine-monitor/reports/build-d3-inputs-$D.log
uv run wa-mine-monitor derive-d3-threshold --config config/luminosity.yaml --date $D
uv run wa-mine-monitor apply-d3-threshold  --config config/luminosity.yaml --date $D
```

Expected outputs (each with a `.manifest.json` beside it):
- `curated/d3-inputs/$D/` — five tables incl. `footprint_support.parquet`
- `curated/d3-threshold/$D/threshold.json` — carries `n_star`, `criteria_passed`, `protocol_digest` equal to the 2026-08-18 freeze
- `curated/register/$D/register.parquet` — `ELIGIBLE_REGISTER_SCHEMA`, every row has one `trajectory_status`

`--protocol-config` is confirmed as the flag name (`cli.py:307`, `ProtocolConfigOption`); the Batch D plan's Step 5 listing omits it.

Any refusal: stop, run `kit:debugging` against the log; do not delete curated outputs by hand.

### B5. Fill the checkpoint and bring results back

```
rsync -av jarrod@192.168.1.75:/mnt/data/wa-mine-monitor/curated/{d3-inputs,d3-threshold,register}/ ~/data/wa-mine-monitor/curated/ --include='*/' --include="$D/**" --exclude='*'
rsync -av jarrod@192.168.1.75:/mnt/data/wa-mine-monitor/raw/dpird_020*/ ~/data/wa-mine-monitor/raw/  # region boundaries; confirm dir name with ls first
```

Fill every `_pending_` field in `docs/checkpoints/batch-d-result.md` from `threshold.json`, the d3-inputs manifests, and:

```
uv run python -c "
from wa_mine_monitor import tables; import pandas as pd
df = tables.read_table('$HOME/data/wa-mine-monitor/curated/register/$D/register.parquet')
print(df['trajectory_status'].value_counts())"
```

Set the status line to `Live run COMPLETE ($D)`; commit the checkpoint. Batch E gate is now open.

---

## Part C — D13 E3: spectral metrics and trajectory schema (kit:build-flow)

> **REMINDER (2026-08-21):** Part A is done on `main`. Part B runs in a separate luminosity session and does NOT gate Part C. Start Part C now in a fresh session with:
>
> > Read `docs/plans/2026-08-21-batch-d-live-run-and-batch-e-e3.md` and execute Part C using the kit:build-flow skill.
>
> Merge this plan branch (`worktree-plan-batch-d-live-run-and-e3`) into `main` first, or build-flow will branch from a `main` that lacks the plan file.


Conventions (from `cli.py`, `register.py`, `tests/test_dea_raster.py`): module-level pyarrow schemas; `tables.write_table(df, path, schema)` / `tables.read_table(path)`; errors are `ValueError` subclasses named `<Module>Error`; tests are plain functions, no conftest. Run `kit:code-standards` for Python before editing.

### Task 1: `spectral_metrics.py` — geomedian site-year metrics with not-computable reasons

**Files:**
- Create: `src/wa_mine_monitor/spectral_metrics.py`
- Test: `tests/test_spectral_metrics.py`

**Step 1: Write the failing tests**

```python
"""D13 E3: per-site-year metric rows from decoded band arrays.

Formulas are the frozen D3 ones (d3_inputs.GEOMEDIAN_METRIC_BANDS /
FC_METRIC_ASSETS); this module adds the E3 row contract: every metric
returns a value OR a not_computable_reason, never a fabricated zero.
"""

import numpy as np
import pytest

from wa_mine_monitor import spectral_metrics as sm


def _gm(nir, swir1, swir2):
    return {
        "nbart_nir": np.asarray(nir, dtype=np.float64),
        "nbart_swir_1": np.asarray(swir1, dtype=np.float64),
        "nbart_swir_2": np.asarray(swir2, dtype=np.float64),
    }


def test_geomedian_nbr_ndmi_formula_fixture():
    rows = sm.geomedian_site_year_metrics(_gm([0.5, 0.5], [0.1, 0.1], [0.3, 0.3]))
    by = {r.metric: r for r in rows}
    assert set(by) == {"nbr", "ndmi"}
    assert by["nbr"].value == pytest.approx((0.5 - 0.3) / (0.5 + 0.3))
    assert by["ndmi"].value == pytest.approx((0.5 - 0.1) / (0.5 + 0.1))
    assert by["nbr"].n_member_pixels == 2
    assert by["nbr"].n_valid_pixels == 2
    assert by["nbr"].computable is True
    assert by["nbr"].not_computable_reason is None


def test_geomedian_null_pixels_reduce_valid_count_not_value():
    rows = sm.geomedian_site_year_metrics(_gm([0.5, np.nan], [0.1, 0.1], [0.3, 0.3]))
    nbr = next(r for r in rows if r.metric == "nbr")
    assert nbr.n_member_pixels == 2
    assert nbr.n_valid_pixels == 1
    assert nbr.value == pytest.approx(0.25)


def test_geomedian_zero_denominator_is_not_computable():
    rows = sm.geomedian_site_year_metrics(_gm([0.0], [0.0], [0.0]))
    for r in rows:
        assert r.computable is False
        assert r.value is None
        assert r.not_computable_reason == "zero_valid_pixels"
        assert r.n_valid_pixels == 0


def test_geomedian_empty_member_set_is_not_computable_with_reason():
    rows = sm.geomedian_site_year_metrics(_gm([], [], []))
    assert {r.not_computable_reason for r in rows} == {"zero_member_pixels"}
    assert all(r.n_member_pixels == 0 for r in rows)


def test_geomedian_missing_band_refuses():
    with pytest.raises(sm.SpectralMetricsError, match="nbart_swir_2"):
        sm.geomedian_site_year_metrics(
            {"nbart_nir": np.array([0.5]), "nbart_swir_1": np.array([0.1])}
        )
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_spectral_metrics.py -q`
Expected: FAIL — `ModuleNotFoundError: wa_mine_monitor.spectral_metrics`

**Step 3: Minimal implementation**

```python
"""Per-site-year spectral metric rows (D13 E3).

Reuses the frozen D3 formulas in `d3_inputs`; adds the E3 contract that a
metric is either a value with its pixel counts or an explicit
not_computable_reason. Nothing here fabricates a zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from wa_mine_monitor import d3_inputs

GEOMEDIAN_BANDS: tuple[str, ...] = tuple(
    sorted({b for pair in d3_inputs.GEOMEDIAN_METRIC_BANDS.values() for b in pair})
)
FC_ASSETS: tuple[str, ...] = tuple(sorted(d3_inputs.FC_METRIC_ASSETS.values()))

#: Closed vocabulary for `not_computable_reason`.
NOT_COMPUTABLE_REASONS: tuple[str, ...] = (
    "zero_member_pixels",
    "zero_valid_pixels",
    "read_failed",
    "item_missing",
)


class SpectralMetricsError(ValueError):
    """A band set the frozen formulas cannot be applied to."""


@dataclass(frozen=True)
class MetricRow:
    metric: str
    value: float | None
    n_member_pixels: int
    n_valid_pixels: int
    computable: bool
    not_computable_reason: str | None
    value_out_of_documented_range: int | None = None


def _require_keys(arrays: Mapping[str, np.ndarray], required: tuple[str, ...]) -> None:
    missing = [k for k in required if k not in arrays]
    if missing:
        raise SpectralMetricsError(f"missing band array(s): {missing}")
    lengths = {arrays[k].shape for k in required}
    if len(lengths) != 1:
        raise SpectralMetricsError(f"band arrays differ in shape: {sorted(lengths)}")


def _not_computable(metric: str, n_member: int, reason: str) -> MetricRow:
    return MetricRow(metric, None, n_member, 0, False, reason)


def geomedian_site_year_metrics(bands: Mapping[str, np.ndarray]) -> list[MetricRow]:
    """NBR and NDMI spatial means over the valid members of one site-year."""
    _require_keys(bands, GEOMEDIAN_BANDS)
    n_member = int(bands[GEOMEDIAN_BANDS[0]].size)
    metrics = list(d3_inputs.GEOMEDIAN_METRIC_BANDS)
    if n_member == 0:
        return [_not_computable(m, 0, "zero_member_pixels") for m in metrics]
    valid = d3_inputs.geomedian_valid_mask(bands)
    n_valid = int(valid.sum())
    if n_valid == 0:
        return [_not_computable(m, n_member, "zero_valid_pixels") for m in metrics]
    values = d3_inputs.geomedian_metrics({k: bands[k][valid] for k in GEOMEDIAN_BANDS})
    return [MetricRow(m, values[m], n_member, n_valid, True, None) for m in metrics]
```

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_spectral_metrics.py -q`
Expected: `5 passed`

### Task 2: `spectral_metrics.py` — FC metrics with out-of-range disclosure

**Files:**
- Modify: `src/wa_mine_monitor/spectral_metrics.py`
- Test: `tests/test_spectral_metrics.py`

**Step 1: Write the failing tests** (append)

```python
def _fc(bs, pv, npv):
    return {
        "bs_pc_50": np.asarray(bs, dtype=np.float64),
        "pv_pc_50": np.asarray(pv, dtype=np.float64),
        "npv_pc_50": np.asarray(npv, dtype=np.float64),
    }


def test_fc_metrics_map_assets_to_metric_names_without_clipping():
    rows = sm.fc_site_year_metrics(_fc([10.0, 120.0], [50.0, 50.0], [40.0, 40.0]))
    by = {r.metric: r for r in rows}
    assert set(by) == {"bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"}
    assert by["bare_soil"].value == pytest.approx(65.0)  # 120 retained, not clipped
    assert by["bare_soil"].value_out_of_documented_range == 1
    assert by["photosynthetic_vegetation"].value_out_of_documented_range == 0
    assert by["bare_soil"].n_valid_pixels == 2


def test_fc_null_pixel_excluded_from_all_three_metrics():
    rows = sm.fc_site_year_metrics(_fc([10.0, np.nan], [50.0, 60.0], [40.0, 40.0]))
    assert all(r.n_member_pixels == 2 and r.n_valid_pixels == 1 for r in rows)
    pv = next(r for r in rows if r.metric == "photosynthetic_vegetation")
    assert pv.value == pytest.approx(50.0)


def test_fc_all_null_is_not_computable():
    rows = sm.fc_site_year_metrics(_fc([np.nan], [np.nan], [np.nan]))
    assert {r.not_computable_reason for r in rows} == {"zero_valid_pixels"}
    assert all(r.value is None and r.value_out_of_documented_range is None for r in rows)
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_spectral_metrics.py -q -k fc`
Expected: FAIL — `AttributeError: ... has no attribute 'fc_site_year_metrics'`

**Step 3: Minimal implementation** (append)

```python
def fc_site_year_metrics(values: Mapping[str, np.ndarray]) -> list[MetricRow]:
    """Bare-soil / PV / NPV spatial means of decoded `_pc_50` assets; values
    above 100 are retained and counted per metric (decode_rules)."""
    _require_keys(values, FC_ASSETS)
    n_member = int(values[FC_ASSETS[0]].size)
    metric_to_asset = d3_inputs.FC_METRIC_ASSETS
    if n_member == 0:
        return [_not_computable(m, 0, "zero_member_pixels") for m in metric_to_asset]
    valid = d3_inputs.fc_valid_mask(values)
    n_valid = int(valid.sum())
    if n_valid == 0:
        return [_not_computable(m, n_member, "zero_valid_pixels") for m in metric_to_asset]
    masked = {k: values[k][valid] for k in FC_ASSETS}
    means = d3_inputs.fc_metrics(masked)
    return [
        MetricRow(
            metric,
            means[metric],
            n_member,
            n_valid,
            True,
            None,
            value_out_of_documented_range=int(np.sum(masked[asset] > 100.0)),
        )
        for metric, asset in metric_to_asset.items()
    ]
```

Use `dea_raster.FC_DOCUMENTED_MAX` (`dea_raster.py:17`, `= 100.0`) instead of the literal.

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_spectral_metrics.py -q`
Expected: `8 passed`

### Task 3: `trajectories.py` — E3 schema and metric vocabulary

**Files:**
- Create: `src/wa_mine_monitor/trajectories.py`
- Test: `tests/test_trajectories.py`

**Step 1: Write the failing tests**

```python
"""D13 E3 trajectory schema: site x year x metric x product variant."""

import pyarrow as pa
import pytest

from wa_mine_monitor import trajectories as tj


def test_schema_has_exactly_the_e3_fields_in_order():
    assert tj.TRAJECTORY_SCHEMA.names == [
        "site_id", "maus_id", "year", "metric", "value", "sensor", "collection_id",
        "item_id", "product_version", "geomad_count", "n_member_pixels",
        "n_valid_pixels", "effective_pixel_support_px", "computable",
        "not_computable_reason", "value_out_of_documented_range",
        "transition_adjacent", "source_snapshot_date", "geometry",
    ]


def test_schema_nullability_matches_e3():
    f = {fld.name: fld for fld in tj.TRAJECTORY_SCHEMA}
    for name in ("site_id", "maus_id", "year", "metric", "collection_id", "item_id",
                 "n_member_pixels", "computable", "transition_adjacent",
                 "source_snapshot_date", "geometry"):
        assert not f[name].nullable, name
    for name in ("value", "geomad_count", "not_computable_reason",
                 "value_out_of_documented_range", "n_valid_pixels", "sensor",
                 "product_version", "effective_pixel_support_px"):
        assert f[name].nullable, name
    assert f["year"].type == pa.int32()
    assert f["value"].type == pa.float64()
    assert f["geomad_count"].type == pa.int64()
    assert f["computable"].type == pa.bool_()
    assert f["geometry"].type == pa.binary()  # WKB, EPSG:3577


def test_metric_vocabulary_is_closed_and_matches_spectral_metrics():
    assert tj.METRICS == (
        "nbr", "ndmi", "bare_soil", "photosynthetic_vegetation",
        "non_photosynthetic_vegetation",
    )
    assert tj.GEOMETRY_CRS == "EPSG:3577"
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_trajectories.py -q`
Expected: FAIL — `ModuleNotFoundError: wa_mine_monitor.trajectories`

**Step 3: Minimal implementation**

```python
"""Tier 1 trajectory table (D13 E3).

One row per (site, year, metric, collection) -- sensor overlaps are
preserved as separate rows, never collapsed. Geometry is Maus-derived and
carried as WKB in EPSG:3577; the whole table is package-bound to
CC-BY-SA-4.0 and private pending Batch G export adjudication.
"""

from __future__ import annotations

import pyarrow as pa

from wa_mine_monitor import d3_inputs

GEOMETRY_CRS = "EPSG:3577"

#: Closed metric vocabulary: D3 geomedian metrics then FC metrics, in the
#: order the frozen protocol declares them.
METRICS: tuple[str, ...] = tuple(d3_inputs.GEOMEDIAN_METRIC_BANDS) + tuple(
    d3_inputs.FC_METRIC_ASSETS
)

TRAJECTORY_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string(), nullable=False),
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("year", pa.int32(), nullable=False),
        pa.field("metric", pa.string(), nullable=False),
        pa.field("value", pa.float64(), nullable=True),
        pa.field("sensor", pa.string(), nullable=True),
        pa.field("collection_id", pa.string(), nullable=False),
        pa.field("item_id", pa.string(), nullable=False),
        pa.field("product_version", pa.string(), nullable=True),
        pa.field("geomad_count", pa.int64(), nullable=True),
        pa.field("n_member_pixels", pa.int64(), nullable=False),
        pa.field("n_valid_pixels", pa.int64(), nullable=True),
        pa.field("effective_pixel_support_px", pa.int64(), nullable=True),
        pa.field("computable", pa.bool_(), nullable=False),
        pa.field("not_computable_reason", pa.string(), nullable=True),
        pa.field("value_out_of_documented_range", pa.int64(), nullable=True),
        pa.field("transition_adjacent", pa.bool_(), nullable=False),
        pa.field("source_snapshot_date", pa.string(), nullable=False),
        pa.field("geometry", pa.binary(), nullable=False),
    ]
)
```

Check `register.REGISTER_SCHEMA` for the actual `site_id`/`maus_id` types before finalising; if `maus_id` is `int64` there, use `int64` here and adjust the test.

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_trajectories.py -q`
Expected: `3 passed`

### Task 4: `trajectories.py` — row validation and Parquet round trip

**Files:**
- Modify: `src/wa_mine_monitor/trajectories.py`
- Test: `tests/test_trajectories.py`

**Step 1: Write the failing tests** (append)

```python
import pandas as pd
from shapely.geometry import box

from wa_mine_monitor import tables


def _row(**over):
    base = dict(
        site_id="S1", maus_id="M1", year=2015, metric="nbr", value=0.25,
        sensor="ls8", collection_id="dea_gm_ls8cls9c", item_id="item-1",
        product_version="3.1", geomad_count=12, n_member_pixels=230,
        n_valid_pixels=228, effective_pixel_support_px=230, computable=True,
        not_computable_reason=None, value_out_of_documented_range=None,
        transition_adjacent=False, source_snapshot_date="2026-08-16",
        geometry=box(0, 0, 30, 30).wkb,
    )
    base.update(over)
    return base


def test_validate_accepts_a_computable_geomedian_row():
    tj.validate_trajectories(pd.DataFrame([_row()]))


def test_validate_refuses_unknown_metric():
    with pytest.raises(tj.TrajectoryError, match="metric"):
        tj.validate_trajectories(pd.DataFrame([_row(metric="ndvi")]))


def test_validate_refuses_computable_row_without_value_and_vice_versa():
    with pytest.raises(tj.TrajectoryError, match="computable"):
        tj.validate_trajectories(pd.DataFrame([_row(value=None)]))
    with pytest.raises(tj.TrajectoryError, match="computable"):
        tj.validate_trajectories(
            pd.DataFrame([_row(computable=False, not_computable_reason="zero_valid_pixels")])
        )


def test_validate_refuses_not_computable_row_without_reason():
    with pytest.raises(tj.TrajectoryError, match="not_computable_reason"):
        tj.validate_trajectories(pd.DataFrame([_row(value=None, computable=False)]))


def test_validate_refuses_geomad_count_on_fc_rows_and_requires_null():
    tj.validate_trajectories(
        pd.DataFrame([_row(metric="bare_soil", collection_id="dea_fc_pc", geomad_count=None)])
    )
    with pytest.raises(tj.TrajectoryError, match="geomad_count"):
        tj.validate_trajectories(
            pd.DataFrame([_row(metric="bare_soil", collection_id="dea_fc_pc", geomad_count=0)])
        )


def test_validate_refuses_duplicate_site_year_metric_collection():
    with pytest.raises(tj.TrajectoryError, match="duplicate"):
        tj.validate_trajectories(pd.DataFrame([_row(), _row()]))


def test_overlapping_collections_are_distinct_rows_not_duplicates():
    df = pd.DataFrame([_row(), _row(collection_id="dea_gm_ls7e", item_id="item-2")])
    tj.validate_trajectories(df)
    assert len(df) == 2


def test_nullable_booleans_and_integers_survive_parquet_round_trip(tmp_path):
    df = pd.DataFrame(
        [
            _row(),
            _row(metric="ndmi", value=None, computable=False,
                 not_computable_reason="zero_valid_pixels", n_valid_pixels=0,
                 geomad_count=None),
        ]
    )
    path = tmp_path / "trajectories.parquet"
    tj.write_trajectories(df, path)
    back = tables.read_table(path)
    assert back["computable"].tolist() == [True, False]
    assert pd.isna(back.loc[1, "value"])
    assert pd.isna(back.loc[1, "geomad_count"])
    assert back.loc[0, "geomad_count"] == 12
    assert back.loc[1, "not_computable_reason"] == "zero_valid_pixels"
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_trajectories.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'validate_trajectories'`

**Step 3: Minimal implementation** (append)

```python
from pathlib import Path

import pandas as pd

from wa_mine_monitor import spectral_metrics, tables

FC_METRICS: frozenset[str] = frozenset(d3_inputs.FC_METRIC_ASSETS)
_KEY = ("site_id", "year", "metric", "collection_id")


class TrajectoryError(ValueError):
    """A trajectory frame that violates the E3 row contract."""


def validate_trajectories(df: pd.DataFrame) -> None:
    """Refuse any frame that breaks the E3 contract. Checks are ordered
    cheapest-first; the first violation is reported."""
    missing = [c for c in TRAJECTORY_SCHEMA.names if c not in df.columns]
    if missing:
        raise TrajectoryError(f"missing column(s): {missing}")
    bad_metric = sorted(set(df["metric"]) - set(METRICS))
    if bad_metric:
        raise TrajectoryError(f"unknown metric value(s): {bad_metric}")
    bad_reason = sorted(
        set(df["not_computable_reason"].dropna()) - set(spectral_metrics.NOT_COMPUTABLE_REASONS)
    )
    if bad_reason:
        raise TrajectoryError(f"unknown not_computable_reason value(s): {bad_reason}")
    computable = df["computable"].astype(bool)
    has_value = df["value"].notna()
    if (computable != has_value).any():
        raise TrajectoryError("computable must be True iff value is non-null")
    if (~computable & df["not_computable_reason"].isna()).any():
        raise TrajectoryError("not_computable_reason is required when computable is False")
    if (computable & df["not_computable_reason"].notna()).any():
        raise TrajectoryError("not_computable_reason must be null when computable is True")
    fc = df["metric"].isin(FC_METRICS)
    if (fc & df["geomad_count"].notna()).any():
        raise TrajectoryError("geomad_count must be null for FC metrics (never fabricated)")
    if df.duplicated(list(_KEY)).any():
        raise TrajectoryError(f"duplicate rows on {_KEY}")


def write_trajectories(df: pd.DataFrame, path: Path) -> None:
    """Validate, then write under `TRAJECTORY_SCHEMA` via `tables.write_table`
    so nullable ints/bools are preserved (no float64 coercion)."""
    validate_trajectories(df)
    out = df.copy()
    for col in ("geomad_count", "n_valid_pixels", "effective_pixel_support_px",
                "value_out_of_documented_range"):
        out[col] = out[col].astype("Int64")
    out["computable"] = out["computable"].astype("boolean")
    out["transition_adjacent"] = out["transition_adjacent"].astype("boolean")
    out["year"] = out["year"].astype("int32")
    tables.write_table(out[TRAJECTORY_SCHEMA.names], path, TRAJECTORY_SCHEMA)
```

Move the `from pathlib import Path` / `pandas` imports to the module top in the final file.

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_trajectories.py -q`
Expected: `11 passed`

### Task 5: Bridge `MetricRow` → trajectory rows

**Files:**
- Modify: `src/wa_mine_monitor/trajectories.py`
- Test: `tests/test_trajectories.py`

**Step 1: Write the failing test** (append)

```python
def test_rows_from_metrics_fans_metric_rows_into_schema_rows():
    from wa_mine_monitor.spectral_metrics import MetricRow

    metric_rows = [
        MetricRow("nbr", 0.2, 230, 228, True, None),
        MetricRow("ndmi", None, 230, 0, False, "zero_valid_pixels"),
    ]
    ctx = tj.RowContext(
        site_id="S1", maus_id="M1", year=2015, sensor="ls8",
        collection_id="dea_gm_ls8cls9c", item_id="item-1", product_version="3.1",
        geomad_count=12, effective_pixel_support_px=230, transition_adjacent=False,
        source_snapshot_date="2026-08-16", geometry_wkb=box(0, 0, 30, 30).wkb,
    )
    df = pd.DataFrame(tj.rows_from_metrics(metric_rows, ctx))
    tj.validate_trajectories(df)
    assert df["metric"].tolist() == ["nbr", "ndmi"]
    assert df.loc[1, "computable"] is False or df.loc[1, "computable"] == False  # noqa: E712
    assert df.loc[0, "geomad_count"] == 12


def test_rows_from_metrics_nulls_geomad_count_for_fc_context():
    from wa_mine_monitor.spectral_metrics import MetricRow

    ctx = tj.RowContext(
        site_id="S1", maus_id="M1", year=2015, sensor=None,
        collection_id="dea_fc_pc", item_id="item-9", product_version=None,
        geomad_count=None, effective_pixel_support_px=230, transition_adjacent=False,
        source_snapshot_date="2026-08-16", geometry_wkb=box(0, 0, 30, 30).wkb,
    )
    rows = tj.rows_from_metrics(
        [MetricRow("bare_soil", 40.0, 230, 230, True, None, value_out_of_documented_range=0)], ctx
    )
    assert rows[0]["geomad_count"] is None
    assert rows[0]["value_out_of_documented_range"] == 0
```

**Step 2: Run to verify failure**

Run: `uv run pytest tests/test_trajectories.py -q -k rows_from_metrics`
Expected: FAIL — `AttributeError: ... 'RowContext'`

**Step 3: Minimal implementation** (append)

```python
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RowContext:
    """Everything about one (site, year, collection) that is not a metric."""

    site_id: str
    maus_id: str
    year: int
    sensor: str | None
    collection_id: str
    item_id: str
    product_version: str | None
    geomad_count: int | None
    effective_pixel_support_px: int | None
    transition_adjacent: bool
    source_snapshot_date: str
    geometry_wkb: bytes


def rows_from_metrics(
    metric_rows: Sequence[spectral_metrics.MetricRow], ctx: RowContext
) -> list[dict[str, object]]:
    return [
        {
            "site_id": ctx.site_id,
            "maus_id": ctx.maus_id,
            "year": ctx.year,
            "metric": m.metric,
            "value": m.value,
            "sensor": ctx.sensor,
            "collection_id": ctx.collection_id,
            "item_id": ctx.item_id,
            "product_version": ctx.product_version,
            "geomad_count": ctx.geomad_count,
            "n_member_pixels": m.n_member_pixels,
            "n_valid_pixels": m.n_valid_pixels,
            "effective_pixel_support_px": ctx.effective_pixel_support_px,
            "computable": m.computable,
            "not_computable_reason": m.not_computable_reason,
            "value_out_of_documented_range": m.value_out_of_documented_range,
            "transition_adjacent": ctx.transition_adjacent,
            "source_snapshot_date": ctx.source_snapshot_date,
            "geometry": ctx.geometry_wkb,
        }
        for m in metric_rows
    ]
```

**Step 4: Run to verify pass**

Run: `uv run pytest tests/test_trajectories.py tests/test_spectral_metrics.py -q`
Expected: `21 passed`

### Task 6: Full battery and draft-plan supersession

**Files:**
- Modify: `docs/plans/2026-08-18-batch-e-implementation.md` (header only)

**Step 1:** Prepend to the 2026-08-18 draft, under `**Status:** DRAFT`:

```
**Superseded (2026-08-21):** Tasks 18–20 are replaced by
`2026-08-21-batch-d-live-run-and-batch-e-e3.md` (E3) and a follow-on E4–E7
plan. CLI name per D13 §5 is `extract-trajectories`. Task 17 is done (90a4f50).
```

**Step 2:** Run the battery.

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
Expected: ruff clean, mypy clean, `701 passed` (680 + 21).

**Step 3:** Run `kit:verify`, then `kit:finish-branch`.

---

## Follow-on plan (not in this document)

After Part B completes and this plan's E3 lands: write `docs/plans/<date>-batch-e-e4-e5.md` covering D13 E4 (`trajectory_extract.py`, `extract-trajectories` CLI with `PartitionResult`, partitioned by `collection_id/year`, reusing `cli._read_footprint_year_bands` and `d3_inputs.select_catalogue_items`; fixture chain = `_seed_d3_inputs_chain` + `build-d3-inputs` + `derive-d3-threshold` + `apply-d3-threshold`) and E5 (`huntly_validation.py`, needs the jarrah Huntly cube path and tolerances confirmed by Jarrod). E1 is partly done (`dea_raster` decoders); the `read_asset_window` interface is absorbed into E4 because `_read_footprint_year_bands` already performs the windowed read.
