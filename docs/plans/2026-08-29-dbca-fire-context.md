# DBCA-060 Fire Context (F3 + F4) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use kit:build-flow to execute this plan.

**Goal:** Close the DBCA mirror adjudication (decision record + A10/L18),
stage and validate the authoritative DBCA-060 snapshot (`fetch-dbca-fire`,
F3), and build the three-state per-site-year fire context
(`fire_context.py` + `build-fire-context`, F4).

**Architecture:** Mirrors the merged SILO feed (F5) exactly: a source
module (`sources/dbca.py`) owning validation/read primitives, a pure
assembly module (`fire_context.py`) testable without I/O, and two typer
commands in `cli.py` reusing the existing snapshot/gate helpers
(`_refuse_if_snapshot_already_finalized`, `_refuse_if_unexpected_files`,
`_verify_snapshot_or_refuse`, `_refuse_if_curated_output_already_exists`,
`finalize_snapshot`/`verify_snapshot`/`write_run_manifest`). Design:
`docs/plans/2026-08-29-dbca-fire-context-design.md` (read it first).

**Tech Stack:** Python 3.12, uv, typer, geopandas/pyogrio/shapely,
pyarrow, pandas. Battery: `uv run ruff check src tests`,
`uv run ruff format --check src tests`, `uv run mypy src scripts`,
`uv run pytest -q -rs`.

**House rules that bind every task:** caller-supplied `--date` (never
`date.today()`); structured JSON refusals via
`typer.echo(json.dumps({"refusal": ...}, indent=2, sort_keys=True))` +
`raise typer.Exit(1) from None`; no test touches the network or the real
2.1 GB GeoPackage; claim boundary — fire context is context only, never
cause; `not_recorded` is NEVER a known-negative fire label.

---

### Task 1: Decision record, amendment A10, limitation L18

**Files:**
- Create: `docs/decisions/2026-08-29-dbca-mirror-declined.md`
- Modify: `docs/amendments-and-limitations.md`

No code. Follow the structure/tone of
`docs/decisions/2026-08-26-silo-gridded-feed.md` (the precedent this
decision cites) and `docs/decisions/2026-08-29-e5-reference-window-correction.md`.

**Step 1: Write the decision record** with these sections, all facts as
given here (owner authorised 2026-08-29):

- **Decision.** The ArcGIS Online mirror route for DBCA-060 stays
  DECLINED for product ingestion, permanently for v1. D13 §6's F1
  ("Adjudicate DBCA-060 mirror provenance and licence evidence",
  `docs/decisions/2026-08-16-d13-batches-c-g-detailing.md:742-790`) is
  dissolved as objectless: F1 exists solely to authorise the mirror, and
  an AUTHORITATIVE Data WA package is already on disk at
  `~/data/jarrah-rehab/raw/dbca-060/2026-07-20/` (zip digests in its
  `SHA256SUMS.txt`; custodian CRS-provenance `metadata.txt`). Precedent:
  the 2026-08-26 SILO decision dissolving D13's credentials
  precondition when the actual route made it objectless.
- **Why the mirror was suspect (recorded for the file).** The mirror is
  a third-party ArcGIS Online service (org id `DN2fPfpggEPlLhP6`,
  identified as "Stantec" only in a sibling project's config comment);
  no item-owner capture, no licence-text capture, no
  authoritative-vs-mirror diff was ever run. None of that evidence is
  needed when the authoritative package itself is the input.
- **F3 obligations this decision assigns:** (a) compute and record the
  sha256 of the unzipped `.gpkg` (the source `SHA256SUMS.txt` covers
  only the two zips); (b) close the licence-evidence gap —
  `src/wa_mine_monitor/licence.py`'s `dbca_060_fire` entry holds
  `licence_id="open"`, `licence_url=""`; the CKAN record (dataset id
  `3ce8a891-b050-4c38-952b-c40ca8bdc042`, verified in jarrah-rehab
  `docs/research/data-source-verification_2026-07-20.md`) says
  `license_id: cc-by`, so the entry becomes CC-BY-4.0 with the
  catalogue URL, and the live F3 run captures the catalogue page as a
  digested evidence file.
- **Frozen F4 coverage window: [1937, snapshot_year - 1]**, calendar
  years per `fih_year1`. 1937 is the dataset's documented earliest
  systematic records; the snapshot year itself is excluded because the
  record for the extract year is incomplete at extract time. Frozen
  here, never inferred from the data.
- **Limitation L18 (declared here, registered in the amendments
  file).** DBCA-060's own scope is fires on DBCA-managed land or where
  DBCA incurred costs; known gaps exist and spatial completeness is not
  modelled. `not_recorded` is therefore a statement about the RECORD
  for a covered year, never about the ground. No output ever treats it
  as a known-negative.

**Step 2: Register A10 and L18** in
`docs/amendments-and-limitations.md`: an `A10` row + short narrative
after A9 (change: "D13 §6 F1 dissolved; mirror declined; authoritative
route only", record: this decision), and an `L18` row after L17 with
the §L18 text above. Match the existing table formats exactly.

**Step 3: Verify** `uv run python -c "print(open('docs/decisions/2026-08-29-dbca-mirror-declined.md').read()[:200])"`
reads back, and grep both files for "A10" and "L18".

---

### Task 2: Correct the DBCA-060 licence entry and matrix row

**Files:**
- Modify: `src/wa_mine_monitor/licence.py` (the `dbca_060_fire` entry, ~line 262)
- Modify: `docs/licensing-matrix.md` (DBCA-060 row, ~line 46, and the
  "Open, context-only" narrative ~line 170)
- Test: `tests/test_licence.py`

**Step 1: Write/adjust the locking test.** Find the existing
`tests/test_licence.py` tests that lock source entries (e.g. the SILO
one ~line 420). Add:

```python
def test_dbca_060_fire_entry_is_cc_by_with_catalogue_evidence() -> None:
    entry = licence.SOURCES["dbca_060_fire"]
    assert entry.licence_id == "CC-BY-4.0"
    assert entry.licence_url == "https://creativecommons.org/licenses/by/4.0/"
    assert entry.redistribute_public is True
    assert "3ce8a891-b050-4c38-952b-c40ca8bdc042" in entry.notes
    assert "NEVER a known-negative" in entry.notes
```

If an existing test asserts `licence_id == "open"` for this entry,
update it to the new values — do not delete the claim-boundary
assertions.

**Step 2: Run to verify it fails:**
`uv run pytest tests/test_licence.py -q -k dbca` — expect FAIL
(`"open" != "CC-BY-4.0"`).

**Step 3: Implement.** In `licence.py` change only this entry:
`licence_id="CC-BY-4.0"`,
`licence_url="https://creativecommons.org/licenses/by/4.0/"`, and
extend `notes` (keep ALL existing claim-boundary sentences) with:
"Licence corrected from the provisional 'open' 2026-08-29: CKAN dataset
3ce8a891-b050-4c38-952b-c40ca8bdc042 records license_id cc-by
(jarrah-verified 2026-07-20); the staged snapshot carries a digested
capture of the catalogue page (see
decisions/2026-08-29-dbca-mirror-declined.md)."

**Step 4:** `uv run pytest tests/test_licence.py -q` — expect PASS, all
tests.

**Step 5: Update `docs/licensing-matrix.md`:** the DBCA-060 row's
licence cell becomes `CC-BY-4.0` (context-only note stays); keep the
narrative block's three-state sentence intact; add one sentence noting
the 2026-08-29 correction + evidence capture. The matrix header says it
must not drift from `licence.py` — re-read the row against the code
after editing.

---

### Task 3: `sources/dbca.py` — constants, validation, attribute scan

**Files:**
- Create: `src/wa_mine_monitor/sources/dbca.py`
- Test: `tests/sources/test_dbca.py` (new)

Facts baked in (from the design doc's prior-art scan): layer
`DBCA_Fire_History_DBCA_060`, source CRS EPSG:4283 (GDA94,
custodian-native), fields `fih_fire_type` (vocabulary exactly
{WF, PB, 999} after `UPPER(TRIM(...))` normalisation — the real GDA94
file carries one raw lowercase `wf`; 999 is a REAL category, never
blank it),
`fih_year1` (int calendar year), `fih_master_key`; `fih_date1`
optional/nullable (Esri epoch-ms, may be pre-1970). Statewide count
~149,621 — but NEVER hardcode that as an assertion; record what is
measured.

**Step 1: Write failing tests.** Build a tiny GPKG fixture helper in
the test file (module-level, reused by Tasks 4/7/8):

```python
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from wa_mine_monitor.sources import dbca

LAYER = dbca.LAYER_NAME


def _square(x: float, y: float, side: float = 0.01) -> Polygon:
    return Polygon([(x, y), (x + side, y), (x + side, y + side), (x, y + side)])


def write_fire_gpkg(
    path: Path,
    rows: list[dict],
    *,
    layer: str = LAYER,
    crs: str = "EPSG:4283",
) -> Path:
    frame = gpd.GeoDataFrame(
        {
            "fih_master_key": [r.get("key", f"K{i}") for i, r in enumerate(rows)],
            "fih_fire_type": [r.get("fire_type", "WF") for r in rows],
            "fih_year1": pd.array([r.get("year", 2000) for r in rows], dtype="int64"),
            "geometry": [r.get("geom", _square(116.0, -32.0)) for r in rows],
        },
        crs=crs,
    )
    frame.to_file(path, driver="GPKG", layer=layer)
    return path


def test_validate_accepts_a_conformant_file_and_reports_counts(tmp_path: Path) -> None:
    path = write_fire_gpkg(
        tmp_path / "fire.gpkg",
        [
            {"fire_type": "WF", "year": 1990},
            {"fire_type": "PB", "year": 1990},
            {"fire_type": "999", "year": 2001},
        ],
    )
    summary = dbca.validate_fire_history_file(path, snapshot_year=2026)
    assert summary.feature_count == 3
    assert summary.counts_by_type == {"999": 1, "PB": 1, "WF": 2}
    assert summary.year_min == 1990
    assert summary.year_max == 2001
    assert summary.crs == "EPSG:4283"


def test_validate_refuses_missing_layer(tmp_path: Path) -> None:
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [{}], layer="WRONG_LAYER")
    with pytest.raises(dbca.DbcaError, match="layer"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


def test_validate_refuses_wrong_crs(tmp_path: Path) -> None:
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [{}], crs="EPSG:4326")
    with pytest.raises(dbca.DbcaError, match="4283"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


def test_validate_tripwires_on_an_unexpected_fire_type_code(tmp_path: Path) -> None:
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [{"fire_type": "MR"}])
    with pytest.raises(dbca.DbcaError, match="MR"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


def test_validate_normalises_case_and_whitespace_variants(tmp_path: Path) -> None:
    # The real GDA94 file carries one raw lowercase `wf` (jarrah census).
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [{"fire_type": " wf "}])
    summary = dbca.validate_fire_history_file(path, snapshot_year=2026)
    assert summary.counts_by_type == {"WF": 1}


def test_validate_refuses_a_year_outside_bounds(tmp_path: Path) -> None:
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [{"year": 1850}])
    with pytest.raises(dbca.DbcaError, match="1850"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)


def test_validate_refuses_an_empty_layer(tmp_path: Path) -> None:
    path = write_fire_gpkg(tmp_path / "fire.gpkg", [])
    with pytest.raises(dbca.DbcaError, match="0 features"):
        dbca.validate_fire_history_file(path, snapshot_year=2026)
```

Also add a missing-required-field refusal test (drop `fih_year1` by
writing a frame without it) matching `"fih_year1"`.

**Step 2:** `uv run pytest tests/sources/test_dbca.py -q` — expect FAIL
(`ModuleNotFoundError` / attribute errors).

**Step 3: Implement `src/wa_mine_monitor/sources/dbca.py`.** Module
docstring must state the claim boundary (context only, `not_recorded`
never a known-negative) and cite the decision record. Shape:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyogrio

LAYER_NAME = "DBCA_Fire_History_DBCA_060"
SOURCE_CRS = "EPSG:4283"
FIRE_TYPES = frozenset({"WF", "PB", "999"})
REQUIRED_FIELDS = ("fih_master_key", "fih_fire_type", "fih_year1")
#: Hard sanity bounds on `fih_year1` -- validation-time only. The frozen
#: F4 coverage window [COVERAGE_START_YEAR, snapshot_year - 1] is a
#: SEPARATE, narrower concept (decision record 2026-08-29).
YEAR_MIN = 1900
COVERAGE_START_YEAR = 1937


class DbcaError(ValueError):
    """DBCA-060 validation or read refused."""


@dataclass(frozen=True)
class FireHistorySummary:
    feature_count: int
    counts_by_type: dict[str, int]
    year_min: int
    year_max: int
    crs: str


def validate_fire_history_file(path: Path, *, snapshot_year: int) -> FireHistorySummary:
    ...
```

Implementation notes: use `pyogrio.list_layers(path)` for the layer
check; `pyogrio.read_info(path, layer=LAYER_NAME)` for CRS + field
names + feature count; attribute scan via
`pyogrio.read_dataframe(path, layer=LAYER_NAME, read_geometry=False, columns=["fih_fire_type", "fih_year1"])`
(never load geometry for validation — the real file is 2.1 GB).
Normalise every `fih_fire_type` with a module-level
`_normalised_code(value)` (`"" if value is None else str(value).strip().upper()`
— jarrah's `events/dbca_fire.py` pattern; the real GDA94 file carries
one raw lowercase `wf`) BEFORE the vocabulary check, and key
`counts_by_type` by the normalised code.
Refusals, each a `DbcaError` with a message naming the offending value:
layer absent (list the layers found); CRS != EPSG:4283; any
`REQUIRED_FIELDS` missing; feature_count == 0 ("0 features"); any
NORMALISED `fih_fire_type` outside `FIRE_TYPES` (name the codes — this
is the MR/PL tripwire; `None` normalises to `""` and tripwires too);
any `fih_year1` null, < YEAR_MIN, or > snapshot_year
(name the offending year). Return the summary with
`counts_by_type` sorted-key dict.

**Step 4:** `uv run pytest tests/sources/test_dbca.py -q` — expect PASS.

**Step 5:** `uv run ruff check src/wa_mine_monitor/sources/dbca.py tests/sources/test_dbca.py && uv run mypy src` — clean.

---

### Task 4: `fetch-dbca-fire` CLI — staging with licence-evidence capture

**Files:**
- Modify: `src/wa_mine_monitor/cli.py` (new command; place it near
  `fetch_silo_cmd`, ~line 1704, and mirror its structure)
- Test: `tests/sources/test_dbca.py` (CLI section)

Command signature:

```python
@app.command("fetch-dbca-fire")
def fetch_dbca_fire_cmd(
    config: Path = ConfigOption,
    date: str = DateOption,
    mode: str = typer.Option("authoritative", "--mode", help="authoritative|mirror"),
    source_dir: Path = typer.Option(
        ...,
        "--source-dir",
        help="Authoritative DBCA-060 package directory (Data WA download).",
    ),
) -> None:
```

Behaviour, in order (each refusal a structured JSON refusal + exit 1):

1. `--mode mirror` → refuse: "the ArcGIS mirror route is declined —
   see docs/decisions/2026-08-29-dbca-mirror-declined.md"; any other
   non-"authoritative" value → refuse naming the two valid modes.
2. Bind to the authoritative package, not a bare file: `source_dir`
   missing on disk → refuse naming the path. Locate exactly one
   `*.gpkg` inside it (refuse on zero or >1, naming what was found).
   `source_dir / "SHA256SUMS.txt"` and `source_dir / "metadata.txt"`
   must both exist → refuse naming the missing file
   (`"stage": "source_package"`). For every entry in the source
   `SHA256SUMS.txt` whose file exists in `source_dir`, recompute and
   compare the digest → any mismatch refuses with
   `"stage": "source_digests"` (the source sums cover only the zips;
   the gpkg digest is computed fresh in step 7).
3. `snapshots.create_snapshot_dir(data_root, "dbca_060_fire", date)`;
   `_refuse_if_snapshot_already_finalized`; stray-file gate
   (`_refuse_if_unexpected_files`) with
   `expected_names = {"metadata.txt", GPKG_NAME, EVIDENCE_NAME,
   "source-SHA256SUMS.txt", "source-metadata.txt"}` where
   `GPKG_NAME = <located gpkg>.name` and
   `EVIDENCE_NAME = "catalogue-page.html"`, closing clause
   "before staging".
4. Copy the gpkg into the snapshot dir (`shutil.copy2`) unless already
   present, plus the source `SHA256SUMS.txt` as
   `source-SHA256SUMS.txt` and the source `metadata.txt` as
   `source-metadata.txt` (renamed so they cannot collide with this
   snapshot's own `metadata.txt`/`SHA256SUMS.txt`); then
   `dbca.validate_fire_history_file(dest,
   snapshot_year=int(date[:4]))` — on `DbcaError`, structured refusal
   with `"stage": "validation"`.
5. Licence-evidence capture: `_fetch_catalogue_page(url) -> bytes`, a
   module-level function using `requests.get(url, timeout=60)` on
   `licence.SOURCES["dbca_060_fire"].source_url`, written to
   `EVIDENCE_NAME`. Any exception → structured refusal
   `"stage": "licence_evidence"` — the snapshot is NEVER finalized
   without evidence. (Tests monkeypatch `_fetch_catalogue_page`.)
6. `snapshots.write_snapshot_metadata(...)` with
   `purpose="DBCA-060 recorded-fire-overlap context (Batch F F3)."` and
   a `licence_note` naming CC-BY-4.0 + the catalogue URL. Then a
   SECOND metadata write is NOT allowed — instead include the
   validation summary in `resolved_args` (below).
7. Stray-file gate again ("before finalizing"); `finalize_snapshot`;
   `verify_snapshot`; `write_run_manifest` with TWO `SourceAsset`
   inputs: the gpkg (`uri=<located source gpkg>.resolve().as_uri()`,
   `sha256=sha256_file(dest)`) and the evidence file
   (`uri=<catalogue url>`, `sha256=sha256_file(evidence_path)`), both
   with this source's licence fields. `resolved_args` records `date`,
   `mode`, `feature_count`, `counts_by_type`, `year_min`, `year_max`.
8. Echo a JSON success payload: `snapshot_dir`, `feature_count`,
   `counts_by_type`, `verified: {ok, bad, missing}`.

**Step 1: Write failing CLI tests** (in `tests/sources/test_dbca.py`,
using `typer.testing.CliRunner` — copy the runner/config-file pattern
from `tests/sources/test_silo.py`, including the
`collect_git_state` monkeypatch). Cases:

```python
def test_fetch_dbca_fire_refuses_mirror_mode_before_any_io(...)
    # exit 1; "mirror route is declined" in output; data_root untouched

def test_fetch_dbca_fire_stages_validates_and_finalizes(...)
    # seed a source dir: valid fixture gpkg + metadata.txt + a small
    # extras.zip + SHA256SUMS.txt listing the zip's real digest;
    # monkeypatch cli_module._fetch_catalogue_page to return
    # b"<html>CC BY 4.0</html>"; exit 0; snapshot dir holds gpkg +
    # catalogue-page.html + metadata.txt + SHA256SUMS.txt +
    # source-SHA256SUMS.txt + source-metadata.txt; manifest has 2
    # inputs; resolved_args carries counts_by_type

def test_fetch_dbca_fire_refuses_source_dir_without_sums(...)
    # source dir with gpkg + metadata.txt but no SHA256SUMS.txt;
    # exit 1, "stage": "source_package"; nothing staged

def test_fetch_dbca_fire_refuses_source_digest_mismatch(...)
    # SHA256SUMS.txt lists a wrong digest for extras.zip; exit 1,
    # "stage": "source_digests"; nothing staged

def test_fetch_dbca_fire_refuses_invalid_gpkg_before_finalize(...)
    # fixture with fire_type "MR"; exit 1; no SHA256SUMS.txt

def test_fetch_dbca_fire_refuses_when_evidence_fetch_fails(...)
    # monkeypatch _fetch_catalogue_page to raise; exit 1; no SHA256SUMS.txt

def test_fetch_dbca_fire_refuses_a_finalized_snapshot(...)
    # run twice; second exits 1 naming the finalized snapshot

def test_fetch_dbca_fire_refuses_stray_files_before_staging(...)
    # pre-create snapshot dir with "stray.part"; exit 1, "before staging"
```

**Step 2:** run them — expect FAIL (no such command).
**Step 3:** implement as specified.
**Step 4:** `uv run pytest tests/sources/test_dbca.py -q` — PASS.
**Step 5:** full battery on touched files:
`uv run ruff check src tests && uv run mypy src` — clean.

---

### Task 5: `fire_context.py` — schema and pure row assembly

**Files:**
- Create: `src/wa_mine_monitor/fire_context.py`
- Test: `tests/test_fire_context.py` (new)

Mirror `climate_context.py`'s split exactly: pure module, no I/O; the
CLI (Task 7) feeds it plain mappings. Module docstring: claim boundary
verbatim ("context only ... never states a cause"), the L18 sentence
("`not_recorded` is a statement about the record, never the ground"),
and the row-count invariant (one row per (site_id, year)
unconditionally).

Public surface:

```python
FIRE_CONTEXT_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string(), nullable=False),
        pa.field("maus_id", pa.string(), nullable=False),
        pa.field("year", pa.int32(), nullable=False),
        pa.field("fire_status", pa.string(), nullable=False),
        pa.field("fire_record_count", pa.int32(), nullable=True),
        pa.field("fire_source_version", pa.string(), nullable=False),
        pa.field("fire_coverage_status", pa.string(), nullable=False),
        pa.field("fire_snapshot_date", pa.string(), nullable=False),
        pa.field("not_computable_reason", pa.string(), nullable=True),
    ]
)

FIRE_STATUS_RECORDED = "recorded"
FIRE_STATUS_NOT_RECORDED = "not_recorded"
FIRE_STATUS_UNKNOWN = "unknown"

COVERAGE_COVERED = "covered"
COVERAGE_OUTSIDE_WINDOW = "outside_window"
COVERAGE_NO_FOOTPRINT = "no_footprint"


class FireContextError(ValueError): ...


def coverage_window(snapshot_year: int) -> tuple[int, int]:
    """Frozen window [1937, snapshot_year - 1] (decision 2026-08-29)."""
    return (dbca.COVERAGE_START_YEAR, snapshot_year - 1)


def assemble_rows(
    *,
    site_maus_pairs: Sequence[tuple[str, str]],
    counts_by_maus_year: Mapping[tuple[str, int], int],
    no_footprint_by_maus: Mapping[str, str],
    years: Sequence[int],
    snapshot_year: int,
    snapshot_date: str,
    source_version: str,
) -> pd.DataFrame: ...
```

Row rules (implement in this exact precedence order):
1. `maus_id in no_footprint_by_maus` → `unknown`,
   `fire_coverage_status=no_footprint`,
   `not_computable_reason=no_footprint_by_maus[maus_id]`,
   `fire_record_count` null — for EVERY year of that site.
2. `counts_by_maus_year.get((maus_id, year), 0) > 0` → `recorded`,
   `covered` if the year is inside `coverage_window(snapshot_year)`
   else `outside_window` (a record outside the window is still a
   record), `fire_record_count` = the count, reason null.
3. count == 0 and year inside the window → `not_recorded`, `covered`,
   `fire_record_count=0`, reason null.
4. count == 0 and year outside the window → `unknown`,
   `outside_window`, count null,
   `not_computable_reason="year outside the declared coverage window
   [<lo>, <hi>]"`.

`FireContextError` on: empty `years`, empty `site_maus_pairs`, or a
negative count. Build the frame with explicit dtypes matching the
schema (`Int32` for the nullable count) and column order ==
`FIRE_CONTEXT_SCHEMA.names`.

Also export `validate_row_counts(frame, *, n_pairs, n_years) -> None`
raising `FireContextError` unless
`len(frame) == n_pairs * n_years` AND the per-status value_counts sum
to `len(frame)` — the D13 F4 reconciliation acceptance.

**Step 1: failing tests** covering: one row per pair×year always;
each rule 1–4 lands the exact tuple of
(status, coverage, count, reason-null-ness); recorded-outside-window
case; `coverage_window(2026) == (1937, 2025)`; reconciliation passes on
assembled output and fails on a mutated (row-dropped) frame; empty
years/pairs raise; column order matches schema.
**Step 2:** run — FAIL (module absent).
**Step 3:** implement.
**Step 4:** `uv run pytest tests/test_fire_context.py -q` — PASS.
**Step 5:** ruff + mypy clean.

---

### Task 6: `sources/dbca.py` — per-footprint fire-year counts

**Files:**
- Modify: `src/wa_mine_monitor/sources/dbca.py`
- Test: `tests/sources/test_dbca.py`

```python
def fire_year_counts_for_footprint(
    gpkg_path: Path,
    footprint_4283: BaseGeometry,
) -> dict[int, int]:
    """Count intersecting fire polygons per `fih_year1` for one footprint.

    Reads ONLY the footprint's bbox window from the GeoPackage
    (`pyogrio` bbox pushdown onto the layer's r-tree) -- the statewide
    file is 2.1 GB and must never be loaded whole. bbox prefilter, then
    an exact `.intersects` test (touching-only bboxes are not
    intersections). All fire types count (WF, PB and 999 are all
    recorded fires).
    """
```

Implementation: `gpd.read_file(gpkg_path, layer=LAYER_NAME,
bbox=tuple(footprint_4283.bounds), columns=["fih_year1"])` (pyogrio
engine); if empty → `{}`; else exact filter
`frame[frame.geometry.intersects(footprint_4283)]`, then
`value_counts` on `fih_year1` → `{int(year): int(n)}`.

**Step 1: failing tests:** (a) two fires intersecting the footprint in
1990 + one in 2001, one fire far away → `{1990: 2, 2001: 1}`;
(b) polygon whose bbox overlaps but geometry doesn't intersect →
excluded; (c) no intersections → `{}`; (d) all three fire types count.
**Step 2:** run — FAIL. **Step 3:** implement. **Step 4:** PASS.
**Step 5:** ruff + mypy clean.

---

### Task 7: `build-fire-context` CLI

**Files:**
- Modify: `src/wa_mine_monitor/cli.py` (new command; place it after
  `build_climate_context_cmd` and mirror its gate structure LINE FOR
  LINE — read `cli.py:2653-3100` first)
- Test: `tests/test_fire_context.py` (CLI section)

Signature: `--config`, `--date`, `--start-year` (required),
`--end-year` (required). Order of operations:

1. GATE 1: inverted year range refused before any I/O (copy the
   climate wording).
2. `output_path = data_root/"curated"/"fire-context"/date/"fire_context.parquet"`;
   `_refuse_if_curated_output_already_exists`.
3. GATE 2: `register.latest_snapshot(data_root, "dbca_060_fire")`;
   locate the gpkg inside as the single `*.gpkg` file FIRST (refuse if
   zero or >1), then
   `_verify_snapshot_or_refuse(dir, source_id="dbca_060_fire",
   required_files=(gpkg.name,))` — the gpkg must be in the hashed set
   (`required_files` is the documented guard against a file dropped in
   after finalisation; `build-climate-context` names every consumed
   SILO file the same way). `snapshot_year = int(dir.name[:4])`;
   `source_version =
   f"dbca-060-{dir.name}-sha256-{sha256_file(gpkg)[:12]}"`.
4. GATE 3: latest curated register, digest-verified, must carry
   `trajectory_status` AND `d3_forced_threshold` (copy the two refusals
   verbatim from climate); `eligible =
   trajectory_extract.select_eligible_sites(register_df)`.
5. GATE 4: crosswalk + Maus, sha-tied — copy the climate
   implementation exactly (manifest Maus input by licence id, sha256
   compare, `tier1_population`, the stable site→maus tie-break, the
   eligible-site-missing-from-crosswalk refusal, the
   missing-geometry refusal). Both refusals are DELIBERATE integrity
   gates (an eligible site must have a high-confidence Maus match; the
   crosswalked maus_id must exist in the sha-tied snapshot) — they are
   NOT downgraded to per-row `unknown`. `no_footprint` is reserved for
   step 6's empty/invalid geometry, so `site_maus_pairs` always covers
   every eligible site and `validate_row_counts` reconciles against
   the full selected population.
6. Footprints: `maus_gdf` reprojected to `crosswalk.TARGET_CRS` as in
   climate, then per distinct `maus_id` reproject the single geometry
   to EPSG:4283 (`gpd.GeoSeries([...], crs=TARGET_CRS).to_crs("EPSG:4283")`).
   A geometry that is empty or invalid
   (`geom.is_empty or not geom.is_valid`) lands in
   `no_footprint_by_maus[maus_id] = "footprint geometry empty or invalid"`
   instead of the counts loop — a per-row unknown, NOT a run abort.
7. Counts loop: for each remaining distinct `maus_id`,
   `dbca.fire_year_counts_for_footprint(gpkg, geom)`; fold into
   `counts_by_maus_year[(maus_id, year)]` keeping ONLY years in
   `range(start_year, end_year + 1)` (the parquet holds requested
   years only; out-of-range fire years are simply not requested rows).
8. `fire_context.assemble_rows(...)`;
   `fire_context.validate_row_counts(frame, n_pairs=len(site_maus_pairs),
   n_years=end_year - start_year + 1)`.
9. Write parquet with `FIRE_CONTEXT_SCHEMA` (mirror how climate writes
   its parquet), then `write_run_manifest` — inputs: the gpkg
   `SourceAsset` (uri = file uri, sha256, dbca licence fields) plus the
   register/crosswalk assets exactly as climate records its inputs;
   `resolved_args`: date, start_year, end_year, n_sites, n_rows,
   status_counts (value_counts of `fire_status` as a dict).
10. Success payload: `output_path`, `rows`, `status_counts`.

**Step 1: failing CLI tests.** Build `_seed_fire_world(...)` modelled
on `tests/test_climate_context.py::_seed_world` (copy the register/
crosswalk/maus seeding helpers' USE, importing or re-implementing
minimally — check whether those helpers are importable; if not, copy
the minimal versions into `tests/test_fire_context.py`), replacing the
SILO snapshot seed with a finalized `raw/dbca_060_fire/<date>/`
snapshot: fixture gpkg (Task 3 helper) + metadata.txt +
`finalize_snapshot`. Cases:

```python
def test_build_fire_context_cli_end_to_end(...)
    # one site, footprint at the fixture fires' location; fire years 1990x2,
    # 2001x1 inside requested range 1989..2002; expect: exit 0; rows ==
    # 14; 1990 recorded count 2; 2001 recorded count 1; all other years
    # not_recorded/covered count 0; manifest exists with dbca input asset;
    # status_counts reconcile

def test_build_fire_context_marks_prewindow_years_unknown(...)
    # requested range includes 1936 -> that row unknown/outside_window

def test_build_fire_context_snapshot_year_excluded_from_window(...)
    # snapshot date 2026-*, requested year 2026 with no record ->
    # unknown/outside_window (not not_recorded)

def test_build_fire_context_recorded_wins_outside_window(...)
    # fire record in the snapshot year itself -> recorded/outside_window

def test_build_fire_context_invalid_footprint_is_unknown_not_abort(...)
    # empty-geometry maus footprint -> every year unknown/no_footprint, exit 0

def test_build_fire_context_refuses_inverted_range(...)
def test_build_fire_context_refuses_existing_output(...)
def test_build_fire_context_refuses_unverified_snapshot(...)
    # unfinalized dbca snapshot -> exit 1
def test_build_fire_context_refuses_gpkg_dropped_in_after_finalize(...)
    # finalize the dbca snapshot WITHOUT the gpkg, copy it in
    # afterwards -> exit 1 (required_files: never hashed)
def test_build_fire_context_refuses_non_d3_register(...)
    # register without d3_forced_threshold -> exit 1
```

**Step 2:** run — FAIL. **Step 3:** implement. **Step 4:** PASS.
**Step 5:** `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src scripts && uv run pytest -q -rs` — full battery green.

---

### Task 8: Docs — roadmap and design-doc cross-references

**Files:**
- Modify: `docs/ROADMAP.md` (row 4)
- Modify: `docs/plans/2026-08-29-dbca-fire-context-design.md` (status line only)

**Step 1:** ROADMAP row 4: replace the "DBCA mirror route blocked
pending evidence adjudication" blocker with: "Mirror declined, F1
dissolved 2026-08-29 (A10); `fetch-dbca-fire` + `build-fire-context`
landed; live staging + build pending owner run; F6 join still requires
E4". Do NOT mark Batch F done — F6 and the live runs remain.
**Step 2:** Add "Status: plan executed <date>" under the design doc's
title. **Step 3:** grep ROADMAP for "adjudication" to confirm the old
blocker text is gone.

---

## Execution notes for build-flow

- Batch 1: Tasks 1, 2, 3 (independent).
- Batch 2: Tasks 4, 5 (4 needs 3; 5 needs nothing but ships with 4).
- Batch 3: Task 6 (needs 3).
- Batch 4: Task 7 (needs 4, 5, 6), Task 8 (docs).
- No live run against the real 2.1 GB snapshot inside the workflow —
  that happens post-merge in the main session.
- No commits inside the workflow.
