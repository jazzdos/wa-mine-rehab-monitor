# WA Mine Rehabilitation Spectral Monitor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Build Batches A (scaffold + ported jarrah machinery + CI) and B
(Tier 0: fetchers, register, crosswalk) of the monitor designed in
`docs/plans/2026-08-15-wa-mine-rehab-monitor-design.md`.

**Architecture:** New `uv`-managed Python package `wa_mine_monitor` in this
repo, importing battle-tested governance modules from the jarrah repo as
clean copies with origin headers, then adding Tier 0: dated-snapshot
fetchers for MINEDEX/tenements/Maus, a statewide register builder, and a
deterministic MINEDEX–Maus crosswalk. Every CLI command writes an artefact
plus an immutable run-manifest sidecar. The licence gate is fail-closed:
MINEDEX public export stays blocked unless the captured DASC evidence
explicitly grants CC-BY-4.0.

**Tech Stack:** Python 3.12, uv, typer, pydantic, pyarrow, geopandas,
shapely, duckdb, pytest, ruff, mypy. Jarrah source repo (read-only),
referenced below as `$JARRAH` — resolved from the `JARRAH_REPO` env var,
or from a gitignored local note if unset; never written into a committed
file as a literal path. Data root: `~/data/wa-mine-monitor`.

**Binding design constraints (from the design doc, enforce in code/tests):**
- Claim scope: descriptive only; no compliance/performance language
  anywhere, including comments and docstrings.
- Population language: "MINEDEX sites in the monitoring frame".
- MINEDEX licence fail-closed rule (design §6).
- Declared Arrow schemas on every parquet write (never inferred).
- Every count table reconciles against its own totals before use.
- Three-count diagnostics: agreed / disagreed / not_computable.
- No `git stash`, `git checkout <path>`, `git reset`, `git clean` in any
  agent step; agents needing isolation get a worktree.

---

## Batch A — scaffold, ported machinery, CI

### Task 1: Repo scaffold

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.python-version`, `LICENSE`,
  `README.md`, `src/wa_mine_monitor/__init__.py`,
  `tests/__init__.py` (empty), `config/base.yaml`

**Step 1:** Write `pyproject.toml`:

```toml
[project]
name = "wa-mine-monitor"
version = "0.1.0"
description = "WA Mine Rehabilitation Spectral Monitor - descriptive spectral chronologies for MINEDEX sites in the monitoring frame"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = [
    "typer>=0.12",
    "pydantic>=2.7",
    "pyyaml>=6.0",
    "pyarrow>=16.0",
    "pandas>=2.2",
    "geopandas>=1.0",
    "shapely>=2.0",
    "duckdb>=1.0",
    "requests>=2.32",
    "pyproj>=3.6",
]

[project.scripts]
wa-mine-monitor = "wa_mine_monitor.cli:app"

[dependency-groups]
dev = ["pytest>=8.0", "ruff>=0.6", "mypy>=1.11", "types-requests", "types-PyYAML"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/wa_mine_monitor"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.mypy]
python_version = "3.12"
strict = false
check_untyped_defs = true

[tool.pytest.ini_options]
testpaths = ["tests"]
```

**Step 2:** Write `.gitignore` — must block all bulk/data artefacts:

```
__pycache__/
*.egg-info/
.venv/
.mypy_cache/
.ruff_cache/
.pytest_cache/
*.tif
*.parquet
*.gpkg
*.geojson
*.zip
*.pmtiles
data/
data_root/
site/build/
```

**Step 3:** `.python-version` containing `3.12`. `LICENSE`: MIT, holder
"Jarrod Baker", year 2026.

**Step 4:** Write `README.md`. Must contain, verbatim as a section, the
claim boundary: the monitor publishes descriptive spectral chronologies
for MINEDEX sites in the monitoring frame; every onset is a spectral
detection presented as a detection year or interval, never an event
date, never an operational rehabilitation date, never a compliance or
performance finding. Note the repo is private until the Tier 0 release
gate (design §8 D2). Credit the DEA "Tracking rehabilitation of mines"
notebook as the method's ancestor.

**Step 5:** Write `config/base.yaml`:

```yaml
run:
  data_root: "~/data/wa-mine-monitor"
  redistribute_public: false
sources:
  minedex_public_export_blocked: true   # flips only via captured CC-BY evidence, see licence.py
```

**Step 6:** `uv sync` then `uv run python -c "import wa_mine_monitor"`.
Expected: no error.

### Task 2: Port governance modules from jarrah

**Files:**
- Create: `src/wa_mine_monitor/provenance.py` (from
  `$JARRAH/src/jarrah_rehab/provenance.py`)
- Create: `src/wa_mine_monitor/secrets.py` (from
  `$JARRAH/src/jarrah_rehab/secrets.py`)
- Create: `src/wa_mine_monitor/manifests.py` (from
  `$JARRAH/src/jarrah_rehab/reporting/manifests.py`)
- Create: `src/wa_mine_monitor/export_gate.py` (from
  `$JARRAH/src/jarrah_rehab/reporting/export.py`)
- Create: `src/wa_mine_monitor/tables.py` (declared-Arrow-schema write
  helpers, from `$JARRAH/src/jarrah_rehab/envelope/io.py` +
  the schema-declaration pattern of `envelope/schemas.py`)
- Test: `tests/test_provenance.py`, `tests/test_secrets.py`,
  `tests/test_manifests.py`, `tests/test_export_gate.py`,
  `tests/test_tables.py` (ported/adapted from the corresponding
  `$JARRAH/tests/` files)

**Step 1:** For each module: copy the jarrah file, then (a) add a header
comment `# Ported from jarrah-rehab <path> at commit <SHA> (2026-08-15);
MIT-relicensed by the same author.` with the SHA from
`git -C "$JARRAH" rev-parse HEAD`; (b) rewrite imports
`jarrah_rehab.*` → `wa_mine_monitor.*`; (c) remove jarrah-only symbols
ONLY where they don't compile (keep changes minimal and mechanical).
`manifests.py` depends on a config type: give it a small local protocol
(`data_root` attribute) instead of importing jarrah's ProjectConfig.

**Step 2:** Port the matching test files the same way. Drop tests bound
to jarrah-domain fixtures; keep every behavioural test that compiles.
Each ported test file must retain at least: manifests — immutability,
byte-stable JSON, secret-scrub disclosure fields, `manifest_matches`;
secrets — whole-word matching and URL-credential cases; export gate —
geometry detection by name token AND value sniffing, `redistribute_public`
row gate; tables — the all-null column keeps its declared type (write a
frame with an all-null date column, read back, assert `date32[day]` not
`null`).

**Step 3:** Run: `uv run pytest -q`. Expected: all pass.

**Step 4:** Run: `uv run ruff check src tests && uv run mypy src`.
Expected: clean (fix mechanically, no behavioural edits).

### Task 3: Config and CLI skeleton

**Files:**
- Create: `src/wa_mine_monitor/config.py`, `src/wa_mine_monitor/cli.py`
- Test: `tests/test_config.py`, `tests/test_cli.py`

**Step 1:** Failing test `tests/test_config.py::test_load_config_resolves_data_root`:

```python
from pathlib import Path
from wa_mine_monitor.config import load_config


def test_load_config_resolves_data_root(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        'run:\n  data_root: "~/data/wa-mine-monitor"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    cfg = load_config(cfg_file)
    assert cfg.run.data_root == Path("~/data/wa-mine-monitor").expanduser()
    assert cfg.run.redistribute_public is False
    assert cfg.sources.minedex_public_export_blocked is True
```

**Step 2:** Run `uv run pytest tests/test_config.py -q`; expect FAIL
(module missing).

**Step 3:** Implement `config.py`: pydantic models `RunConfig`
(`data_root: Path` with expanduser validator, `redistribute_public: bool`),
`SourcesConfig` (`minedex_public_export_blocked: bool = True`),
`ProjectConfig`, and `load_config(path) -> ProjectConfig` via
`yaml.safe_load`.

**Step 4:** `cli.py`: `typer.Typer()` named `app` with a `--config`
option pattern (default `config/base.yaml`) and one working command
`config-check` that loads and echoes the secret-scrubbed config JSON.
Test in `tests/test_cli.py` with `typer.testing.CliRunner`: exit code 0
and data_root in output.

**Step 5:** `uv run pytest -q` — all pass.

### Task 4: Source licence registry (fail-closed)

**Files:**
- Create: `src/wa_mine_monitor/licence.py`
- Create: `docs/licensing-matrix.md`
- Test: `tests/test_licence.py`

**Step 1:** Failing tests:

```python
import pytest
from wa_mine_monitor.licence import SOURCES, minedex_redistribution_allowed


def test_every_source_has_required_fields():
    for s in SOURCES.values():
        assert s.source_url and s.licence_id and s.attribution_text
        assert s.redistribute_public in (True, False)


def test_minedex_defaults_blocked(tmp_path):
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_minedex_unblocks_only_on_explicit_ccby_evidence(tmp_path):
    (tmp_path / "licence_evidence.json").write_text(
        '{"resource": "MINEDEX DASC download", "explicit_grant": "CC-BY-4.0", '
        '"contrary_notice": false, "captured": "2026-08-15", '
        '"evidence_files": ["landing.html", "bundle_readme.txt"]}'
    )
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is True


def test_minedex_stays_blocked_on_contrary_notice(tmp_path):
    (tmp_path / "licence_evidence.json").write_text(
        '{"resource": "MINEDEX DASC download", "explicit_grant": "CC-BY-4.0", '
        '"contrary_notice": true, "captured": "2026-08-15", "evidence_files": []}'
    )
    assert minedex_redistribution_allowed(evidence_dir=tmp_path) is False
```

**Step 2:** Run, expect FAIL. **Step 3:** Implement: frozen dataclass
`SourceLicence(source_id, title, source_url, licence_id, licence_url,
attribution_text, redistribute_public, notes)`; `SOURCES` dict pinning
the design §6 table exactly (DEA collections CC-BY-4.0; DMIRS-003
CC-BY-4.0; DMIRS-001 licence_id `"CONFLICT:cc-nc-vs-cc-by"` and
`redistribute_public=False`; Maus CC-BY-SA-4.0; Hansen CC-BY-4.0 with
BOTH credit strings in `attribution_text`; DBCA-060; SILO).
`minedex_redistribution_allowed(evidence_dir)` returns True only when
`licence_evidence.json` exists, parses, has
`explicit_grant == "CC-BY-4.0"`, `contrary_notice is False`, and a
non-empty `evidence_files` list. Any parse error → False (fail-closed,
never raise).

**Step 4:** All tests pass. **Step 5:** Write `docs/licensing-matrix.md`
from `SOURCES` content: one row per source (source, exact resource URL,
snapshot policy, licence version, attribution text, transformation
notice, redistribution decision) + MIT-for-code statement + jarrah-port
provenance note.

### Task 5: Snapshot layout + CI

**Files:**
- Create: `src/wa_mine_monitor/snapshots.py`
- Create: `.github/workflows/test.yml`
- Test: `tests/test_snapshots.py`

**Step 1:** Failing test: `create_snapshot_dir(root, "minedex")` returns
`root/raw/minedex/<YYYY-MM-DD>/`; `write_snapshot_metadata(dir, source=...,
endpoint=..., licence_note=...)` writes `metadata.txt`;
`finalize_snapshot(dir)` writes `SHA256SUMS.txt` covering every file and
`verify_snapshot(dir)` returns (n_ok, n_bad, n_missing) — three counts.
Tampering with a byte makes verify report 1 bad.

**Step 2–4:** Implement minimally (reuse `provenance.sha256_file`), tests
green.

**Step 5:** `.github/workflows/test.yml`, same shape as
`$JARRAH/.github/workflows/test.yml`: pinned ubuntu-24.04, actions pinned
by SHA (copy the pins), uv sync, `ruff check`, `ruff format --check`,
`mypy src scripts` (create empty `scripts/` with `.gitkeep`),
`pytest -q -rs`, `permissions: contents: read`. No CD.

**Step 6:** Full local battery: `uv run ruff check src tests && uv run
mypy src && uv run pytest -q`. Expected: green. This closes Batch A.

---

## Batch B — Tier 0

> **Superseded acquisition route (2026-08-16).** Tasks 6, 7 and 11 below
> were written against the SLIP direct-download endpoints
> (`data-downloads.slip.wa.gov.au`), which proved auth-gated behind
> Landgate SSO during Task 11. Ruling D6
> (`docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md`)
> replaced that route with the DMIRS DASC statewide bundles, and the tree
> implements D6: two-bundle MINEDEX snapshots, pinned DASC file ids with
> product-identity validation, and the `adjudicate-minedex-licence`
> command (D7). Where a task's text below names a SLIP URL or a
> pre-adjudication evidence state, the D6–D8 record and the Tier 0
> handoff's resume sequence supersede it.

Fixture-first rule for every fetcher: unit tests run against small
committed fixtures under `tests/fixtures/` (hand-built, licence-clean,
<10 KB each); live network hits happen only in the CLI command run by
the operator, never in pytest.

### Task 6: fetch-tenements (DMIRS-003, CC-BY)

**Files:**
- Create: `src/wa_mine_monitor/sources/__init__.py`,
  `src/wa_mine_monitor/sources/tenements.py`
- Modify: `src/wa_mine_monitor/cli.py` (add `fetch-tenements`)
- Test: `tests/sources/test_tenements.py`

**Step 1:** Failing tests for the pure parts: `slip_download_url()`
returns the pinned DMIRS-003 GeoPackage URL
(`https://data-downloads.slip.wa.gov.au/DMIRS-001/...` pattern is for
minedex; for DMIRS-003 pin
`https://data-downloads.slip.wa.gov.au/DMIRS-003/Geopackage`);
`validate_tenements_gpkg(path)` on a 3-feature fixture GeoPackage
returns a summary with feature count, CRS string, and column names, and
raises `SnapshotValidationError` naming the file when the layer is
missing or empty.

**Step 2:** Implement. Build the fixture in-test with geopandas (3 toy
polygons, columns `fmt_tenid`, `tenstatus`, `holder1`, CRS EPSG:4326)
written to `tmp_path` — no binary fixture committed.

**Step 3:** CLI `fetch-tenements`: create snapshot dir, stream-download
the GeoPackage (requests, chunked, timeout, explicit User-Agent), write
`metadata.txt` (endpoint, licence CC-BY-4.0 + Data WA record URL,
purpose), validate, finalize SHA256SUMS, write run manifest listing the
snapshot as output. CLI test: monkeypatch the downloader to copy the
fixture; assert snapshot layout + manifest exist and verify passes.

**Step 4:** `uv run pytest -q` green.

### Task 7: fetch-minedex (DMIRS-001, licence-evidence capture)

**Files:**
- Create: `src/wa_mine_monitor/sources/minedex.py`
- Modify: `src/wa_mine_monitor/cli.py`
- Test: `tests/sources/test_minedex.py`

MINEDEX sites GeoPackage downloads from the SLIP endpoint
(`https://data-downloads.slip.wa.gov.au/DMIRS-001/Geopackage`). The DASC
route (`https://dasc.dmirs.wa.gov.au/home?productAlias=MINEDEX`) is the
licence-evidence page. The command must do BOTH: fetch the data, and
capture licence evidence WITHOUT adjudicating it.

**Step 1:** Failing tests:
- `capture_licence_evidence(snapshot_dir, landing_html=...)` writes
  `landing.html` and a `licence_evidence.json` with
  `explicit_grant: null, contrary_notice: null, adjudicated: false` —
  capture never self-adjudicates; a human (or codex ruling recorded in
  the repo) fills the grant fields later. Assert
  `minedex_redistribution_allowed()` on that dir returns False.
- `validate_minedex_gpkg(path)` on a toy 4-feature fixture (columns
  `site_code`, `site_title`, `site_commodity`, `stage`, `operator`,
  point geometry) returns counts by `stage` and raises on empty.

**Step 2:** Implement; CLI `fetch-minedex` downloads the GeoPackage,
fetches the DASC landing HTML (best-effort; on failure record
`landing_fetch_failed` in evidence json — still fail-closed), captures
evidence, validates, finalizes, writes manifest. The manifest input list
carries `redistribute_public=False` for this source (from
`licence.SOURCES`).

**Step 3:** CLI test with both downloads monkeypatched. Green battery.

### Task 8: fetch-maus-extract (CC-BY-SA, WA clip)

**Files:**
- Create: `src/wa_mine_monitor/sources/maus.py`
- Modify: `src/wa_mine_monitor/cli.py`
- Test: `tests/sources/test_maus.py`

The jarrah repo already holds the global Maus v2 GeoPackage at
`~/data/jarrah-rehab/raw/maus-v2/2026-07-20/` — reuse it read-only
rather than re-downloading; provenance records the PANGAEA DOI AND the
local snapshot path + SHA256.

**Step 1:** Failing test: `clip_to_wa(gdf)` clips a toy global frame
(one polygon in WA at lon 120 lat −30, one in Brazil) to the WA bbox
`(112.5, -35.5, 129.1, -13.5)` and returns only the WA polygon with an
added `maus_id` stable id column (row hash of WKB, first 12 hex chars).

**Step 2:** Implement. CLI `fetch-maus-extract --source-gpkg <path>`:
read, clip, write `wa_extract.gpkg` into a dated snapshot with
metadata.txt quoting CC-BY-SA-4.0 and the modification statement
("clipped to WA bbox from the global v2 dataset"), finalize, manifest.
The output asset carries licence CC-BY-SA-4.0 and
`redistribute_public=True` WITH a `share_alike=True` note (per design:
the Maus-derived package publishes separately under CC-BY-SA).

**Step 3:** Tests green.

### Task 9: build-register (Tier 0 assembly)

**Files:**
- Create: `src/wa_mine_monitor/register.py`
- Modify: `src/wa_mine_monitor/cli.py`
- Test: `tests/test_register.py`

**Step 1:** Failing tests, pure function `build_register(minedex_gdf,
tenements_gdf)`:
- returns a DataFrame with declared schema (write through `tables.py`
  declared Arrow schema; columns: `site_id`, `site_name`, `commodity`,
  `stage`, `operator_at_snapshot`, `snapshot_date`, `lon`, `lat`,
  `n_tenements_intersecting`, `inclusion_status`);
- `inclusion_status` classifies stage values into the design §3
  categories (`operating`, `care_and_maintenance`, `closed`, `deposit`,
  `prospect`, `other`) via an explicit declared mapping — unknown stage
  values map to `other`, never dropped, and the mapping is exported as
  a constant so the site can print it;
- a companion `register_counts(df)` returns counts per
  `inclusion_status` PLUS a `total` row, and
  `reconcile_counts(counts)` asserts category counts sum to total
  (raises naming the gap otherwise) — the DBCA-060 arithmetic rule.

**Step 2:** Implement minimally. **Step 3:** CLI `build-register` reads
the latest snapshots of minedex + tenements, runs the join
(`n_tenements_intersecting` via spatial join), writes
`curated/register/<snapshot_date>/register.parquet` (declared schema) +
`register_counts.json` + a `reconciliation.md` (counts + source totals +
pass/fail), + manifest. Geometry handling: the parquet carries lon/lat
ONLY (points as columns); the export gate must pass it; MINEDEX
redistribution state is read from `licence.py` and stamped into the
manifest (`minedex_public_export_blocked: true` until evidence lands).

**Step 4:** CLI test on fixtures; assert reconciliation passes and the
manifest carries the blocked flag. Battery green.

### Task 10: build-crosswalk (MINEDEX–Maus, deterministic)

**Files:**
- Create: `src/wa_mine_monitor/crosswalk.py`
- Modify: `src/wa_mine_monitor/cli.py`
- Test: `tests/test_crosswalk.py`

**Step 1:** Failing tests for `build_crosswalk(minedex_gdf, maus_gdf)`
(both in EPSG:3577; reproject inside the CLI, not the function):
- a site point INSIDE a Maus polygon → one row, `match_method =
  "point_in_polygon"`, `distance_m = 0.0`, `confidence = "high"`;
- a site within 2,000 m of exactly one polygon → `match_method =
  "nearest_within_2000m"`, recorded distance, `confidence = "medium"`;
- a site within 2,000 m of TWO polygons → one row per candidate
  polygon, `confidence = "low"`, `ambiguity_n = 2` (one-to-many kept
  explicit, never resolved silently);
- two sites inside one polygon → both rows kept, polygon's
  `shared_by_n = 2` (many-to-one explicit);
- a site >2,000 m from every polygon → one row with `maus_id = null`,
  `match_method = "unmatched"`, `confidence = "none"`;
- every row has `manual_review_status = "unreviewed"`;
- `crosswalk_counts(df)` returns counts by confidence + total,
  reconciled by `reconcile_counts`.

**Step 2:** Implement with geopandas `sjoin` / `sjoin_nearest`;
distances via projected CRS; deterministic ordering (sort by site_id,
maus_id) so the output is byte-stable.

**Step 3:** CLI `build-crosswalk`: read register + Maus WA extract,
reproject both to EPSG:3577, run, write
`curated/crosswalk/<date>/crosswalk.parquet` (declared schema; carries
maus_id + scalar fields, NO Maus geometry — the geometry stays in the
CC-BY-SA package) + counts json + manifest.

**Step 4:** Tier 1 population rule as a pure function
`tier1_population(crosswalk_df)` returning only `confidence == "high"`
rows, with a test asserting medium/low/none are excluded and counted.

**Step 5:** Full battery: `uv run ruff check src tests && uv run mypy
src && uv run pytest -q`. Green closes Batch B.

### Task 11: Tier 0 acceptance run (operator step, live network)

Run in order, checking each exit code and reading each manifest:
`uv run wa-mine-monitor fetch-tenements`, `fetch-minedex`,
`adjudicate-minedex-licence`,
`fetch-maus-extract --source-gpkg <local Maus v2 source GeoPackage>`,
`build-register`, `build-crosswalk`. Then verify: snapshot SHA256SUMS
all verify; register reconciliation PASS; crosswalk counts reconcile;
MINEDEX evidence json shows the D7-adjudicated closed state
(`adjudicated: true`, `contrary_notice: true`,
`minedex_redistribution_allowed` False); export gate blocks a register
export while `minedex_public_export_blocked` is true. Record actual
counts (sites per stage, crosswalk confidence distribution) in
`docs/checkpoints/tier0-result.md`. (Task text updated 2026-08-16 per
D9 step 1; the run is recorded in that checkpoint — PASSED.)

---

## Later batches (heading level only — detail after Tier 0 lands)

- **Batch C — DEA epoch-coverage + volume re-derivation:** STAC catalogue
  module pinning the verified collection names with the non-zero-item
  assertion; per-site epoch coverage counts into the register; re-derive
  the Tier 1 volume estimate against the real register (design §5).
- **Batch D — D3 threshold derivation:** pixel-support simulation on
  large high-confidence footprints; `derive-threshold` command; the
  never-relaxed acceptance criteria of design §8 D3.
- **Batch E — Tier 1 trajectory extraction:** windowed zonal reads over
  geomedian + FC percentiles; sensor/version/count columns; overlap-year
  sensitivity runs; validation against the jarrah Huntly cube FIRST.
- **Batch F — fire/climate context:** DBCA-060 `fire_status` three-state
  join; SILO covariates.
- **Batch G — export + site:** CC-BY-SA Maus package split; versioned
  data releases; static MapLibre/PMTiles site; D2 public-repo gate
  checklist; D5 Pages gate checklist.
- **Batch H — Tier 2:** LEARNINGS.md + pre-registration guard, D4 region
  ranking, ported compositing engine, calibrated chronologies.
