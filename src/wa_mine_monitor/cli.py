"""Command-line entry point for the WA mine rehabilitation monitor.

Every subcommand takes a `--config` option (default `config/base.yaml`,
resolved relative to the current working directory) rather than a hardcoded
path, so the same command can be run against a fixture config in tests and
against the real project config in operation.
"""

from __future__ import annotations

import contextlib
import csv
import functools
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date as dt_date
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
import rasterio  # type: ignore[import-untyped]
import requests
import shapely
import typer
import yaml
from pydantic import ValidationError

from wa_mine_monitor import (
    climate_context,
    crosswalk,
    d3_inputs,
    d3_protocol,
    d3_threshold,
    dea_coverage,
    dea_raster,
    dea_volume,
    export_gate,
    fire_context,
    huntly_validation,
    licence,
    manifests,
    maus_footprints,
    pixel_support,
    public_rc,
    register,
    release,
    snapshots,
    spectral_metrics,
    trajectories,
    trajectory_extract,
)
from wa_mine_monitor.config import ProjectConfig, load_config
from wa_mine_monitor.http import (
    HttpClient,
    HttpRequestRefused,
    HttpRetryExhausted,
    map_concurrent,
)
from wa_mine_monitor.provenance import SourceAsset, collect_git_state, sha256_file
from wa_mine_monitor.secrets import redact_secrets, scrub_string_leaves
from wa_mine_monitor.source_catalogue import DEA_COLLECTIONS, SourceSpec
from wa_mine_monitor.sources import dbca, silo, wa_regions
from wa_mine_monitor.sources.dea import (
    DEA_RETRY_POLICY,
    CatalogueValidationError,
    collection_url,
    fetch_collection_catalogue,
    new_dea_client,
)
from wa_mine_monitor.sources.maus import (
    SnapshotValidationError as MausSnapshotValidationError,
)
from wa_mine_monitor.sources.maus import (
    clip_to_wa,
    read_source_gpkg,
    validate_maus_extract,
)
from wa_mine_monitor.sources.minedex import (
    DASC_MINEDEX_CSV_URL,
    DASC_MINEDEX_SHP_URL,
    DATAWA_METADATA_FILENAME,
    DATAWA_PACKAGE_SHOW_URL,
    LICENCE_PDF_FILENAME,
    MINEDEX_CODE_COLUMN_DTYPES,
    MINEDEX_CSV_ZIP_FILENAME,
    MINEDEX_SHP_ZIP_FILENAME,
    LicenceEvidenceCaptureError,
    capture_licence_evidence,
    download_minedex_zip,
    fetch_datawa_package_show,
    validate_minedex_bundles,
)
from wa_mine_monitor.sources.minedex import (
    SnapshotValidationError as MinedexSnapshotValidationError,
)
from wa_mine_monitor.sources.silo import (
    SiloError,
    annual_object_name,
    annual_object_url,
    download_annual_file,
    validate_daily_rain_file,
)
from wa_mine_monitor.sources.tenements import (
    DASC_TENEMENTS_SHP_URL,
    TENEMENTS_SHAPEFILE_BASENAME,
    TENEMENTS_ZIP_FILENAME,
    SnapshotValidationError,
    download_tenements_zip,
    validate_tenements_zip,
)
from wa_mine_monitor.tables import read_table, write_table

#: `pretty_exceptions_show_locals=False` -- Typer/Click's default
#: (`True`) prints every local variable's value into an unhandled
#: traceback, which would print the parsed config mapping (and any
#: credential-shaped value inside it) for any exception this module does
#: not explicitly catch below. Explicit off, not relied on by omission.
app = typer.Typer(pretty_exceptions_show_locals=False)

#: Default `--config` value. Resolved relative to the CWD at invocation
#: time, not at import time, so the default tracks wherever the command is
#: actually run from.
DEFAULT_CONFIG_PATH = Path("config/base.yaml")

ConfigOption = typer.Option(
    DEFAULT_CONFIG_PATH,
    "--config",
    help="Path to the project config YAML.",
    exists=True,
    dir_okay=False,
    readable=True,
)

#: This checkout's root, used only to collect this run's git state for a
#: manifest (`collect_git_state(_REPO_ROOT)`) -- three parents up from this
#: file (`src/wa_mine_monitor/cli.py` -> `wa_mine_monitor` -> `src` -> repo
#: root). Every test that reaches a manifest-writing command monkeypatches
#: `collect_git_state` itself rather than depending on this checkout's real
#: commit history, the same convention `tests/test_provenance.py` documents
#: for git-state tests generally.
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Production `fetch-silo` asserts the downloaded grid spans WA (see
#: `silo.validate_daily_rain_file`). CLI tests monkeypatch this to False
#: so their fixtures can be a few cells rather than a continental grid;
#: the coverage rule itself is tested at unit level in
#: `tests/sources/test_silo.py`.
_SILO_REQUIRE_STATEWIDE = True


def _collect_git_state_disclosing_gaps(repo_root: Path) -> dict[str, Any]:
    """Collect this run's git state via `collect_git_state`, without ever
    crashing the command over it.

    `collect_git_state` shells out to `git rev-parse HEAD` with
    `check=True`, which raises `subprocess.CalledProcessError` (git exit
    128) whenever `repo_root` is a repository with no commits yet -- the
    EXACT state this project's own freshly-initialised repos start in (see
    `CLAUDE.md`'s cross-task ledger: "zero commits exist") -- or when
    `repo_root` is not a git checkout at all (a non-editable wheel install
    puts `_REPO_ROOT` at `lib/python3.12`, several directories short of any
    `.git`). `FileNotFoundError` covers the same "cannot answer" outcome
    when the `git` executable itself is not on `PATH`. Before this guard,
    every one of this module's three fetch commands died with an unhandled
    traceback on either condition -- after already creating an (otherwise
    empty) snapshot directory -- because every test that reaches a
    manifest-writing command monkeypatches `collect_git_state` itself and so
    never exercises the real subprocess call.

    Neither condition is refused: a run manifest is still useful provenance
    without a commit sha, and refusing outright would make every command in
    this module unusable in exactly the repo state the project is in right
    now. Instead the gap is disclosed in the returned mapping, the same
    "disclose, don't crash" discipline `manifests.py` already applies to a
    scrubbed diff (`git.diff_scrubbed`) -- never a fabricated sha standing
    in for a commit that does not exist:

    - `unborn_head=True`: `repo_root` IS a git repository, but has no
      commits (`git rev-parse HEAD` fails with something other than "not a
      git repository", e.g. "unknown revision or path not in the working
      tree").
    - `git_available=False`: `repo_root` is not a git repository at all, or
      `git` is not runnable.

    Either branch also sets `sha=None`, `dirty=None` (unknown -- `git
    status` was never reached, so this is honestly "unknown", not a guessed
    `False`) and `diff=""`, and records the raw failure in
    `git_state_error` for a human reading the manifest later. This is the
    one helper the three fetch commands below share, so the guard cannot
    drift between them.
    """
    try:
        return collect_git_state(repo_root)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        not_a_repo = "not a git repository" in stderr
        return {
            "sha": None,
            "dirty": None,
            "diff": "",
            "unborn_head": not not_a_repo,
            "git_available": not not_a_repo,
            "git_state_error": stderr or f"{exc!r}",
        }
    except FileNotFoundError as exc:
        return {
            "sha": None,
            "dirty": None,
            "diff": "",
            "unborn_head": False,
            "git_available": False,
            "git_state_error": str(exc),
        }


def _load_config_or_exit(config: Path) -> ProjectConfig:
    """Load `config` via `load_config`, or echo a REDACTED structured error
    and exit 1 -- the single config-loading path for every command in this
    module.

    This exists as one helper rather than as a call each command repeats,
    because a bare `load_config(config)` leaks credential-shaped values two
    separate ways, and a per-command copy of the guard is a per-command
    chance to omit one of them:

    - `pydantic.ValidationError` embeds the ENTIRE offending input in its
      message and `repr` (`input_value={'api_token': 'X'}`), so this renders
      only `exc.errors(include_input=False, include_url=False)` -- error
      type/location/message, nothing from the input. `scrub_text_secrets`
      was considered and rejected: it closes assignment-shaped fragments
      (`key=value` / `key: value`), not the dict-repr shape pydantic emits.
    - `yaml.MarkedYAMLError.__str__` (and so `str(exc)`) embeds
      `Mark.get_snippet()` -- the offending SOURCE LINE from the file,
      verbatim, including any credential on it. This renders only
      `_yaml_marked_error_detail(exc)`: type name, the parser's short
      structural `problem` description, and the 1-indexed line/column. Any
      other `yaml.YAMLError` (no mark) falls back to the type name alone.

    `app`'s `pretty_exceptions_show_locals=False` does NOT close either
    leak: both live in the exception's own `str()`, not in the traceback's
    locals frame -- so the guard must be an explicit `except`, and every
    command must go through it. `raise typer.Exit(1) from None` suppresses
    the chained original, whose own rendering carries the same leaks.
    """
    try:
        return load_config(config)
    except (ValidationError, OSError, yaml.YAMLError) as exc:
        if isinstance(exc, ValidationError):
            detail: object = exc.errors(include_input=False, include_url=False)
        elif isinstance(exc, yaml.MarkedYAMLError):
            detail = _yaml_marked_error_detail(exc)
        elif isinstance(exc, yaml.YAMLError):
            detail = {"error_type": type(exc).__name__}
        else:
            detail = str(exc)
        typer.echo(json.dumps({"config_error": detail}, indent=2, sort_keys=True, default=str))
        raise typer.Exit(1) from None


def _validate_snapshot_date(value: str) -> str:
    """Validate `--date` is `YYYY-MM-DD`.

    The snapshot date is always explicit, never computed from the clock --
    see `snapshots.py`'s module docstring: it is a fact about when a fetch
    happened, and belongs to whoever ran it, not to whoever re-imports this
    module later.
    """
    try:
        dt_date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"--date must be YYYY-MM-DD, got {value!r}") from exc
    return value


DateOption = typer.Option(
    ...,
    "--date",
    help="Snapshot date, YYYY-MM-DD. Always explicit, never computed.",
    callback=_validate_snapshot_date,
)


def _validate_optional_snapshot_date(value: str | None) -> str | None:
    """Same validation as `_validate_snapshot_date`, but `None` passes through.

    For `--date` options that default to "the latest snapshot" (e.g.
    `adjudicate-minedex-licence`) rather than requiring an explicit date.
    """
    if value is None:
        return None
    return _validate_snapshot_date(value)


OptionalDateOption = typer.Option(
    None,
    "--date",
    help="Snapshot date, YYYY-MM-DD. Defaults to the latest MINEDEX snapshot.",
    callback=_validate_optional_snapshot_date,
)

#: `apply-d3-threshold`'s Batch E Task 0 forced-144 entry flag (default off,
#: `docs/decisions/2026-08-25-batch-e-forced-threshold-entry.md`) -- a module-
#: level singleton, the same B008 discipline `ConfigOption`/`DateOption`/
#: `OptionalDateOption` apply, rather than a `typer.Option(...)` call inline
#: in the function signature's defaults.
ForcedThresholdOption = typer.Option(
    False,
    "--forced-threshold/--no-forced-threshold",
    help=(
        "Batch E Task 0 forced-144 entry path (docs/decisions/"
        "2026-08-25-batch-e-forced-threshold-entry.md): when the threshold "
        "artefact's criteria_passed is False, judge eligibility at the "
        "pre-registered forced-144 fallback instead of stamping every judged "
        "site threshold_not_computed. NEVER flips criteria_passed. Requires "
        "--decision-record."
    ),
)

#: `apply-d3-threshold`'s `--decision-record` companion to `--forced-
#: threshold` -- required (and checked to name an existing file) only when
#: `--forced-threshold` is passed; its path is recorded verbatim in the run
#: manifest alongside `forced_threshold` so the disclosure and its authority
#: travel together.
DecisionRecordOption = typer.Option(
    None,
    "--decision-record",
    help=(
        "Path to the owner decision record authorising --forced-threshold. "
        "Required when --forced-threshold is passed; must name an existing "
        "file. Recorded verbatim in the run manifest alongside "
        "forced_threshold, so the disclosure and its authority travel "
        "together."
    ),
)

#: Required `--source-gpkg` option for `fetch-maus-extract`: the LOCAL global
#: Maus v2 GeoPackage this command reads, read-only -- never downloaded by
#: this project. `exists=True, dir_okay=False, readable=True` rejects a
#: missing/unreadable path before any snapshot directory is even created,
#: the same fast-fail discipline `ConfigOption` gives `--config`.
SourceGpkgOption = typer.Option(
    ...,
    "--source-gpkg",
    help=(
        "Path to the local global Maus et al. v2 GeoPackage (read-only; "
        "this project never re-downloads it)."
    ),
    exists=True,
    dir_okay=False,
    readable=True,
)


#: `fetch-dbca-fire`'s `--source-dir`: the authoritative DBCA-060 package
#: directory (a Data WA download this command never fetches itself). No
#: `exists=True` here -- unlike `SourceGpkgOption` -- because a missing
#: `--source-dir` is refused with this module's own structured JSON error
#: (`"stage": "source_package"`), not typer's built-in path validation.
DbcaSourceDirOption = typer.Option(
    ...,
    "--source-dir",
    help="Authoritative DBCA-060 package directory (Data WA download).",
)


#: DPIRD-020 via the SLIP public ArcGIS REST layer, pinned 2026-08-21
#: (Data WA catalogue record `regional-development-commission-boundaries`,
#: licence CC-BY-4.0 re-verified the same day via the CKAN API). The
#: previous pin, `https://data-downloads.slip.wa.gov.au/DPIRD-020/Geopackage`
#: (2026-08-16), now redirects to SLIP SSO and returns 403 anonymously.
#: `orderByFields=objectid` keeps the byte stream -- and so the snapshot
#: digest -- stable across fetches of unchanged data.
_RDC_REGIONS_DOWNLOAD_URL = (
    "https://public-services.slip.wa.gov.au/public/rest/services/"
    "SLIP_Public_Services/Boundaries/MapServer/25/query"
    "?where=1%3D1&outFields=*&outSR=4326&orderByFields=objectid&f=geojson"
)
_RDC_REGIONS_FILENAME = "regions.geojson"

ProtocolConfigOption = typer.Option(
    Path("config/d3.yaml"),
    "--protocol-config",
    help="Path to the D3 protocol YAML to freeze.",
)

ReadWorkersOption = typer.Option(
    8,
    "--read-workers",
    min=1,
    help="Concurrent raster reads per footprint (round-trip-latency bound; wall-clock only, outputs identical).",
)

ScopeOption = typer.Option(
    "sites",
    "--scope",
    help=(
        "'sites' extracts only the --site-id values given (they must be D3-eligible); "
        "'statewide' extracts every eligible site and is REFUSED until validate-huntly "
        "has written a passing verdict."
    ),
)
SiteIdOption = typer.Option(
    None,
    "--site-id",
    help="Repeatable. Required for --scope sites; rejected for --scope statewide.",
)


def _fetch_catalogue_page(url: str) -> bytes:
    """Download the licence-evidence catalogue page at `url` (network seam,
    monkeypatchable). Used by `fetch_dbca_fire_cmd` to capture proof of the
    licence terms displayed at the Data WA catalogue page for DBCA-060 --
    the snapshot is never finalized without this evidence."""
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def _fetch_region_boundaries_bytes() -> bytes:
    """Download the pinned DPIRD-020 GeoJSON (network seam, monkeypatchable)."""
    client = HttpClient()
    return client.get_bytes(_RDC_REGIONS_DOWNLOAD_URL)


class RegionPayloadError(ValueError):
    """The fetched region-boundary bytes are not a complete GeoJSON body."""


def _refuse_unless_complete_geojson(payload: bytes) -> None:
    """Refuse by SHAPE before GDAL sees the bytes: a login page or an
    ArcGIS page-limited result must never become a snapshot."""
    try:
        body = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegionPayloadError(
            f"region boundaries payload is not a GeoJSON FeatureCollection: {exc}"
        ) from exc
    if not isinstance(body, dict) or body.get("type") != "FeatureCollection":
        found = body.get("type") if isinstance(body, dict) else type(body).__name__
        raise RegionPayloadError(
            "region boundaries payload is not a GeoJSON FeatureCollection "
            f"(top-level type={found!r})"
        )
    if body.get("exceededTransferLimit"):
        raise RegionPayloadError(
            "region boundaries payload has exceededTransferLimit=true -- the REST "
            "layer paged the result; refusing a partial region set"
        )


def _refuse_if_snapshot_already_finalized(
    snapshot_dir: Path,
    *,
    config: Mapping[str, Any],
    git_state: Mapping[str, Any],
) -> None:
    """Refuse a re-run against an already-finalized snapshot, BEFORE any
    download, read or write happens.

    `snapshots.create_snapshot_dir` is deliberately idempotent -- a re-run
    against an already-created snapshot DIRECTORY is a legitimate resume --
    but that idempotency does not extend to a snapshot whose
    `SHA256SUMS.txt` already exists: `finalize_snapshot` is the one part of
    the pipeline that is NOT safe to repeat blindly, and every command that
    reaches this point has already downloaded and written files by the time
    it would discover that the eventual `finalize_snapshot` call is doomed
    (`FileExistsError`, uncaught, after the damage is done -- see the
    regression this guard closes). So this checks `SHA256SUMS.txt` FIRST,
    before `fetch_tenements`/`fetch_minedex`/`fetch_maus_extract` touch the
    network, the local source file, or the snapshot directory's contents at
    all.

    Also runs `manifests.preflight_manifest_conflict` against the eventual
    manifest path (`<sums_path>.run_manifest.json`) at the same point, so a
    run doomed by a DIFFERENT provenance (changed config, changed git state)
    is refused in milliseconds too, rather than only after a wasted
    download -- the same one-sided pre-flight `preflight_manifest_conflict`
    is designed for.

    Raises `typer.Exit(1)` (after echoing a structured JSON error naming the
    path) on either refusal; returns `None` when neither guard fires. A
    `None` here is not a promise the eventual write will succeed --
    `resolved_args`, `inputs` and the artefact's own hash remain unknowable
    this early, per `preflight_manifest_conflict`'s own documented
    one-sidedness.
    """
    sums_path = snapshot_dir / snapshots.SHA256SUMS_FILENAME
    if sums_path.exists():
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{sums_path} already exists -- this snapshot was already "
                        "finalized by an earlier run. Re-running would download and "
                        "overwrite the snapshot's files before discovering that "
                        "finalize_snapshot() must refuse, silently mutating what an "
                        "earlier run measured against. Refusing before any download, "
                        "read or write happens. Move the existing snapshot directory "
                        "aside, or choose a different --date, to fetch again."
                    ),
                    "snapshot_dir": str(snapshot_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    conflict = manifests.preflight_manifest_conflict(sums_path, config=config, git_state=git_state)
    if conflict is not None:
        typer.echo(json.dumps({"refusal": conflict}, indent=2, sort_keys=True))
        raise typer.Exit(1)


def _refuse_if_unexpected_files(
    snapshot_dir: Path,
    *,
    expected_names: set[str],
    closing_clause: str,
) -> None:
    """Refuse when `snapshot_dir` holds file(s) not accounted for by
    `expected_names`.

    Shared by `fetch_silo_cmd`'s two unexpected-file gates: a pre-download
    gate (BEFORE any bytes are fetched -- each annual SILO object is ~410 MB
    and this command is built to run off a metered connection) and a
    pre-finalize gate (catching anything that appeared during the run,
    before `finalize_snapshot` hashes the directory's contents into
    `SHA256SUMS.txt`). `closing_clause` names which point in the run the
    refusal message trails off into (e.g. "before fetching" or "before
    finalizing").
    """
    actual_names = {p.name for p in snapshot_dir.iterdir() if p.is_file()}
    unexpected = sorted(actual_names - expected_names)
    if unexpected:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "snapshot directory holds unexpected file(s) not accounted for "
                        f"by this run's year range: {unexpected} -- delete them, or "
                        f"re-run over the year range that covers them, {closing_clause}"
                    ),
                    "unexpected_files": unexpected,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None


def _refuse_if_curated_output_already_exists(
    output_path: Path,
    *,
    config: Mapping[str, Any],
    git_state: Mapping[str, Any],
) -> None:
    """Refuse a re-run against an already-built curated artefact, BEFORE any
    write happens.

    Mirrors `_refuse_if_snapshot_already_finalized`'s discipline for
    `build-register` and `build-crosswalk`, neither of which carried an
    equivalent guard: a second run against the same `--date` overwrote
    `register.parquet`/`crosswalk.parquet` (and their `*_counts.json` and,
    for `build-register`, `reconciliation.md` siblings) and only THEN
    discovered -- via an uncaught `FileExistsError` out of
    `write_run_manifest`, after the damage was done -- that the manifest
    sitting beside the OLD file could not be overwritten. The result was a
    curated artefact that no longer matched the manifest describing it
    (stale `output.sha256`, stale `resolved_args`, a stale input snapshot
    directory), while the run itself died with a bare traceback rather than
    the structured JSON refusal every other refusal in this module emits.

    Checking `output_path.exists()` FIRST is what actually closes the
    reported regression: two runs against unchanged `config`/`git_state` but
    a newer source snapshot are IDENTICAL on every field
    `preflight_manifest_conflict` compares (`_PREFLIGHT_KNOWN_FIELDS` covers
    `argv`/`config`/`git`/`package_versions`, none of which changed), so
    that check alone would return `None` and let the second run proceed to
    overwrite the artefact before failing at the very end. `preflight_
    manifest_conflict` is still checked second, as defence in depth for the
    narrower case it does catch (a changed config or git state with no
    output file yet on disk, e.g. a manifest left behind by an artefact that
    was since deleted).

    Raises `typer.Exit(1)` (after echoing a structured JSON refusal naming
    `output_path`) on either check; returns `None` when neither fires.
    """
    if output_path.exists():
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{output_path} already exists -- this curated artefact was already "
                        "built by an earlier run. Re-running would overwrite it (and its "
                        "counts/reconciliation siblings) before discovering that the manifest "
                        "write at the end of this run must refuse, silently mutating what an "
                        "earlier run measured against. Refusing before any read or write "
                        "happens. Move the existing output directory aside, or choose a "
                        "different --date, to build again."
                    ),
                    "output_path": str(output_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    conflict = manifests.preflight_manifest_conflict(
        output_path, config=config, git_state=git_state
    )
    if conflict is not None:
        typer.echo(json.dumps({"refusal": conflict}, indent=2, sort_keys=True))
        raise typer.Exit(1)


def _latest_curated_dated_dir(base_dir: Path, *, label: str) -> Path:
    """Return the most recently dated `<base_dir>/<date>/` directory.

    Calls `snapshots.latest_dated_subdir` -- the SAME dated-directory scan
    `register.latest_snapshot` calls for a raw snapshot parent
    (`<data_root>/raw/<source_id>/`), applied here to a curated-artefact
    parent (`<data_root>/curated/<artefact>/`) instead. This function does
    not re-implement that scan; it supplies `base_dir` and turns a `None`
    into a raise. It also does not check for `SHA256SUMS.txt` -- a curated
    directory carries a run manifest, not a raw snapshot's checksum
    manifest, and is never passed through `_verify_snapshot_or_refuse`
    (`snapshots.latest_dated_subdir`'s own docstring states this split; see
    `build_crosswalk_cmd`, whose curated register lookup calls this
    function and is NOT gated the way its Maus raw-snapshot lookup is).

    Raises `register.NoSnapshotFoundError`, naming `label` and `base_dir`,
    when `base_dir` does not exist or holds no date-named subdirectory --
    the same exception type `register.latest_snapshot` raises, so a single
    `except` clause at the call site handles both a missing raw snapshot
    and a missing curated directory.
    """
    result = snapshots.latest_dated_subdir(base_dir)
    if result is None:
        raise register.NoSnapshotFoundError(
            f"no dated directory found for {label!r} under {base_dir} -- build it first"
        )
    return result


def _verify_snapshot_or_refuse(
    snapshot_dir: Path, *, source_id: str, required_files: tuple[str, ...] = ()
) -> dict[str, int]:
    """Verify a raw snapshot's integrity before a build command reads it, or
    refuse the whole run.

    `register.latest_snapshot` selects a snapshot by DATE alone -- it does
    not know whether the directory it returns was ever finalized, let alone
    whether its files still match their recorded digests. Without this gate a
    validation-refused or interrupted fetch (which leaves a dated directory
    with no `SHA256SUMS.txt`) silently becomes the register's or crosswalk's
    source: the build exits 0, `reconciliation.md` prints PASS, and nothing
    anywhere discloses that the input was never verified. The same gap covers
    a finalized snapshot whose files were altered afterwards.

    `SHA256SUMS.txt` has no opinion on a file it never hashed
    (`snapshots.snapshot_entries`'s own docstring) -- so `verify_snapshot`'s
    three counts being clean does NOT mean the specific file the caller is
    about to read was ever covered. A snapshot finalized without
    `minedex.gpkg`/`tenements.gpkg`/`wa_extract.gpkg` present, with the file
    dropped in afterwards, passes `verify_snapshot` at
    `{n_ok: 1, n_bad: 0, n_missing: 0}` while the dropped-in GeoPackage was
    never hashed at all -- the build then reads it, writes a curated
    artefact, and the manifest asserts a verification that never touched the
    file the artefact was actually built from. `required_files` closes that:
    every relative POSIX path in it must be a key of
    `snapshots.snapshot_entries(snapshot_dir)` -- checked BEFORE
    `verify_snapshot`'s counts are read, so the refusal names "never hashed"
    distinctly from "hashed and bad".

    So: `snapshots.snapshot_entries` runs FIRST, and the command refuses
    (structured JSON, exit 1) when

    - `SHA256SUMS.txt` is absent (`OSError` -- the snapshot was never
      finalized) or unparseable (`ValueError`), or
    - any `required_files` entry is not among the hashed paths, or
    - `snapshots.verify_snapshot` then finds any file bad or missing
      (`n_bad`/`n_missing` non-zero), with all THREE counts named in the
      refusal -- never a collapsed pass/fail, per `verify_snapshot`'s own
      three-counts contract.

    Returns the `{"n_ok": ..., "n_bad": ..., "n_missing": ...}` triple on
    success, so the caller can record what was verified in the run
    manifest's `resolved_args` -- an artefact that rests on a verified input
    says so itself, rather than leaving a reader to re-run the check.
    """
    try:
        hashed_entries = snapshots.snapshot_entries(snapshot_dir)
    except OSError:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"snapshot {snapshot_dir} for source {source_id!r} has no "
                        f"{snapshots.SHA256SUMS_FILENAME} -- it was never finalized. A "
                        "validation-refused or interrupted fetch leaves exactly this "
                        "state, so building from it would silently consume data that "
                        "never passed verification. Re-fetch the source (or move the "
                        "unfinalized directory aside) before building."
                    ),
                    "snapshot_dir": str(snapshot_dir),
                    "source_id": source_id,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    except ValueError as exc:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"snapshot {snapshot_dir} for source {source_id!r} has an "
                        f"unreadable {snapshots.SHA256SUMS_FILENAME}: {exc}"
                    ),
                    "snapshot_dir": str(snapshot_dir),
                    "source_id": source_id,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    missing_required_files = [
        relative_path for relative_path in required_files if relative_path not in hashed_entries
    ]
    if missing_required_files:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"snapshot {snapshot_dir} for source {source_id!r} was finalized "
                        f"without {missing_required_files!r} -- {snapshots.SHA256SUMS_FILENAME} "
                        "has no opinion on a file it never hashed, so a file dropped into "
                        "this directory AFTER finalize_snapshot ran would be read as though "
                        "it were verified input. Re-fetch the source (with the expected "
                        "file present at finalize time) rather than building from an "
                        "unhashed file."
                    ),
                    "snapshot_dir": str(snapshot_dir),
                    "source_id": source_id,
                    "missing_required_files": missing_required_files,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)

    if n_bad or n_missing:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"snapshot {snapshot_dir} for source {source_id!r} fails "
                        f"integrity verification: {n_ok} file(s) ok, {n_bad} file(s) "
                        f"with a digest mismatch, {n_missing} file(s) named in "
                        f"{snapshots.SHA256SUMS_FILENAME} but absent. The snapshot was "
                        "altered (or truncated) after finalize_snapshot ran; re-fetch "
                        "the source rather than building from tampered input."
                    ),
                    "snapshot_dir": str(snapshot_dir),
                    "source_id": source_id,
                    "n_ok": n_ok,
                    "n_bad": n_bad,
                    "n_missing": n_missing,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    return {"n_ok": n_ok, "n_bad": n_bad, "n_missing": n_missing}


def _write_table_or_refuse(
    df: pd.DataFrame,
    path: Path,
    schema: pa.Schema,
    *,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """Write `df` to `path` under `schema`, refusing with structured JSON.

    `write_table` conforms a frame to an EXPLICITLY declared Arrow schema,
    which is the point of it -- nothing about the output's column types is
    inferred from the rows. The consequence is that a frame whose values do
    not match the declaration is a WRITE-time failure, and pyarrow raises it
    as `ArrowTypeError`/`ArrowInvalid` rather than as anything this module
    was catching: an uncaught traceback with empty stdout, the failure class
    the structured-refusal wrappers around `build_register`/`build_crosswalk`
    exist to eliminate. It is reachable on a live run -- `REGISTER_SCHEMA`
    declares `site_id`/`site_name`/`commodity`/`stage`/
    `owners_at_snapshot` as `pa.string()` while `register.build_register`
    copies `Sites.csv`'s columns across verbatim, and its own required-column
    check (`register._REQUIRED_SITES_COLUMNS`) is presence-only, saying
    nothing about dtype. A DASC `Sites.csv` whose `SiteCode` arrives as an
    integer therefore reaches this write intact and fails it (`Expected a
    string or bytes dtype, got int64`).

    REFUSAL, not coercion, is deliberate: an `astype(str)` here would satisfy
    the schema by fabricating the literal string `"nan"` for every null and
    would be a transformation applied to the data that nothing in the
    artefact's own provenance record discloses. The exception text names the
    offending column and is carried through verbatim.

    An entirely-null numeric column is NOT this case and is not refused --
    pyarrow casts an all-missing column to the declared type, which is
    exactly what passing `schema=` explicitly buys.

    `payload` supplies the extra keys (input paths, etc.) merged into the
    refusal JSON beside `refusal` and `output`.
    """
    try:
        write_table(df, path, schema)
    except (pa.ArrowInvalid, pa.ArrowTypeError, ValueError) as exc:
        # A conversion failure happens inside `Table.from_pandas`, before any
        # byte is written, so `path` normally does not exist here at all. A
        # failure DURING `pq.write_table` could leave a truncated file beside
        # a refusal, which the next run's
        # `_refuse_if_curated_output_already_exists` would then read as a
        # completed artefact -- so a partial file is removed and the removal
        # is DISCLOSED in the payload rather than done silently.
        partial_output_removed = False
        if path.exists():
            path.unlink()
            partial_output_removed = True
        typer.echo(
            json.dumps(
                {
                    **(dict(payload) if payload else {}),
                    "refusal": str(exc),
                    "output": str(path),
                    "partial_output_removed": partial_output_removed,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None


def _digest_verified_manifest(artefact_path: Path) -> dict[str, Any]:
    """Parse and digest-verify the run manifest beside `artefact_path`, or
    refuse (structured JSON, exit 1) before anything downstream reads it.

    Every command that consumes an already-built curated artefact needs the
    SAME check `build-dea-coverage` (Task 11) first applied to its source
    register: the manifest must exist, must parse, and its recorded
    `output.sha256` must still match the artefact's CURRENT bytes -- an
    artefact digest-verified once at build time and never re-checked at
    consumption time is exactly how a later hand-edit or partial
    re-generation goes silently unnoticed. Defined once here rather than
    inlined per command, so the check cannot drift between callers.

    Returns the parsed manifest `dict` on success.
    """
    manifest_path = Path(str(artefact_path) + manifests.MANIFEST_SUFFIX)
    if not manifest_path.exists():
        typer.echo(
            json.dumps(
                {"refusal": f"no run manifest beside {artefact_path}"}, indent=2, sort_keys=True
            )
        )
        raise typer.Exit(1)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_sha = manifest["output"]["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        typer.echo(
            json.dumps(
                {
                    "refusal": f"{manifest_path} is missing or unparseable: {exc}",
                    "stage": "digest-verification",
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    actual_sha = sha256_file(artefact_path)
    if actual_sha != manifest_sha:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"digest mismatch: {artefact_path} hashes {actual_sha[:12]}..., "
                        f"its manifest records {manifest_sha[:12]}... -- the artefact "
                        f"changed after its manifest was written"
                    ),
                    "stage": "digest-verification",
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    return manifest


def _load_dea_items(catalogue_dir: Path) -> dict[str, list[Any]]:
    """Flatten a captured `raw/dea_stac/<date>/` snapshot's item pages into
    one `source_id -> [feature, ...]` mapping, over `source_catalogue.
    DEA_COLLECTIONS`.

    Shared by `build-dea-coverage` and `derive-dea-volume`, both of which
    rebuild the SAME captured items from a `catalogue_dir` -- one to count
    epochs, the other to rebuild the item and asset indexes a Tier 1 volume
    estimate reads.
    """
    items_by_source: dict[str, list[Any]] = {}
    for spec in DEA_COLLECTIONS:
        features: list[Any] = []
        for page_path in sorted((catalogue_dir / spec.collection_id).glob("items_page_*.json")):
            page = json.loads(page_path.read_text(encoding="utf-8"))
            features.extend(page.get("features") or [])
        items_by_source[spec.source_id] = features
    return items_by_source


@app.callback()
def main() -> None:
    """WA Mine Rehabilitation Spectral Monitor.

    An explicit callback keeps this a multi-command group even while it
    carries only one subcommand -- without it, Typer/Click collapses a
    single-command app so its one command absorbs the top-level invocation
    and a name like `config-check` is rejected as an unexpected argument.
    """


@app.command("config-check")
def config_check(config: Path = ConfigOption) -> None:
    """Load the project config and echo it as secret-scrubbed JSON.

    A quick sanity check that a given config file parses and resolves the
    way the caller expects, without ever printing a credential-shaped value.

    The success path runs `scrub_string_leaves(redact_secrets(...))` -- the
    same pair `manifests.write_run_manifest` persists a config under -- rather
    than `redact_secrets` alone. `redact_secrets` is key-name based, so a
    secret carried BY VALUE under a non-credential-shaped key (`slip_endpoint:
    "https://user:pw@slip.wa.gov.au/download"`, admissible because
    `ProjectConfig` sets `extra="allow"`) survives it untouched, and the
    terminal echo would otherwise be strictly weaker than the record written
    to disk from the identical mapping.

    The failure path is `_load_config_or_exit`, shared with every fetch
    command in this module -- see that helper for what each of the
    validation-error and malformed-YAML branches redacts and why.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    scrubbed = scrub_string_leaves(redact_secrets(resolved.model_dump(mode="json")))
    typer.echo(json.dumps(scrubbed, indent=2, sort_keys=True))


@app.command("fetch-tenements")
def fetch_tenements(config: Path = ConfigOption, date: str = DateOption) -> None:
    """Fetch a dated DMIRS-003 Mining Tenements snapshot (CC-BY-4.0).

    Creates `<data_root>/raw/dmirs_003_tenements/<date>/`, downloads the
    pinned DASC bundle zip (`DASC_TENEMENTS_SHP_URL`, DASC file id 2056; see
    `sources/tenements.py`'s module docstring for the D6 ruling that
    replaced the auth-gated SLIP GeoPackage route), writes `metadata.txt`
    (DASC product identity, endpoint, licence and the Data WA record URL,
    purpose), validates the download (`validate_tenements_zip`) BEFORE
    anything is finalized -- an empty, unreadable or member-incomplete
    download must never be checksummed and manifested as though it were a
    good snapshot -- then finalizes `SHA256SUMS.txt` and writes an immutable
    run manifest alongside it. The manifest's `output` is the finalized
    `SHA256SUMS.txt` itself (it enumerates every file the snapshot holds);
    its one `inputs` entry carries the pinned endpoint and this source's
    licence fields from `licence.SOURCES`.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    source = licence.SOURCES["dmirs_003_tenements"]
    endpoint = DASC_TENEMENTS_SHP_URL

    snapshot_dir = snapshots.create_snapshot_dir(
        resolved.run.data_root, "dmirs_003_tenements", date
    )
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    _refuse_if_snapshot_already_finalized(snapshot_dir, config=resolved_config, git_state=git_state)

    zip_path = snapshot_dir / TENEMENTS_ZIP_FILENAME
    try:
        download_tenements_zip(endpoint, zip_path)
    except Exception as exc:  # noqa: BLE001 -- surfaced as a structured refusal, not a traceback
        typer.echo(
            json.dumps(
                {
                    "refusal": f"Tenements DASC bundle download failed: {exc}",
                    "stage": "download",
                    "url": endpoint,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source=f"{source.title} (DASC file id 2056)",
        endpoint=endpoint,
        licence_note=(
            f"{source.licence_id} -- in-bundle {LICENCE_PDF_FILENAME}; "
            f"Data WA record: {source.source_url}"
        ),
        purpose=(
            "DMIRS-003 Mining Tenements DASC bundle snapshot for the WA mine "
            "rehabilitation spectral monitor's monitoring frame."
        ),
    )

    try:
        summary = validate_tenements_zip(zip_path)
    except SnapshotValidationError as exc:
        typer.echo(
            json.dumps(
                {"validation_error": str(exc), "stage": "validation"}, indent=2, sort_keys=True
            )
        )
        raise typer.Exit(1) from None

    sums_path = snapshots.finalize_snapshot(snapshot_dir)
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)

    input_asset = SourceAsset(
        uri=endpoint,
        sha256=sha256_file(zip_path),
        collection=None,
        snapshot_date=dt_date.fromisoformat(date),
        licence=source.licence_id,
        redistribute_public=source.redistribute_public,
    )
    manifests.write_run_manifest(
        output=sums_path,
        inputs=[input_asset],
        config=resolved_config,
        git_state=git_state,
        resolved_args={"date": date, "validation_summary": summary},
    )

    typer.echo(
        json.dumps(
            {
                "snapshot_dir": str(snapshot_dir),
                "verify": {"ok": n_ok, "bad": n_bad, "missing": n_missing},
                "validation_summary": summary,
                "manifest_path": str(sums_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("fetch-minedex")
def fetch_minedex(config: Path = ConfigOption, date: str = DateOption) -> None:
    """Fetch a dated DMIRS-001 MINEDEX snapshot AND capture (never adjudicate) licence evidence.

    Creates `<data_root>/raw/dmirs_001_minedex/<date>/`, downloads BOTH
    pinned DASC bundles -- the SHP zip (`DASC_MINEDEX_SHP_URL`, id 3978) and
    the CSV zip (`DASC_MINEDEX_CSV_URL`, id 3981); see `sources/minedex.py`'s
    module docstring for the D6 ruling that replaced the auth-gated SLIP
    GeoPackage route -- then makes a BEST-EFFORT fetch of the Data WA CKAN
    `package_show` metadata record (`DATAWA_PACKAGE_SHOW_URL`) -- on ANY
    exception, the metadata text is treated as `None` and the run continues
    rather than aborting, because MINEDEX is fail-closed either way (see
    `licence.py`'s module docstring): a failed metadata fetch cannot make
    the result any less blocked than a successful one.

    `capture_licence_evidence` then extracts the in-bundle `Licence_CCBY4.pdf`
    byte-identically, writes `datawa_package_show.json` (when fetched) and
    `licence_evidence.json` with `explicit_grant`/`contrary_notice` both
    `null` and `adjudicated: false` -- this command NEVER adjudicates the
    conflict itself; the separate `adjudicate-minedex-licence` command
    applies the D7 ruling later, against this already-finalized snapshot.
    `metadata.txt` quotes `licence.SOURCES["dmirs_001_minedex"].notes`
    verbatim (the CONFLICT note) plus both bundles' DASC product identity.

    Both bundles are validated TOGETHER (`validate_minedex_bundles`) BEFORE
    anything is finalized, per D6's atomicity requirement -- a download,
    capture or validation failure in EITHER bundle leaves the WHOLE snapshot
    unfinalized (no `SHA256SUMS.txt`), each stage's refusal a structured
    JSON payload naming which stage failed rather than an uncaught
    traceback. Once every stage succeeds, `SHA256SUMS.txt` is finalized ONCE
    (covering both zips, the metadata, the evidence JSON and its captured
    artefacts together) and an immutable run manifest is written alongside
    it. The manifest's two `inputs` entries (one per bundle) carry
    `redistribute_public=False` and the CONFLICT licence identifier from
    `licence.SOURCES`, unconditionally -- captured evidence never flips this
    command's own manifest, only a later `adjudicate-minedex-licence` run
    and `licence.minedex_redistribution_allowed` at an export boundary.

    That manifest's `output.sha256` pins `SHA256SUMS.txt` as this fetch left
    it, and a later `adjudicate-minedex-licence` run legitimately rewrites
    one of that file's lines -- so on an adjudicated snapshot the digest
    recorded here no longer matches the live file. That is disclosed, not
    silent: the adjudication writes its own manifest beside
    `licence_evidence.json` carrying the before/after digests of both files
    it changed and a `supersedes_manifest` pointer back to this one. This
    manifest is never rewritten (manifests are immutable provenance
    records); the pair is what reconciles.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    source = licence.SOURCES["dmirs_001_minedex"]
    shp_url = DASC_MINEDEX_SHP_URL
    csv_url = DASC_MINEDEX_CSV_URL
    datawa_url = DATAWA_PACKAGE_SHOW_URL

    snapshot_dir = snapshots.create_snapshot_dir(resolved.run.data_root, "dmirs_001_minedex", date)
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    _refuse_if_snapshot_already_finalized(snapshot_dir, config=resolved_config, git_state=git_state)

    shp_zip_path = snapshot_dir / MINEDEX_SHP_ZIP_FILENAME
    csv_zip_path = snapshot_dir / MINEDEX_CSV_ZIP_FILENAME

    try:
        download_minedex_zip(shp_url, shp_zip_path)
    except Exception as exc:  # noqa: BLE001 -- surfaced as a structured refusal, not a traceback
        typer.echo(
            json.dumps(
                {
                    "refusal": f"MINEDEX SHP bundle download failed: {exc}",
                    "stage": "download_shp",
                    "url": shp_url,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    try:
        download_minedex_zip(csv_url, csv_zip_path)
    except Exception as exc:  # noqa: BLE001 -- surfaced as a structured refusal, not a traceback
        typer.echo(
            json.dumps(
                {
                    "refusal": f"MINEDEX CSV bundle download failed: {exc}",
                    "stage": "download_csv",
                    "url": csv_url,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    try:
        datawa_json_text: str | None = fetch_datawa_package_show(datawa_url)
    except Exception:  # noqa: BLE001 -- best-effort by design, see docstring above
        datawa_json_text = None

    try:
        capture_licence_evidence(snapshot_dir, csv_zip_path, datawa_json_text, captured=date)
    except LicenceEvidenceCaptureError as exc:
        typer.echo(
            json.dumps(
                {"refusal": str(exc), "stage": "capture_licence_evidence"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source=f"{source.title} (DASC SHP file id 3978, CSV file id 3981)",
        endpoint=f"{shp_url} ; {csv_url}",
        licence_note=f"{source.licence_id} -- {source.notes}",
        purpose=(
            "DMIRS-001 MINEDEX DASC bundle snapshot for the WA mine rehabilitation "
            "spectral monitor's monitoring frame; licence evidence captured, NOT "
            "adjudicated -- see licence_evidence.json and the "
            "adjudicate-minedex-licence command."
        ),
    )

    try:
        summary = validate_minedex_bundles(shp_zip_path, csv_zip_path)
    except MinedexSnapshotValidationError as exc:
        typer.echo(
            json.dumps(
                {"validation_error": str(exc), "stage": "validation"}, indent=2, sort_keys=True
            )
        )
        raise typer.Exit(1) from None

    sums_path = snapshots.finalize_snapshot(snapshot_dir)
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)

    input_assets = [
        SourceAsset(
            uri=shp_url,
            sha256=sha256_file(shp_zip_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(date),
            licence=source.licence_id,
            redistribute_public=source.redistribute_public,
        ),
        SourceAsset(
            uri=csv_url,
            sha256=sha256_file(csv_zip_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(date),
            licence=source.licence_id,
            redistribute_public=source.redistribute_public,
        ),
    ]
    manifests.write_run_manifest(
        output=sums_path,
        inputs=input_assets,
        config=resolved_config,
        git_state=git_state,
        resolved_args={"date": date, "validation_summary": summary},
    )

    typer.echo(
        json.dumps(
            {
                "snapshot_dir": str(snapshot_dir),
                "verify": {"ok": n_ok, "bad": n_bad, "missing": n_missing},
                "validation_summary": summary,
                "datawa_fetch_succeeded": datawa_json_text is not None,
                "manifest_path": str(sums_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("adjudicate-minedex-licence")
def adjudicate_minedex_licence(
    config: Path = ConfigOption, date: str | None = OptionalDateOption
) -> None:
    """Apply the D7 MINEDEX licence ruling to an already-FINALIZED snapshot.

    D7 (`docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md`)
    keeps MINEDEX public redistribution closed: the bundled `Licence_CCBY4.
    pdf` is explicit evidence of a CC-BY-4.0 grant, but `contrary_notice:
    false` cannot be recorded while Data WA's own catalogue record labels
    the same dataset CC-BY-NC-4.0. This command is the ONE place that
    ruling is applied -- `fetch-minedex` and `capture_licence_evidence`
    never adjudicate, only capture.

    `--date` defaults to the latest MINEDEX snapshot (`register.
    latest_snapshot`); pass it explicitly to adjudicate an older one.

    Refuses (structured JSON, exit 1), naming the reason and never mutating
    `licence_evidence.json`, when:

    - no dated MINEDEX snapshot is found (only when `--date` is omitted);
    - the target snapshot has no `SHA256SUMS.txt` (never finalized --
      `fetch-minedex` must complete successfully first);
    - `licence_evidence.json` is missing or does not parse as a JSON object;
    - it is already adjudicated (`adjudicated: true`) -- idempotence,
      naming the existing adjudication record so a re-run cannot silently
      no-op over a DIFFERENT decision;
    - its `evidence_files` is missing either required name
      (`Licence_CCBY4.pdf` or `datawa_package_show.json` -- D7 requires
      BOTH the byte-identical PDF and a captured Data WA metadata record);
    - any evidence file is not hashed, with a matching digest, in
      `SHA256SUMS.txt` (`licence.minedex_evidence_is_hashed`) -- covers both
      a snapshot never finalized with the evidence present and evidence
      tampered with since finalize.

    On success, rewrites `licence_evidence.json` in place with
    `explicit_grant: "CC-BY-4.0"`, `contrary_notice: true`,
    `adjudicated: true`, `decision: "licence conflict; redistribution
    closed"`, `ruling_reference` naming the decisions doc path, and
    `evidence_json_sha256_before` recording the digest `finalize_snapshot`
    originally captured for this file -- preserving
    `resource`/`captured`/`evidence_files` unchanged. Then re-signs this
    ONE file's line in the snapshot's `SHA256SUMS.txt`
    (`snapshots.update_snapshot_entry`) so the edit is visible to the
    integrity gate rather than making every later `verify_snapshot` call
    against this snapshot report it as tampered: this is the declared,
    narrow exception to post-finalize immutability, applied only to this
    one file and recorded, not bypassed. Echoes a JSON summary including
    the POST-adjudication state of
    `licence.minedex_redistribution_allowed(snapshot_dir)`, which stays
    `False`: `contrary_notice: true` is exactly what keeps that gate closed,
    so adjudicating the conflict is not the same act as resolving it.

    Then writes its OWN run manifest beside the evidence JSON
    (`licence_evidence.json.run_manifest.json`), because this command is the
    one place that deliberately mutates a FINALIZED snapshot and
    `update_snapshot_entry` rewrites `SHA256SUMS.txt` -- the very artefact
    `fetch-minedex` pinned under its manifest's `output.sha256`. Without a
    record of its own, a successful adjudication leaves the fetch manifest
    asserting a digest the live `SHA256SUMS.txt` no longer has, and anyone
    verifying the snapshot against its own manifest reads a correctly
    adjudicated snapshot as tampering. So `resolved_args` carries the
    before/after sha256 of BOTH files this command changes
    (`evidence_json_sha256_before`/`_after`,
    `sha256sums_sha256_before`/`_after`, plus
    `evidence_json_sha256_recorded_at_finalize`, the digest
    `finalize_snapshot` captured, which the evidence JSON itself also
    records), a root-relative `supersedes_manifest` pointer to
    `SHA256SUMS.txt.run_manifest.json` with the `supersedes_manifest_
    output_sha256` this run retires -- read off that manifest, `null` with
    `supersedes_manifest_read_error` naming the reason if it cannot be read,
    never silently assumed to agree -- and the ruling reference, decision and
    post-adjudication gate value. The manifest's `timestamp` is what dates
    the adjudication; the evidence JSON carries no clock of its own. Its
    `inputs` are the evidence artefacts named by `evidence_files`, each with
    the digest `SHA256SUMS.txt` records for it, `redistribute_public=False`
    unconditionally. This manifest, like `fetch-minedex`'s own, is written
    AFTER finalize and so is not itself listed in `SHA256SUMS.txt`;
    `verify_snapshot` counts only what that file names, so it is unaffected.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    source = licence.SOURCES["dmirs_001_minedex"]
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)

    if date is None:
        try:
            snapshot_dir = register.latest_snapshot(resolved.run.data_root, "dmirs_001_minedex")
        except register.NoSnapshotFoundError as exc:
            typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
            raise typer.Exit(1) from None
    else:
        snapshot_dir = Path(resolved.run.data_root) / "raw" / "dmirs_001_minedex" / date

    sums_path = snapshot_dir / snapshots.SHA256SUMS_FILENAME
    if not sums_path.exists():
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{snapshot_dir} was never finalized (no "
                        f"{snapshots.SHA256SUMS_FILENAME}) -- fetch-minedex must "
                        "complete successfully before adjudication"
                    ),
                    "snapshot_dir": str(snapshot_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    evidence_path = snapshot_dir / licence.EVIDENCE_FILENAME
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(
            json.dumps(
                {
                    "refusal": f"{evidence_path} is missing or unparseable: {exc}",
                    "snapshot_dir": str(snapshot_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    if not isinstance(payload, dict):
        typer.echo(
            json.dumps(
                {
                    "refusal": f"{evidence_path} does not parse as a JSON object",
                    "snapshot_dir": str(snapshot_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    if payload.get("adjudicated") is True:
        typer.echo(
            json.dumps(
                {
                    "refusal": "already adjudicated",
                    "existing_adjudication": payload,
                    "snapshot_dir": str(snapshot_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    raw_evidence_files = payload.get("evidence_files")
    # A non-list (missing, or some other JSON shape) narrows to the empty
    # list, so BOTH required names report missing and the refusal below
    # fires -- the same verdict the previous two-branch form reached, with
    # the names bound to a list type the manifest's `inputs` can iterate.
    # The refusal still echoes the RAW value, so a malformed
    # `evidence_files` is visible as what it actually was.
    evidence_files: list[str] = (
        [str(name) for name in raw_evidence_files] if isinstance(raw_evidence_files, list) else []
    )
    required_evidence_names = {LICENCE_PDF_FILENAME, DATAWA_METADATA_FILENAME}
    missing_required_names = sorted(required_evidence_names - set(evidence_files))
    if missing_required_names:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"evidence_files is missing required name(s) "
                        f"{missing_required_names} -- D7 requires both the licence PDF "
                        "and a captured Data WA metadata record before adjudication"
                    ),
                    "evidence_files": raw_evidence_files,
                    "snapshot_dir": str(snapshot_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    if not licence.minedex_evidence_is_hashed(snapshot_dir):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"evidence files are not all hashed, OK, in "
                        f"{snapshots.SHA256SUMS_FILENAME} -- the snapshot may never "
                        "have been finalized with this evidence present, or the "
                        "evidence has been tampered with since finalize"
                    ),
                    "snapshot_dir": str(snapshot_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    # `minedex_evidence_is_hashed` above confirmed the evidence JSON is both
    # listed in SHA256SUMS.txt and byte-identical to its recorded digest --
    # i.e. unadjudicated and untampered since finalize. Record the digest
    # finalize_snapshot captured, THEN rewrite the file, THEN re-sign its one
    # SHA256SUMS.txt line to match -- `update_snapshot_entry` is the declared
    # exception to post-finalize immutability, and skipping it here would
    # make both `snapshots.verify_snapshot` and the evidence-digest half of
    # `minedex_evidence_is_hashed` report this file as tampered on every
    # later run that reads this snapshot.
    evidence_json_sha256_before = snapshots.snapshot_entries(snapshot_dir)[
        licence.EVIDENCE_FILENAME
    ]

    updated_payload = dict(payload)
    updated_payload["explicit_grant"] = "CC-BY-4.0"
    updated_payload["contrary_notice"] = True
    updated_payload["adjudicated"] = True
    updated_payload["decision"] = "licence conflict; redistribution closed"
    updated_payload["ruling_reference"] = (
        "docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md"
    )
    updated_payload["evidence_json_sha256_before"] = evidence_json_sha256_before

    # Both files this command changes are digested on BOTH sides of the
    # change. `SHA256SUMS.txt` in particular is the output artefact
    # `fetch-minedex`'s own run manifest pins under `output.sha256`, so
    # rewriting one line of it retires that recorded digest; capturing the
    # before value here is what lets this command's manifest EXPLAIN the
    # mismatch a later reader will find, instead of leaving it to read as
    # tampering.
    sums_sha256_before = sha256_file(sums_path)
    evidence_json_sha256_on_disk_before = sha256_file(evidence_path)

    evidence_path.write_text(
        json.dumps(updated_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    snapshots.update_snapshot_entry(snapshot_dir, licence.EVIDENCE_FILENAME)

    evidence_json_sha256_after = sha256_file(evidence_path)
    sums_sha256_after = sha256_file(sums_path)

    fetch_manifest_path = Path(str(sums_path) + manifests.MANIFEST_SUFFIX)
    superseded_manifest, superseded_manifest_root = manifests.root_relative_path(
        fetch_manifest_path, config=resolved_config
    )
    # The exact value this run retires, read off the superseded manifest
    # itself rather than assumed to equal `sums_sha256_before`: a manifest
    # that is missing or unreadable is recorded as `null` (with the reason
    # named), never silently reported as agreeing.
    superseded_output_sha256: str | None = None
    superseded_manifest_read_error: str | None = None
    try:
        superseded_output_sha256 = json.loads(fetch_manifest_path.read_text(encoding="utf-8"))[
            "output"
        ]["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        superseded_manifest_read_error = f"{type(exc).__name__}: {exc}"

    gate_after = licence.minedex_redistribution_allowed(snapshot_dir)

    try:
        snapshot_date = dt_date.fromisoformat(snapshot_dir.name)
    except ValueError:
        snapshot_date = None

    evidence_digests = snapshots.snapshot_entries(snapshot_dir)
    adjudication_manifest = manifests.write_run_manifest(
        output=evidence_path,
        inputs=[
            SourceAsset(
                uri=str(snapshot_dir / name),
                sha256=evidence_digests.get(name),
                collection=None,
                snapshot_date=snapshot_date,
                licence=source.licence_id,
                redistribute_public=source.redistribute_public,
            )
            for name in evidence_files
        ],
        config=resolved_config,
        git_state=git_state,
        resolved_args={
            "date": date,
            "snapshot_date": snapshot_dir.name,
            "ruling_reference": updated_payload["ruling_reference"],
            "decision": updated_payload["decision"],
            "minedex_redistribution_allowed": gate_after,
            "evidence_json_sha256_recorded_at_finalize": evidence_json_sha256_before,
            "evidence_json_sha256_before": evidence_json_sha256_on_disk_before,
            "evidence_json_sha256_after": evidence_json_sha256_after,
            "sha256sums_sha256_before": sums_sha256_before,
            "sha256sums_sha256_after": sums_sha256_after,
            "supersedes_manifest": superseded_manifest,
            "supersedes_manifest_root": superseded_manifest_root,
            "supersedes_manifest_output_sha256": superseded_output_sha256,
            "supersedes_manifest_read_error": superseded_manifest_read_error,
        },
    )

    typer.echo(
        json.dumps(
            {
                "snapshot_dir": str(snapshot_dir),
                "licence_evidence": updated_payload,
                "minedex_redistribution_allowed": gate_after,
                "manifest_path": str(evidence_path) + manifests.MANIFEST_SUFFIX,
                "supersedes_manifest": superseded_manifest,
                "sha256sums_sha256_before": sums_sha256_before,
                "sha256sums_sha256_after": sums_sha256_after,
                "manifest_timestamp": adjudication_manifest["timestamp"],
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("fetch-maus-extract")
def fetch_maus_extract(
    config: Path = ConfigOption,
    date: str = DateOption,
    source_gpkg: Path = SourceGpkgOption,
) -> None:
    """Fetch a dated Maus et al. v2 WA extract (CC-BY-SA-4.0) from a LOCAL source GeoPackage.

    Never downloads: `--source-gpkg` points at the global v2 GeoPackage the
    jarrah data root already holds
    (`~/data/jarrah-rehab/raw/maus-v2/2026-07-20/`); the exact path is
    always caller-supplied, never hardcoded here. Reads it
    (`read_source_gpkg`) and clips to `WA_BBOX`, adding a deterministic
    `maus_id` per feature (`clip_to_wa`) -- per the design's D1 decision,
    Maus v2 polygons are the sole Tier 1 measurement footprint. Writes
    `wa_extract.gpkg` into `<data_root>/raw/maus_v2/<date>/`, then
    `metadata.txt` quoting the CC-BY-SA-4.0 licence, this source's
    attribution text, and the modification statement "clipped to the WA
    bounding box from the global v2 dataset" -- the CC-BY-SA grant
    conditions reuse on stating that a modification was made, so this is
    not optional prose. Validates the written extract
    (`validate_maus_extract`) BEFORE anything is finalized -- a
    zero-feature extract (no source feature fell inside `WA_BBOX`) must
    never be checksummed and manifested as though it were a good snapshot
    -- then finalizes `SHA256SUMS.txt` and writes an immutable run
    manifest.

    Provenance records BOTH the PANGAEA DOI and the local source path with
    its own sha256, per the task brief. The manifest's one `inputs` entry
    is the LOCAL source GeoPackage actually consumed this run
    (`uri=str(source_gpkg)`, `sha256=sha256_file(source_gpkg)`,
    `licence="CC-BY-SA-4.0"`, `redistribute_public=True`, root-relativised by
    `write_run_manifest` itself under `inputs[0].uri`/`uri_root`);
    `resolved_args` separately carries the DOI (`source_doi`,
    `licence.SOURCES["maus_v2"].source_url`) and the local path AGAIN --
    `inputs[0].uri` alone is not enough, because a caller reading
    `resolved_args` should not have to cross-reference `inputs` to learn
    which local file this run actually read. This second copy is reduced the
    same way, via `manifests.root_relative_path` (`source_local_path`,
    `source_local_path_root` naming which root -- `"data_root"` or
    `"unrooted"`, the latter because the source gpkg this project reads from
    is deliberately kept OUTSIDE the project's own data root): every
    filesystem path this module records is root-relative, per
    `manifests.py`'s own module docstring, never the absolute account path.
    This command never passes an explicit `argv` to `write_run_manifest`, so
    the manifest's own `argv`/`argv_paths_root` fall back to the REAL
    `sys.argv` -- which, on a live invocation, carries this same
    `--source-gpkg` path a fourth time. `write_run_manifest` reduces every
    argv token that resolves to an existing local path (not just `argv[0]`)
    before it is written, so that fourth copy is root-relative too; see
    `manifests._root_relative_argv_token`.
    `metadata.txt`'s purpose field records the source file's basename and
    sha256, for the same reason. `resolved_args` also records the OUTPUT
    extract's own licence terms
    (`output_licence`, `output_redistribute_public`,
    `output_share_alike: True`, `output_share_alike_note` quoting this
    source's registry notes verbatim) -- per the design's §6/§8 D1, the
    Maus-derived package publishes SEPARATELY under CC-BY-SA-4.0 with
    attribution, source link and modification statement, ShareAlike
    applied conservatively to the whole package with no scalar-field
    carve-outs asserted. `SourceAsset` (`provenance.py`) carries no
    `share_alike` field of its own, so this is recorded in `resolved_args`
    rather than by extending that shared model for one source.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    source = licence.SOURCES["maus_v2"]

    snapshot_dir = snapshots.create_snapshot_dir(resolved.run.data_root, "maus_v2", date)
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    _refuse_if_snapshot_already_finalized(snapshot_dir, config=resolved_config, git_state=git_state)

    source_gdf = read_source_gpkg(source_gpkg)
    wa_gdf = clip_to_wa(source_gdf)

    extract_path = snapshot_dir / "wa_extract.gpkg"
    wa_gdf.to_file(extract_path, driver="GPKG", layer="wa_extract")

    source_sha256 = sha256_file(source_gpkg)
    source_local_path, source_local_path_root = manifests.root_relative_path(
        source_gpkg, config=resolved_config
    )

    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source=source.title,
        endpoint=source.source_url,
        licence_note=f"{source.licence_id} -- {source.attribution_text}",
        purpose=(
            "Maus et al. v2 WA extract for the WA mine rehabilitation "
            "spectral monitor's Tier 1 measurement footprint (design D1); "
            "clipped to the WA bounding box from the global v2 dataset. "
            f"Local source GeoPackage: {Path(source_gpkg).name} (sha256 {source_sha256})."
        ),
    )

    try:
        summary = validate_maus_extract(extract_path)
    except MausSnapshotValidationError as exc:
        typer.echo(json.dumps({"validation_error": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    sums_path = snapshots.finalize_snapshot(snapshot_dir)
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)

    input_asset = SourceAsset(
        uri=str(source_gpkg),
        sha256=source_sha256,
        collection=None,
        snapshot_date=dt_date.fromisoformat(date),
        licence=source.licence_id,
        redistribute_public=source.redistribute_public,
    )
    manifests.write_run_manifest(
        output=sums_path,
        inputs=[input_asset],
        config=resolved_config,
        git_state=git_state,
        resolved_args={
            "date": date,
            "validation_summary": summary,
            "source_doi": source.source_url,
            "source_local_path": source_local_path,
            "source_local_path_root": source_local_path_root,
            "output_licence": source.licence_id,
            "output_redistribute_public": source.redistribute_public,
            "output_share_alike": True,
            "output_share_alike_note": source.notes,
        },
    )

    typer.echo(
        json.dumps(
            {
                "snapshot_dir": str(snapshot_dir),
                "verify": {"ok": n_ok, "bad": n_bad, "missing": n_missing},
                "validation_summary": summary,
                "manifest_path": str(sums_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("fetch-silo")
def fetch_silo_cmd(
    config: Path = ConfigOption,
    date: str = DateOption,
    start_year: int = typer.Option(1987, "--start-year", help="First year to fetch (inclusive)."),
    end_year: int = typer.Option(
        ..., "--end-year", help="Last year to fetch (inclusive). Required, never defaulted."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the planned objects and destination, then exit without network or disk I/O.",
    ),
) -> None:
    """Fetch a dated SILO gridded daily-rainfall snapshot (CC BY 4.0).

    Downloads one `daily_rain` annual NetCDF object per year in
    `[start_year, end_year]` from the anonymous AWS open-data bucket
    (`s3://silo-open-data`, no credential -- see `sources/silo.py`'s module
    docstring), into `<data_root>/raw/silo/<date>/`.

    `--end-year` is REQUIRED and never defaults to the current year: the
    house rule against `date.today()` in artefact-shaping arguments applies
    to a rolling upper bound just as much as to `snapshot_date` itself --
    two runs of "the same" command on different days would otherwise fetch
    a different set of years, which is exactly the kind of silent drift
    `_validate_snapshot_date`'s docstring warns against. The caller states
    the range explicitly, every time.

    Each annual object is ~410 MB, so this command is deliberately careful
    on a metered connection: `--dry-run` discloses the full object list and
    destination with zero network and zero disk writes (before even
    `create_snapshot_dir`); an already-valid file at the destination is
    validated and SKIPPED rather than re-pulled (a resume, not a retry);
    and, per file, a fresh download lands at a sibling `.part` path and is
    renamed onto the real name only after `validate_daily_rain_file`
    passes, so a truncated transfer never sits at the real filename for a
    later run to (wrongly) treat as already-fetched.

    `_SILO_REQUIRE_STATEWIDE` gates whether validation asserts WA coverage
    -- see that module-level constant's own docstring.

    Because `finalize_snapshot` hashes EVERY file under the snapshot
    directory regardless of whether this run's loop wrote or looked at it
    (`snapshots.py:182-186`), this command also refuses if the directory
    holds anything outside the exact expected set (`metadata.txt` plus one
    named object per requested year) BEFORE finalizing -- a stray `.part`
    from an earlier interrupted download, or a file left by a previous run
    over a different year range, would otherwise be swept into
    `SHA256SUMS.txt` and verify clean: a finalized snapshot silently
    carrying a truncated or unrelated file.

    The manifest carries one `SourceAsset` per file this run's loop
    resolved (fetched or resumed), each with the bucket URL as `uri` and
    this source's CC-BY-4.0 licence fields from `licence.SOURCES["silo"]`.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")

    if start_year > end_year:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"--start-year {start_year} is after --end-year {end_year} -- "
                        "refusing an inverted year range"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    if start_year < 1889:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"--start-year {start_year} is before 1889 -- SILO rainfall "
                        "grids begin in 1889"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    years = list(range(start_year, end_year + 1))
    urls = [annual_object_url("daily_rain", year) for year in years]

    if dry_run:
        typer.echo(
            json.dumps(
                {
                    "dry_run": True,
                    "objects": urls,
                    "destination": str(resolved.run.data_root / "raw" / "silo" / date),
                    "note": "each annual object is ~410 MB; run off a metered connection",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    source = licence.SOURCES["silo"]
    snapshot_dir = snapshots.create_snapshot_dir(resolved.run.data_root, "silo", date)
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    _refuse_if_snapshot_already_finalized(snapshot_dir, config=resolved_config, git_state=git_state)

    expected_names = {"metadata.txt"} | {annual_object_name("daily_rain", year) for year in years}
    _refuse_if_unexpected_files(
        snapshot_dir, expected_names=expected_names, closing_clause="before fetching"
    )

    n_fetched = 0
    n_resumed = 0
    assets: list[SourceAsset] = []
    for year, url in zip(years, urls, strict=True):
        dest = snapshot_dir / annual_object_name("daily_rain", year)
        if dest.exists():
            try:
                validate_daily_rain_file(dest, year=year, require_statewide=_SILO_REQUIRE_STATEWIDE)
            except SiloError as exc:
                typer.echo(
                    json.dumps(
                        {
                            "refusal": (
                                f"{dest.name} already exists but failed validation: {exc} -- "
                                "delete the partial file and re-run"
                            ),
                            "stage": "validation",
                            "file": dest.name,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                raise typer.Exit(1) from None
            n_resumed += 1
        else:
            part_path = dest.with_suffix(dest.suffix + ".part")
            try:
                download_annual_file(url, part_path)
            except Exception as exc:  # noqa: BLE001 -- surfaced as a structured refusal
                typer.echo(
                    json.dumps(
                        {
                            "refusal": f"SILO annual object download failed: {exc}",
                            "stage": "download",
                            "url": url,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                raise typer.Exit(1) from None
            part_path.replace(dest)
            try:
                validate_daily_rain_file(dest, year=year, require_statewide=_SILO_REQUIRE_STATEWIDE)
            except SiloError as exc:
                typer.echo(
                    json.dumps(
                        {
                            "refusal": f"{dest.name} failed validation: {exc}",
                            "stage": "validation",
                            "file": dest.name,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                raise typer.Exit(1) from None
            n_fetched += 1
        assets.append(
            SourceAsset(
                uri=url,
                sha256=sha256_file(dest),
                collection=None,
                snapshot_date=dt_date.fromisoformat(date),
                licence=source.licence_id,
                redistribute_public=source.redistribute_public,
            )
        )

    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source=f"{source.title} (gridded daily_rain, annual NetCDF)",
        endpoint=annual_object_url("daily_rain", start_year),
        licence_note=f"{source.licence_id} -- {source.licence_url}",
        purpose="SILO gridded daily rainfall for Batch F climate context.",
    )

    expected_names = {"metadata.txt"} | {annual_object_name("daily_rain", year) for year in years}
    _refuse_if_unexpected_files(
        snapshot_dir, expected_names=expected_names, closing_clause="before finalizing"
    )

    sums_path = snapshots.finalize_snapshot(snapshot_dir)
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)

    manifests.write_run_manifest(
        output=sums_path,
        inputs=assets,
        config=resolved_config,
        git_state=git_state,
        resolved_args={
            "date": date,
            "start_year": start_year,
            "end_year": end_year,
            "variable": "daily_rain",
            "fetched": n_fetched,
            "resumed": n_resumed,
        },
    )

    typer.echo(
        json.dumps(
            {
                "snapshot_dir": str(snapshot_dir),
                "fetched": n_fetched,
                "resumed": n_resumed,
                "verify": {"ok": n_ok, "bad": n_bad, "missing": n_missing},
                "manifest_path": str(sums_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("fetch-dbca-fire")
def fetch_dbca_fire_cmd(
    config: Path = ConfigOption,
    date: str = DateOption,
    mode: str = typer.Option(
        "authoritative", "--mode", help="authoritative|mirror -- mirror is declined."
    ),
    source_dir: Path = DbcaSourceDirOption,
) -> None:
    """Stage a dated DBCA-060 fire-history snapshot from an authoritative,
    already-downloaded Data WA package directory (CC BY 4.0).

    `--mode` accepts only `authoritative`: the ArcGIS mirror route stays
    declined (`docs/decisions/2026-08-29-dbca-mirror-declined.md`) and
    `mirror` refuses fail-closed, before any I/O, naming that record.

    `--source-dir` must already hold exactly one `*.gpkg`, a
    `SHA256SUMS.txt`, and a `metadata.txt` -- this command never downloads
    anything itself, it only stages and validates a package a human already
    fetched from Data WA. Every zip entry named in the source
    `SHA256SUMS.txt` that exists in `--source-dir` is digest-verified
    before staging; the GeoPackage itself is not covered by those sums (they
    cover only the zips), so its digest is computed fresh here and recorded
    in the run manifest.

    Staging copies the GeoPackage plus the source `SHA256SUMS.txt` and
    `metadata.txt` (renamed `source-SHA256SUMS.txt` / `source-metadata.txt`
    so they cannot collide with this snapshot's own `metadata.txt` /
    `SHA256SUMS.txt`) into `<data_root>/raw/dbca_060_fire/<date>/`, then runs
    `dbca.validate_fire_history_file` before anything is finalized.

    Licence evidence -- the Data WA catalogue page for DBCA-060 -- is
    fetched and written as `catalogue-page.html` before the snapshot is
    finalized; the snapshot is NEVER finalized without it, so a fetch
    failure refuses the run even though staging and validation already
    passed.

    The stray-file gate (`_refuse_if_unexpected_files`) runs both before
    staging and before finalizing, mirroring `fetch-silo`'s discipline. The
    manifest carries two `SourceAsset` inputs: the source GeoPackage (its
    own resolved file:// URI) and the catalogue page (the Data WA URL),
    both under `licence.SOURCES["dbca_060_fire"]`.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")

    if mode != "authoritative":
        if mode == "mirror":
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            "--mode mirror -- the ArcGIS mirror route is declined -- see "
                            "docs/decisions/2026-08-29-dbca-mirror-declined.md"
                        )
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"--mode {mode!r} is not recognised -- valid modes are "
                            "'authoritative' or 'mirror'"
                        )
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        raise typer.Exit(1) from None

    if not source_dir.is_dir():
        typer.echo(
            json.dumps(
                {"refusal": f"--source-dir {source_dir} does not exist or is not a directory"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    gpkgs = sorted(source_dir.glob("*.gpkg"))
    if len(gpkgs) != 1:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"--source-dir {source_dir} must hold exactly one *.gpkg, found "
                        f"{[p.name for p in gpkgs]}"
                    ),
                    "stage": "source_package",
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    source_gpkg = gpkgs[0]

    source_sums_path = source_dir / "SHA256SUMS.txt"
    source_metadata_path = source_dir / "metadata.txt"
    for required in (source_sums_path, source_metadata_path):
        if not required.is_file():
            typer.echo(
                json.dumps(
                    {
                        "refusal": f"--source-dir {source_dir} is missing {required.name}",
                        "stage": "source_package",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1) from None

    n_source_digests_verified = 0
    for line in source_sums_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        expected_digest, _, name = line.partition("  ")
        name = name.strip()
        entry_path = source_dir / name
        if not entry_path.is_file():
            continue
        n_source_digests_verified += 1
        actual_digest = sha256_file(entry_path)
        if actual_digest != expected_digest:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"{name} digest mismatch: SHA256SUMS.txt says {expected_digest}, "
                            f"actual is {actual_digest}"
                        ),
                        "stage": "source_digests",
                        "file": name,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1) from None

    # A sums file none of whose entries name a file that is present verifies
    # nothing -- refusing here keeps the gate fail-closed instead of letting a
    # stripped-down source directory pass as digest-checked.
    if n_source_digests_verified == 0:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"no entry in {source_sums_path} names a file present in "
                        f"--source-dir {source_dir} -- nothing could be digest-verified"
                    ),
                    "stage": "source_digests",
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    source = licence.SOURCES["dbca_060_fire"]
    snapshot_dir = snapshots.create_snapshot_dir(resolved.run.data_root, "dbca_060_fire", date)
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    _refuse_if_snapshot_already_finalized(snapshot_dir, config=resolved_config, git_state=git_state)

    gpkg_name = source_gpkg.name
    evidence_name = "catalogue-page.html"
    expected_names = {
        "metadata.txt",
        gpkg_name,
        evidence_name,
        "source-SHA256SUMS.txt",
        "source-metadata.txt",
    }
    _refuse_if_unexpected_files(
        snapshot_dir, expected_names=expected_names, closing_clause="before staging"
    )

    dest = snapshot_dir / gpkg_name
    if not dest.exists():
        shutil.copy2(source_gpkg, dest)
    shutil.copy2(source_sums_path, snapshot_dir / "source-SHA256SUMS.txt")
    shutil.copy2(source_metadata_path, snapshot_dir / "source-metadata.txt")

    try:
        summary = dbca.validate_fire_history_file(dest, snapshot_year=int(date[:4]))
    except dbca.DbcaError as exc:
        typer.echo(
            json.dumps(
                {"refusal": f"{dest.name} failed validation: {exc}", "stage": "validation"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    try:
        evidence_bytes = _fetch_catalogue_page(source.source_url)
    except Exception as exc:  # noqa: BLE001 -- surfaced as a structured refusal
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"licence-evidence fetch of {source.source_url} failed: {exc} -- "
                        "the snapshot is never finalized without evidence"
                    ),
                    "stage": "licence_evidence",
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    evidence_text = evidence_bytes.decode("utf-8", errors="replace").lower()
    licence_markers = ("cc-by", "cc by", "creative commons attribution")
    if not any(marker in evidence_text for marker in licence_markers):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"licence-evidence capture of {source.source_url} contains none of "
                        f"the expected licence markers {list(licence_markers)} -- a "
                        "maintenance page, consent shell, or unrelated response is not "
                        "evidence, and the snapshot is never finalized without evidence"
                    ),
                    "stage": "licence_evidence",
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    evidence_path = snapshot_dir / evidence_name
    evidence_path.write_bytes(evidence_bytes)

    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source=f"{source.title} ({dbca.LAYER_NAME})",
        endpoint=source.source_url,
        licence_note=f"{source.licence_id} -- catalogue page: {source.source_url}",
        purpose="DBCA-060 recorded-fire-overlap context (Batch F F3).",
    )

    _refuse_if_unexpected_files(
        snapshot_dir, expected_names=expected_names, closing_clause="before finalizing"
    )

    sums_path = snapshots.finalize_snapshot(snapshot_dir)
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)

    assets = [
        SourceAsset(
            uri=source_gpkg.resolve().as_uri(),
            sha256=sha256_file(dest),
            collection=None,
            snapshot_date=dt_date.fromisoformat(date),
            licence=source.licence_id,
            redistribute_public=source.redistribute_public,
        ),
        SourceAsset(
            uri=source.source_url,
            sha256=sha256_file(evidence_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(date),
            licence=source.licence_id,
            redistribute_public=source.redistribute_public,
        ),
    ]

    manifests.write_run_manifest(
        output=sums_path,
        inputs=assets,
        config=resolved_config,
        git_state=git_state,
        resolved_args={
            "date": date,
            "mode": mode,
            "feature_count": summary.feature_count,
            "counts_by_type": summary.counts_by_type,
            "year_min": summary.year_min,
            "year_max": summary.year_max,
        },
    )

    typer.echo(
        json.dumps(
            {
                "snapshot_dir": str(snapshot_dir),
                "feature_count": summary.feature_count,
                "counts_by_type": summary.counts_by_type,
                "verified": {"ok": n_ok, "bad": n_bad, "missing": n_missing},
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


def _csv_zip_member_row_count(zip_path: Path, member_name: str) -> int:
    """The number of DATA rows (excluding the header) `member_name` carries
    inside the zip at `zip_path`, counted via `csv.reader` -- deliberately
    NOT `pandas.read_csv`, the library `register.build_register`'s caller
    already used to read the same member into the frame under test.

    The CSV-bundle analogue of the former GeoPackage-era `_snapshot_feature_
    count` (`pyogrio.read_info` against `gpd.read_file`): a second,
    INDEPENDENT code path to the same number, so a truncated, filtered or
    partially-read pandas frame makes the two disagree and `build_register_
    cmd`'s row-count reconciliation has something to fail against, rather
    than comparing the frame under test against itself
    (`register.build_reconciliation_report`'s own docstring).
    """
    with zipfile.ZipFile(zip_path) as zf, zf.open(member_name) as raw:
        wrapped = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        row_count = sum(1 for _ in csv.reader(wrapped))
    return row_count - 1  # exclude the header row


def _shapefile_zip_feature_count(zip_path: Path, shapefile_basename: str) -> int:
    """The feature count `zip_path`'s `<shapefile_basename>.shp` member
    reports for ITSELF, read via `pyogrio.read_info` through GDAL's
    `/vsizip/` virtual filesystem -- the zip is never extracted to a
    temporary directory, and the shapefile is never loaded into the
    `geopandas.GeoDataFrame` `build_register` is about to be handed, for the
    same "independent of the frame under test" reason `_csv_zip_member_row_
    count` reads `Sites.csv` via `csv.reader` rather than `pandas.read_csv`.

    `read_info` reads layer metadata only -- it does not load geometry -- so
    this costs nothing on a statewide download.
    """
    return int(pyogrio.read_info(f"/vsizip/{zip_path}/{shapefile_basename}.shp")["features"])


@app.command("build-register")
def build_register_cmd(config: Path = ConfigOption, date: str = DateOption) -> None:
    """Build the Tier 0 statewide register from the LATEST MINEDEX and DMIRS-003 Tenements snapshots.

    Rewired for the D6-D8 rulings
    (`docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md`):
    MINEDEX is read from D6's DASC CSV bundle (`Sites.csv`/`ProjectsOwners.csv`
    inside `minedex_gda2020_csv.zip`), never a GeoPackage, and tenements from
    D6's DASC SHP bundle (`CurrentTenements.shp` inside
    `tenements_current_gda2020_shp.zip`, read via the `/vsizip/` GDAL virtual
    filesystem -- the zip is never extracted to a temporary directory).

    Locates the most recent dated snapshot directory for each source
    (`register.latest_snapshot`) under `<data_root>/raw/dmirs_001_minedex/`
    and `<data_root>/raw/dmirs_003_tenements/` -- refusing, naming the
    missing source, when either has no snapshot at all. Each selected
    snapshot is then integrity-verified BEFORE anything is read off it
    (`_verify_snapshot_or_refuse`, `required_files=(MINEDEX_CSV_ZIP_FILENAME,)`/
    `(TENEMENTS_ZIP_FILENAME,)` -- exactly the one file this command reads
    from each snapshot): an unfinalized snapshot (no `SHA256SUMS.txt` -- what
    a validation-refused or interrupted fetch leaves behind) or one whose
    files no longer match their recorded digests refuses the run with the
    three verification counts named, and the successful (ok, bad, missing)
    triple per snapshot is recorded in the run manifest's `resolved_args`.

    `Sites.csv`/`ProjectsOwners.csv` are read with `pandas.read_csv(zf.open(...),
    encoding="utf-8-sig")` -- the measured DASC BOM convention -- and
    `CurrentTenements.shp` with `geopandas.read_file` against the `/vsizip/`
    path. Any of `KeyError` (a named member absent from the zip),
    `zipfile.BadZipFile`, `pyogrio.errors.DataSourceError` or `OSError`
    (a corrupt or unreadable zip/shapefile) refuses as structured JSON,
    naming the offending zip path, before `register.build_register` ever
    runs; `register.build_register`'s own declared `ValueError` guards
    (missing/renamed required columns, an unset tenements CRS, a non-Point
    tenements geometry) are wrapped the same way.

    `register.build_register` then builds the register (one row per
    `Sites.csv` record; point geometry constructed HERE from `Latitude`/
    `Longitude` under `register.MINEDEX_SITES_SOURCE_CRS`;
    `n_tenements_intersecting` via a spatial join against the tenements
    frame; `owners_at_snapshot` via `register.owners_by_project` joined
    through `ProjectCode`, per D8), and this command checks TWO
    reconciliations before writing anything
    (`register.build_reconciliation_report`): the register's own row count
    against `Sites.csv`'s own row count, and `register_counts`'
    per-`inclusion_status` counts against their own total. That row count is
    read back INDEPENDENTLY of the `pandas.read_csv` frame just built
    (`_csv_zip_member_row_count`, `csv.reader`) and the tenements feature
    count independently of the `geopandas.read_file` frame just built
    (`_shapefile_zip_feature_count`, `pyogrio.read_info`) -- never `len()` of
    the frame this command just read, which would compare the frame under
    test against itself and make the check unfailable.
    `reconciliation.md` additionally DISCLOSES `register.owner_join_
    disclosures` (D8's six owner-join counts), how many register rows
    carry no usable location (a `Sites.csv` record whose `Latitude`/
    `Longitude` are null -> `lon`/`lat` null), and `register.
    site_id_duplication_counts` (the register is one row per `Sites.csv`
    record, so `site_id` -- `SiteCode` verbatim -- is no longer guaranteed
    unique; a healthy real extract measurably carries some); none of the
    three ever decides the verdict, and all are echoed on stdout and
    recorded in the run manifest (`resolved_args["owner_join_
    disclosures"]`, `resolved_args["n_sites_null_coordinates"]`,
    `resolved_args["site_id_duplication"]`). A fourth is echoed on stdout
    and recorded in the manifest the same way, but is not (yet) rendered
    into `reconciliation.md`: `register.tenement_count_disclosure`
    (`resolved_args["tenement_count_disclosure"]`, D12.2) -- how many
    register rows had `n_tenements_intersecting` actually computed
    (located sites) versus NOT COMPUTED (coordinate-less sites, `pd.NA`,
    never a fabricated `0`), plus how many of the computed rows are a
    genuine zero; the three counts always reconcile against `sites_total`
    by construction (see that function's docstring). A fifth, `register.
    owner_row_composition` (`resolved_args["owner_row_composition"]`,
    D12.2), is disclosed the same way -- echoed on stdout and recorded in
    the manifest, not rendered into `reconciliation.md` -- and splits the
    `ProjectsOwners.csv` frame this command already has in hand into
    CURRENT (blank `EndDate`) and ENDED (non-blank `EndDate`) row counts,
    reconciling against that frame's own row total by construction; see
    that function's docstring for why this matters (the real 2026-08-14
    extract happens to be current-only, so D8's `owners_at_snapshot`
    'current owner' filter has had no bite, and nothing pinned or disclosed
    that property until now). None of the five ever decides the
    reconciliation verdict. Either reconciliation
    check failing refuses the whole run (structured JSON error, exit 1)
    before any artefact is written -- the same validate-before-finalize
    discipline `fetch-minedex`/`fetch-tenements` apply to a bad download. A
    re-run against a `--date` this command has already built is refused the
    same way, BEFORE `register.parquet`/`register_counts.json`/
    `reconciliation.md` are touched (`_refuse_if_curated_output_already_
    exists`) -- move the existing `curated/register/<date>/` directory
    aside, or choose a different `--date`, to build again.

    `register.build_register` is called with the MINEDEX snapshot
    directory's OWN date (`minedex_snapshot_dir.name`), never the `--date`
    argument -- `snapshot_date` is the register's "owners recorded in the
    MINEDEX snapshot dated `<snapshot_date>`" identity field, so it must
    name the snapshot the rows were actually read from. `--date` only names
    the curated OUTPUT directory (`curated/register/<date>/`) and the run's
    build date; the two are independent and will usually differ (a register
    is commonly built some days after the snapshot it reads).

    On success, writes into `<data_root>/curated/register/<date>/`:
    `register.parquet` (declared `register.REGISTER_SCHEMA`, via
    `tables.write_table`; geometry as `lon`/`lat` float columns ONLY, so it
    passes the ported export gate's geometry-column check),
    `register_counts.json` and `reconciliation.md` (the counts table,
    source feature totals, owner-join disclosures and the PASS/FAIL line --
    always PASS here, since a FAIL refused the write above). An immutable
    run manifest is written alongside `register.parquet`, with two `inputs`
    entries (the MINEDEX CSV zip and tenements SHP zip actually read, each
    carrying that source's own `licence.SOURCES` licence fields) and
    `resolved_args["minedex_public_export_blocked"]` -- computed as `not
    licence.minedex_redistribution_allowed(minedex_snapshot_dir)`, so every
    register artefact's own manifest discloses whether MINEDEX-derived
    public export is currently blocked, rather than leaving a reader to
    re-derive it. This mirrors `config/base.yaml`'s `sources.
    minedex_public_export_blocked` field name, but is computed fresh from
    the actual snapshot's captured evidence each run -- never read off the
    static config default, which could drift from what a given snapshot's
    evidence actually supports. The manifest also carries
    `resolved_args["register_lonlat_crs"]` (`register.REGISTER_LONLAT_CRS`,
    the CRS `register.parquet`'s `lon`/`lat` are always written in) and
    `resolved_args["minedex_source_crs"]` (`register.MINEDEX_SITES_SOURCE_
    CRS` -- the DECLARED CRS `Sites.csv`'s bare `Latitude`/`Longitude`
    floats are constructed under; `Sites.csv` itself carries no CRS
    metadata, so this is recorded as a declared assumption, not something
    read off the file) -- CLAUDE.md's rule that an inferred or assumed CRS
    must be recorded, not left invisible in the artefact.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")

    try:
        minedex_snapshot_dir = register.latest_snapshot(resolved.run.data_root, "dmirs_001_minedex")
        tenements_snapshot_dir = register.latest_snapshot(
            resolved.run.data_root, "dmirs_003_tenements"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    # Integrity gate BEFORE anything is read off either snapshot: a
    # date-selected directory is not yet a verified input. See
    # `_verify_snapshot_or_refuse` for the failure this closes (an
    # unfinalized or tampered snapshot becoming the register's source with
    # exit 0 and a PASS reconciliation).
    minedex_snapshot_verification = _verify_snapshot_or_refuse(
        minedex_snapshot_dir,
        source_id="dmirs_001_minedex",
        required_files=(MINEDEX_CSV_ZIP_FILENAME,),
    )
    tenements_snapshot_verification = _verify_snapshot_or_refuse(
        tenements_snapshot_dir,
        source_id="dmirs_003_tenements",
        required_files=(TENEMENTS_ZIP_FILENAME,),
    )

    minedex_csv_zip_path = minedex_snapshot_dir / MINEDEX_CSV_ZIP_FILENAME
    tenements_zip_path = tenements_snapshot_dir / TENEMENTS_ZIP_FILENAME
    try:
        with zipfile.ZipFile(minedex_csv_zip_path) as zf:
            with zf.open("Sites.csv") as handle:
                # Pinned via `MINEDEX_CODE_COLUMN_DTYPES` -- see that
                # constant's docstring (`sources/minedex.py`) for why
                # `ProjectCode`/`SiteCode`/`OwnerCode` must never be left to
                # per-frame dtype inference: this is the same join key
                # `ProjectsOwners.csv` is read under two lines below, and a
                # numeric ProjectCode that infers differently in the two
                # frames silently fails every owner match.
                sites_df = pd.read_csv(
                    handle, encoding="utf-8-sig", dtype=MINEDEX_CODE_COLUMN_DTYPES
                )
            with zf.open("ProjectsOwners.csv") as handle:
                owners_df = pd.read_csv(
                    handle, encoding="utf-8-sig", dtype=MINEDEX_CODE_COLUMN_DTYPES
                )
        tenements_gdf = gpd.read_file(
            f"/vsizip/{tenements_zip_path}/{TENEMENTS_SHAPEFILE_BASENAME}.shp"
        )
        df = register.build_register(sites_df, owners_df, tenements_gdf, minedex_snapshot_dir.name)
    except (KeyError, zipfile.BadZipFile, pyogrio.errors.DataSourceError, OSError) as exc:
        # `_verify_snapshot_or_refuse`'s `required_files` gate above already
        # refuses a snapshot finalized without `minedex_gda2020_csv.zip`/
        # `tenements_current_gda2020_shp.zip` present, or one where either
        # file no longer matches its recorded digest -- so by the time this
        # line runs, both paths ARE named in `SHA256SUMS.txt` and DO match
        # what was hashed. This still catches a hash-matching zip that is
        # nonetheless unreadable (corrupt at capture time, so the bad bytes
        # themselves were what got hashed), or one missing a named member
        # (`KeyError` from `zf.open`). Rendered as the same structured JSON
        # refusal every other refusal in this module emits, rather than an
        # uncaught traceback with empty stdout.
        typer.echo(
            json.dumps(
                {
                    "refusal": str(exc),
                    "minedex_csv_zip": str(minedex_csv_zip_path),
                    "tenements_zip": str(tenements_zip_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    except (ValueError, AttributeError, TypeError) as exc:
        # `register.build_register`'s declared input guards (missing/renamed
        # required columns, an unset tenements CRS, a non-Point tenements
        # geometry) all raise `ValueError` naming the offence. `AttributeError`/
        # `TypeError` are a defensive backstop, not a declared guard's own
        # error type: `owners_by_project`'s null-`OwnerName` crash
        # (`name.strip()` on a `float("nan")` surfaced by a pandas dtype
        # quirk) is now fixed at its source, but this widened clause means
        # the NEXT source-shape defect this project has not yet measured
        # still reaches the caller as the same structured JSON refusal every
        # other refusal in this module emits, rather than an uncaught
        # traceback with empty stdout. The payload names both source zips so
        # the message resolves to a path on disk.
        typer.echo(
            json.dumps(
                {
                    "refusal": str(exc),
                    "minedex_csv_zip": str(minedex_csv_zip_path),
                    "tenements_zip": str(tenements_zip_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    counts = register.register_counts(df)
    owner_join_disclosures = register.owner_join_disclosures(sites_df, owners_df)
    # D12.2: the ProjectsOwners.csv current/ended split, computed directly
    # off the frame this command already has in hand -- see
    # `register.owner_row_composition`'s docstring for why this is disclosed
    # here too, alongside `owner_join_disclosures`, rather than left for a
    # reader to re-derive from the MINEDEX snapshot's own validation summary.
    owner_row_composition = register.owner_row_composition(owners_df)
    report = register.build_reconciliation_report(
        df,
        counts,
        # Both totals are read back INDEPENDENTLY of the frames just built
        # above, never as `len()` of those same frames -- see `_csv_zip_
        # member_row_count`/`_shapefile_zip_feature_count` for why that would
        # make the row-count check unfailable.
        minedex_feature_count=_csv_zip_member_row_count(minedex_csv_zip_path, "Sites.csv"),
        tenements_feature_count=_shapefile_zip_feature_count(
            tenements_zip_path, TENEMENTS_SHAPEFILE_BASENAME
        ),
        owner_join_disclosures=owner_join_disclosures,
    )
    if not report.passed:
        typer.echo(json.dumps({"reconciliation_error": report.text}, indent=2, sort_keys=True))
        raise typer.Exit(1)

    output_dir = resolved.run.data_root / "curated" / "register" / date
    register_path = output_dir / "register.parquet"

    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    _refuse_if_curated_output_already_exists(
        register_path, config=resolved_config, git_state=git_state
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_table_or_refuse(
        df,
        register_path,
        register.REGISTER_SCHEMA,
        payload={
            "minedex_csv_zip": str(minedex_csv_zip_path),
            "tenements_zip": str(tenements_zip_path),
        },
    )

    counts_path = output_dir / "register_counts.json"
    counts_path.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n")

    reconciliation_path = output_dir / "reconciliation.md"
    reconciliation_path.write_text(report.text)

    minedex_public_export_blocked = not licence.minedex_redistribution_allowed(minedex_snapshot_dir)
    # Disclosed rather than refused (see `register.build_reconciliation_
    # report`): a source record with no coordinates is a property of the
    # snapshot, and the register keeps one row per source record either way.
    # Carrying the count in the manifest and on stdout means a reader never
    # has to re-derive it from the parquet to know the artefact holds rows
    # that cannot be located. Read back off the BUILT register
    # (`register.count_rows_without_location`) rather than off `sites_df`
    # directly, so every route into the register is counted by the same
    # definition -- see that function's docstring.
    n_sites_null_coordinates = register.count_rows_without_location(df)
    # Same discipline: `site_id` (Sites.csv's own SiteCode, verbatim) is no
    # longer guaranteed unique now the register is one row per Sites.csv
    # record -- see `register.site_id_duplication_counts`'s docstring.
    # Disclosed rather than refused; `build-crosswalk` is what acts on it.
    site_id_duplication = register.site_id_duplication_counts(df)
    # D12.2 (`docs/decisions/2026-08-16-d9-d12-commit-remote-naming-
    # sequencing.md`): NOT COMPUTED (coordinate-less sites) disclosed
    # separately from a genuine computed zero -- see `register.
    # tenement_count_disclosure`'s docstring for the reconciliation identity
    # (`n_sites_tenement_count_computed + n_sites_tenement_count_not_computed
    # == sites_total`) this always satisfies by construction.
    tenement_count_disclosure = register.tenement_count_disclosure(df)

    minedex_source = licence.SOURCES["dmirs_001_minedex"]
    tenements_source = licence.SOURCES["dmirs_003_tenements"]
    input_assets = [
        SourceAsset(
            uri=str(minedex_csv_zip_path),
            sha256=sha256_file(minedex_csv_zip_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(minedex_snapshot_dir.name),
            licence=minedex_source.licence_id,
            redistribute_public=minedex_source.redistribute_public,
        ),
        SourceAsset(
            uri=str(tenements_zip_path),
            sha256=sha256_file(tenements_zip_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(tenements_snapshot_dir.name),
            licence=tenements_source.licence_id,
            redistribute_public=tenements_source.redistribute_public,
        ),
    ]
    minedex_snapshot_dir_relative, minedex_snapshot_dir_root = manifests.root_relative_path(
        minedex_snapshot_dir, config=resolved_config
    )
    tenements_snapshot_dir_relative, tenements_snapshot_dir_root = manifests.root_relative_path(
        tenements_snapshot_dir, config=resolved_config
    )
    try:
        manifests.write_run_manifest(
            output=register_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                # Root-relativised via `manifests.root_relative_path`, the
                # same treatment `fetch-maus-extract` gives
                # `resolved_args.source_local_path` -- an absolute account
                # path (`~/data/wa-mine-monitor/raw/...` under the real
                # config) does not belong in a manifest sidecar that ships
                # alongside a publishable artefact.
                "minedex_snapshot_dir": minedex_snapshot_dir_relative,
                "minedex_snapshot_dir_root": minedex_snapshot_dir_root,
                "tenements_snapshot_dir": tenements_snapshot_dir_relative,
                "tenements_snapshot_dir_root": tenements_snapshot_dir_root,
                "counts": counts,
                "minedex_public_export_blocked": minedex_public_export_blocked,
                "register_lonlat_crs": register.REGISTER_LONLAT_CRS,
                "minedex_source_crs": register.MINEDEX_SITES_SOURCE_CRS,
                "owner_join_disclosures": owner_join_disclosures,
                "owner_row_composition": owner_row_composition,
                "n_sites_null_coordinates": n_sites_null_coordinates,
                "site_id_duplication": site_id_duplication,
                "tenement_count_disclosure": tenement_count_disclosure,
                # The (ok, bad, missing) verification triple per input
                # snapshot, as measured by `_verify_snapshot_or_refuse`
                # before anything was read -- the artefact's own manifest
                # says its inputs were verified, and against how many files.
                "minedex_snapshot_verification": minedex_snapshot_verification,
                "tenements_snapshot_verification": tenements_snapshot_verification,
            },
        )
    except FileExistsError as exc:
        # The residual race `_refuse_if_curated_output_already_exists`'s
        # pre-flight cannot close: `resolved_args`/`inputs`/the artefact's own
        # `sha256` are only knowable AFTER the write above, so a manifest
        # written concurrently between that check and this call still lands
        # here. Render it as the same structured JSON refusal every other
        # refusal in this module emits, rather than an uncaught traceback.
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {
                "register_path": str(register_path),
                "counts": counts,
                "reconciliation": "PASS" if report.passed else "FAIL",
                "minedex_public_export_blocked": minedex_public_export_blocked,
                "owner_join_disclosures": owner_join_disclosures,
                "owner_row_composition": owner_row_composition,
                "n_sites_null_coordinates": n_sites_null_coordinates,
                "site_id_duplication": site_id_duplication,
                "tenement_count_disclosure": tenement_count_disclosure,
                "manifest_path": str(register_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("build-crosswalk")
def build_crosswalk_cmd(config: Path = ConfigOption, date: str = DateOption) -> None:
    """Build the MINEDEX-Maus crosswalk from the LATEST curated register and the latest Maus WA extract snapshot.

    Locates the most recent dated `curated/register/<date>/` directory
    (`_latest_curated_dated_dir`) and the most recent dated `raw/maus_v2/
    <date>/` snapshot (`register.latest_snapshot`) -- refusing, naming
    whichever is missing, when either has no directory at all. The Maus
    snapshot is then integrity-verified before anything is read off it
    (`_verify_snapshot_or_refuse`, same gate as `build-register`'s raw
    inputs; the curated register carries a run manifest rather than a
    `SHA256SUMS.txt` and is not gated here), with the (ok, bad, missing)
    triple recorded in the run manifest's `resolved_args`. Reads
    `register.parquet`'s `site_id`/`lon`/`lat` columns and rebuilds point
    geometry in `register.REGISTER_LONLAT_CRS` (the register's own declared
    CRS for those columns -- see that constant's docstring, and `register.
    build_register`'s docstring for how it is enforced at write time), and
    reads `wa_extract.gpkg`'s `maus_id`/geometry columns. BOTH are reprojected to
    `crosswalk.TARGET_CRS` (EPSG:3577) HERE, in the CLI, never inside
    `crosswalk.build_crosswalk` itself (see that module's docstring for why
    reprojection is deliberately kept out of the pure function).

    Before matching, `crosswalk.filter_register_for_crosswalk` reduces the
    register frame to the population `build_crosswalk` can actually score:
    a null `site_id`, a `site_id` duplicated across more than one register
    row (the register is one row per `Sites.csv` record, and `Sites.csv`'s
    own `SiteCode` carries measured duplication on the real product --
    `register.site_id_duplication_counts` discloses the identical property
    at `build-register` time), and a row with no usable `lon`/`lat` (the
    MINEDEX record carried no geometry; `build-register` also discloses
    this count in its `reconciliation.md`) are all EXCLUDED from the
    crosswalk's input population rather than refusing the whole run --
    these are measured properties of a healthy real MINEDEX extract, not
    defects in this build. The exclusion counts
    (`crosswalk.CROSSWALK_EXCLUSION_KEYS`) are echoed on stdout and
    recorded in the run manifest's `resolved_args["crosswalk_population"]`,
    so a reader never has to re-derive how many register rows were excluded
    or why.

    `crosswalk.build_crosswalk` then matches every FILTERED register site
    against the Maus polygons (containment, then nearest-within-2000m; see
    that function's docstring for the full matching rule, and for its own
    input guards, which still run as a backstop over the filtered frame and
    are rendered here as a structured JSON refusal, exit 1, before anything
    is written, on anything the filter above failed to catch). `crosswalk.
    crosswalk_counts` reconciles the result's per-confidence distinct-site
    counts against their own total (raising before anything is written, on
    the same "reconcile before trusting" discipline `build-register`
    applies to `register_counts`).

    On success, writes into `<data_root>/curated/crosswalk/<date>/`:
    `crosswalk.parquet` (declared `crosswalk.CROSSWALK_SCHEMA`, via
    `tables.write_table` -- scalar fields and `maus_id` ONLY, no Maus
    geometry column at all, so the CC-BY-SA-4.0 Maus polygon geometry never
    reaches this artefact; see `crosswalk.py`'s module docstring) and
    `crosswalk_counts.json`. An immutable run manifest is written alongside
    `crosswalk.parquet`, with two `inputs` entries: the register parquet
    actually read (recorded `redistribute_public=False` -- the register can
    carry MINEDEX-derived attributes that are fail-closed per `licence.py`,
    and this manifest does not attempt to re-derive that per-run; see
    `build-register`'s own `minedex_public_export_blocked` field for the
    authoritative answer) and the Maus WA extract snapshot actually read
    (carrying `licence.SOURCES["maus_v2"]`'s own licence fields). A re-run
    against a `--date` this command has already built is refused, BEFORE
    `crosswalk.parquet`/`crosswalk_counts.json` are touched (`_refuse_if_
    curated_output_already_exists`) -- move the existing `curated/crosswalk/
    <date>/` directory aside, or choose a different `--date`, to build
    again.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")

    register_root = resolved.run.data_root / "curated" / "register"
    try:
        register_dir = _latest_curated_dated_dir(register_root, label="curated/register")
        maus_snapshot_dir = register.latest_snapshot(resolved.run.data_root, "maus_v2")
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    # Same integrity gate `build-register` applies to its raw inputs: the
    # Maus snapshot is date-selected, so verify it before reading it. The
    # curated register directory is NOT gated here -- it is a curated
    # artefact carrying a run manifest, not a raw snapshot with a
    # `SHA256SUMS.txt`.
    maus_snapshot_verification = _verify_snapshot_or_refuse(
        maus_snapshot_dir, source_id="maus_v2", required_files=("wa_extract.gpkg",)
    )

    register_path = register_dir / "register.parquet"
    maus_path = maus_snapshot_dir / "wa_extract.gpkg"
    try:
        register_df = pd.read_parquet(register_path)
        # `crosswalk.filter_register_for_crosswalk` reduces the register to
        # the population `crosswalk.build_crosswalk` can actually score --
        # see that function's docstring and this command's own docstring for
        # why a null/duplicate `site_id` or a coordinate-less row is
        # EXCLUDED here rather than left for `build_crosswalk`'s own guards
        # to hard-refuse the whole run on an expected property of a healthy
        # real MINEDEX extract. `crosswalk_population_counts` is echoed on
        # stdout and recorded in the run manifest below.
        crosswalk_population, crosswalk_population_counts = crosswalk.filter_register_for_crosswalk(
            register_df
        )
        minedex_gdf = gpd.GeoDataFrame(
            crosswalk_population[["site_id"]],
            geometry=gpd.points_from_xy(crosswalk_population["lon"], crosswalk_population["lat"]),
            crs=register.REGISTER_LONLAT_CRS,
        ).to_crs(crosswalk.TARGET_CRS)

        maus_source_gdf = gpd.read_file(maus_path)
        maus_gdf = maus_source_gdf[["maus_id", "geometry"]].to_crs(crosswalk.TARGET_CRS)
    except (pyogrio.errors.DataSourceError, OSError) as exc:
        # `_verify_snapshot_or_refuse`'s `required_files=("wa_extract.gpkg",)`
        # gate above already refuses a Maus snapshot finalized without that
        # file present, or one where it no longer matches its recorded
        # digest -- the identical gate `build_register_cmd` applies to
        # minedex.gpkg/tenements.gpkg. This still catches a hash-matching
        # `wa_extract.gpkg` that is nonetheless unreadable as a datasource,
        # or a found `curated/register/<date>/` directory that lacks
        # `register.parquet` (`pd.read_parquet` raises `FileNotFoundError`,
        # an `OSError` subclass; the curated register is not covered by this
        # gate at all -- see this command's docstring). Rendered as the same
        # structured JSON refusal every other refusal in this module emits,
        # rather than an uncaught traceback with empty stdout.
        typer.echo(
            json.dumps(
                {
                    "refusal": str(exc),
                    "register_parquet": str(register_path),
                    "maus_gpkg": str(maus_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    except (KeyError, crosswalk.CrosswalkInputError) as exc:
        # Two distinct schema-drift shapes, reduced to the same structured
        # refusal. `crosswalk.CrosswalkInputError` is `crosswalk.
        # filter_register_for_crosswalk`'s own explicit check over
        # `register.parquet` (`site_id`/`lon`/`lat`) -- its message already
        # names every missing column, so it is used verbatim. A bare
        # `KeyError` can still reach here only from the `wa_extract.gpkg`
        # column-slice above (`maus_id`/`geometry`), which has no such
        # explicit check and raises on direct bracket access -- named the
        # same way `build_register_cmd` names a renamed MINEDEX column via
        # its `ValueError` branch.
        detail = (
            str(exc)
            if isinstance(exc, crosswalk.CrosswalkInputError)
            else f"wa_extract.gpkg is missing the expected column {exc}"
        )
        typer.echo(
            json.dumps(
                {
                    "refusal": detail,
                    "register_parquet": str(register_path),
                    "maus_gpkg": str(maus_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    try:
        df = crosswalk.build_crosswalk(minedex_gdf, maus_gdf)
    except crosswalk.CrosswalkInputError as exc:
        # `build_crosswalk`'s own declared input guards (duplicate/null
        # `site_id`, duplicate/null `maus_id`, a site with no usable point,
        # a missing required column) all raise `crosswalk.
        # CrosswalkInputError` -- a `ValueError` subclass, never a bare
        # `ValueError` -- naming the offending values. Rendered as the same
        # structured JSON refusal every other refusal in this module emits,
        # rather than reaching the terminal as a traceback with empty
        # stdout. Catching this specific subclass, not bare `ValueError`,
        # is deliberate: an unrelated `ValueError` raised inside the
        # matching passes (a pandas/shapely internal, not an input-shape
        # problem) must propagate uncaught rather than being reported as a
        # clean refusal that misattributes a real defect. See
        # `crosswalk.CrosswalkInputError`'s own docstring.
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    counts = crosswalk.crosswalk_counts(df)
    row_total = crosswalk.crosswalk_row_total(df)
    # `crosswalk_counts` deliberately does NOT carry `row_total` (see that
    # function's docstring -- mixing it in broke `register.reconcile_
    # counts`'s round-trip invariant on the returned dict). This CLI-level
    # merge is a TERMINAL disclosure dict for `crosswalk_counts.json`, the
    # run manifest and stdout only -- never re-fed into `reconcile_counts`.
    disclosed_counts = {**counts, "row_total": row_total}

    output_dir = resolved.run.data_root / "curated" / "crosswalk" / date
    crosswalk_path = output_dir / "crosswalk.parquet"

    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    _refuse_if_curated_output_already_exists(
        crosswalk_path, config=resolved_config, git_state=git_state
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_table_or_refuse(
        df,
        crosswalk_path,
        crosswalk.CROSSWALK_SCHEMA,
        payload={
            "register_parquet": str(register_path),
            "maus_gpkg": str(maus_path),
        },
    )

    counts_path = output_dir / "crosswalk_counts.json"
    counts_path.write_text(json.dumps(disclosed_counts, indent=2, sort_keys=True) + "\n")

    maus_source = licence.SOURCES["maus_v2"]
    input_assets = [
        SourceAsset(
            uri=str(register_path),
            sha256=sha256_file(register_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(register_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(maus_path),
            sha256=sha256_file(maus_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(maus_snapshot_dir.name),
            licence=maus_source.licence_id,
            redistribute_public=maus_source.redistribute_public,
        ),
    ]
    register_dir_relative, register_dir_root = manifests.root_relative_path(
        register_dir, config=resolved_config
    )
    maus_snapshot_dir_relative, maus_snapshot_dir_root = manifests.root_relative_path(
        maus_snapshot_dir, config=resolved_config
    )
    try:
        manifests.write_run_manifest(
            output=crosswalk_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                # Root-relativised via `manifests.root_relative_path` -- see
                # the identical treatment in `build_register_cmd`.
                "register_dir": register_dir_relative,
                "register_dir_root": register_dir_root,
                "maus_snapshot_dir": maus_snapshot_dir_relative,
                "maus_snapshot_dir_root": maus_snapshot_dir_root,
                "counts": disclosed_counts,
                # See `build_register_cmd`: the input snapshot's verification
                # triple, measured before anything was read.
                "maus_snapshot_verification": maus_snapshot_verification,
                # `crosswalk.CROSSWALK_EXCLUSION_KEYS` -- how many register
                # rows this run excluded from the crosswalk's input
                # population, and why, per `crosswalk.filter_register_for_
                # crosswalk`'s docstring.
                "crosswalk_population": crosswalk_population_counts,
            },
        )
    except FileExistsError as exc:
        # See the identical comment in `build_register_cmd`: the residual
        # race `_refuse_if_curated_output_already_exists`'s pre-flight
        # cannot close, rendered as structured JSON rather than a traceback.
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {
                "crosswalk_path": str(crosswalk_path),
                "counts": disclosed_counts,
                "crosswalk_population": crosswalk_population_counts,
                "manifest_path": str(crosswalk_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("build-climate-context")
def build_climate_context_cmd(
    config: Path = ConfigOption,
    date: str = DateOption,
    start_year: int = typer.Option(..., "--start-year"),
    end_year: int = typer.Option(..., "--end-year"),
) -> None:
    """Build SILO rainfall context rows (D13 F5) for every D3-eligible site,
    one row per `(site_id, year)` for `year` in `[start_year, end_year]`.

    Reads the LATEST verified `raw/silo/<date>/` snapshot (`fetch-silo`'s
    output), the LATEST curated D3-eligibility-annotated register
    (`apply-d3-threshold`'s output), the LATEST curated Tier 1 crosswalk,
    and the raw Maus snapshot the crosswalk was built from -- the same
    resolution and integrity discipline `extract-trajectories` applies to
    each. Context rows are context only (see `climate_context.py`'s
    docstring): this command never joins climate to trajectory rows and
    never states a cause.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    # GATE 1 -- year range, BEFORE any I/O. Without this, `range(start_year,
    # end_year + 1)` is empty while the baseline union stays populated, so
    # every gate below passes, `assemble_rows` honestly emits zero rows, and
    # this command writes a schema-valid, finalized `climate_context.parquet`
    # holding no site-years at all -- an empty curated artefact that
    # verifies clean is worse than a refusal, because the next run of
    # `_refuse_if_curated_output_already_exists` then defends it forever.
    if start_year > end_year:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"--start-year {start_year} is after --end-year {end_year} -- "
                        "refusing an inverted year range"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    output_dir = data_root / "curated" / "climate-context" / date
    output_path = output_dir / "climate_context.parquet"
    _refuse_if_curated_output_already_exists(
        output_path, config=resolved_config, git_state=git_state
    )

    requested_years = list(range(start_year, end_year + 1))
    # Never a silently narrower baseline (D13 F5): a missing baseline year
    # and a missing requested year are both refused by the SAME gate below,
    # naming every missing year, so a caller can never mistake "some SILO
    # files present" for "the whole baseline is present".
    needed_years = sorted(
        set(range(climate_context.BASELINE_START_YEAR, climate_context.BASELINE_END_YEAR + 1))
        | set(requested_years)
    )

    # GATE 2 -- the latest SILO snapshot: digest-verified, and must carry
    # every needed year's daily_rain object (the full 1991-2020 baseline
    # PLUS every requested year).
    try:
        silo_snapshot_dir = register.latest_snapshot(data_root, "silo")
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    silo_verification = _verify_snapshot_or_refuse(
        silo_snapshot_dir,
        source_id="silo",
        required_files=tuple(annual_object_name("daily_rain", year) for year in needed_years),
    )

    # GATE 3 -- latest curated register: digest-verified, and must be the
    # D3-eligibility-annotated register `apply-d3-threshold` writes -- the
    # same two column checks `extract-trajectories` GATE 2 applies.
    try:
        register_dir = _latest_curated_dated_dir(
            data_root / "curated" / "register", label="curated/register"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    register_path = register_dir / "register.parquet"
    register_manifest = _digest_verified_manifest(register_path)
    register_df = read_table(register_path)
    if "trajectory_status" not in register_df.columns:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "latest curated register is not D3-eligibility-annotated -- run "
                        "apply-d3-threshold first"
                    ),
                    "register_path": str(register_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if "d3_forced_threshold" not in register_df.columns:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "latest curated register predates the d3_forced_threshold column -- "
                        "run apply-d3-threshold to re-write it"
                    ),
                    "register_path": str(register_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    eligible = trajectory_extract.select_eligible_sites(register_df)

    # GATE 4 -- crosswalk + Maus snapshot, resolved and sha256-tied exactly
    # as `extract-trajectories` GATE 4 does.
    try:
        crosswalk_dir = _latest_curated_dated_dir(
            data_root / "curated" / "crosswalk", label="curated/crosswalk"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    crosswalk_path = crosswalk_dir / "crosswalk.parquet"
    crosswalk_manifest = _digest_verified_manifest(crosswalk_path)
    crosswalk_df = read_table(crosswalk_path)
    tier1_df = crosswalk.tier1_population(crosswalk_df)

    maus_licence_id = licence.SOURCES["maus_v2"].licence_id
    crosswalk_maus_input = next(
        (
            asset
            for asset in crosswalk_manifest.get("inputs", [])
            if asset.get("licence") == maus_licence_id
        ),
        None,
    )
    if crosswalk_maus_input is None:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{crosswalk_path}'s manifest carries no Maus input (licence "
                        f"{maus_licence_id!r}) -- cannot verify which Maus snapshot it "
                        "was built from"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    crosswalk_maus_sha256 = crosswalk_maus_input["sha256"]

    try:
        maus_snapshot_dir = register.latest_snapshot(data_root, "maus_v2")
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    _verify_snapshot_or_refuse(
        maus_snapshot_dir, source_id="maus_v2", required_files=("wa_extract.gpkg",)
    )
    maus_path = maus_snapshot_dir / "wa_extract.gpkg"
    maus_gpkg_sha256 = sha256_file(maus_path)
    if maus_gpkg_sha256 != crosswalk_maus_sha256:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"latest maus_v2 raw snapshot ({maus_path}) hashes "
                        f"{maus_gpkg_sha256[:12]}..., but the crosswalk's manifest records "
                        f"Maus sha256 {crosswalk_maus_sha256[:12]}... -- the latest raw Maus "
                        "snapshot is not the one the crosswalk was built from"
                    ),
                    "maus_gpkg_sha256": maus_gpkg_sha256,
                    "crosswalk_maus_gpkg_sha256": crosswalk_maus_sha256,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    try:
        maus_source_gdf = gpd.read_file(maus_path)
        maus_gdf = maus_source_gdf[["maus_id", "geometry"]].to_crs(crosswalk.TARGET_CRS)
    except (pyogrio.errors.DataSourceError, OSError) as exc:
        typer.echo(
            json.dumps({"refusal": str(exc), "maus_gpkg": str(maus_path)}, indent=2, sort_keys=True)
        )
        raise typer.Exit(1) from None
    except KeyError as exc:
        typer.echo(
            json.dumps(
                {
                    "refusal": f"wa_extract.gpkg is missing the expected column {exc}",
                    "maus_gpkg": str(maus_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    maus_geom_by_id: dict[str, Any] = dict(
        zip(maus_gdf["maus_id"].astype(str), maus_gdf.geometry, strict=True)
    )

    # Site->Maus tie-break, reproduced EXACTLY from `extract-trajectories`
    # (mirrors `register.py`'s own eligibility tie-break, ~1373: stable sort
    # by `["site_id", "maus_id"]`, `drop_duplicates(keep="first")` -- the
    # lexicographically SMALLEST `maus_id` per site) -- a site CAN carry more
    # than one `confidence == "high"` crosswalk row (overlapping Maus
    # polygons), and this ensures the same footprint is picked here as was
    # picked for that site's own D3 eligibility judgement.
    tier1_dedup = tier1_df.sort_values(
        ["site_id", "maus_id"], na_position="last", kind="stable"
    ).drop_duplicates(subset="site_id", keep="first")
    maus_id_by_site: dict[str, str] = dict(
        zip(tier1_dedup["site_id"].astype(str), tier1_dedup["maus_id"].astype(str), strict=True)
    )

    # An eligible site absent from the Tier 1 crosswalk population is
    # refused by name, not silently dropped: `assemble_rows`' row-count
    # guarantee reconciles against `site_maus_pairs`, which by then has
    # already excluded the missing site, so it cannot catch this itself.
    site_maus_pairs: list[tuple[str, str]] = []
    for site in eligible:
        maus_id = maus_id_by_site.get(site)
        if maus_id is None:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"eligible site {site!r} is not in the Tier 1 crosswalk "
                            f"population ({crosswalk_path}) -- an eligible site must have a "
                            "high-confidence Maus match"
                        )
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1)
        site_maus_pairs.append((site, maus_id))

    missing_geometry = sorted({m for _s, m in site_maus_pairs} - set(maus_geom_by_id))
    if missing_geometry:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"maus_id(s) {missing_geometry} (from eligible sites) are absent "
                        f"from the latest Maus snapshot ({maus_path})"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # Centroids in EQUAL-AREA, then to geographic. `maus_gdf` is already
    # reprojected to `crosswalk.TARGET_CRS` (EPSG:3577) above, so `.centroid`
    # is taken there -- a centroid taken directly in degrees is not the
    # footprint's areal centre, and taking it in this project's own
    # equal-area CRS keeps it consistent with the footprint areas the rest
    # of the pipeline reports. Only THEN is the point reprojected to
    # EPSG:4326 for the SILO grid lookup, which is indexed in degrees.
    distinct_maus_ids = sorted({m for _s, m in site_maus_pairs})
    centroids_3577 = gpd.GeoSeries(
        [maus_geom_by_id[m] for m in distinct_maus_ids], crs=crosswalk.TARGET_CRS
    ).centroid
    centroids_4326 = centroids_3577.to_crs("EPSG:4326")

    grid = silo.read_grid(
        silo_snapshot_dir / annual_object_name("daily_rain", climate_context.BASELINE_START_YEAR)
    )

    cell_id_by_maus: dict[str, str] = {}
    # (lat_i, lon_i) per REAL (in-grid) occupied cell -- rainfall is read
    # once per cell, not once per site or per maus_id, since sites/footprints
    # sharing a location share a cell.
    real_cell_indices: dict[str, tuple[int, int]] = {}
    baseline_gap_by_cell: dict[str, str] = {}
    for maus_id, centroid in zip(distinct_maus_ids, centroids_4326, strict=True):
        lon, lat = float(centroid.x), float(centroid.y)
        try:
            lat_i, lon_i = grid.cell_index_for_point(lat=lat, lon=lon)
        except SiloError as exc:
            # A footprint outside the grid becomes a per-cell not-computable
            # entry, not a run abort (D13 F5) -- and never snaps to an edge
            # cell. The pseudo-cell id is point-derived (not a real grid
            # cell centre) purely so this maus_id still has SOME cell to
            # carry on its rows; `baseline_gap_by_cell` makes every year for
            # it `not_computable` unconditionally, so nothing ever tries to
            # read rainfall for it.
            pseudo_cell = silo.cell_id(lat=lat, lon=lon)
            cell_id_by_maus[maus_id] = pseudo_cell
            baseline_gap_by_cell[pseudo_cell] = f"footprint outside the SILO grid: {exc}"
            continue
        cell = silo.cell_id(lat=grid.lats[lat_i], lon=grid.lons[lon_i])
        cell_id_by_maus[maus_id] = cell
        real_cell_indices[cell] = (lat_i, lon_i)

    metrics_by_cell_year: dict[tuple[str, int], silo.AnnualMetrics] = {}
    not_computable_by_cell_year: dict[tuple[str, int], str] = {}
    baseline_annuals_by_cell: dict[str, list[float]] = {}
    silo_files_read: set[int] = set()
    # One `cells_daily_series` call per year, not one `cell_daily_series`
    # call per (cell, year): it opens `path` once, verifies that year's own
    # lat/lon coordinate arrays against `grid` (the BASELINE_START_YEAR
    # reference) BEFORE reading any cell, and only then reads every real
    # cell needed from it. A year whose file has a flipped or shifted grid
    # is refused here rather than silently indexed against the wrong cell.
    for year in needed_years:
        path = silo_snapshot_dir / annual_object_name("daily_rain", year)
        silo_files_read.add(year)
        try:
            series_by_cell = silo.cells_daily_series(path, indices=real_cell_indices, grid=grid)
        except SiloError as exc:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"{path} failed grid verification against the "
                            f"{climate_context.BASELINE_START_YEAR} reference grid: {exc}"
                        )
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1) from None
        for cell, series in series_by_cell.items():
            try:
                metrics_by_cell_year[(cell, year)] = silo.annual_metrics(series)
            except silo.SiloNotComputableError as exc:
                not_computable_by_cell_year[(cell, year)] = str(exc)

    baseline_years = range(
        climate_context.BASELINE_START_YEAR, climate_context.BASELINE_END_YEAR + 1
    )
    for cell in real_cell_indices:
        missing_baseline_years = [
            year for year in baseline_years if (cell, year) not in metrics_by_cell_year
        ]
        if missing_baseline_years:
            baseline_gap_by_cell[cell] = (
                f"baseline missing year(s) {missing_baseline_years} (of "
                f"{climate_context.BASELINE_START_YEAR}-{climate_context.BASELINE_END_YEAR})"
            )
        else:
            baseline_annuals_by_cell[cell] = [
                metrics_by_cell_year[(cell, year)].annual_rainfall_mm for year in baseline_years
            ]

    try:
        rows_df = climate_context.assemble_rows(
            site_maus_pairs=site_maus_pairs,
            cell_id_by_maus=cell_id_by_maus,
            metrics_by_cell_year=metrics_by_cell_year,
            not_computable_by_cell_year=not_computable_by_cell_year,
            baseline_annuals_by_cell=baseline_annuals_by_cell,
            baseline_gap_by_cell=baseline_gap_by_cell,
            years=requested_years,
            snapshot_date=silo_snapshot_dir.name,
            source_version=licence.SOURCES["silo"].title,
        )
    except climate_context.ClimateContextError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_table_or_refuse(rows_df, output_path, climate_context.CLIMATE_CONTEXT_SCHEMA)

    silo_source = licence.SOURCES["silo"]
    input_assets = [
        SourceAsset(
            uri=str(register_path),
            sha256=register_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(register_dir.name),
            licence=licence.SOURCES["dmirs_001_minedex"].licence_id,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(crosswalk_path),
            sha256=crosswalk_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(crosswalk_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(maus_path),
            sha256=maus_gpkg_sha256,
            collection=None,
            snapshot_date=dt_date.fromisoformat(maus_snapshot_dir.name),
            licence=maus_licence_id,
            redistribute_public=False,
        ),
        *(
            SourceAsset(
                uri=str(silo_snapshot_dir / annual_object_name("daily_rain", year)),
                sha256=sha256_file(silo_snapshot_dir / annual_object_name("daily_rain", year)),
                collection=None,
                snapshot_date=dt_date.fromisoformat(silo_snapshot_dir.name),
                licence=silo_source.licence_id,
                redistribute_public=silo_source.redistribute_public,
            )
            for year in sorted(silo_files_read)
        ),
    ]

    try:
        manifests.write_run_manifest(
            output=output_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                "start_year": start_year,
                "end_year": end_year,
                "register_dir": str(register_dir),
                "crosswalk_dir": str(crosswalk_dir),
                "maus_snapshot_dir": str(maus_snapshot_dir),
                "silo_snapshot_dir": str(silo_snapshot_dir),
                "silo_snapshot_verification": silo_verification,
                "n_eligible_sites": len(eligible),
                "n_cells": len(cell_id_by_maus),
            },
        )
    except FileExistsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    n_computed = int((rows_df["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED).sum())
    n_not_computable = int(
        (rows_df["climate_status"] == climate_context.CLIMATE_STATUS_NOT_COMPUTABLE).sum()
    )
    typer.echo(
        json.dumps(
            {
                "output_path": str(output_path),
                "manifest_path": str(output_path) + manifests.MANIFEST_SUFFIX,
                "rows": len(rows_df),
                "n_computed": n_computed,
                "n_not_computable": n_not_computable,
                "n_cells": len(cell_id_by_maus),
                "n_sites": len({s for s, _m in site_maus_pairs}),
                "start_year": start_year,
                "end_year": end_year,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("build-fire-context")
def build_fire_context_cmd(
    config: Path = ConfigOption,
    date: str = DateOption,
    start_year: int = typer.Option(..., "--start-year"),
    end_year: int = typer.Option(..., "--end-year"),
) -> None:
    """Build DBCA-060 fire-history context rows (D13 F4) for every D3-eligible
    site, one row per `(site_id, year)` for `year` in `[start_year, end_year]`.

    Mirrors `build-climate-context`'s gate structure exactly (GATE 1
    inverted-range refusal before any I/O; GATE 2 latest verified
    `raw/dbca_060_fire/<date>/` snapshot; GATE 3 latest D3-eligibility-
    annotated register; GATE 4 crosswalk + Maus snapshot, sha-tied). Context
    rows are context only (see `fire_context.py`'s docstring): this command
    never joins fire history to trajectory rows and never states a cause.
    `not_recorded` is NEVER a known-negative fire label.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    # GATE 1 -- year range, BEFORE any I/O. See `build_climate_context_cmd`'s
    # identical gate for why an inverted range must never reach `assemble_
    # rows` and produce a schema-valid, finalized, empty artefact.
    if start_year > end_year:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"--start-year {start_year} is after --end-year {end_year} -- "
                        "refusing an inverted year range"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    output_dir = data_root / "curated" / "fire-context" / date
    output_path = output_dir / "fire_context.parquet"
    _refuse_if_curated_output_already_exists(
        output_path, config=resolved_config, git_state=git_state
    )

    requested_years = list(range(start_year, end_year + 1))

    # GATE 2 -- the latest DBCA-060 fire snapshot: locate the single *.gpkg
    # inside it FIRST (refuse if zero or more than one), then digest-verify
    # the snapshot with that gpkg named in `required_files` -- an unlisted
    # file dropped in after finalisation otherwise passes verification (same
    # gate `build_climate_context_cmd` GATE 2 applies to the SILO files it
    # consumes).
    try:
        dbca_snapshot_dir = register.latest_snapshot(data_root, "dbca_060_fire")
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    gpkgs = sorted(dbca_snapshot_dir.glob("*.gpkg"))
    if len(gpkgs) != 1:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{dbca_snapshot_dir} must hold exactly one *.gpkg, found "
                        f"{[p.name for p in gpkgs]}"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    gpkg_path = gpkgs[0]

    dbca_verification = _verify_snapshot_or_refuse(
        dbca_snapshot_dir, source_id="dbca_060_fire", required_files=(gpkg_path.name,)
    )
    snapshot_year = int(dbca_snapshot_dir.name[:4])
    gpkg_sha256 = sha256_file(gpkg_path)
    source_version = f"dbca-060-{dbca_snapshot_dir.name}-sha256-{gpkg_sha256[:12]}"

    # GATE 3 -- latest curated register: digest-verified, and must be the
    # D3-eligibility-annotated register `apply-d3-threshold` writes -- the
    # same two column checks `build_climate_context_cmd` GATE 3 applies.
    try:
        register_dir = _latest_curated_dated_dir(
            data_root / "curated" / "register", label="curated/register"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    register_path = register_dir / "register.parquet"
    register_manifest = _digest_verified_manifest(register_path)
    register_df = read_table(register_path)
    if "trajectory_status" not in register_df.columns:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "latest curated register is not D3-eligibility-annotated -- run "
                        "apply-d3-threshold first"
                    ),
                    "register_path": str(register_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if "d3_forced_threshold" not in register_df.columns:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "latest curated register predates the d3_forced_threshold column -- "
                        "run apply-d3-threshold to re-write it"
                    ),
                    "register_path": str(register_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    eligible = trajectory_extract.select_eligible_sites(register_df)

    # GATE 4 -- crosswalk + Maus snapshot, resolved and sha256-tied exactly
    # as `build_climate_context_cmd` GATE 4 does.
    try:
        crosswalk_dir = _latest_curated_dated_dir(
            data_root / "curated" / "crosswalk", label="curated/crosswalk"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    crosswalk_path = crosswalk_dir / "crosswalk.parquet"
    crosswalk_manifest = _digest_verified_manifest(crosswalk_path)
    crosswalk_df = read_table(crosswalk_path)
    tier1_df = crosswalk.tier1_population(crosswalk_df)

    maus_licence_id = licence.SOURCES["maus_v2"].licence_id
    crosswalk_maus_input = next(
        (
            asset
            for asset in crosswalk_manifest.get("inputs", [])
            if asset.get("licence") == maus_licence_id
        ),
        None,
    )
    if crosswalk_maus_input is None:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{crosswalk_path}'s manifest carries no Maus input (licence "
                        f"{maus_licence_id!r}) -- cannot verify which Maus snapshot it "
                        "was built from"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    crosswalk_maus_sha256 = crosswalk_maus_input["sha256"]

    try:
        maus_snapshot_dir = register.latest_snapshot(data_root, "maus_v2")
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    _verify_snapshot_or_refuse(
        maus_snapshot_dir, source_id="maus_v2", required_files=("wa_extract.gpkg",)
    )
    maus_path = maus_snapshot_dir / "wa_extract.gpkg"
    maus_gpkg_sha256 = sha256_file(maus_path)
    if maus_gpkg_sha256 != crosswalk_maus_sha256:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"latest maus_v2 raw snapshot ({maus_path}) hashes "
                        f"{maus_gpkg_sha256[:12]}..., but the crosswalk's manifest records "
                        f"Maus sha256 {crosswalk_maus_sha256[:12]}... -- the latest raw Maus "
                        "snapshot is not the one the crosswalk was built from"
                    ),
                    "maus_gpkg_sha256": maus_gpkg_sha256,
                    "crosswalk_maus_gpkg_sha256": crosswalk_maus_sha256,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    try:
        maus_source_gdf = gpd.read_file(maus_path)
        maus_gdf = maus_source_gdf[["maus_id", "geometry"]].to_crs(crosswalk.TARGET_CRS)
    except (pyogrio.errors.DataSourceError, OSError) as exc:
        typer.echo(
            json.dumps({"refusal": str(exc), "maus_gpkg": str(maus_path)}, indent=2, sort_keys=True)
        )
        raise typer.Exit(1) from None
    except KeyError as exc:
        typer.echo(
            json.dumps(
                {
                    "refusal": f"wa_extract.gpkg is missing the expected column {exc}",
                    "maus_gpkg": str(maus_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    maus_geom_by_id: dict[str, Any] = dict(
        zip(maus_gdf["maus_id"].astype(str), maus_gdf.geometry, strict=True)
    )

    # Site->Maus tie-break, reproduced EXACTLY from `build_climate_context_
    # cmd` (mirrors `register.py`'s own eligibility tie-break: stable sort
    # by `["site_id", "maus_id"]`, `drop_duplicates(keep="first")` -- the
    # lexicographically SMALLEST `maus_id` per site).
    tier1_dedup = tier1_df.sort_values(
        ["site_id", "maus_id"], na_position="last", kind="stable"
    ).drop_duplicates(subset="site_id", keep="first")
    maus_id_by_site: dict[str, str] = dict(
        zip(tier1_dedup["site_id"].astype(str), tier1_dedup["maus_id"].astype(str), strict=True)
    )

    # An eligible site absent from the Tier 1 crosswalk population is
    # refused by name, not silently dropped: `assemble_rows`' row-count
    # guarantee reconciles against `site_maus_pairs`, which by then has
    # already excluded the missing site, so it cannot catch this itself.
    # `no_footprint` is reserved for step 6's empty/invalid geometry below --
    # this is a DELIBERATE integrity gate, never downgraded to a per-row
    # unknown.
    site_maus_pairs: list[tuple[str, str]] = []
    for site in eligible:
        maus_id = maus_id_by_site.get(site)
        if maus_id is None:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"eligible site {site!r} is not in the Tier 1 crosswalk "
                            f"population ({crosswalk_path}) -- an eligible site must have a "
                            "high-confidence Maus match"
                        )
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1)
        site_maus_pairs.append((site, maus_id))

    missing_geometry = sorted({m for _s, m in site_maus_pairs} - set(maus_geom_by_id))
    if missing_geometry:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"maus_id(s) {missing_geometry} (from eligible sites) are absent "
                        f"from the latest Maus snapshot ({maus_path})"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # Per distinct maus_id, reproject the single footprint geometry to
    # EPSG:4283 (the fire layer's own CRS) -- an empty or invalid geometry
    # becomes a per-row `unknown`/`no_footprint`, never a run abort.
    distinct_maus_ids = sorted({m for _s, m in site_maus_pairs})
    footprints_4283 = gpd.GeoSeries(
        [maus_geom_by_id[m] for m in distinct_maus_ids], crs=crosswalk.TARGET_CRS
    ).to_crs("EPSG:4283")

    no_footprint_by_maus: dict[str, str] = {}
    counts_by_maus_year: dict[tuple[str, int], int] = {}
    for maus_id, geom in zip(distinct_maus_ids, footprints_4283, strict=True):
        if geom.is_empty or not geom.is_valid:
            no_footprint_by_maus[maus_id] = "footprint geometry empty or invalid"
            continue
        year_counts = dbca.fire_year_counts_for_footprint(gpkg_path, geom)
        for year, count in year_counts.items():
            if start_year <= year <= end_year:
                counts_by_maus_year[(maus_id, year)] = count

    try:
        rows_df = fire_context.assemble_rows(
            site_maus_pairs=site_maus_pairs,
            counts_by_maus_year=counts_by_maus_year,
            no_footprint_by_maus=no_footprint_by_maus,
            years=requested_years,
            snapshot_year=snapshot_year,
            snapshot_date=dbca_snapshot_dir.name,
            source_version=source_version,
        )
    except fire_context.FireContextError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    fire_context.validate_row_counts(
        rows_df, n_pairs=len(site_maus_pairs), n_years=end_year - start_year + 1
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_table_or_refuse(rows_df, output_path, fire_context.FIRE_CONTEXT_SCHEMA)

    dbca_source = licence.SOURCES["dbca_060_fire"]
    input_assets = [
        SourceAsset(
            uri=str(gpkg_path.resolve().as_uri()),
            sha256=gpkg_sha256,
            collection=None,
            snapshot_date=dt_date.fromisoformat(dbca_snapshot_dir.name),
            licence=dbca_source.licence_id,
            redistribute_public=dbca_source.redistribute_public,
        ),
        SourceAsset(
            uri=str(register_path),
            sha256=register_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(register_dir.name),
            licence=licence.SOURCES["dmirs_001_minedex"].licence_id,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(crosswalk_path),
            sha256=crosswalk_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(crosswalk_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(maus_path),
            sha256=maus_gpkg_sha256,
            collection=None,
            snapshot_date=dt_date.fromisoformat(maus_snapshot_dir.name),
            licence=maus_licence_id,
            redistribute_public=False,
        ),
    ]

    status_counts = {
        str(status): int(count) for status, count in rows_df["fire_status"].value_counts().items()
    }

    try:
        manifests.write_run_manifest(
            output=output_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                "start_year": start_year,
                "end_year": end_year,
                "register_dir": str(register_dir),
                "crosswalk_dir": str(crosswalk_dir),
                "maus_snapshot_dir": str(maus_snapshot_dir),
                "dbca_snapshot_dir": str(dbca_snapshot_dir),
                "dbca_snapshot_verification": dbca_verification,
                "n_sites": len({s for s, _m in site_maus_pairs}),
                "n_rows": len(rows_df),
                "status_counts": status_counts,
            },
        )
    except FileExistsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {
                "output_path": str(output_path),
                "manifest_path": str(output_path) + manifests.MANIFEST_SUFFIX,
                "rows": len(rows_df),
                "status_counts": status_counts,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("fetch-dea-catalogue")
def fetch_dea_catalogue(
    config: Path = ConfigOption,
    date: str = DateOption,
) -> None:
    """Capture the four pinned DEA STAC collections into one dated snapshot.

    Fetches collection JSON + every WA-bbox item page for each collection in
    `source_catalogue.DEA_COLLECTIONS`, validates health (stub signature,
    zero/duplicate items, licence consistency, required assets), and writes
    an immutable snapshot at `<data_root>/raw/dea_stac/<date>/` with one run
    manifest carrying four SourceAsset inputs. Any single collection failing
    refuses the WHOLE run before finalization -- a partial catalogue would
    silently understate coverage downstream (C3/C5).
    """
    resolved = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    snapshot_dir = snapshots.create_snapshot_dir(resolved.run.data_root, "dea_stac", date)
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    _refuse_if_snapshot_already_finalized(snapshot_dir, config=resolved_config, git_state=git_state)

    client = new_dea_client()

    def fetch_one(
        spec: SourceSpec,
    ) -> tuple[SourceSpec, tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]]:
        return spec, fetch_collection_catalogue(client, spec)

    try:
        fetched = map_concurrent(
            fetch_one, DEA_COLLECTIONS, max_workers=DEA_RETRY_POLICY.max_workers
        )
    except CatalogueValidationError as exc:
        typer.echo(
            json.dumps(
                {"refusal": f"DEA catalogue validation failed: {exc}", "stage": "validation"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    except Exception as exc:  # noqa: BLE001 -- surfaced as a structured refusal
        typer.echo(
            json.dumps(
                {"refusal": f"DEA catalogue fetch failed: {exc}", "stage": "download"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    collection_summaries = []
    collection_digests: dict[str, str] = {}
    for spec, (collection, pages, summary) in fetched:
        subdir = snapshot_dir / spec.collection_id
        subdir.mkdir(parents=True, exist_ok=True)
        collection_path = subdir / "collection.json"
        collection_path.write_text(
            json.dumps(collection, indent=2, sort_keys=True), encoding="utf-8"
        )
        for page_number, page in enumerate(pages, start=1):
            (subdir / f"items_page_{page_number:04d}.json").write_text(
                json.dumps(page, indent=2, sort_keys=True), encoding="utf-8"
            )
        # D13 C2's "response digest": the digest of the CAPTURED
        # collection.json bytes as they landed on disk, so the summary and
        # the snapshot's own SHA256SUMS entry describe the same bytes. Hashed
        # ONCE here and reused below for both the summary field and the
        # manifest's SourceAsset -- never re-read from disk a second time.
        collection_digests[spec.source_id] = sha256_file(collection_path)
        collection_summaries.append(
            {
                **summary,
                "source_id": spec.source_id,
                "fetch_date": date,
                "collection_response_sha256": collection_digests[spec.source_id],
            }
        )

    (snapshot_dir / "catalogue_summary.json").write_text(
        json.dumps({"collections": collection_summaries}, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    licence_notes = "; ".join(
        f"{spec.collection_id}: {licence.SOURCES[spec.source_id].licence_id}"
        for spec in DEA_COLLECTIONS
    )
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="DEA Explorer STAC (four pinned annual collections)",
        endpoint=collection_url(DEA_COLLECTIONS[0].collection_id).rsplit("/", 2)[0],
        licence_note=f"Licences re-read from each captured collection.json: {licence_notes}",
        purpose=(
            "Pinned DEA STAC catalogue snapshot for the WA mine rehabilitation "
            "spectral monitor's epoch-coverage index and volume estimate."
        ),
    )
    sums_path = snapshots.finalize_snapshot(snapshot_dir)
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)

    input_assets = [
        SourceAsset(
            uri=collection_url(spec.collection_id),
            sha256=collection_digests[spec.source_id],
            collection=spec.collection_id,
            snapshot_date=dt_date.fromisoformat(date),
            licence=licence.SOURCES[spec.source_id].licence_id,
            redistribute_public=licence.SOURCES[spec.source_id].redistribute_public,
        )
        for spec in DEA_COLLECTIONS
    ]
    manifests.write_run_manifest(
        output=sums_path,
        inputs=input_assets,
        config=resolved_config,
        git_state=git_state,
        resolved_args={"date": date, "collections": collection_summaries},
    )

    typer.echo(
        json.dumps(
            {
                "snapshot_dir": str(snapshot_dir),
                "verify": {"ok": n_ok, "bad": n_bad, "missing": n_missing},
                "collections": collection_summaries,
                "manifest_path": str(sums_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("build-maus-footprint-areas")
def build_maus_footprint_areas_cmd(config: Path = ConfigOption, date: str = DateOption) -> None:
    """Reduce the latest Maus WA extract snapshot to per-`maus_id` footprint SCALARS.

    D13 Batch C direction (`docs/decisions/2026-08-16-batch-c-footprint-input-
    direction.md`, option C): the Tier 1 volume estimator needs a per-site
    footprint SIZE, never the CC-BY-SA-4.0 Maus geometry itself, so this
    artefact carries `maus_id`/`footprint_area_m2`/`footprint_bbox_width_m`/
    `footprint_bbox_height_m` and nothing else -- no geometry column, ever.

    Mirrors `build-crosswalk`'s Maus handling exactly: `register.
    latest_snapshot(data_root, "maus_v2")` selects the latest dated `raw/
    maus_v2/<date>/` snapshot, `_verify_snapshot_or_refuse` integrity-gates
    it (`required_files=("wa_extract.gpkg",)`, the same gate `build-
    crosswalk` applies), then `maus_id`/`geometry` are read off `wa_extract.
    gpkg` and reprojected to `crosswalk.TARGET_CRS` (EPSG:3577, equal-area,
    metres) HERE, in the CLI -- never inside `maus_footprints.
    derive_footprint_stats`, which refuses a frame not already in that CRS
    rather than silently reprojecting a caller that never declared one.
    `derive_footprint_stats` then reduces each polygon to its three scalars,
    refusing (not dropping) a null/empty geometry, a non-positive area, or a
    duplicated `maus_id` -- a dropped footprint would silently become the
    downstream estimator's floor window rather than a measured size.

    On success, writes `<data_root>/curated/maus_footprint_areas/<date>/
    footprint_areas.parquet` (declared `maus_footprints.
    MAUS_FOOTPRINT_STATS_SCHEMA`, via `_write_table_or_refuse`) with an
    immutable run manifest alongside it: one `SourceAsset` input (the Maus
    GeoPackage actually read, its `sha256`, the snapshot date, and
    `licence.SOURCES["maus_v2"]`'s own licence fields), and `resolved_args`
    carrying the Maus snapshot directory (root-relativised via `manifests.
    root_relative_path`), the Maus GeoPackage's own `sha256` (recorded again
    here, separately from `inputs[0].sha256`, so `derive-dea-volume` can
    later refuse a crosswalk/footprint pair built from different Maus
    snapshots by comparing this field alone -- see the decision doc's
    "Rejecting A"), the snapshot verification triple, the CRS, the footprint
    count, and `output_licence="CC-BY-SA-4.0"` /
    `output_share_alike=True` -- the artefact stays in the Maus CC-BY-SA
    lineage even though it carries no geometry. A re-run against a `--date`
    already built here is refused BEFORE anything is read or written
    (`_refuse_if_curated_output_already_exists`, the same guard `build-
    crosswalk` applies).
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)

    try:
        maus_snapshot_dir = register.latest_snapshot(resolved.run.data_root, "maus_v2")
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    # Same integrity gate `build-crosswalk` applies to its Maus input: the
    # snapshot is date-selected, so verify it before reading it.
    maus_snapshot_verification = _verify_snapshot_or_refuse(
        maus_snapshot_dir, source_id="maus_v2", required_files=("wa_extract.gpkg",)
    )

    maus_path = maus_snapshot_dir / "wa_extract.gpkg"
    try:
        maus_source_gdf = gpd.read_file(maus_path)
        # `maus_id`/`geometry` ONLY -- no other Maus attribute ever reaches
        # this artefact. Reprojected here, never inside `derive_footprint_
        # stats` (see this command's docstring).
        maus_gdf = maus_source_gdf[["maus_id", "geometry"]].to_crs(crosswalk.TARGET_CRS)
    except (pyogrio.errors.DataSourceError, OSError) as exc:
        # Same failure class `build-crosswalk` catches here: a hash-matching
        # `wa_extract.gpkg` that is nonetheless unreadable as a datasource.
        typer.echo(
            json.dumps(
                {"refusal": str(exc), "maus_gpkg": str(maus_path)},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    except KeyError as exc:
        # `wa_extract.gpkg` missing `maus_id` or `geometry` -- named the same
        # way `build-crosswalk`'s identical column-slice failure is named.
        typer.echo(
            json.dumps(
                {
                    "refusal": f"wa_extract.gpkg is missing the expected column {exc}",
                    "maus_gpkg": str(maus_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    try:
        stats = maus_footprints.derive_footprint_stats(maus_gdf)
    except maus_footprints.FootprintStatsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    output_dir = resolved.run.data_root / "curated" / "maus_footprint_areas" / date
    output_path = output_dir / "footprint_areas.parquet"
    _refuse_if_curated_output_already_exists(
        output_path, config=resolved_config, git_state=git_state
    )

    # Every manifest ingredient computed BEFORE the artefact is written (the
    # ordering `build-dea-coverage` establishes): a `_write_table_or_refuse`
    # success followed by a manifest-ingredient failure would strand a
    # manifestless `footprint_areas.parquet` that the existing-output guard
    # above would then refuse to repair on a re-run.
    maus_gpkg_sha256 = sha256_file(maus_path)
    maus_snapshot_dir_relative, maus_snapshot_dir_root = manifests.root_relative_path(
        maus_snapshot_dir, config=resolved_config
    )
    maus_source = licence.SOURCES["maus_v2"]
    input_assets = [
        SourceAsset(
            uri=str(maus_path),
            sha256=maus_gpkg_sha256,
            collection=None,
            snapshot_date=dt_date.fromisoformat(maus_snapshot_dir.name),
            licence=maus_source.licence_id,
            redistribute_public=maus_source.redistribute_public,
        ),
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_table_or_refuse(
        stats,
        output_path,
        maus_footprints.MAUS_FOOTPRINT_STATS_SCHEMA,
        payload={"maus_gpkg": str(maus_path)},
    )

    try:
        manifests.write_run_manifest(
            output=output_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                "maus_snapshot_dir": maus_snapshot_dir_relative,
                "maus_snapshot_dir_root": maus_snapshot_dir_root,
                "maus_gpkg_sha256": maus_gpkg_sha256,
                "maus_snapshot_verification": maus_snapshot_verification,
                "crs": crosswalk.TARGET_CRS,
                "n_footprints": len(stats),
                "output_licence": "CC-BY-SA-4.0",
                "output_share_alike": True,
            },
        )
    except FileExistsError as exc:
        # See the identical comment in `build_crosswalk_cmd`: the residual
        # race `_refuse_if_curated_output_already_exists`'s pre-flight
        # cannot close, rendered as structured JSON rather than a traceback.
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {
                "output_path": str(output_path),
                "n_footprints": len(stats),
                "maus_gpkg": str(maus_path),
                "manifest_path": str(output_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("export-release")
def export_release_cmd(
    package: str = typer.Option(
        ..., "--package", help="Key into release.PACKAGES naming the release package to build."
    ),
    config: Path = ConfigOption,
    date: str = DateOption,
) -> None:
    """Publish `--package` as a licence-gated public release, for `--date`.

    The first production caller of `export_gate.export_public`
    (`export_gate.py`'s own docstring names this as the caller that must
    exist before its enforcement claim is true). `--package` must be a key
    of `release.PACKAGES` -- the closed registry of things this project
    releases -- or the run refuses before touching `config`; an unregistered
    package name is a typo, never a request to release something ad hoc.

    Mirrors `build-maus-footprint-areas`'s discipline exactly: config load,
    git state, a refuse-before-read guard on the OUTPUT directory
    (`<data_root>/releases/<date>/<package>/`, checked via
    `_refuse_if_curated_output_already_exists` before the curated input is
    even opened, so a re-run against an already-published `--date` never
    re-reads or re-hashes anything), then the curated input is read through
    `_digest_verified_manifest` -- the same digest-verification `derive-dea-
    volume` applies to its own curated inputs -- so a curated artefact
    hand-edited or partially rebuilt since it was written never reaches a
    public release.

    `release.prepare_for_export` attaches the row-level `redistribute_public`
    gate from `licence.SOURCES[spec.source_id]` (the one licence-fact
    registry this project maintains; see `licence.py`), and
    `export_gate.export_public` then enforces it: a `PermissionError` --
    raised on ANY row whose gate is not exactly `True`, never a partial
    filter -- becomes a JSON refusal and exit 1 here, the same translation
    every other domain exception in this module receives. Row filtering is
    prohibited by design (D13 Batch G): a package that mixes redistributable
    and restricted rows fails as a whole, because a silently filtered
    release reconciles against nothing and hides that a restricted source
    reached the boundary.

    On success, writes three files under
    `<data_root>/releases/<date>/<package>/`: the published parquet (the
    curated artefact's own declared schema, minus whatever
    `export_public` dropped), `ATTRIBUTION.txt` (byte-exact
    `release.attribution_block(spec)` -- the CC-BY-SA obligations shipped
    WITH the package, not only recorded in the manifest), and an immutable
    run manifest with one `SourceAsset` input (the curated artefact actually
    read) and `resolved_args` carrying the package name, the release and
    curated dates, `output_licence`, `output_share_alike`, the attribution
    block, and the `dropped_columns` list `export_public` recorded on
    `DataFrame.attrs["export_public"]` -- so a reader of the manifest alone
    can see exactly what left the curated frame at this boundary.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    spec = release.PACKAGES.get(package)
    if spec is None:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"unknown release package {package!r} -- must be one of "
                        f"{sorted(release.PACKAGES)}"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    output_dir = data_root / "releases" / date / package
    output_path = output_dir / spec.filename
    # Refuse-before-read: the OUTPUT directory is checked before the curated
    # input is opened at all, the same ordering `build_maus_footprint_areas_
    # cmd` applies to ITS output -- a re-run against an already-published
    # `--date` must not re-hash a curated artefact it is about to refuse to
    # overwrite anyway.
    _refuse_if_curated_output_already_exists(
        output_path, config=resolved_config, git_state=git_state
    )

    curated_path = data_root / "curated" / spec.curated_dir / date / spec.filename
    curated_manifest = _digest_verified_manifest(curated_path)
    curated_sha256 = curated_manifest["output"]["sha256"]

    frame = read_table(curated_path)
    prepared = release.prepare_for_export(frame, spec)
    try:
        published = export_gate.export_public(prepared)
    except PermissionError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    dropped_columns = published.attrs["export_public"]["dropped_columns"]
    attribution = release.attribution_block(spec)
    source = licence.SOURCES[spec.source_id]

    input_assets = [
        SourceAsset(
            uri=str(curated_path),
            sha256=curated_sha256,
            collection=None,
            snapshot_date=dt_date.fromisoformat(date),
            licence=source.licence_id,
            redistribute_public=source.redistribute_public,
        ),
    ]

    # The published parquet is declared against the CURATED artefact's own
    # schema, filtered to the columns `export_public` actually kept -- never
    # inferred from `published`'s rows, the same declared-schema discipline
    # `write_table` enforces everywhere else in this module.
    source_schema = pq.read_schema(curated_path)
    output_schema = pa.schema([field for field in source_schema if field.name in published.columns])

    # The licence obligation is asymmetric: released data must NEVER exist on
    # disk without its attribution (source link, licence link, modification
    # statement) beside it, while attribution without data is merely inert.
    # `ATTRIBUTION.txt` is therefore written FIRST, so a failed parquet write
    # never strands a CC-BY-SA-derived file unattributed -- do not reorder
    # this to "parquet then attribution" as a simplification.
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "ATTRIBUTION.txt").write_text(attribution, encoding="utf-8")
    _write_table_or_refuse(
        published,
        output_path,
        output_schema,
        payload={"curated_path": str(curated_path)},
    )

    try:
        manifests.write_run_manifest(
            output=output_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "package": package,
                "date": date,
                "curated_date": date,
                "output_licence": spec.output_licence,
                "output_share_alike": spec.share_alike,
                "attribution_block": attribution,
                "dropped_columns": dropped_columns,
            },
        )
    except FileExistsError as exc:
        # Same residual-race translation `build_maus_footprint_areas_cmd`
        # applies at its own manifest write.
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {
                "output_path": str(output_path),
                "package": package,
                "n_rows": len(published),
                "dropped_columns": dropped_columns,
                "manifest_path": str(output_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


#: `--version` must match this before any I/O happens (D13 §8 P3): a
#: CalVer-ish `YYYY.MM.DD` with an optional `-<suffix>` (a same-day
#: respin), e.g. `2026.08.29` or `2026.08.29-hotfix`. Not validated as a
#: real calendar date -- this is a release LABEL, not a date field -- so
#: `9999.99.99` passes the regex; that is deliberate, the same way
#: `--date`'s own `YYYY-MM-DD` check only shapes the string, and it is the
#: version-directory immutability check (not this regex) that is the real
#: guard against a re-run silently overwriting a prior release.
_PUBLIC_RC_VERSION_PATTERN = re.compile(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}(-[a-z0-9]+)?")


@app.command("build-tier0-public-rc")
def build_tier0_public_rc_cmd(
    config: Path = ConfigOption,
    version: str = typer.Option(
        ...,
        "--version",
        help="Immutable release version, e.g. 2026.08.29 (YYYY.MM.DD[-suffix]).",
    ),
) -> None:
    """Build the two licence-clean Tier 0 public-RC fallback packages (D13 §8 P3).

    Two small, source-derived reference layers -- DMIRS-003 tenements
    geometry and the project's own Maus et al. v2 WA extract -- assembled by
    `public_rc.assemble_tier0_tenements` / `assemble_tier0_maus` and cross-
    checked by `public_rc.reconcile_packages`. See `public_rc.py`'s module
    docstring for why these packages are NOT run through `export_gate.
    export_public`: that function's geometry drop guards the MINEDEX-frame
    register products, and D13 §8 P3 explicitly permits geometry in both
    packages built here. "Tier 0 public-RC" names licence-clean reference-
    layer fallbacks, never a public MINEDEX site register -- no MINEDEX-
    derived aggregate is ever built or shipped by this command.

    Mirrors `build-climate-context`'s shape: config load, git state, a
    pre-I/O `--version` regex gate, a refuse-before-read guard on the
    OUTPUT version directory (immutable -- a rebuild is a new `--version`,
    never a re-run against the same one), then `_verify_snapshot_or_refuse`
    on the LATEST `dmirs_003_tenements` and `maus_v2` raw snapshots (which
    may carry different dates; both are recorded), assembly, reconciliation,
    write, two run manifests, and a JSON echo. Every failure path -- a
    missing snapshot (`register.NoSnapshotFoundError`), an unverified or
    tampered snapshot, an unreadable shapefile/GeoPackage, a lineage or
    schema-drift refusal from `public_rc`, a reconciliation mismatch, or a
    write failure -- is translated to a structured JSON refusal and exit 1,
    never an escaping traceback.

    On success, writes into `<data_root>/releases/tier0-public-rc/<version>/`:
    `tier0-tenements.parquet`, `tier0-maus-wa.parquet` (both GeoParquet, via
    `GeoDataFrame.to_parquet` -- geometry-bearing, so `_write_table_or_refuse`
    is not used here), `RELEASE_NOTES.md` (`public_rc.render_release_notes`,
    registry-driven licence/attribution text, never hardcoded), and one
    immutable run manifest per package. Each manifest's one `SourceAsset`
    input records a snapshot-relative `uri` (never an absolute local path),
    the sha256 `snapshots.snapshot_entries` recorded for that exact file at
    finalize time, and the licence/redistribution fields from `licence.
    SOURCES` -- the same registry-driven discipline `export-release` applies
    to its own `SourceAsset`. Each package's `resolved_args` also records
    `dropped_source_columns` (the assembly functions' disclosed
    drop-with-disclosure lists -- open on the tenements side, the closed
    `MAUS_BENIGN_SOURCE_COLUMNS` allowlist on the maus side), so a reader of
    the manifest alone can see exactly which benign source columns (e.g.
    `HOLDER1`, `ISO3_CODE`) never made it into the public artefact.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    # GATE 1 -- version string, BEFORE any I/O. An immutable release
    # version is a label a human chooses, not something this command should
    # ever compute or coerce -- a malformed value is refused here rather
    # than silently accepted as a directory name that later tooling (the
    # public-facing release index, `scripts/audit_release_payload.py`) may
    # not expect.
    if not _PUBLIC_RC_VERSION_PATTERN.fullmatch(version):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"--version {version!r} does not match the required "
                        f"{_PUBLIC_RC_VERSION_PATTERN.pattern!r} shape (YYYY.MM.DD[-suffix])"
                    ),
                    "stage": "bad_version",
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # GATE 2 -- refuse-before-read: the OUTPUT version directory is checked
    # BEFORE either raw snapshot is even looked up, the same ordering
    # `export_release_cmd` applies to its own output -- a re-run against an
    # already-published `--version` must not re-hash or re-read anything on
    # its way to the refusal. A published Tier 0 version is immutable by
    # design: a rebuild is always a NEW `--version`, never an overwrite.
    output_dir = data_root / "releases" / "tier0-public-rc" / version
    if output_dir.exists():
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"release version {version!r} already exists at {output_dir} -- "
                        "Tier 0 public-RC versions are immutable; build a new --version "
                        "to publish a rebuild rather than overwriting this one"
                    ),
                    "stage": "version_exists",
                    "output_dir": str(output_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # GATE 3 -- latest raw snapshots for both sources must exist at all.
    try:
        tenements_snapshot_dir = register.latest_snapshot(data_root, "dmirs_003_tenements")
        maus_snapshot_dir = register.latest_snapshot(data_root, "maus_v2")
    except register.NoSnapshotFoundError as exc:
        typer.echo(
            json.dumps(
                {"refusal": str(exc), "stage": "snapshot_missing"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    # GATE 4 -- integrity-verify both snapshots BEFORE either is read, the
    # same digest-verification discipline `build_register_cmd`/`build_
    # climate_context_cmd` apply to their own raw inputs.
    tenements_verification = _verify_snapshot_or_refuse(
        tenements_snapshot_dir,
        source_id="dmirs_003_tenements",
        required_files=(TENEMENTS_ZIP_FILENAME,),
    )
    maus_verification = _verify_snapshot_or_refuse(
        maus_snapshot_dir, source_id="maus_v2", required_files=("wa_extract.gpkg",)
    )

    tenements_zip_path = tenements_snapshot_dir / TENEMENTS_ZIP_FILENAME
    maus_path = maus_snapshot_dir / "wa_extract.gpkg"
    tenements_date = tenements_snapshot_dir.name
    maus_date = maus_snapshot_dir.name

    # Both frames are read FULL -- never pre-selected -- so `public_rc`'s
    # lineage gate sees every input column; see `public_rc.py`'s module
    # docstring point 1 and `assemble_tier0_tenements`/`assemble_tier0_maus`'s
    # own docstrings for why a caller that pre-selects columns here would
    # blind that gate.
    try:
        tenements_gdf = gpd.read_file(
            f"/vsizip/{tenements_zip_path}/{TENEMENTS_SHAPEFILE_BASENAME}.shp"
        )
    except (zipfile.BadZipFile, pyogrio.errors.DataSourceError, OSError) as exc:
        typer.echo(
            json.dumps(
                {
                    "refusal": str(exc),
                    "stage": "source_read",
                    "tenements_zip": str(tenements_zip_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    try:
        maus_gdf = gpd.read_file(maus_path)
    except (pyogrio.errors.DataSourceError, OSError) as exc:
        typer.echo(
            json.dumps(
                {
                    "refusal": str(exc),
                    "stage": "source_read",
                    "maus_gpkg": str(maus_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    try:
        tenements_frame, dropped_source_columns = public_rc.assemble_tier0_tenements(
            tenements_gdf, snapshot_date=tenements_date
        )
        maus_frame, maus_dropped_source_columns = public_rc.assemble_tier0_maus(
            maus_gdf, snapshot_date=maus_date
        )
    except public_rc.PublicRcError as exc:
        typer.echo(
            json.dumps(
                {"refusal": str(exc), "stage": "assembly"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    try:
        counts = public_rc.reconcile_packages(
            tenements_frame,
            maus_frame,
            n_tenements_source=len(tenements_gdf),
            n_maus_source=len(maus_gdf),
        )
    except public_rc.PublicRcError as exc:
        typer.echo(
            json.dumps(
                {"refusal": str(exc), "stage": "reconciliation"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    output_dir.mkdir(parents=True, exist_ok=True)
    tenements_path = output_dir / "tier0-tenements.parquet"
    maus_output_path = output_dir / "tier0-maus-wa.parquet"
    try:
        tenements_frame.to_parquet(tenements_path)
        maus_frame.to_parquet(maus_output_path)
    except OSError as exc:
        typer.echo(
            json.dumps(
                {"refusal": str(exc), "stage": "write"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    release_notes = public_rc.render_release_notes(version, tenements_date, maus_date)
    (output_dir / "RELEASE_NOTES.md").write_text(release_notes, encoding="utf-8")

    tenements_entries = snapshots.snapshot_entries(tenements_snapshot_dir)
    maus_entries = snapshots.snapshot_entries(maus_snapshot_dir)
    tenements_source = licence.SOURCES["dmirs_003_tenements"]
    maus_source = licence.SOURCES["maus_v2"]

    # These two manifests ship INSIDE the public release payload itself
    # (`public_audit.audit_release_dir` scans every file under `output_dir`,
    # `*.run_manifest.json` included) -- unlike every other command's
    # manifest, which stays in the private `data_root` tree. The full
    # `resolved_config` every other command embeds carries this project's
    # internal `sources.minedex_public_export_blocked` field, which itself
    # contains the literal substring "minedex" and would trip `public_audit`'s
    # MINEDEX-lineage content check on a file the audit is supposed to pass
    # clean -- so only the one config fact `write_run_manifest` actually
    # needs (`data_root`, for root-relativising `output.path`/`inputs[].uri`)
    # is passed here, never the full internal config.
    public_manifest_config: dict[str, Any] = {"run": {"data_root": str(data_root)}}

    try:
        manifests.write_run_manifest(
            output=tenements_path,
            inputs=[
                SourceAsset(
                    uri=f"raw/dmirs_003_tenements/{tenements_date}/{TENEMENTS_ZIP_FILENAME}",
                    sha256=tenements_entries[TENEMENTS_ZIP_FILENAME],
                    collection=None,
                    snapshot_date=dt_date.fromisoformat(tenements_date),
                    licence=tenements_source.licence_id,
                    redistribute_public=tenements_source.redistribute_public,
                ),
            ],
            config=public_manifest_config,
            git_state=git_state,
            resolved_args={
                "version": version,
                "tenements_snapshot_date": tenements_date,
                "maus_snapshot_date": maus_date,
                "tenements_snapshot_verification": tenements_verification,
                "dropped_source_columns": dropped_source_columns,
            },
        )
        manifests.write_run_manifest(
            output=maus_output_path,
            inputs=[
                SourceAsset(
                    uri=f"raw/maus_v2/{maus_date}/wa_extract.gpkg",
                    sha256=maus_entries["wa_extract.gpkg"],
                    collection=None,
                    snapshot_date=dt_date.fromisoformat(maus_date),
                    licence=maus_source.licence_id,
                    redistribute_public=maus_source.redistribute_public,
                ),
            ],
            config=public_manifest_config,
            git_state=git_state,
            resolved_args={
                "version": version,
                "tenements_snapshot_date": tenements_date,
                "maus_snapshot_date": maus_date,
                "maus_snapshot_verification": maus_verification,
                "dropped_source_columns": maus_dropped_source_columns,
            },
        )
    except FileExistsError as exc:
        # Same residual-race translation `export_release_cmd` applies at its
        # own manifest write: the pre-flight `output_dir.exists()` check
        # above cannot close a race against a concurrent run.
        typer.echo(json.dumps({"refusal": str(exc), "stage": "write"}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {
                "version": version,
                "tenements_path": str(tenements_path),
                "maus_path": str(maus_output_path),
                "tenements_manifest_path": str(tenements_path) + manifests.MANIFEST_SUFFIX,
                "maus_manifest_path": str(maus_output_path) + manifests.MANIFEST_SUFFIX,
                "counts": counts,
                "tenements_snapshot_date": tenements_date,
                "maus_snapshot_date": maus_date,
                "dropped_source_columns": dropped_source_columns,
                "maus_dropped_source_columns": maus_dropped_source_columns,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("build-dea-coverage")
def build_dea_coverage(
    config: Path = ConfigOption,
    date: str = DateOption,
    catalogue_date: str = typer.Option(
        ...,
        "--catalogue-date",
        help="Dated raw/dea_stac/<date>/ snapshot to read the catalogue from.",
        callback=_validate_snapshot_date,
    ),
) -> None:
    """Enrich the latest curated register with DEA epoch coverage.

    Writes a NEW `curated/register/<date>/register.parquet` under
    `ENRICHED_REGISTER_SCHEMA`; the accepted Batch B artefact is never
    mutated. Refuses on: source register digest mismatch against its own
    manifest, catalogue snapshot verification failure, row loss/gain/
    reorder, or an existing output at `<date>`.
    """
    resolved = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    # 1. Source register: latest curated/register/<date>/, digest-verified
    #    against its own manifest's output.sha256 (`_digest_verified_
    #    manifest`, shared with `derive-dea-volume`).
    register_root = data_root / "curated" / "register"
    try:
        register_dir = _latest_curated_dated_dir(register_root, label="curated/register")
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    register_path = register_dir / "register.parquet"
    register_manifest_path = Path(str(register_path) + manifests.MANIFEST_SUFFIX)
    register_manifest = _digest_verified_manifest(register_path)
    actual_sha = register_manifest["output"]["sha256"]

    # 2. Catalogue snapshot: verified via SHA256SUMS.
    catalogue_dir = data_root / "raw" / "dea_stac" / catalogue_date
    _verify_snapshot_or_refuse(
        catalogue_dir, source_id="dea_stac", required_files=("catalogue_summary.json",)
    )
    catalogue_manifest_path = catalogue_dir / (
        snapshots.SHA256SUMS_FILENAME + manifests.MANIFEST_SUFFIX
    )

    # 3. Load items per source from the snapshot pages (`_load_dea_items`,
    #    shared with `derive-dea-volume`).
    items_by_source = _load_dea_items(catalogue_dir)

    # 4. Coverage + enrichment.
    register_df = read_table(register_path)
    item_index, duplicates_refused = dea_coverage.build_item_index(items_by_source)
    coverage_df, disclosures = dea_coverage.count_site_epochs(
        register_df, item_index, duplicates_refused=duplicates_refused
    )
    try:
        enriched = register.enrich_register_with_dea_coverage(register_df, coverage_df)
    except register.RegisterEnrichmentError as exc:
        typer.echo(
            json.dumps({"refusal": str(exc), "stage": "enrichment"}, indent=2, sort_keys=True)
        )
        raise typer.Exit(1) from None

    # 5. Compute EVERY manifest ingredient BEFORE the artefact is written.
    #    `_write_table_or_refuse` followed by a manifest failure would strand
    #    a manifestless register.parquet that the existing-output guard then
    #    refuses to repair on the re-run -- the artefact and its provenance
    #    must fail together or land together.
    out_dir = data_root / "curated" / "register" / date
    out_path = out_dir / "register.parquet"
    _refuse_if_curated_output_already_exists(out_path, config=resolved_config, git_state=git_state)

    # `root_relative_path` takes a MAPPING (it calls `config.get`) and returns
    # `(reduced_path, root_name)`; both halves are recorded, the way
    # `fetch-maus-extract` records `source_local_path`/`_root`.
    source_register_manifest, source_register_manifest_root = manifests.root_relative_path(
        register_manifest_path, config=resolved_config
    )
    source_catalogue_manifest, source_catalogue_manifest_root = manifests.root_relative_path(
        catalogue_manifest_path, config=resolved_config
    )
    catalogue_sums_path = catalogue_dir / snapshots.SHA256SUMS_FILENAME
    catalogue_sums_sha = sha256_file(catalogue_sums_path)

    input_assets = [
        SourceAsset(
            uri=str(register_path),
            sha256=actual_sha,
            collection=None,
            snapshot_date=None,
            licence=licence.SOURCES["dmirs_001_minedex"].licence_id,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(catalogue_sums_path),
            sha256=catalogue_sums_sha,
            collection="dea_stac",
            snapshot_date=dt_date.fromisoformat(catalogue_date),
            licence="CC-BY-4.0",
            redistribute_public=True,
        ),
    ]

    # 6. Ingredients all in hand -- now write the artefact, then its manifest.
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_table_or_refuse(enriched, out_path, register.ENRICHED_REGISTER_SCHEMA)
    try:
        manifests.write_run_manifest(
            output=out_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                "catalogue_date": catalogue_date,
                "source_register_manifest": source_register_manifest,
                "source_register_manifest_root": source_register_manifest_root,
                "source_catalogue_manifest": source_catalogue_manifest,
                "source_catalogue_manifest_root": source_catalogue_manifest_root,
                "dea_coverage_disclosure": disclosures,
                "minedex_public_export_blocked": resolved.sources.minedex_public_export_blocked,
                "register_rows_before": len(register_df),
                "register_rows_after": len(enriched),
            },
        )
    except FileExistsError as exc:
        # See the identical comment in `build_register_cmd`/`build_crosswalk_
        # cmd`/`build_maus_footprint_areas_cmd`: the residual race `_refuse_
        # if_curated_output_already_exists`'s pre-flight cannot close,
        # rendered as structured JSON rather than a traceback.
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {
                "output": str(out_path),
                "register_rows_before": len(register_df),
                "register_rows_after": len(enriched),
                "dea_coverage_disclosure": disclosures,
                "manifest_path": str(out_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


#: Metric identifiers each pinned collection's `asset_roles` make
#: computable -- informational only (`dea_volume.derive_volume_estimate`
#: never branches on these strings; they are echoed into the estimate so a
#: reader knows WHY these assets were selected). Geomedian's six
#: reflectance bands make NBR/NDMI/NDVI directly computable
#: (`source_catalogue.py`'s own docstring); FC-percentile's bare/
#: photosynthetic/non-photosynthetic bands are the fractional-cover
#: components themselves.
_DEA_METRIC_IDS: dict[str, tuple[str, ...]] = {
    "dea_gm_ls5t": ("nbr", "ndmi", "ndvi"),
    "dea_gm_ls7e": ("nbr", "ndmi", "ndvi"),
    "dea_gm_ls8cls9c": ("nbr", "ndmi", "ndvi"),
    "dea_fc_pc": ("bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"),
}

#: DECLARED bytes-per-pixel fallback per collection, used only where the
#: captured items published no `raster:bands` metadata to observe it from
#: (`dea_volume.derive_volume_estimate`'s `bytes_per_pixel_source="assumed"`
#: branch). Geomedian bands are int16 (2 bytes); FC-percentile bands are
#: uint8 percentages (1 byte) -- both DEA's published product dtypes.
_DEA_ASSUMED_BYTES_PER_PIXEL: dict[str, int] = {
    "dea_gm_ls5t": 2,
    "dea_gm_ls7e": 2,
    "dea_gm_ls8cls9c": 2,
    "dea_fc_pc": 1,
}

#: DECLARED tile-side fallback (pixels), same role as `_DEA_ASSUMED_BYTES_
#: PER_PIXEL` above -- DEA's standard geomedian/FC-percentile tile size.
_DEA_ASSUMED_TILE_PIXELS_PER_SIDE = 3200


@app.command("derive-dea-volume")
def derive_dea_volume_cmd(config: Path = ConfigOption, date: str = DateOption) -> None:
    """Derive the Tier 1 DEA data-volume estimate from real inputs.

    Locates the LATEST `curated/crosswalk/<date>/`, `curated/
    maus_footprint_areas/<date>/` and `curated/register/<date>/`
    directories, digest-verifying each artefact against its own manifest
    (`_digest_verified_manifest` -- the same check `build-dea-coverage`
    applies to its source register). The register must be DEA-ENRICHED
    (carry `register.DEA_COVERAGE_COLUMNS`); a bare Batch B register
    refuses, naming `build-dea-coverage`.

    Refuses unless the crosswalk manifest and the footprint-area manifest
    record the SAME Maus GeoPackage sha256. This is the whole reason the
    footprint scalars are their own artefact
    (`docs/decisions/2026-08-16-batch-c-footprint-input-direction.md`):
    `maus_id` is derived from clipped geometry (`sources/maus.py::
    _geometry_id`), so a crosswalk built from one Maus snapshot and
    footprints derived from another can join cleanly on ids that no longer
    mean the same polygon -- equal digests are the only cheap proof they
    came from the same snapshot.

    Rebuilds the item and asset indexes from the DEA STAC snapshot named in
    the enriched register's own manifest
    (`resolved_args["catalogue_date"]`), joins the high-confidence
    crosswalk population to the footprint scalars
    (`maus_footprints.join_site_footprints`), and calls the pure `dea_volume.
    derive_volume_estimate` -- everything passed to it is an ordinary pandas
    frame or a frozen declared input, never geometry.

    Writes `<data_root>/reports/dea-volume/<date>/estimate.json` plus an
    immutable run manifest (`inputs` = the register/crosswalk/footprints/
    catalogue assets actually read, `resolved_args` carrying the four
    source manifests' own digests). No public export: this report stays
    under `data_root`, exactly like the artefacts it is derived from.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    # 1. Latest crosswalk / footprint-areas / register directories.
    try:
        crosswalk_dir = _latest_curated_dated_dir(
            data_root / "curated" / "crosswalk", label="curated/crosswalk"
        )
        footprints_dir = _latest_curated_dated_dir(
            data_root / "curated" / "maus_footprint_areas",
            label="curated/maus_footprint_areas",
        )
        register_dir = _latest_curated_dated_dir(
            data_root / "curated" / "register", label="curated/register"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    crosswalk_path = crosswalk_dir / "crosswalk.parquet"
    footprints_path = footprints_dir / "footprint_areas.parquet"
    register_path = register_dir / "register.parquet"

    # Digest-verify all three parquets against their own manifests BEFORE
    # anything downstream reads them (same order Task 11 established).
    crosswalk_manifest = _digest_verified_manifest(crosswalk_path)
    footprints_manifest = _digest_verified_manifest(footprints_path)
    register_manifest = _digest_verified_manifest(register_path)

    # 2. The register must be DEA-ENRICHED -- build-dea-coverage's output,
    #    not the bare Batch B register.
    register_df = read_table(register_path)
    if any(column not in register_df.columns for column in register.DEA_COVERAGE_COLUMNS):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "latest curated register is not DEA-enriched -- run "
                        "build-dea-coverage first"
                    ),
                    "register_path": str(register_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # 3. The Maus-digest equality refusal -- see this command's docstring.
    #    `build-crosswalk`'s manifest records the Maus GeoPackage's sha256
    #    on its `inputs` entry carrying the Maus licence (the module never
    #    writes it into `resolved_args`); `build-maus-footprint-areas`
    #    records it directly at `resolved_args.maus_gpkg_sha256`.
    maus_licence_id = licence.SOURCES["maus_v2"].licence_id
    crosswalk_maus_input = next(
        (
            asset
            for asset in crosswalk_manifest.get("inputs", [])
            if asset.get("licence") == maus_licence_id
        ),
        None,
    )
    if crosswalk_maus_input is None:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{crosswalk_path}'s manifest carries no Maus input (licence "
                        f"{maus_licence_id!r}) -- cannot verify it was built from the "
                        "same Maus snapshot as the footprint artefact"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    crosswalk_maus_sha256 = crosswalk_maus_input["sha256"]
    try:
        footprints_maus_sha256 = footprints_manifest["resolved_args"]["maus_gpkg_sha256"]
    except KeyError:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{footprints_path}'s manifest does not record "
                        "resolved_args.maus_gpkg_sha256"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    if crosswalk_maus_sha256 != footprints_maus_sha256:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"crosswalk ({crosswalk_path}) and footprint-areas "
                        f"({footprints_path}) were built from DIFFERENT Maus GeoPackage "
                        f"snapshots -- crosswalk records {crosswalk_maus_sha256[:12]}..., "
                        f"footprints records {footprints_maus_sha256[:12]}.... maus_id is "
                        "derived from clipped geometry, so a join on maus_id alone cannot "
                        "detect this; refusing rather than silently mixing two snapshots' "
                        "geometry."
                    ),
                    "crosswalk_maus_gpkg_sha256": crosswalk_maus_sha256,
                    "footprints_maus_gpkg_sha256": footprints_maus_sha256,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # 4. Catalogue snapshot the register was enriched from -- verified, then
    #    rebuilt into the item and asset indexes.
    try:
        catalogue_date = register_manifest["resolved_args"]["catalogue_date"]
    except KeyError:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{register_path}'s manifest does not record "
                        "resolved_args.catalogue_date -- not a build-dea-coverage output"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    catalogue_dir = data_root / "raw" / "dea_stac" / catalogue_date
    _verify_snapshot_or_refuse(
        catalogue_dir, source_id="dea_stac", required_files=("catalogue_summary.json",)
    )
    catalogue_summary = json.loads(
        (catalogue_dir / "catalogue_summary.json").read_text(encoding="utf-8")
    )
    items_by_source = _load_dea_items(catalogue_dir)
    item_index, _duplicates_refused = dea_coverage.build_item_index(items_by_source)
    asset_index, _asset_disclosures = dea_coverage.build_asset_index(items_by_source)

    # 5. Declared inputs -- every constant echoed into the estimate/manifest
    #    rather than hard-coded inside the estimator (D13 Batch C amendment
    #    1a/1c). `year_ranges` come from each captured `collection.json`'s
    #    own temporal extent, recorded in `catalogue_summary.json` at fetch
    #    time -- never invented here.
    year_ranges: dict[str, dea_volume.YearRange] = {}
    for entry in catalogue_summary.get("collections", []):
        start, end = entry["temporal_extent"]
        year_ranges[entry["source_id"]] = dea_volume.YearRange(
            first_year=int(str(start)[:4]), last_year=int(str(end)[:4])
        )
    selections = tuple(
        dea_volume.CollectionSelection(
            source_id=spec.source_id,
            metric_ids=_DEA_METRIC_IDS[spec.source_id],
            asset_keys=tuple(spec.asset_roles),
            assumed_bytes_per_pixel=_DEA_ASSUMED_BYTES_PER_PIXEL[spec.source_id],
            assumed_tile_pixels_per_side=_DEA_ASSUMED_TILE_PIXELS_PER_SIDE,
        )
        for spec in DEA_COLLECTIONS
    )
    window_policy = dea_volume.WindowPolicy()

    # 6. High-confidence crosswalk rows joined to the digest-verified
    #    footprint scalars, many-to-one on maus_id.
    crosswalk_df = read_table(crosswalk_path)
    footprint_stats = read_table(footprints_path)
    try:
        footprints_df = maus_footprints.join_site_footprints(
            crosswalk.tier1_population(crosswalk_df), footprint_stats
        )
    except maus_footprints.FootprintStatsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    try:
        estimate = dea_volume.derive_volume_estimate(
            crosswalk_df=crosswalk_df,
            register_df=register_df,
            footprints_df=footprints_df,
            item_index=item_index,
            asset_index=asset_index,
            selections=selections,
            year_ranges=year_ranges,
            window_policy=window_policy,
        )
    except (dea_volume.VolumePopulationError, ValueError) as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    # 7. Every manifest ingredient computed BEFORE the artefact is written
    #    (the Task 11 ordering rule -- see `build_maus_footprint_areas_cmd`
    #    and `build_dea_coverage` for the identical discipline).
    source_manifest_digests = {
        "register": sha256_file(Path(str(register_path) + manifests.MANIFEST_SUFFIX)),
        "crosswalk": sha256_file(Path(str(crosswalk_path) + manifests.MANIFEST_SUFFIX)),
        "footprints": sha256_file(Path(str(footprints_path) + manifests.MANIFEST_SUFFIX)),
        "catalogue": sha256_file(
            catalogue_dir / (snapshots.SHA256SUMS_FILENAME + manifests.MANIFEST_SUFFIX)
        ),
    }
    estimate["source_manifest_digests"] = source_manifest_digests

    output_dir = data_root / "reports" / "dea-volume" / date
    output_path = output_dir / "estimate.json"
    _refuse_if_curated_output_already_exists(
        output_path, config=resolved_config, git_state=git_state
    )

    register_dir_relative, register_dir_root = manifests.root_relative_path(
        register_dir, config=resolved_config
    )
    crosswalk_dir_relative, crosswalk_dir_root = manifests.root_relative_path(
        crosswalk_dir, config=resolved_config
    )
    footprints_dir_relative, footprints_dir_root = manifests.root_relative_path(
        footprints_dir, config=resolved_config
    )
    catalogue_dir_relative, catalogue_dir_root = manifests.root_relative_path(
        catalogue_dir, config=resolved_config
    )
    catalogue_sums_path = catalogue_dir / snapshots.SHA256SUMS_FILENAME

    input_assets = [
        SourceAsset(
            uri=str(register_path),
            sha256=sha256_file(register_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(register_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(crosswalk_path),
            sha256=sha256_file(crosswalk_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(crosswalk_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(footprints_path),
            sha256=sha256_file(footprints_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(footprints_dir.name),
            licence="CC-BY-SA-4.0",
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(catalogue_sums_path),
            sha256=sha256_file(catalogue_sums_path),
            collection="dea_stac",
            snapshot_date=dt_date.fromisoformat(catalogue_date),
            licence="CC-BY-4.0",
            redistribute_public=True,
        ),
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(estimate, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )

    try:
        manifests.write_run_manifest(
            output=output_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                "register_dir": register_dir_relative,
                "register_dir_root": register_dir_root,
                "crosswalk_dir": crosswalk_dir_relative,
                "crosswalk_dir_root": crosswalk_dir_root,
                "footprints_dir": footprints_dir_relative,
                "footprints_dir_root": footprints_dir_root,
                "catalogue_dir": catalogue_dir_relative,
                "catalogue_dir_root": catalogue_dir_root,
                "catalogue_date": catalogue_date,
                "source_manifest_digests": source_manifest_digests,
                "selections": estimate["selections"],
                "year_ranges": estimate["year_ranges"],
                "window_policy": estimate["window_policy"],
            },
        )
    except FileExistsError as exc:
        # See the identical comment in `build_maus_footprint_areas_cmd`: the
        # residual race `_refuse_if_curated_output_already_exists`'s
        # pre-flight cannot close, rendered as structured JSON rather than a
        # traceback.
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {
                "output_path": str(output_path),
                "manifest_path": str(output_path) + manifests.MANIFEST_SUFFIX,
                "population": estimate["population"],
                "bytes": estimate["bytes"],
                "tiles": estimate["tiles"],
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("fetch-region-boundaries")
def cmd_fetch_region_boundaries(
    config: Path = ConfigOption,
    date: str = DateOption,
) -> None:
    """Capture the pinned DPIRD-020 RDC boundaries into a dated snapshot.

    Downloads the GeoJSON, validates it with
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
    except (HttpRequestRefused, HttpRetryExhausted) as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    try:
        _refuse_unless_complete_geojson(payload)
    except RegionPayloadError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    regions_path = snapshot_dir / _RDC_REGIONS_FILENAME
    regions_path.write_bytes(payload)

    try:
        regions = wa_regions.load_regions(regions_path)
    except Exception as exc:  # noqa: BLE001
        regions_path.unlink(missing_ok=True)
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    # Finalize + manifest: mirror fetch-maus-extract
    sums_path = snapshots.finalize_snapshot(snapshot_dir)
    _n_ok, _n_bad, _n_missing = snapshots.verify_snapshot(snapshot_dir)

    input_asset = SourceAsset(
        uri=_RDC_REGIONS_DOWNLOAD_URL,
        sha256=sha256_file(regions_path),
        collection=None,
        snapshot_date=dt_date.fromisoformat(date),
        licence="CC-BY-4.0",
        redistribute_public=True,
    )

    manifest_path = manifests.write_run_manifest(
        output=sums_path,
        inputs=[input_asset],
        config=resolved_config,
        git_state=git_state,
        resolved_args={
            "date": date,
            "source_url": _RDC_REGIONS_DOWNLOAD_URL,
            "region_count": len(regions),
        },
    )

    typer.echo(
        json.dumps(
            {
                "snapshot_dir": str(snapshot_dir),
                "region_count": len(regions),
                "regions": sorted(regions["region_name"].tolist()),
                "manifest_path": str(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )


@app.command("freeze-d3-protocol")
def cmd_freeze_d3_protocol(
    config: Path = ConfigOption,
    protocol_config: Path = ProtocolConfigOption,
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

    # Single lineage check: refuse if ANY dated directory exists
    base_dir = resolved.run.data_root / "curated" / "d3-protocol"
    if base_dir.exists():
        dated_dirs = list(base_dir.glob("????-??-??"))
        if dated_dirs:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"d3-protocol already has dated directory {dated_dirs[0].name}. "
                            "Single lineage: superseding a frozen protocol requires human "
                            "decision recorded in a decision doc. Move the existing snapshot "
                            "aside or delete it and re-run."
                        )
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1)

    output_dir = resolved.run.data_root / "curated" / "d3-protocol" / date
    output_path = output_dir / "protocol.json"
    _refuse_if_curated_output_already_exists(
        output_path, config=resolved_config, git_state=git_state
    )

    protocol_source_sha = sha256_file(protocol_config)
    source_path, source_root = manifests.root_relative_path(protocol_config, config=resolved_config)
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

    # Atomic finalize via .tmp directory
    tmp_dir = output_dir.parent / f"{output_dir.name}.tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_output_path = tmp_dir / "protocol.json"

    tmp_output_path.write_text(
        json.dumps(
            {
                "protocol": d3_protocol.canonical_protocol(protocol),
                "protocol_digest": digest,
            },
            indent=2,
            sort_keys=True,
        )
    )

    try:
        manifests.write_run_manifest(
            output=tmp_output_path,
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

    # Re-check existing output before rename
    if output_path.exists():
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{output_path} already exists -- this curated artefact was already "
                        "built by an earlier run. Refusing before rename."
                    ),
                    "output_path": str(output_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # Rename tmp directory into place (atomic)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_dir, output_dir)

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


def _capture_asset_http_headers(
    dataset: rasterio.DatasetReader, href: str
) -> tuple[str | None, str | None]:
    """Best-effort HTTP ETag/Last-Modified for `href`.

    A local-file href (the only kind any fixture ever uses) never touches
    the HTTP layer at all -- both fields stay `None`, disclosed as null
    rather than a fabricated "unchanged" sentinel. A genuine `http(s)` href
    is read through GDAL's `/vsicurl/` layer, which -- when it populated
    one -- caches the response headers on the dataset under the `HEADERS`
    metadata domain; a driver/version that never populated it leaves both
    fields `None`, exactly like a local file.
    """
    if not href.startswith(("http://", "https://")):
        return None, None
    try:
        headers = dataset.tags(ns="HEADERS")
    except Exception:  # noqa: BLE001 -- best-effort only, never a run refusal
        return None, None
    return headers.get("ETag"), headers.get("Last-Modified")


def _decode_d3_bands(band_values: Mapping[str, np.ndarray], *, kind: str) -> dict[str, np.ndarray]:
    """`dea_raster`'s decode rule for `kind` ("geomedian" | "fc"), applied
    to every band array. FC's out-of-range count is intentionally not
    returned here -- Phase A/B only need computability and metric values;
    a future FC-specific disclosure can read `dea_raster.decode_fc` directly."""
    if kind == "geomedian":
        return {band: dea_raster.decode_geomedian(values) for band, values in band_values.items()}
    return {band: dea_raster.decode_fc(values)[0] for band, values in band_values.items()}


def _read_footprint_year_bands(
    *,
    source_id: str,
    kind: str,
    year: int,
    touched_tiles: Sequence[str],
    members: Sequence[d3_inputs.Member],
    item_index: Mapping[tuple[str, str, int], Mapping[str, Any]],
    phase: str,
) -> tuple[dict[str, np.ndarray], list[dict[str, object]]]:
    """Read this footprint's `members` from every band asset the
    `(source_id, year)` items on `touched_tiles` carry.

    One windowed read per (band, tile) via `d3_inputs.read_member_values`;
    returns the RAW (undecoded) band arrays keyed by asset key, plus one
    `D3_EXTRACTION_ASSETS_SCHEMA` row per (tile, band) asset actually
    opened, phase-tagged so Phase B can refuse an asset whose ETag changed
    since Phase A read it.
    """
    hrefs_by_tile: dict[str, dict[str, str]] = {}
    for tile_id in touched_tiles:
        try:
            hrefs_by_tile[tile_id] = d3_inputs.resolve_band_hrefs(
                item_index[(source_id, tile_id, year)], kind=kind
            )
        except d3_inputs.D3InputsError as exc:
            raise d3_inputs.D3InputsError(
                f"failed to resolve raster band asset(s) for "
                f"(source_id={source_id!r}, tile_id={tile_id!r}, year={year}): {exc}"
            ) from exc
    band_keys = sorted(next(iter(hrefs_by_tile.values())).keys())
    band_values: dict[str, np.ndarray] = {}
    extraction_rows: list[dict[str, object]] = []
    for band_key in band_keys:
        with contextlib.ExitStack() as stack:
            datasets: dict[str, Any] = {}
            for tile_id in touched_tiles:
                href = hrefs_by_tile[tile_id][band_key]
                try:
                    dataset = stack.enter_context(rasterio.open(href))
                except (rasterio.errors.RasterioError, OSError) as exc:
                    raise d3_inputs.D3InputsError(
                        f"failed to open raster asset for "
                        f"(source_id={source_id!r}, tile_id={tile_id!r}, year={year}, "
                        f"band={band_key!r}, href={href!r}): {exc}"
                    ) from exc
                datasets[tile_id] = dataset
                etag, last_modified = _capture_asset_http_headers(dataset, href)
                extraction_rows.append(
                    {
                        "source_id": source_id,
                        "tile_id": tile_id,
                        "year": year,
                        "asset_key": band_key,
                        "href": href,
                        "etag": etag,
                        "last_modified": last_modified,
                        "phase": phase,
                    }
                )
            try:
                band_values[band_key] = d3_inputs.read_member_values(datasets, members)
            except (rasterio.errors.RasterioError, OSError) as exc:
                raise d3_inputs.D3InputsError(
                    f"failed reading raster asset(s) for "
                    f"(source_id={source_id!r}, tile_id(s)={sorted(datasets)!r}, "
                    f"year={year}, band={band_key!r}): {exc}"
                ) from exc
    return band_values, extraction_rows


def _run_reads_in_serial_order[T](jobs: Sequence[Callable[[], T]], *, workers: int) -> list[T]:
    """Run `jobs` concurrently on a thread pool and return their results in
    SUBMISSION order. If any job raised, the exception of the FIRST failing
    job in submission order is re-raised (after every job has finished), so
    refusal text never depends on thread timing. `workers=1` takes the same
    path. Used for raster reads, which are round-trip-latency bound."""
    if workers < 1:
        raise ValueError(f"read_workers must be >= 1, got {workers}")
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        futures = [pool.submit(job) for job in jobs]
        results: list[T] = []
        first_error: BaseException | None = None
        for future in futures:
            try:
                results.append(future.result())
            except BaseException as exc:  # noqa: BLE001 -- re-raised below in serial order
                if first_error is None:
                    first_error = exc
    if first_error is not None:
        raise first_error
    return results


def _footprint_pixel_support(
    items_by_source: Mapping[str, Sequence[Mapping[str, Any]]],
    footprint_geometry: Mapping[str, Any],
) -> tuple[
    dict[str, int],
    dict[str, tuple[d3_inputs.Member, ...]],
    dict[str, set[str]],
    dict[str, str],
]:
    """Decision 7: pixel support per footprint against each intersecting
    tile's ACTUAL grid, read from a catalogue item asset.

    Shared by `build_d3_inputs_cmd` (which computes it over every Tier 1
    footprint, to select and stratify) and `extract_trajectories_cmd` (which
    computes it only over the footprints the extraction touches) -- lifted
    here rather than left inline so a trajectory row's `effective_pixel_
    support_px` and D3's own simulated support are computed by the exact
    same code, never two paths that could quietly diverge.

    First discovers one tile grid per (collection, tile) by opening ONE
    band asset per tile (whichever item's assets resolve first for that
    tile), then, for every `footprint_geometry` entry, intersects it against
    every tile whose bounds it could plausibly overlap and unions the
    member pixel indices across tiles.

    Returns `(effective_support, footprint_members, footprint_tiles,
    support_not_computed_reason)`: the first three keyed by `maus_id`, over
    only the footprints with non-empty support; the fourth also keyed by
    `maus_id`, naming why a `footprint_geometry` entry produced none (the
    caller is responsible for merging this into its own broader disclosure
    dict, since a footprint dropped upstream -- missing geometry, outside
    every RDC region -- never reaches this function at all).

    Refuses (structured JSON, exit 1) on a raster asset that fails to open
    during tile-grid discovery, or on a `pixel_support.PixelSupportError`.
    """
    tile_grids: dict[str, pixel_support.GridSpec] = {}
    tile_bounds: dict[str, tuple[float, float, float, float]] = {}
    for source_id, items in items_by_source.items():
        kind = d3_inputs.D3_COLLECTION_KIND.get(source_id)
        if kind is None:
            continue
        for item in items:
            properties = item.get("properties") or {}
            tile_id = str(properties.get("odc:region_code") or "")
            if not tile_id or tile_id in tile_grids:
                continue
            try:
                hrefs = d3_inputs.resolve_band_hrefs(item, kind=kind)
            except d3_inputs.D3InputsError:
                continue
            href = next(iter(hrefs.values()))
            stamp = str(properties.get("datetime") or "")
            item_year = int(stamp[:4]) if len(stamp) >= 4 and stamp[:4].isdigit() else None
            try:
                with rasterio.open(href) as dataset:
                    tile_grids[tile_id] = d3_inputs.grid_spec_from_dataset(dataset, tile_id=tile_id)
                    bounds = dataset.bounds
                    tile_bounds[tile_id] = (bounds.left, bounds.bottom, bounds.right, bounds.top)
            except (rasterio.errors.RasterioError, OSError) as exc:
                typer.echo(
                    json.dumps(
                        {
                            "refusal": (
                                "failed to open raster asset during tile-grid discovery "
                                f"for (source_id={source_id!r}, tile_id={tile_id!r}, "
                                f"year={item_year!r}, href={href!r}): {exc}"
                            )
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                raise typer.Exit(1) from None

    effective_support: dict[str, int] = {}
    footprint_members: dict[str, tuple[d3_inputs.Member, ...]] = {}
    footprint_tiles: dict[str, set[str]] = {}
    support_not_computed_reason: dict[str, str] = {}
    try:
        for maus_id, geometry in footprint_geometry.items():
            minx, miny, maxx, maxy = geometry.bounds
            member_set: set[d3_inputs.Member] = set()
            touched_set: set[str] = set()
            for tile_id, grid in tile_grids.items():
                tminx, tminy, tmaxx, tmaxy = tile_bounds[tile_id]
                if maxx < tminx or minx > tmaxx or maxy < tminy or miny > tmaxy:
                    continue
                support = pixel_support.build_pixel_support(geometry, crosswalk.TARGET_CRS, grid)
                if support is None or support.effective_pixel_support_px == 0:
                    continue
                touched_set.add(tile_id)
                member_set.update((tile_id, r, c) for r, c in support.member_indices)
            if member_set:
                footprint_members[maus_id] = tuple(sorted(member_set))
                footprint_tiles[maus_id] = touched_set
                effective_support[maus_id] = len(member_set)
            else:
                support_not_computed_reason[maus_id] = (
                    "no pixel centre of any intersecting tile is covered by this footprint"
                )
    except pixel_support.PixelSupportError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    return effective_support, footprint_members, footprint_tiles, support_not_computed_reason


@app.command("build-d3-inputs")
def build_d3_inputs_cmd(
    config: Path = ConfigOption,
    protocol_config: Path = ProtocolConfigOption,
    date: str = DateOption,
    read_workers: int = ReadWorkersOption,
) -> None:
    """Build the D3 reduced-support simulation inputs from real DEA rasters.

    The full pipeline (D13 task D3): footprint strata (region/commodity/
    shape) over the Tier 1 population, ACTUAL pixel support per footprint
    against each intersecting tile's ACTUAL grid, one selected catalogue
    item per (collection, tile, year), Phase A validity-only reads over
    every candidate footprint (support >= 144 AND at least one epoch
    covered), stratum adequacy and selection over the frozen 54-stratum
    space, Phase B value reads and 100-replicate reduced-support simulation
    for SELECTED footprints only, and per-footprint Spearman rank
    correlation between full- and reduced-support metric series.

    Every gate mirrors `derive-dea-volume`'s discipline (digest-verified
    latest curated inputs, a Maus-snapshot sha256 equality gate) plus two
    the frozen D3 protocol adds: single-lineage frozen-protocol lookup with
    a recomputed-digest drift check, and a code/procedures-text consistency
    check (`d3_inputs.check_procedures_consistency`) -- the protocol digest
    alone proves the YAML has not changed since freezing, not that the CODE
    consuming it still agrees with what the YAML documents.

    Writes FIVE tables under `<data_root>/curated/d3-inputs/<date>/`
    (`support_inputs.parquet`, `support_spearman.parquet`, `footprint_support.
    parquet`, `stratum_summary.parquet`, `extraction_assets.parquet`),
    assembled in a `.tmp` sibling directory and `os.replace`d into place
    only once every table and its run manifest wrote cleanly -- a partial
    five-table write must never be mistaken for a completed run.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    # GATE 0 -- preflight existing-output, BEFORE any snapshot or raster
    # access (not even the frozen protocol is read yet).
    output_dir = data_root / "curated" / "d3-inputs" / date
    if output_dir.exists():
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{output_dir} already exists -- this curated artefact was already "
                        "built by an earlier run. Refusing before any snapshot or raster "
                        "access. Move the existing output directory aside, or choose a "
                        "different --date, to build again."
                    ),
                    "output_dir": str(output_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # GATE 1 -- the frozen protocol: exactly one dated lineage, digest
    # verified, recomputed digest must match (drift check), and the
    # procedures text must still name this module's own frozen constants.
    d3_protocol_root = data_root / "curated" / "d3-protocol"
    dated_protocol_dirs = (
        sorted(d3_protocol_root.glob("????-??-??")) if d3_protocol_root.exists() else []
    )
    if not dated_protocol_dirs:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"no frozen D3 protocol under {d3_protocol_root} -- run "
                        "freeze-d3-protocol first"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if len(dated_protocol_dirs) > 1:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"curated/d3-protocol has {len(dated_protocol_dirs)} dated "
                        f"directories {[d.name for d in dated_protocol_dirs]} -- single "
                        "lineage violated: a frozen protocol may never be superseded "
                        "silently. Move all but one dated directory aside."
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    protocol_dir = dated_protocol_dirs[0]
    protocol_artifact_path = protocol_dir / "protocol.json"
    _digest_verified_manifest(protocol_artifact_path)
    frozen_digest = json.loads(protocol_artifact_path.read_text(encoding="utf-8"))[
        "protocol_digest"
    ]

    try:
        protocol = d3_protocol.load_protocol(protocol_config)
    except (d3_protocol.D3ProtocolError, OSError, yaml.YAMLError) as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    recomputed_digest = d3_protocol.protocol_digest(protocol)
    if recomputed_digest != frozen_digest:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"protocol drift: --protocol-config ({protocol_config}) recomputes "
                        f"digest {recomputed_digest[:12]}..., the frozen artefact at "
                        f"{protocol_artifact_path} records {frozen_digest[:12]}... -- the "
                        "config changed after freeze-d3-protocol ran. No accuracy result may "
                        "change sample definitions or criteria after freezing."
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    try:
        d3_inputs.check_procedures_consistency(protocol.procedures)
    except d3_inputs.D3InputsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    # GATE 2 -- the enriched register: latest, digest-verified, must carry
    # register.DEA_COVERAGE_COLUMNS.
    try:
        register_dir = _latest_curated_dated_dir(
            data_root / "curated" / "register", label="curated/register"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    register_path = register_dir / "register.parquet"
    register_manifest = _digest_verified_manifest(register_path)
    register_df = read_table(register_path)
    if any(column not in register_df.columns for column in register.DEA_COVERAGE_COLUMNS):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "latest curated register is not DEA-enriched -- run "
                        "build-dea-coverage first"
                    ),
                    "register_path": str(register_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # GATE 3 -- the crosswalk: latest, digest-verified; Tier 1 population.
    try:
        crosswalk_dir = _latest_curated_dated_dir(
            data_root / "curated" / "crosswalk", label="curated/crosswalk"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    crosswalk_path = crosswalk_dir / "crosswalk.parquet"
    crosswalk_manifest = _digest_verified_manifest(crosswalk_path)
    crosswalk_df = read_table(crosswalk_path)
    tier1_df = crosswalk.tier1_population(crosswalk_df)

    # GATE 4 -- footprint areas: latest, digest-verified; Maus sha256
    # equality gate between the crosswalk and footprint-areas manifests
    # (mirrors `derive-dea-volume`'s identical check verbatim).
    try:
        footprints_dir = _latest_curated_dated_dir(
            data_root / "curated" / "maus_footprint_areas",
            label="curated/maus_footprint_areas",
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    footprints_path = footprints_dir / "footprint_areas.parquet"
    footprints_manifest = _digest_verified_manifest(footprints_path)

    maus_licence_id = licence.SOURCES["maus_v2"].licence_id
    crosswalk_maus_input = next(
        (
            asset
            for asset in crosswalk_manifest.get("inputs", [])
            if asset.get("licence") == maus_licence_id
        ),
        None,
    )
    if crosswalk_maus_input is None:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{crosswalk_path}'s manifest carries no Maus input (licence "
                        f"{maus_licence_id!r}) -- cannot verify it was built from the "
                        "same Maus snapshot as the footprint artefact"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    crosswalk_maus_sha256 = crosswalk_maus_input["sha256"]
    try:
        footprints_maus_sha256 = footprints_manifest["resolved_args"]["maus_gpkg_sha256"]
    except KeyError:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{footprints_path}'s manifest does not record "
                        "resolved_args.maus_gpkg_sha256"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    if crosswalk_maus_sha256 != footprints_maus_sha256:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"crosswalk ({crosswalk_path}) and footprint-areas "
                        f"({footprints_path}) were built from DIFFERENT Maus GeoPackage "
                        f"snapshots -- crosswalk records {crosswalk_maus_sha256[:12]}..., "
                        f"footprints records {footprints_maus_sha256[:12]}.... maus_id is "
                        "derived from clipped geometry, so a join on maus_id alone cannot "
                        "detect this; refusing rather than silently mixing two snapshots' "
                        "geometry."
                    ),
                    "crosswalk_maus_gpkg_sha256": crosswalk_maus_sha256,
                    "footprints_maus_gpkg_sha256": footprints_maus_sha256,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # GATE 5 -- the RDC regions snapshot: latest raw snapshot, integrity
    # verified, loaded via `wa_regions.load_regions`.
    try:
        regions_dir = register.latest_snapshot(data_root, "wa_rdc_regions")
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    _verify_snapshot_or_refuse(
        regions_dir, source_id="wa_rdc_regions", required_files=(_RDC_REGIONS_FILENAME,)
    )
    regions_path = regions_dir / _RDC_REGIONS_FILENAME
    try:
        regions_gdf = wa_regions.load_regions(regions_path)
    except wa_regions.RegionExtractError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    # GATE 6 -- the Maus geometry snapshot: verified, AND its sha256 must
    # ALSO equal the crosswalk manifest's own Maus digest (gate 4 already
    # tied the footprint-areas artefact to that same digest).
    try:
        maus_snapshot_dir = register.latest_snapshot(data_root, "maus_v2")
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    _verify_snapshot_or_refuse(
        maus_snapshot_dir, source_id="maus_v2", required_files=("wa_extract.gpkg",)
    )
    maus_path = maus_snapshot_dir / "wa_extract.gpkg"
    maus_gpkg_sha256 = sha256_file(maus_path)
    if maus_gpkg_sha256 != crosswalk_maus_sha256:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"latest maus_v2 raw snapshot ({maus_path}) hashes "
                        f"{maus_gpkg_sha256[:12]}..., but the crosswalk's manifest records "
                        f"Maus sha256 {crosswalk_maus_sha256[:12]}... -- the latest raw Maus "
                        "snapshot is not the one the crosswalk was built from"
                    ),
                    "maus_gpkg_sha256": maus_gpkg_sha256,
                    "crosswalk_maus_gpkg_sha256": crosswalk_maus_sha256,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    try:
        maus_source_gdf = gpd.read_file(maus_path)
        maus_gdf = maus_source_gdf[["maus_id", "geometry"]].to_crs(crosswalk.TARGET_CRS)
    except (pyogrio.errors.DataSourceError, OSError) as exc:
        typer.echo(
            json.dumps({"refusal": str(exc), "maus_gpkg": str(maus_path)}, indent=2, sort_keys=True)
        )
        raise typer.Exit(1) from None
    except KeyError as exc:
        typer.echo(
            json.dumps(
                {
                    "refusal": f"wa_extract.gpkg is missing the expected column {exc}",
                    "maus_gpkg": str(maus_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    # GATE 7 -- the DEA STAC catalogue snapshot named by the enriched
    # register's own manifest.
    try:
        catalogue_date = register_manifest["resolved_args"]["catalogue_date"]
    except KeyError:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{register_path}'s manifest does not record "
                        "resolved_args.catalogue_date -- not a build-dea-coverage output"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    catalogue_dir = data_root / "raw" / "dea_stac" / catalogue_date
    _verify_snapshot_or_refuse(
        catalogue_dir, source_id="dea_stac", required_files=("catalogue_summary.json",)
    )
    items_by_source = _load_dea_items(catalogue_dir)

    # --- Footprint strata (decisions 9-10): region / commodity / shape. ---
    maus_geom_by_id: dict[str, Any] = dict(
        zip(maus_gdf["maus_id"].astype(str), maus_gdf.geometry, strict=True)
    )
    tier1_maus_ids = sorted(set(tier1_df["maus_id"].dropna().astype(str)))

    footprint_geometry: dict[str, Any] = {}
    support_not_computed_reason: dict[str, str] = {}
    for maus_id in tier1_maus_ids:
        geometry = maus_geom_by_id.get(maus_id)
        if geometry is None:
            support_not_computed_reason[maus_id] = "maus_id absent from the latest Maus snapshot"
        elif geometry.is_empty:
            support_not_computed_reason[maus_id] = "empty geometry"
        elif not geometry.is_valid:
            support_not_computed_reason[maus_id] = "invalid geometry"
        else:
            footprint_geometry[maus_id] = geometry

    try:
        shape_class_by_id = {
            maus_id: d3_protocol.shape_class(d3_inputs.footprint_compactness(geometry), protocol)
            for maus_id, geometry in footprint_geometry.items()
        }
    except (d3_inputs.D3InputsError, d3_protocol.D3ProtocolError) as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    region_by_id: dict[str, str] = {}
    region_disclosure: dict[str, Any] = {
        "n_ambiguous_boundary_points": 0,
        "n_footprints_outside_rdc_regions": 0,
        "footprints_outside_rdc_regions": [],
    }
    if footprint_geometry:
        points_gdf = gpd.GeoDataFrame(
            {"site_id": list(footprint_geometry.keys())},
            geometry=[geometry.representative_point() for geometry in footprint_geometry.values()],
            crs=crosswalk.TARGET_CRS,
        )
        try:
            points_gdf, outside_ids = d3_protocol.partition_uncovered_points(
                points_gdf, regions_gdf
            )
        except d3_protocol.D3ProtocolError as exc:
            typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
            raise typer.Exit(1) from None
        # Decision 2026-08-21: exclude-with-disclosure, bounded by the ceiling.
        # `n_for_ceiling` is Tier-1 footprints with usable Maus geometry
        # (`footprint_geometry`'s size at this point: `tier1_maus_ids` minus
        # the missing/empty/invalid-geometry exclusions above). This is
        # DELIBERATELY not `n_candidate_footprints` (support >= 144px and
        # >=1 epoch year, computed later from `footprint_support_df`) --
        # the ceiling must be checkable before support is derived at all, so
        # it is measured against the broader population the region split
        # actually partitions, not the narrower "candidate" population.
        n_for_ceiling = len(footprint_geometry)
        if outside_ids and len(outside_ids) / n_for_ceiling > d3_protocol.MAX_UNCOVERED_FRACTION:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"{len(outside_ids)} of {n_for_ceiling} Tier-1 footprints with "
                            f"usable Maus geometry ({len(outside_ids) / n_for_ceiling:.1%}) lie "
                            f"outside every RDC polygon, above the "
                            f"{d3_protocol.MAX_UNCOVERED_FRACTION:.0%} ceiling -- check the "
                            f"region snapshot: {outside_ids}"
                        )
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1) from None
        for maus_id in outside_ids:
            footprint_geometry.pop(maus_id, None)
            shape_class_by_id.pop(maus_id, None)
            support_not_computed_reason[maus_id] = d3_protocol.OUTSIDE_RDC_REGIONS_REASON
        region_disclosure["n_footprints_outside_rdc_regions"] = len(outside_ids)
        region_disclosure["footprints_outside_rdc_regions"] = outside_ids
        if not points_gdf.empty:
            try:
                region_series, ambiguity = d3_protocol.assign_regions(
                    points_gdf, regions_gdf, protocol
                )
            except d3_protocol.D3ProtocolError as exc:
                typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
                raise typer.Exit(1) from None
            region_disclosure.update(ambiguity)
            region_by_id = dict(zip(points_gdf["site_id"], region_series, strict=True))

    try:
        commodity_by_id, commodity_disclosure = d3_inputs.assign_footprint_commodities(
            tier1_maus_ids, tier1_df, register_df, protocol
        )
    except d3_protocol.D3ProtocolError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    # --- Support (decision 7): pixel support per footprint against each
    # intersecting tile's ACTUAL grid, read from a catalogue item asset
    # (`_footprint_pixel_support`, shared with `extract_trajectories_cmd` so
    # a trajectory row's `effective_pixel_support_px` is the SAME number D3
    # thresholded on). ---
    effective_support, footprint_members, footprint_tiles, footprint_support_reasons = (
        _footprint_pixel_support(items_by_source, footprint_geometry)
    )
    support_not_computed_reason.update(footprint_support_reasons)

    # --- Item selection rule (frozen in procedures.item_selection). ---
    touched_tile_ids = sorted({t for tiles in footprint_tiles.values() for t in tiles})
    try:
        item_index = d3_inputs.select_catalogue_items(items_by_source, touched_tile_ids)
    except d3_inputs.D3InputsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    years_by_source_tile: dict[tuple[str, str], set[int]] = {}
    for source_id, tile_id, year in item_index:
        years_by_source_tile.setdefault((source_id, tile_id), set()).add(year)

    def _epoch_covered_years(touched_tiles: Sequence[str]) -> set[int]:
        covered: set[int] = set()
        for source_id in d3_inputs.D3_COLLECTION_KIND:
            common: set[int] | None = None
            for tile_id in touched_tiles:
                tile_years = years_by_source_tile.get((source_id, tile_id), set())
                common = tile_years if common is None else (common & tile_years)
            if common:
                covered |= common
        return covered

    # --- Phase A (validity): candidate footprints only. ---
    _GEOMEDIAN_SOURCES = ("dea_gm_ls5t", "dea_gm_ls7e", "dea_gm_ls8cls9c")
    computable_by_footprint: dict[str, dict[int, dict[str, bool]]] = {}
    n_epoch_covered_by_id: dict[str, int] = {}
    n_full_support_by_id: dict[str, int] = {}
    phase_a_extraction_rows: list[dict[str, object]] = []
    n_footprint_years_not_computable = 0

    for maus_id, members in footprint_members.items():
        touched = sorted(footprint_tiles[maus_id])
        candidate_years = _epoch_covered_years(touched)
        n_epoch_covered_by_id[maus_id] = len(candidate_years)
        n_full = 0
        computable_by_footprint[maus_id] = {}
        if effective_support[maus_id] >= d3_protocol.MIN_FULL_SUPPORT_PX and candidate_years:
            read_keys: list[tuple[int, str, str]] = []
            read_jobs: list[
                Callable[[], tuple[dict[str, np.ndarray], list[dict[str, object]]]]
            ] = []
            for year in sorted(candidate_years):
                for source_id, kind in d3_inputs.D3_COLLECTION_KIND.items():
                    if not all((source_id, tile_id, year) in item_index for tile_id in touched):
                        continue
                    read_keys.append((year, source_id, kind))
                    read_jobs.append(
                        functools.partial(
                            _read_footprint_year_bands,
                            source_id=source_id,
                            kind=kind,
                            year=year,
                            touched_tiles=touched,
                            members=members,
                            item_index=item_index,
                            phase="a",
                        )
                    )
            try:
                read_results = _run_reads_in_serial_order(read_jobs, workers=read_workers)
            except (rasterio.errors.RasterioError, OSError, d3_inputs.D3InputsError) as exc:
                typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
                raise typer.Exit(1) from None
            by_year_source: dict[int, dict[str, bool]] = {}
            for (year, source_id, kind), (raw_bands, extraction_rows) in zip(
                read_keys, read_results, strict=True
            ):
                phase_a_extraction_rows.extend(extraction_rows)
                decoded = _decode_d3_bands(raw_bands, kind=kind)
                by_year_source.setdefault(year, {})[source_id] = d3_inputs.year_computable(
                    decoded,
                    kind=kind,
                    min_valid_member_fraction=protocol.adequacy.min_valid_member_fraction,
                )
            for year in sorted(candidate_years):
                by_source = by_year_source.get(year, {})
                computable_by_footprint[maus_id][year] = by_source
                fc_ok = by_source.get("dea_fc_pc", False)
                gm_ok = any(by_source.get(s, False) for s in _GEOMEDIAN_SOURCES)
                if fc_ok and gm_ok:
                    n_full += 1
                else:
                    n_footprint_years_not_computable += 1
        n_full_support_by_id[maus_id] = n_full

    # --- Footprint strata frame -- one row per Tier 1 footprint. ---
    footprint_rows: list[dict[str, object]] = []
    for maus_id in tier1_maus_ids:
        support_px = effective_support.get(maus_id)
        n_epoch = n_epoch_covered_by_id.get(maus_id, 0)
        footprint_rows.append(
            {
                "maus_id": maus_id,
                "region": region_by_id.get(maus_id),
                "commodity_group": commodity_by_id.get(maus_id),
                "shape_class": shape_class_by_id.get(maus_id),
                "effective_pixel_support_px": support_px,
                "support_not_computed_reason": support_not_computed_reason.get(maus_id),
                "n_epoch_covered_years": n_epoch,
                "n_full_support_years": n_full_support_by_id.get(maus_id, 0),
                "candidate": bool(
                    support_px is not None
                    and support_px >= d3_protocol.MIN_FULL_SUPPORT_PX
                    and n_epoch > 0
                ),
            }
        )
    footprints_frame = pd.DataFrame(footprint_rows)
    stratified = footprints_frame.dropna(subset=["region", "commodity_group", "shape_class"])

    # --- Adequacy + selection (full 54-stratum space). ---
    adequacy = d3_protocol.stratum_adequacy(stratified, protocol)
    selected = d3_protocol.select_stratum_footprints(stratified, protocol)
    selected_maus_ids = sorted({mid for members in selected.values() for mid in members})

    input_digests_json = d3_inputs.canonical_input_digests(
        catalogue=sha256_file(
            catalogue_dir / (snapshots.SHA256SUMS_FILENAME + manifests.MANIFEST_SUFFIX)
        ),
        register=sha256_file(Path(str(register_path) + manifests.MANIFEST_SUFFIX)),
        crosswalk=sha256_file(Path(str(crosswalk_path) + manifests.MANIFEST_SUFFIX)),
        footprint_areas=sha256_file(Path(str(footprints_path) + manifests.MANIFEST_SUFFIX)),
        maus=sha256_file(
            maus_snapshot_dir / (snapshots.SHA256SUMS_FILENAME + manifests.MANIFEST_SUFFIX)
        ),
        regions=sha256_file(
            regions_dir / (snapshots.SHA256SUMS_FILENAME + manifests.MANIFEST_SUFFIX)
        ),
        protocol=sha256_file(Path(str(protocol_artifact_path) + manifests.MANIFEST_SUFFIX)),
    )

    total_counts = (
        stratified.groupby(["region", "commodity_group", "shape_class"])["maus_id"].nunique()
        if not stratified.empty
        else pd.Series(dtype="int64")
    )
    stratum_rows: list[dict[str, object]] = []
    for region in protocol.regions:
        for commodity_group in protocol.commodity_groups:
            for shape_cls in ("elongated", "intermediate", "compact"):
                stratum = (region, commodity_group, shape_cls)
                info = adequacy[stratum]
                stratum_rows.append(
                    {
                        "region": region,
                        "commodity_group": commodity_group,
                        "shape_class": shape_cls,
                        "n_footprints": int(total_counts.get(stratum, 0)),
                        "n_adequate_footprints": cast(int, info["n_footprints_meeting_years"]),
                        "adequate": bool(info["adequate"]),
                        "n_selected": len(selected.get(stratum, ())),
                        "protocol_digest": frozen_digest,
                        "input_manifest_digests": input_digests_json,
                    }
                )
    stratum_summary_df = pd.DataFrame(stratum_rows)
    n_strata_adequate = int(stratum_summary_df["adequate"].sum())
    n_strata_inadequate = int((~stratum_summary_df["adequate"]).sum())

    footprint_support_rows = [
        {
            **row,
            "selected": row["maus_id"] in selected_maus_ids,
            "protocol_digest": frozen_digest,
            "input_manifest_digests": input_digests_json,
        }
        for row in footprint_rows
    ]
    footprint_support_df = pd.DataFrame(footprint_support_rows)
    n_candidate_footprints = int(footprint_support_df["candidate"].sum())
    n_selected_footprints = int(footprint_support_df["selected"].sum())
    n_footprints_support_not_computed = int(
        footprint_support_df["effective_pixel_support_px"].isna().sum()
    )

    # --- Phase B (values): SELECTED footprints only. ---
    phase_a_etag_by_asset = {
        (r["source_id"], r["tile_id"], r["year"], r["asset_key"]): r["etag"]
        for r in phase_a_extraction_rows
    }
    support_inputs_rows: list[dict[str, object]] = []
    spearman_rows: list[dict[str, object]] = []
    phase_b_extraction_rows: list[dict[str, object]] = []
    n_footprint_years_simulated = 0
    n_spearman_not_computable = 0

    for maus_id in selected_maus_ids:
        touched = sorted(footprint_tiles[maus_id])
        members = footprint_members[maus_id]
        region = region_by_id[maus_id]
        commodity_group = commodity_by_id[maus_id]
        shape_cls = shape_class_by_id[maus_id]

        full_support_years = sorted(
            year
            for year, by_source in computable_by_footprint[maus_id].items()
            if by_source.get("dea_fc_pc", False)
            and any(by_source.get(s, False) for s in _GEOMEDIAN_SOURCES)
        )
        reduced_by_source_metric_support: dict[tuple[str, str, int], dict[int, list[float]]] = {}
        full_value_by_key: dict[tuple[int, str, str], float] = {}

        phase_b_read_keys: list[tuple[int, str, str]] = []
        phase_b_read_jobs: list[
            Callable[[], tuple[dict[str, np.ndarray], list[dict[str, object]]]]
        ] = []
        for year in full_support_years:
            by_source = computable_by_footprint[maus_id][year]
            for source_id, kind in d3_inputs.D3_COLLECTION_KIND.items():
                if not by_source.get(source_id, False):
                    continue
                phase_b_read_keys.append((year, source_id, kind))
                phase_b_read_jobs.append(
                    functools.partial(
                        _read_footprint_year_bands,
                        source_id=source_id,
                        kind=kind,
                        year=year,
                        touched_tiles=touched,
                        members=members,
                        item_index=item_index,
                        phase="b",
                    )
                )
        try:
            phase_b_read_results = _run_reads_in_serial_order(
                phase_b_read_jobs, workers=read_workers
            )
        except (rasterio.errors.RasterioError, OSError, d3_inputs.D3InputsError) as exc:
            typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
            raise typer.Exit(1) from None

        for (year, source_id, kind), (raw_bands, extraction_rows) in zip(
            phase_b_read_keys, phase_b_read_results, strict=True
        ):
            for row in extraction_rows:
                key = (row["source_id"], row["tile_id"], row["year"], row["asset_key"])
                prior_etag = phase_a_etag_by_asset.get(key)
                if prior_etag is not None and row["etag"] is not None and prior_etag != row["etag"]:
                    typer.echo(
                        json.dumps(
                            {
                                "refusal": (
                                    f"asset {row['href']} ETag changed between Phase A "
                                    f"and Phase B: {prior_etag} -> {row['etag']}"
                                )
                            },
                            indent=2,
                            sort_keys=True,
                        )
                    )
                    raise typer.Exit(1)
            phase_b_extraction_rows.extend(extraction_rows)
            decoded = _decode_d3_bands(raw_bands, kind=kind)
            result = d3_inputs.simulate_footprint_year(
                maus_id=maus_id,
                year=year,
                source_id=source_id,
                members=members,
                band_values=decoded,
                kind=kind,
                supports=protocol.supports,
                replicates=protocol.replicates,
                protocol_digest=frozen_digest,
                min_valid_member_fraction=protocol.adequacy.min_valid_member_fraction,
            )
            if result is None:
                continue
            rows, reduced_series = result
            n_footprint_years_simulated += 1
            for row in rows:
                full_value_by_key[(year, source_id, cast(str, row["metric_id"]))] = cast(
                    float, row["full_value"]
                )
                support_inputs_rows.append(
                    {
                        **row,
                        "region": region,
                        "commodity_group": commodity_group,
                        "shape_class": shape_cls,
                        "input_manifest_digests": input_digests_json,
                    }
                )
            for (metric_id, support_px), values in reduced_series.items():
                reduced_by_source_metric_support.setdefault((source_id, metric_id, support_px), {})[
                    year
                ] = values

        for (source_id, metric_id, support_px), by_year in reduced_by_source_metric_support.items():
            years = sorted(by_year)
            if len(years) < d3_inputs.MIN_SPEARMAN_YEARS:
                continue
            full_series = pd.Series(
                [full_value_by_key[(year, source_id, metric_id)] for year in years]
            )
            for replicate in range(protocol.replicates):
                reduced_series_r = pd.Series([by_year[year][replicate] for year in years])
                rho = d3_inputs.spearman(full_series, reduced_series_r)
                if rho is None:
                    n_spearman_not_computable += 1
                    continue
                spearman_rows.append(
                    {
                        "maus_id": maus_id,
                        "source_id": source_id,
                        "metric_id": metric_id,
                        "support_px": support_px,
                        "replicate": replicate,
                        "spearman": rho,
                        "n_years": len(years),
                        "protocol_digest": frozen_digest,
                        "input_manifest_digests": input_digests_json,
                    }
                )

    support_inputs_df = pd.DataFrame(
        support_inputs_rows, columns=list(d3_inputs.D3_SUPPORT_INPUTS_SCHEMA.names)
    )
    spearman_df = pd.DataFrame(spearman_rows, columns=list(d3_inputs.D3_SPEARMAN_SCHEMA.names))
    extraction_assets_df = pd.DataFrame(
        phase_a_extraction_rows + phase_b_extraction_rows,
        columns=list(d3_inputs.D3_EXTRACTION_ASSETS_SCHEMA.names),
    )

    # --- Assemble outputs atomically: `.tmp` dir, five tables + manifests,
    # then `os.replace` (decision 15). ---
    maus_source = licence.SOURCES["maus_v2"]
    regions_source = licence.SOURCES["wa_rdc_regions"]
    input_assets = [
        SourceAsset(
            uri=str(protocol_artifact_path),
            sha256=sha256_file(protocol_artifact_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(protocol_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(register_path),
            sha256=sha256_file(register_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(register_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(crosswalk_path),
            sha256=sha256_file(crosswalk_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(crosswalk_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(footprints_path),
            sha256=sha256_file(footprints_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(footprints_dir.name),
            licence="CC-BY-SA-4.0",
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(maus_path),
            sha256=maus_gpkg_sha256,
            collection=None,
            snapshot_date=dt_date.fromisoformat(maus_snapshot_dir.name),
            licence=maus_source.licence_id,
            redistribute_public=maus_source.redistribute_public,
        ),
        SourceAsset(
            uri=str(regions_path),
            sha256=sha256_file(regions_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(regions_dir.name),
            licence=regions_source.licence_id,
            redistribute_public=regions_source.redistribute_public,
        ),
        SourceAsset(
            uri=str(catalogue_dir / snapshots.SHA256SUMS_FILENAME),
            sha256=sha256_file(catalogue_dir / snapshots.SHA256SUMS_FILENAME),
            collection="dea_stac",
            snapshot_date=dt_date.fromisoformat(catalogue_date),
            licence="CC-BY-4.0",
            redistribute_public=True,
        ),
    ]
    resolved_args = {
        "date": date,
        "protocol_digest": frozen_digest,
        "input_manifest_digests": json.loads(input_digests_json),
        "catalogue_date": catalogue_date,
        "region_ambiguity": region_disclosure,
        "commodity_ties": commodity_disclosure,
        "n_candidate_footprints": n_candidate_footprints,
        "n_selected_footprints": n_selected_footprints,
        "read_workers": read_workers,
    }

    tmp_dir = output_dir.parent / f"{output_dir.name}.tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    table_specs: tuple[tuple[str, pd.DataFrame, pa.Schema], ...] = (
        ("support_inputs.parquet", support_inputs_df, d3_inputs.D3_SUPPORT_INPUTS_SCHEMA),
        ("support_spearman.parquet", spearman_df, d3_inputs.D3_SPEARMAN_SCHEMA),
        ("footprint_support.parquet", footprint_support_df, d3_inputs.D3_FOOTPRINT_SUPPORT_SCHEMA),
        ("stratum_summary.parquet", stratum_summary_df, d3_inputs.D3_STRATUM_SUMMARY_SCHEMA),
        ("extraction_assets.parquet", extraction_assets_df, d3_inputs.D3_EXTRACTION_ASSETS_SCHEMA),
    )
    manifest_paths: list[str] = []
    for filename, frame, schema in table_specs:
        tmp_path = tmp_dir / filename
        _write_table_or_refuse(frame, tmp_path, schema, payload={"table": filename})
        try:
            manifests.write_run_manifest(
                output=tmp_path,
                inputs=input_assets,
                config=resolved_config,
                git_state=git_state,
                resolved_args=resolved_args,
            )
        except FileExistsError as exc:
            typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
            raise typer.Exit(1) from None
        manifest_paths.append(str(output_dir / filename) + manifests.MANIFEST_SUFFIX)

    if output_dir.exists():
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{output_dir} already exists -- this curated artefact was already "
                        "built by an earlier run. Refusing before rename."
                    ),
                    "output_dir": str(output_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_dir, output_dir)

    typer.echo(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "protocol_digest": frozen_digest,
                "n_candidate_footprints": n_candidate_footprints,
                "n_selected_footprints": n_selected_footprints,
                "read_workers": read_workers,
                "n_strata_adequate": n_strata_adequate,
                "n_strata_inadequate": n_strata_inadequate,
                "n_footprint_years_simulated": n_footprint_years_simulated,
                "n_footprint_years_not_computable": n_footprint_years_not_computable,
                "n_footprints_support_not_computed": n_footprints_support_not_computed,
                "n_spearman_not_computable": n_spearman_not_computable,
                "region_ambiguity": region_disclosure,
                "commodity_ties": commodity_disclosure,
                "manifest_paths": manifest_paths,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


def _d3_threshold_stratum_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    """`stratum_summary` rows -> plain-Python-typed records (explicit `int()`
    casts, matching this module's existing convention elsewhere, rather than
    leaning on `json.dumps(default=str)` and silently turning a count into a
    string)."""
    return [
        {
            "region": row.region,
            "commodity_group": row.commodity_group,
            "shape_class": row.shape_class,
            "n_footprints": int(row.n_footprints),
            "n_adequate_footprints": int(row.n_adequate_footprints),
            "n_selected": int(row.n_selected),
        }
        for row in frame.itertuples(index=False)
    ]


@app.command("derive-d3-threshold")
def derive_d3_threshold_cmd(
    config: Path = ConfigOption,
    protocol_config: Path = ProtocolConfigOption,
    date: str = DateOption,
) -> None:
    """Evaluate the D3 reduced-support accuracy threshold (D13 Batch D task D4).

    Reads the five `build-d3-inputs` tables from the latest curated
    `d3-inputs/<date>/` directory and the single frozen D3 protocol, then
    finds the smallest pixel support strictly below the full-support floor
    (144) at which every accuracy criterion passes for every adequate
    stratum (`d3_threshold.evaluate_threshold`).

    Gates mirror `build-d3-inputs`'s discipline: (1) the frozen protocol --
    single dated lineage, digest-verified, recomputed from
    `--protocol-config` and checked against the frozen digest (identical to
    `build-d3-inputs` gate 1, including its procedures-text consistency
    check); (2) the latest curated `d3-inputs/<date>/` directory, with ALL
    FIVE tables digest-verified via their run manifests; (3) every table's
    `protocol_digest` column must equal the frozen digest -- refusing an
    input set built before the protocol was (re-)frozen; (4) the four
    digest-bearing tables' `input_manifest_digests` values must be
    identical across tables -- refusing a mixed input set assembled from
    two different `build-d3-inputs` runs.

    Writes `curated/d3-threshold/<date>/threshold.json`: the serialized
    `ThresholdResult` (n*, criteria_passed, nominal_area_m2, protocol_digest,
    per-support detail with counts, failed_criteria), `adequate_strata`/
    `inadequate_strata` (with counts, from `stratum_summary`), and the input
    table paths + digests -- assembled in a `.tmp` sibling directory and
    `os.replace`d into place only once the artefact and its run manifest
    both wrote cleanly.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    # GATE 0 -- preflight existing-output, BEFORE any protocol or input read.
    output_dir = data_root / "curated" / "d3-threshold" / date
    if output_dir.exists():
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{output_dir} already exists -- this curated artefact was already "
                        "built by an earlier run. Refusing before any input access. Move "
                        "the existing output directory aside, or choose a different --date, "
                        "to build again."
                    ),
                    "output_dir": str(output_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # GATE 1 -- the frozen protocol: identical to `build-d3-inputs` gate 1.
    d3_protocol_root = data_root / "curated" / "d3-protocol"
    dated_protocol_dirs = (
        sorted(d3_protocol_root.glob("????-??-??")) if d3_protocol_root.exists() else []
    )
    if not dated_protocol_dirs:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"no frozen D3 protocol under {d3_protocol_root} -- run "
                        "freeze-d3-protocol first"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if len(dated_protocol_dirs) > 1:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"curated/d3-protocol has {len(dated_protocol_dirs)} dated "
                        f"directories {[d.name for d in dated_protocol_dirs]} -- single "
                        "lineage violated: a frozen protocol may never be superseded "
                        "silently. Move all but one dated directory aside."
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    protocol_dir = dated_protocol_dirs[0]
    protocol_artifact_path = protocol_dir / "protocol.json"
    _digest_verified_manifest(protocol_artifact_path)
    frozen_digest = json.loads(protocol_artifact_path.read_text(encoding="utf-8"))[
        "protocol_digest"
    ]

    try:
        protocol = d3_protocol.load_protocol(protocol_config)
    except (d3_protocol.D3ProtocolError, OSError, yaml.YAMLError) as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    recomputed_digest = d3_protocol.protocol_digest(protocol)
    if recomputed_digest != frozen_digest:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"protocol drift: --protocol-config ({protocol_config}) recomputes "
                        f"digest {recomputed_digest[:12]}..., the frozen artefact at "
                        f"{protocol_artifact_path} records {frozen_digest[:12]}... -- the "
                        "config changed after freeze-d3-protocol ran. No accuracy result may "
                        "change sample definitions or criteria after freezing."
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    try:
        d3_inputs.check_procedures_consistency(protocol.procedures)
    except d3_inputs.D3InputsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    # GATE 2 -- the latest curated d3-inputs directory: ALL FIVE tables
    # digest-verified via their run manifests.
    try:
        d3_inputs_dir = _latest_curated_dated_dir(
            data_root / "curated" / "d3-inputs", label="curated/d3-inputs"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    table_filenames = (
        "support_inputs.parquet",
        "support_spearman.parquet",
        "footprint_support.parquet",
        "stratum_summary.parquet",
        "extraction_assets.parquet",
    )
    table_paths = {name: d3_inputs_dir / name for name in table_filenames}
    table_manifests: dict[str, dict[str, Any]] = {
        name: _digest_verified_manifest(path) for name, path in table_paths.items()
    }
    digest_bearing_tables = (
        "support_inputs.parquet",
        "support_spearman.parquet",
        "footprint_support.parquet",
        "stratum_summary.parquet",
    )
    table_frames: dict[str, pd.DataFrame] = {
        name: read_table(table_paths[name]) for name in digest_bearing_tables
    }

    # GATE 3 -- every table's protocol_digest column must equal the frozen
    # digest -- refuse an input set built under a different protocol.
    observed_protocol_digests: set[str] = set()
    for name in digest_bearing_tables:
        frame = table_frames[name]
        if len(frame):
            observed_protocol_digests.update(str(v) for v in frame["protocol_digest"].unique())
    if len(observed_protocol_digests) > 1:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{d3_inputs_dir} carries MIXED protocol_digest values across its "
                        f"tables {sorted(observed_protocol_digests)} -- these tables were "
                        "built under a different protocol from each other, not a single "
                        "consistent run."
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if observed_protocol_digests:
        (actual_protocol_digest,) = observed_protocol_digests
        if actual_protocol_digest != frozen_digest:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"{d3_inputs_dir} was built under a different protocol: its "
                            f"tables record protocol_digest {actual_protocol_digest[:12]}..., "
                            f"the currently frozen protocol at {protocol_artifact_path} "
                            f"records {frozen_digest[:12]}.... Run build-d3-inputs again "
                            "under the currently frozen protocol before deriving a "
                            "threshold from it."
                        ),
                        "d3_inputs_protocol_digest": actual_protocol_digest,
                        "frozen_protocol_digest": frozen_digest,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1)

    # GATE 4 -- the four digest-bearing tables' input_manifest_digests
    # values must be identical across tables -- refuse a mixed input set.
    observed_input_digests: set[str] = set()
    for name in digest_bearing_tables:
        frame = table_frames[name]
        if len(frame):
            observed_input_digests.update(str(v) for v in frame["input_manifest_digests"].unique())
    if len(observed_input_digests) > 1:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{d3_inputs_dir} carries MIXED input_manifest_digests values across "
                        "its tables -- these tables were assembled from different "
                        "build-d3-inputs runs (or a partially overwritten one), not a "
                        "single consistent input set."
                    ),
                    "observed_input_manifest_digests": sorted(observed_input_digests),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # --- Evaluate the threshold. ---
    threshold_inputs = d3_threshold.ThresholdInputs(
        support_inputs=table_frames["support_inputs.parquet"],
        support_spearman=table_frames["support_spearman.parquet"],
        footprint_support=table_frames["footprint_support.parquet"],
        stratum_summary=table_frames["stratum_summary.parquet"],
    )
    try:
        result = d3_threshold.evaluate_threshold(threshold_inputs, protocol)
    except d3_threshold.D3ThresholdError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    stratum_summary_df = table_frames["stratum_summary.parquet"]
    adequate_strata = _d3_threshold_stratum_records(
        stratum_summary_df[stratum_summary_df["adequate"]]
    )
    inadequate_strata = _d3_threshold_stratum_records(
        stratum_summary_df[~stratum_summary_df["adequate"]]
    )

    input_tables_disclosure = {
        name: {
            "path": str(table_paths[name]),
            "sha256": table_manifests[name]["output"]["sha256"],
        }
        for name in table_filenames
    }

    threshold_payload: dict[str, object] = {
        "n_star": result.n_star,
        "criteria_passed": result.criteria_passed,
        "nominal_area_m2": result.nominal_area_m2,
        "protocol_digest": result.protocol_digest,
        "per_support": list(result.per_support),
        "failed_criteria": list(result.failed_criteria),
        "adequate_strata": adequate_strata,
        "inadequate_strata": inadequate_strata,
        "input_tables": input_tables_disclosure,
    }

    # --- Assemble output atomically: `.tmp` dir, artefact + manifest, then
    # `os.replace` (mirrors `build-d3-inputs` decision 15). ---
    tmp_dir = output_dir.parent / f"{output_dir.name}.tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_output_path = tmp_dir / "threshold.json"
    tmp_output_path.write_text(json.dumps(threshold_payload, indent=2, sort_keys=True, default=str))

    input_assets = [
        SourceAsset(
            uri=str(protocol_artifact_path),
            sha256=sha256_file(protocol_artifact_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(protocol_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        *[
            SourceAsset(
                uri=str(table_paths[name]),
                sha256=table_manifests[name]["output"]["sha256"],
                collection=None,
                snapshot_date=dt_date.fromisoformat(d3_inputs_dir.name),
                licence=None,
                redistribute_public=False,
            )
            for name in table_filenames
        ],
    ]

    try:
        manifests.write_run_manifest(
            output=tmp_output_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                "protocol_digest": frozen_digest,
                "d3_inputs_dir": str(d3_inputs_dir),
                "n_star": result.n_star,
                "criteria_passed": result.criteria_passed,
            },
        )
    except FileExistsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    if output_dir.exists():
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{output_dir} already exists -- this curated artefact was already "
                        "built by an earlier run. Refusing before rename."
                    ),
                    "output_dir": str(output_dir),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_dir, output_dir)

    output_path = output_dir / "threshold.json"
    manifest_path = str(output_path) + manifests.MANIFEST_SUFFIX
    typer.echo(
        json.dumps(
            {
                "output_path": str(output_path),
                "n_star": result.n_star,
                "criteria_passed": result.criteria_passed,
                "nominal_area_m2": result.nominal_area_m2,
                "n_strata_adequate": len(adequate_strata),
                "n_strata_inadequate": len(inadequate_strata),
                "manifest_path": manifest_path,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


@app.command("apply-d3-threshold")
def apply_d3_threshold_cmd(
    config: Path = ConfigOption,
    date: str = DateOption,
    forced_threshold: bool = ForcedThresholdOption,
    decision_record: Path | None = DecisionRecordOption,
) -> None:
    """Apply the derived D3 reduced-support threshold to the latest register
    (D13 D5): every register row gets exactly one `trajectory_status`
    (`register._TRAJECTORY_STATUSES`) plus `effective_pixel_support_px`/
    `d3_threshold_px`/`d3_eligible`/`d3_forced_threshold` (`register.assign_
    trajectory_eligibility`).

    `--forced-threshold` (default off, Batch E Task 0) judges eligibility
    at the pre-registered forced-144 fallback even when the threshold
    artefact's `criteria_passed` is `False`, rather than stamping every
    judged site `threshold_not_computed`. It is refused unless
    `--decision-record` names an existing file; that path, and
    `forced_threshold` itself, are recorded verbatim in the run manifest
    alongside the unchanged `criteria_passed` -- this command NEVER flips
    `criteria_passed`, under `--forced-threshold` or otherwise: it stays
    the frozen record of the D3 outcome.

    Gates, all digest-verified before anything downstream reads them: (1)
    the latest curated register -- must be DEA-enriched (`register.
    DEA_COVERAGE_COLUMNS`), the same check `derive-dea-volume` applies to
    its own register input; (2) the latest curated crosswalk -- loaded
    WHOLE (every confidence tier, never filtered to `tier1_population`), so
    a site matched at medium/low/none confidence is distinguishable from
    one absent from the crosswalk altogether; (3) the single frozen D3
    protocol; (4) the latest curated `d3-threshold/<date>/threshold.json`,
    whose own `protocol_digest` must equal the frozen protocol's --
    refused (missing artefact, altered artefact, or protocol mismatch)
    rather than applied; a `criteria_passed=False` artefact is itself
    APPLIED, never refused (decision 14); (5) the latest curated
    `d3-inputs/<date>/footprint_support.parquet`, digest-verified via its
    OWN manifest, whose `protocol_digest` column must match the
    threshold's -- an unverified support table must never determine
    eligibility.

    Writes a NEW dated `curated/register/<date>/register.parquet` under
    `register.ELIGIBLE_REGISTER_SCHEMA` (the accepted enriched register is
    never mutated -- the same distinct-date convention `build-dea-coverage`
    uses). The run manifest records per-status counts, computed/zero/
    not-computed pixel-support counts, the threshold artefact's own
    digest, `criteria_passed`, and (copied verbatim from the threshold
    artefact) its `failed_criteria` disclosure.
    """
    if forced_threshold and (decision_record is None or not decision_record.is_file()):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "--forced-threshold requires --decision-record naming an existing "
                        "file -- the disclosure and its authority travel together (Batch E "
                        "Task 0, docs/decisions/2026-08-25-batch-e-forced-threshold-entry.md)"
                    ),
                    "decision_record": str(decision_record) if decision_record else None,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    # GATE 1 -- latest curated register: digest-verified, DEA-enriched
    # (mirrors derive_dea_volume_cmd's identical check).
    try:
        register_dir = _latest_curated_dated_dir(
            data_root / "curated" / "register", label="curated/register"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    register_path = register_dir / "register.parquet"
    register_manifest = _digest_verified_manifest(register_path)
    register_df = read_table(register_path)
    if any(column not in register_df.columns for column in register.DEA_COVERAGE_COLUMNS):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "latest curated register is not DEA-enriched -- run "
                        "build-dea-coverage first"
                    ),
                    "register_path": str(register_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # GATE 2 -- latest curated crosswalk: digest-verified, ALL confidence
    # tiers loaded, so rule 1 (unmatched entirely) is distinguishable from
    # rule 2 (matched, not high confidence).
    try:
        crosswalk_dir = _latest_curated_dated_dir(
            data_root / "curated" / "crosswalk", label="curated/crosswalk"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    crosswalk_path = crosswalk_dir / "crosswalk.parquet"
    crosswalk_manifest = _digest_verified_manifest(crosswalk_path)
    crosswalk_df = read_table(crosswalk_path)

    # GATE 3 -- the single frozen D3 protocol (identical single-lineage
    # discipline to derive_d3_threshold_cmd's gate 1, minus the
    # --protocol-config recompute -- this command only needs the frozen
    # digest itself, to compare against the threshold artefact's).
    d3_protocol_root = data_root / "curated" / "d3-protocol"
    dated_protocol_dirs = (
        sorted(d3_protocol_root.glob("????-??-??")) if d3_protocol_root.exists() else []
    )
    if not dated_protocol_dirs:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"no frozen D3 protocol under {d3_protocol_root} -- run "
                        "freeze-d3-protocol first"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if len(dated_protocol_dirs) > 1:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"curated/d3-protocol has {len(dated_protocol_dirs)} dated "
                        f"directories {[d.name for d in dated_protocol_dirs]} -- single "
                        "lineage violated: a frozen protocol may never be superseded "
                        "silently. Move all but one dated directory aside."
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    protocol_dir = dated_protocol_dirs[0]
    protocol_artifact_path = protocol_dir / "protocol.json"
    _digest_verified_manifest(protocol_artifact_path)
    frozen_digest = json.loads(protocol_artifact_path.read_text(encoding="utf-8"))[
        "protocol_digest"
    ]

    # GATE 4 -- latest curated d3-threshold artefact: digest-verified,
    # protocol_digest equal to the frozen protocol's. `criteria_passed`
    # False is APPLIED here, never refused (decision 14).
    try:
        threshold_dir = _latest_curated_dated_dir(
            data_root / "curated" / "d3-threshold", label="curated/d3-threshold"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    threshold_path = threshold_dir / "threshold.json"
    threshold_manifest = _digest_verified_manifest(threshold_path)
    threshold_payload = json.loads(threshold_path.read_text(encoding="utf-8"))
    threshold_protocol_digest = threshold_payload.get("protocol_digest")
    if threshold_protocol_digest != frozen_digest:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{threshold_path} was derived under a different protocol: it "
                        f"records protocol_digest {str(threshold_protocol_digest)[:12]}..., "
                        f"the currently frozen protocol at {protocol_artifact_path} records "
                        f"{frozen_digest[:12]}.... Run derive-d3-threshold again under the "
                        "currently frozen protocol before applying it."
                    ),
                    "threshold_protocol_digest": threshold_protocol_digest,
                    "frozen_protocol_digest": frozen_digest,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    n_star = int(threshold_payload["n_star"])
    criteria_passed = bool(threshold_payload["criteria_passed"])
    failed_criteria = list(threshold_payload.get("failed_criteria", []))
    applied_threshold_px = n_star if criteria_passed else d3_protocol.MIN_FULL_SUPPORT_PX

    # GATE 5 -- latest curated d3-inputs footprint_support.parquet:
    # digest-verified via its OWN manifest; its protocol_digest column
    # must match the threshold's -- an unverified support table must never
    # determine eligibility.
    try:
        d3_inputs_dir = _latest_curated_dated_dir(
            data_root / "curated" / "d3-inputs", label="curated/d3-inputs"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    footprint_support_path = d3_inputs_dir / "footprint_support.parquet"
    footprint_support_manifest = _digest_verified_manifest(footprint_support_path)
    footprint_support_df = read_table(footprint_support_path)
    observed_support_digests: set[str] = set()
    if len(footprint_support_df):
        observed_support_digests.update(
            str(v) for v in footprint_support_df["protocol_digest"].unique()
        )
    if len(observed_support_digests) > 1:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{footprint_support_path} carries MIXED protocol_digest values "
                        f"{sorted(observed_support_digests)} -- not a single consistent "
                        "build-d3-inputs run."
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if observed_support_digests:
        (actual_support_digest,) = observed_support_digests
        if actual_support_digest != threshold_protocol_digest:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"{footprint_support_path} was built under a different protocol: "
                            f"it records protocol_digest {actual_support_digest[:12]}..., the "
                            f"threshold artefact at {threshold_path} records "
                            f"{str(threshold_protocol_digest)[:12]}.... An unverified support "
                            "table must not determine eligibility -- run build-d3-inputs and "
                            "derive-d3-threshold again under the same protocol before "
                            "applying."
                        ),
                        "footprint_support_protocol_digest": actual_support_digest,
                        "threshold_protocol_digest": threshold_protocol_digest,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1)

    # --- Join + assign D5 status/eligibility (pure logic in register.py). ---
    try:
        eligible_df = register.assign_trajectory_eligibility(
            register_df,
            crosswalk_df,
            footprint_support_df,
            n_star=applied_threshold_px,
            criteria_passed=criteria_passed,
            forced_threshold=forced_threshold,
        )
    except ValueError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    try:
        register.validate_eligible_register(eligible_df)
    except register.TrajectoryStatusError as exc:
        typer.echo(
            json.dumps({"refusal": str(exc), "stage": "self-check"}, indent=2, sort_keys=True)
        )
        raise typer.Exit(1) from None

    if len(eligible_df) != len(register_df):
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"apply-d3-threshold changed the register's row count: "
                        f"{len(register_df)} row(s) in, {len(eligible_df)} out -- this is a "
                        "defect in assign_trajectory_eligibility; refusing rather than "
                        "writing a corrupted register"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    n_by_status = {
        status: int((eligible_df["trajectory_status"] == status).sum())
        for status in register._TRAJECTORY_STATUSES
    }
    n_eligible = n_by_status["eligible"]

    support_computed = eligible_df["effective_pixel_support_px"].notna()
    n_support_computed = int(support_computed.sum())
    n_support_zero = int(
        (eligible_df.loc[support_computed, "effective_pixel_support_px"] == 0).sum()
    )
    n_support_not_computed = int((~support_computed).sum())

    # --- Ingredients all computed -- write the artefact, then its manifest. ---
    out_dir = data_root / "curated" / "register" / date
    out_path = out_dir / "register.parquet"
    _refuse_if_curated_output_already_exists(out_path, config=resolved_config, git_state=git_state)

    input_assets = [
        SourceAsset(
            uri=str(register_path),
            sha256=register_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=None,
            licence=licence.SOURCES["dmirs_001_minedex"].licence_id,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(crosswalk_path),
            sha256=crosswalk_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=None,
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(threshold_path),
            sha256=threshold_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(threshold_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(footprint_support_path),
            sha256=footprint_support_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(d3_inputs_dir.name),
            licence=None,
            redistribute_public=False,
        ),
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_table_or_refuse(eligible_df, out_path, register.ELIGIBLE_REGISTER_SCHEMA)
    try:
        manifests.write_run_manifest(
            output=out_path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            resolved_args={
                "date": date,
                "protocol_digest": frozen_digest,
                "threshold_dir": str(threshold_dir),
                "d3_inputs_dir": str(d3_inputs_dir),
                "n_star": n_star,
                "d3_threshold_px": applied_threshold_px,
                "criteria_passed": criteria_passed,
                "forced_threshold": forced_threshold,
                "decision_record": str(decision_record) if decision_record else None,
                "failed_criteria": failed_criteria,
                "n_by_status": n_by_status,
                "n_support_computed": n_support_computed,
                "n_support_zero": n_support_zero,
                "n_support_not_computed": n_support_not_computed,
                "register_rows_before": len(register_df),
                "register_rows_after": len(eligible_df),
            },
        )
    except FileExistsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    typer.echo(
        json.dumps(
            {
                "output_path": str(out_path),
                "d3_threshold_px": applied_threshold_px,
                "criteria_passed": criteria_passed,
                "n_eligible": n_eligible,
                "n_by_status": n_by_status,
                "rows": len(eligible_df),
                "manifest_path": str(out_path) + manifests.MANIFEST_SUFFIX,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


def _not_computable_metric_rows(
    *, kind: str, reason: str, ctx_kwargs: Mapping[str, Any], item_id: str
) -> list[dict[str, object]]:
    """One not-computable row per metric of `kind`, carrying `reason`.

    `n_member_pixels` is the footprint's effective pixel support (a real
    measured number -- the read failed, the footprint did not) and
    `n_valid_pixels` is NULL, because nothing was read to count. Used for
    `read_failed` and `item_missing`, the two reasons that arise outside
    `spectral_metrics` (which owns `zero_member_pixels`/
    `zero_valid_pixels`).
    """
    metrics = (
        list(d3_inputs.GEOMEDIAN_METRIC_BANDS)
        if kind == "geomedian"
        else list(d3_inputs.FC_METRIC_ASSETS)
    )
    support = ctx_kwargs.get("effective_pixel_support_px") or 0
    ctx = trajectories.RowContext(item_id=item_id, **dict(ctx_kwargs))
    metric_rows = [
        spectral_metrics.MetricRow(
            metric=metric,
            value=None,
            n_member_pixels=int(support),
            n_valid_pixels=0,
            computable=False,
            not_computable_reason=reason,
        )
        for metric in metrics
    ]
    rows = trajectories.rows_from_metrics(metric_rows, ctx)
    for row in rows:
        row["n_valid_pixels"] = None
    return rows


@app.command("extract-trajectories")
def extract_trajectories_cmd(
    config: Path = ConfigOption,
    date: str = DateOption,
    scope: str = ScopeOption,
    site_id: list[str] | None = SiteIdOption,
) -> None:
    """Extract the Tier 1 trajectory table (D13 E4) into resumable
    `collection_id/year` Parquet partitions under
    `curated/trajectories/<date>/`.

    Input is the LATEST curated register, which must be the eligible
    register `apply-d3-threshold` writes (`register.
    ELIGIBLE_REGISTER_SCHEMA`): only `trajectory_status == "eligible"`
    rows are extracted, per D13 E4 acceptance. Every other artefact this
    reads -- the DEA catalogue snapshot, the crosswalk, the Maus
    footprints, the Maus snapshot -- is digest-verified through the same
    gates `build-d3-inputs` applies, and the raster reads go through the
    SAME `_read_footprint_year_bands` the D3 threshold was measured with,
    so a trajectory value and a D3 simulation value can never diverge
    through a second code path.

    Resumability: a partition already carrying a digest-verified
    `part-NNNN.parquet` is SKIPPED (counted as `existing`), never
    re-read. A re-run over a covered partition writes the next version
    beside it rather than mutating it -- partitions are immutable.

    Every metric row is written: a row either carries a value, or carries
    `computable=False` with a `not_computable_reason` from
    `spectral_metrics.NOT_COMPUTABLE_REASONS`. A raster read that fails is
    `read_failed`; a (collection, tile, year) with no catalogue item is
    `item_missing`. Nothing is dropped and nothing is zero-filled.

    `--scope statewide` is refused until `validate-huntly` has written a
    passing, digest-verified verdict
    (`trajectory_extract.require_huntly_gate`).
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    # GATE 1 -- scope validation, before ANY I/O.
    if scope not in ("sites", "statewide"):
        typer.echo(
            json.dumps(
                {"refusal": f"--scope must be 'sites' or 'statewide', got {scope!r}"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if scope == "sites" and not site_id:
        typer.echo(
            json.dumps(
                {"refusal": "--scope sites requires at least one --site-id"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if scope == "statewide" and site_id:
        typer.echo(
            json.dumps(
                {"refusal": "--site-id is not accepted with --scope statewide"},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if scope == "statewide":
        try:
            trajectory_extract.require_huntly_gate(data_root)
        except trajectory_extract.TrajectoryExtractError as exc:
            typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
            raise typer.Exit(1) from None

    # GATE 2 -- latest curated register: digest-verified, and must be the
    # D3-eligibility-annotated register apply-d3-threshold writes.
    try:
        register_dir = _latest_curated_dated_dir(
            data_root / "curated" / "register", label="curated/register"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    register_path = register_dir / "register.parquet"
    register_manifest = _digest_verified_manifest(register_path)
    register_df = read_table(register_path)
    if "trajectory_status" not in register_df.columns:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "latest curated register is not D3-eligibility-annotated -- run "
                        "apply-d3-threshold first"
                    ),
                    "register_path": str(register_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if "d3_forced_threshold" not in register_df.columns:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        "latest curated register predates the d3_forced_threshold column -- "
                        "run apply-d3-threshold to re-write it"
                    ),
                    "register_path": str(register_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # GATE 3 -- eligibility: only `trajectory_status == "eligible"` sites
    # may be extracted; an explicitly requested site that is not eligible
    # is refused by name rather than silently dropped.
    eligible = trajectory_extract.select_eligible_sites(register_df)
    if scope == "sites":
        requested = list(site_id or [])
        ineligible = sorted(set(requested) - set(eligible))
        if ineligible:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"--site-id value(s) {ineligible} are not D3-eligible in the "
                            f"latest curated register ({register_path})"
                        ),
                        "ineligible_site_ids": ineligible,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1)
        extracted_sites = sorted(set(requested))
    else:
        extracted_sites = eligible

    # `d3_forced_threshold` (decision 2026-08-23) is per-site and lives on
    # the register itself -- computed here, from the FULL eligible
    # register, not from `extracted_sites`.
    d3_forced_threshold_by_site = (
        register_df.loc[register_df["site_id"].isin(eligible)]
        .set_index("site_id")["d3_forced_threshold"]
        .to_dict()
    )

    # GATE 4 -- crosswalk, footprint areas, Maus snapshot, DEA catalogue,
    # frozen protocol: same digest-verification discipline as
    # `build_d3_inputs_cmd`'s gates 1/3/4/6, minus the region gate (this
    # command never stratifies) and minus the `--protocol-config` drift
    # check (this command takes no `--protocol-config`; it only needs the
    # frozen digest itself, for provenance).
    d3_protocol_root = data_root / "curated" / "d3-protocol"
    dated_protocol_dirs = (
        sorted(d3_protocol_root.glob("????-??-??")) if d3_protocol_root.exists() else []
    )
    if not dated_protocol_dirs:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"no frozen D3 protocol under {d3_protocol_root} -- run "
                        "freeze-d3-protocol first"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    if len(dated_protocol_dirs) > 1:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"curated/d3-protocol has {len(dated_protocol_dirs)} dated "
                        f"directories {[d.name for d in dated_protocol_dirs]} -- single "
                        "lineage violated: a frozen protocol may never be superseded "
                        "silently. Move all but one dated directory aside."
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    protocol_dir = dated_protocol_dirs[0]
    protocol_artifact_path = protocol_dir / "protocol.json"
    _digest_verified_manifest(protocol_artifact_path)
    frozen_digest = json.loads(protocol_artifact_path.read_text(encoding="utf-8"))[
        "protocol_digest"
    ]

    try:
        crosswalk_dir = _latest_curated_dated_dir(
            data_root / "curated" / "crosswalk", label="curated/crosswalk"
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    crosswalk_path = crosswalk_dir / "crosswalk.parquet"
    crosswalk_manifest = _digest_verified_manifest(crosswalk_path)
    crosswalk_df = read_table(crosswalk_path)
    tier1_df = crosswalk.tier1_population(crosswalk_df)

    try:
        footprints_dir = _latest_curated_dated_dir(
            data_root / "curated" / "maus_footprint_areas",
            label="curated/maus_footprint_areas",
        )
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    footprints_path = footprints_dir / "footprint_areas.parquet"
    footprints_manifest = _digest_verified_manifest(footprints_path)

    maus_licence_id = licence.SOURCES["maus_v2"].licence_id
    crosswalk_maus_input = next(
        (
            asset
            for asset in crosswalk_manifest.get("inputs", [])
            if asset.get("licence") == maus_licence_id
        ),
        None,
    )
    if crosswalk_maus_input is None:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{crosswalk_path}'s manifest carries no Maus input (licence "
                        f"{maus_licence_id!r}) -- cannot verify it was built from the "
                        "same Maus snapshot as the footprint artefact"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    crosswalk_maus_sha256 = crosswalk_maus_input["sha256"]
    try:
        footprints_maus_sha256 = footprints_manifest["resolved_args"]["maus_gpkg_sha256"]
    except KeyError:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{footprints_path}'s manifest does not record "
                        "resolved_args.maus_gpkg_sha256"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    if crosswalk_maus_sha256 != footprints_maus_sha256:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"crosswalk ({crosswalk_path}) and footprint-areas "
                        f"({footprints_path}) were built from DIFFERENT Maus GeoPackage "
                        f"snapshots -- crosswalk records {crosswalk_maus_sha256[:12]}..., "
                        f"footprints records {footprints_maus_sha256[:12]}.... maus_id is "
                        "derived from clipped geometry, so a join on maus_id alone cannot "
                        "detect this; refusing rather than silently mixing two snapshots' "
                        "geometry."
                    ),
                    "crosswalk_maus_gpkg_sha256": crosswalk_maus_sha256,
                    "footprints_maus_gpkg_sha256": footprints_maus_sha256,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    try:
        maus_snapshot_dir = register.latest_snapshot(data_root, "maus_v2")
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    _verify_snapshot_or_refuse(
        maus_snapshot_dir, source_id="maus_v2", required_files=("wa_extract.gpkg",)
    )
    maus_path = maus_snapshot_dir / "wa_extract.gpkg"
    maus_gpkg_sha256 = sha256_file(maus_path)
    if maus_gpkg_sha256 != crosswalk_maus_sha256:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"latest maus_v2 raw snapshot ({maus_path}) hashes "
                        f"{maus_gpkg_sha256[:12]}..., but the crosswalk's manifest records "
                        f"Maus sha256 {crosswalk_maus_sha256[:12]}... -- the latest raw Maus "
                        "snapshot is not the one the crosswalk was built from"
                    ),
                    "maus_gpkg_sha256": maus_gpkg_sha256,
                    "crosswalk_maus_gpkg_sha256": crosswalk_maus_sha256,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    try:
        maus_source_gdf = gpd.read_file(maus_path)
        maus_gdf = maus_source_gdf[["maus_id", "geometry"]].to_crs(crosswalk.TARGET_CRS)
    except (pyogrio.errors.DataSourceError, OSError) as exc:
        typer.echo(
            json.dumps({"refusal": str(exc), "maus_gpkg": str(maus_path)}, indent=2, sort_keys=True)
        )
        raise typer.Exit(1) from None
    except KeyError as exc:
        typer.echo(
            json.dumps(
                {
                    "refusal": f"wa_extract.gpkg is missing the expected column {exc}",
                    "maus_gpkg": str(maus_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    # The DEA STAC catalogue: unlike `build_d3_inputs_cmd` (which pins the
    # exact snapshot the DEA-ENRICHED register was built from, via that
    # register's own manifest), this command's "latest curated register" is
    # the ELIGIBLE register apply-d3-threshold writes, whose manifest never
    # carries a `catalogue_date` (D13 D5's resolved_args are threshold/
    # eligibility fields, not a catalogue pointer). The latest RAW dea_stac
    # snapshot -- the same lookup already used for maus_v2 above -- is the
    # equivalent, equally-verified substitute.
    try:
        catalogue_dir = register.latest_snapshot(data_root, "dea_stac")
    except register.NoSnapshotFoundError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    _verify_snapshot_or_refuse(
        catalogue_dir, source_id="dea_stac", required_files=("catalogue_summary.json",)
    )
    items_by_source = _load_dea_items(catalogue_dir)

    maus_geom_by_id: dict[str, Any] = dict(
        zip(maus_gdf["maus_id"].astype(str), maus_gdf.geometry, strict=True)
    )
    # A site CAN carry more than one `confidence == "high"` crosswalk row
    # (overlapping Maus polygons) -- a bare `dict(zip(site_id, maus_id))`
    # would silently keep whichever row happens to come LAST in `tier1_df`,
    # which is NOT necessarily the footprint the site's D3 eligibility was
    # judged against. `register.py`'s own eligibility tie-break (~1373:
    # stable sort by `["site_id", "maus_id"]`, `drop_duplicates(keep=
    # "first")` -- the lexicographically SMALLEST `maus_id` per site) is
    # mirrored here EXACTLY, so this command never extracts a site on a
    # footprint that did not pass its own eligibility comparison.
    tier1_dedup = tier1_df.sort_values(
        ["site_id", "maus_id"], na_position="last", kind="stable"
    ).drop_duplicates(subset="site_id", keep="first")
    maus_id_by_site: dict[str, str] = dict(
        zip(tier1_dedup["site_id"].astype(str), tier1_dedup["maus_id"].astype(str), strict=True)
    )

    # Sharing disclosure (decision 2026-08-25): the number of ELIGIBLE
    # sites on each `maus_id`, computed from the FULL eligible register
    # (`eligible`, from GATE 3's `select_eligible_sites`) -- never from
    # `extracted_sites`, or a `--site-id` run would understate how many
    # eligible sites share a footprint. Reuses `maus_id_by_site` (built
    # above) with a guarded lookup: a stale crosswalk vs. register can
    # leave an eligible site out of the Tier 1 crosswalk population, and
    # that must surface as the same JSON refusal as below, not a bare
    # KeyError.
    eligible_maus_ids: list[str] = []
    for site in eligible:
        maus_id = maus_id_by_site.get(site)
        if maus_id is None:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"eligible site {site!r} is not in the Tier 1 crosswalk "
                            f"population ({crosswalk_path}) -- an eligible site must have a "
                            "high-confidence Maus match"
                        )
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1)
        eligible_maus_ids.append(maus_id)
    shared_footprint_site_count_by_maus = (
        pd.Series(eligible_maus_ids).value_counts().astype("int64").to_dict()
    )

    sites_by_maus_id: dict[str, list[str]] = {}
    for site in extracted_sites:
        maus_id = maus_id_by_site.get(site)
        if maus_id is None:
            typer.echo(
                json.dumps(
                    {
                        "refusal": (
                            f"eligible site {site!r} is not in the Tier 1 crosswalk "
                            f"population ({crosswalk_path}) -- an eligible site must have a "
                            "high-confidence Maus match"
                        )
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise typer.Exit(1)
        sites_by_maus_id.setdefault(maus_id, []).append(site)
    for sites in sites_by_maus_id.values():
        sites.sort()

    footprint_geometry: dict[str, Any] = {
        maus_id: maus_geom_by_id[maus_id]
        for maus_id in sites_by_maus_id
        if maus_id in maus_geom_by_id
    }
    missing_geometry = sorted(set(sites_by_maus_id) - set(footprint_geometry))
    if missing_geometry:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"maus_id(s) {missing_geometry} (from eligible sites) are absent "
                        f"from the latest Maus snapshot ({maus_path})"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)

    # Hoisted above the partition loop (rather than built afterwards, as a
    # single block reused only at the end): the resume-binding check below,
    # inside the loop, needs this run's input sha256 set to compare against
    # an already-verified partition's OWN recorded inputs, before deciding
    # whether that partition may be skipped.
    input_assets = [
        SourceAsset(
            uri=str(register_path),
            sha256=register_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(register_dir.name),
            licence=licence.SOURCES["dmirs_001_minedex"].licence_id,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(crosswalk_path),
            sha256=crosswalk_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(crosswalk_dir.name),
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(footprints_path),
            sha256=footprints_manifest["output"]["sha256"],
            collection=None,
            snapshot_date=dt_date.fromisoformat(footprints_dir.name),
            licence="CC-BY-SA-4.0",
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(maus_path),
            sha256=maus_gpkg_sha256,
            collection=None,
            snapshot_date=dt_date.fromisoformat(maus_snapshot_dir.name),
            licence=maus_licence_id,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(catalogue_dir / snapshots.SHA256SUMS_FILENAME),
            sha256=sha256_file(catalogue_dir / snapshots.SHA256SUMS_FILENAME),
            collection="dea_stac",
            snapshot_date=dt_date.fromisoformat(catalogue_dir.name),
            licence="CC-BY-4.0",
            redistribute_public=True,
        ),
        SourceAsset(
            uri=str(protocol_artifact_path),
            sha256=sha256_file(protocol_artifact_path),
            collection=None,
            snapshot_date=dt_date.fromisoformat(protocol_dir.name),
            licence=None,
            redistribute_public=False,
        ),
    ]
    # Snapshotted ONCE for this invocation and reused for both the
    # resume-binding check below and every `write_run_manifest` call this
    # command makes -- never re-derived independently, so a partition
    # written earlier in THIS SAME run is never compared against a
    # different package snapshot than the one it was written with.
    package_versions = manifests.installed_package_versions()

    # GATE 5 -- refuse a re-run against an already-finished batch summary.
    # PARTITIONS are deliberately not covered by this check -- resuming
    # into an existing dated directory is the point of E4.
    out_dir = data_root / "curated" / "trajectories" / date
    _refuse_if_curated_output_already_exists(
        out_dir / "extraction_summary.json", config=resolved_config, git_state=git_state
    )

    # Tile grids and per-footprint pixel support: identical to
    # build-d3-inputs' decision-7 block, so a trajectory row's
    # `effective_pixel_support_px` is the SAME number D3 thresholded on.
    effective_support, footprint_members, footprint_tiles, _unused_reasons = (
        _footprint_pixel_support(items_by_source, footprint_geometry)
    )
    touched_tile_ids = sorted({t for tiles in footprint_tiles.values() for t in tiles})
    try:
        item_index = d3_inputs.select_catalogue_items(items_by_source, touched_tile_ids)
    except d3_inputs.D3InputsError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    covered_years_by_source: dict[str, set[int]] = {}
    for source_id, tile_id, year in item_index:
        covered_years_by_source.setdefault(source_id, set()).add(year)
    transition_flags = trajectory_extract.transition_adjacent_years(covered_years_by_source)

    # GATE 6 -- refuse a dated directory carrying a partition this run's
    # catalogue snapshot does not cover. The loop below only visits
    # (collection, year) pairs `covered_years_by_source` derives from the
    # CURRENT catalogue, digest- and schema-verifying each; a partition
    # left on disk from an earlier run against a DIFFERENT catalogue
    # snapshot (or otherwise outside that set) would never be visited,
    # never checked, yet would still sit inside the directory the
    # completion summary finalizes. A finalized dataset directory must
    # contain nothing this run did not verify.
    try:
        on_disk = trajectory_extract.existing_partitions(out_dir)
    except trajectory_extract.TrajectoryExtractError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None
    expected_partitions = {
        (trajectory_extract.collection_id_for_source(source_id), year)
        for source_id in d3_inputs.D3_COLLECTION_KIND
        for year in covered_years_by_source.get(source_id, set())
    }
    stray_partitions = sorted(set(on_disk) - expected_partitions)
    if stray_partitions:
        stray_names = [f"collection_id={cid}/year={yr}" for cid, yr in stray_partitions]
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{out_dir} contains partition(s) {stray_names} that this run's "
                        "catalogue snapshot does not cover, so they cannot be digest- or "
                        "schema-verified -- re-extract under a NEW dated output directory, "
                        "or restore the catalogue snapshot this directory was built from"
                    )
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None

    rows_by_partition: dict[tuple[str, int], list[dict[str, object]]] = {}
    total = trajectory_extract.PartitionResult()

    for source_id, kind in d3_inputs.D3_COLLECTION_KIND.items():
        collection_id = trajectory_extract.collection_id_for_source(source_id)
        sensor = trajectory_extract.sensor_for_source(source_id)
        for year in sorted(covered_years_by_source.get(source_id, set())):
            partition = trajectory_extract.partition_dir(out_dir, collection_id, year)
            try:
                already = trajectory_extract.verified_parts(
                    partition, expected_schema=trajectories.TRAJECTORY_SCHEMA
                )
            except trajectory_extract.TrajectoryExtractError as exc:
                typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
                raise typer.Exit(1) from None
            if already:
                # GATE 7 -- a partition digest+schema verifies clean does
                # NOT mean it was written by THIS run: the skip decision
                # above binds only on (collection_id, year). A partition
                # left by an earlier, interrupted run against a DIFFERENT
                # `--site-id` scope, a different catalogue/register/
                # crosswalk/footprints/Maus snapshot, a different config, or
                # different code must never be silently absorbed -- the
                # final summary would then claim THIS invocation's
                # scope/sites/inputs over rows produced under the old ones.
                # A resumed run must be the SAME run, or refuse.
                for part_path in already:
                    part_manifest = json.loads(
                        Path(str(part_path) + manifests.MANIFEST_SUFFIX).read_text(encoding="utf-8")
                    )
                    mismatches = trajectory_extract.resume_binding_mismatches(
                        part_manifest,
                        date=date,
                        scope=scope,
                        site_ids=extracted_sites,
                        input_assets=input_assets,
                        config=resolved_config,
                        git_state=git_state,
                        package_versions=package_versions,
                    )
                    if mismatches:
                        typer.echo(
                            json.dumps(
                                {
                                    "refusal": (
                                        f"{partition} (collection_id={collection_id}, "
                                        f"year={year}) was written by a DIFFERENT run -- "
                                        f"its {', '.join(mismatches)} differ(s) from this "
                                        "invocation. A resumed run must be the SAME run "
                                        "(same scope, sites, inputs, config and code) or "
                                        "refuse; re-extract under a NEW dated output "
                                        "directory."
                                    ),
                                    "partition": str(partition),
                                    "part_path": str(part_path),
                                    "differing_fields": mismatches,
                                },
                                indent=2,
                                sort_keys=True,
                            )
                        )
                        raise typer.Exit(1) from None
                total = total + trajectory_extract.PartitionResult(existing=1)
                continue

            partition_rows: list[dict[str, object]] = []
            # ONE read per FOOTPRINT, then fan out to the sites that map to
            # it. A trajectory value is a function of `maus_id`, never of
            # `site_id`: looping per-site would re-read the same pixels
            # once per site sharing a footprint, for byte-identical values.
            for maus_id, footprint_sites in sorted(sites_by_maus_id.items()):
                members = footprint_members.get(maus_id, ())
                touched = sorted(footprint_tiles.get(maus_id, ()))
                geometry_wkb = shapely.to_wkb(footprint_geometry[maus_id])
                ctx_kwargs = {
                    "maus_id": maus_id,
                    "year": year,
                    "sensor": sensor,
                    "collection_id": collection_id,
                    "product_version": None,
                    "geomad_count": None,
                    "effective_pixel_support_px": effective_support.get(maus_id),
                    "transition_adjacent": bool(transition_flags.get(year, False)),
                    "shared_footprint_site_count": shared_footprint_site_count_by_maus[maus_id],
                    "source_snapshot_date": maus_snapshot_dir.name,
                    "geometry_wkb": geometry_wkb,
                }
                missing_tiles = [t for t in touched if (source_id, t, year) not in item_index]
                if not touched or missing_tiles:
                    for site in footprint_sites:
                        partition_rows.extend(
                            _not_computable_metric_rows(
                                kind=kind,
                                reason="item_missing",
                                ctx_kwargs={
                                    **ctx_kwargs,
                                    "site_id": site,
                                    "d3_forced_threshold": bool(d3_forced_threshold_by_site[site]),
                                },
                                item_id="",
                            )
                        )
                    continue
                item = item_index[(source_id, touched[0], year)]
                properties = item.get("properties") or {}
                ctx_kwargs["product_version"] = properties.get("odc:dataset_version")
                item_id = str(item.get("id") or "")
                try:
                    raw_bands, _extraction_rows = _read_footprint_year_bands(
                        source_id=source_id,
                        kind=kind,
                        year=year,
                        touched_tiles=touched,
                        members=members,
                        item_index=item_index,
                        phase="e4",
                    )
                except (rasterio.errors.RasterioError, OSError, d3_inputs.D3InputsError):
                    # A read failure is DISCLOSED per metric, never a zero
                    # and never a dropped row (D13 E1 acceptance). It is
                    # disclosed for EVERY site on the footprint -- one
                    # failed read is one failed read, but it denies all of
                    # them a value and each must say so on its own row.
                    for site in footprint_sites:
                        partition_rows.extend(
                            _not_computable_metric_rows(
                                kind=kind,
                                reason="read_failed",
                                ctx_kwargs={
                                    **ctx_kwargs,
                                    "site_id": site,
                                    "d3_forced_threshold": bool(d3_forced_threshold_by_site[site]),
                                },
                                item_id=item_id,
                            )
                        )
                    continue
                decoded = _decode_d3_bands(raw_bands, kind=kind)
                metric_rows = (
                    spectral_metrics.geomedian_site_year_metrics(decoded)
                    if kind == "geomedian"
                    else spectral_metrics.fc_site_year_metrics(decoded)
                )
                # Same `metric_rows` object for every site on this
                # footprint: the values ARE identical and must not be
                # recomputed into a near-identical float.
                for site in footprint_sites:
                    ctx = trajectories.RowContext(
                        item_id=item_id,
                        site_id=site,
                        d3_forced_threshold=bool(d3_forced_threshold_by_site[site]),
                        **ctx_kwargs,
                    )
                    partition_rows.extend(trajectories.rows_from_metrics(metric_rows, ctx))

            if not partition_rows:
                total = total + trajectory_extract.PartitionResult(refused_empty=1)
                continue
            rows_by_partition[(collection_id, year)] = partition_rows

    # Nothing is written until EVERY partition's rows are in hand: a
    # partial failure above exits before this point, so a batch summary can
    # never describe a half-finished extraction. `input_assets` itself was
    # built earlier, above the partition loop -- see the comment there.
    written: list[dict[str, object]] = []
    for (collection_id, year), partition_rows in sorted(rows_by_partition.items()):
        partition = trajectory_extract.partition_dir(out_dir, collection_id, year)
        frame = pd.DataFrame(partition_rows)
        try:
            path, result = trajectory_extract.write_partition(frame, partition)
        except (trajectory_extract.TrajectoryExtractError, trajectories.TrajectoryError) as exc:
            typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
            raise typer.Exit(1) from None
        manifests.write_run_manifest(
            output=path,
            inputs=input_assets,
            config=resolved_config,
            git_state=git_state,
            package_versions=package_versions,
            resolved_args={
                "date": date,
                "scope": scope,
                "collection_id": collection_id,
                "year": year,
                "n_sites": len(extracted_sites),
                "site_ids": extracted_sites,
                **result.as_dict(),
            },
        )
        written.append({"collection_id": collection_id, "year": year, "path": str(path)})
        total = total + result

    summary_path = out_dir / "extraction_summary.json"
    summary = {
        "date": date,
        "scope": scope,
        "site_ids": extracted_sites,
        "partitions": written,
        "protocol_digest": frozen_digest,
        **total.as_dict(),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    manifests.write_run_manifest(
        output=summary_path,
        inputs=input_assets,
        config=resolved_config,
        git_state=git_state,
        package_versions=package_versions,
        resolved_args={"date": date, "scope": scope, **total.as_dict()},
    )
    typer.echo(
        json.dumps(
            {
                "output_dir": str(out_dir),
                "summary_path": str(summary_path),
                "manifest_path": str(summary_path) + manifests.MANIFEST_SUFFIX,
                "n_partitions_written": len(written),
                **total.as_dict(),
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


ReferenceCubeOption = typer.Option(
    ...,
    "--reference-cube",
    help=(
        "Parquet path of the jarrah Huntly per-site-year series "
        "(default reference: .../probe-out/detection_estimand/"
        "series_incumbent_w1.parquet; the w3/shifted variants share its schema)."
    ),
)
CompositesDirOption = typer.Option(
    ...,
    "--composites-dir",
    help=(
        "The jarrah pilot cube the reference was built from "
        "(.../interim/pilot/composites), holding nbart/ and fractional_cover/ "
        "annual COGs. The monitor's zonal engine samples THESE rasters, so the "
        "comparison is like-for-like and the D13 tolerances are meaningful."
    ),
)
SiteMetaOption = typer.Option(
    ...,
    "--site-meta",
    help=(
        "Parquet with site_id, x_incumbent, y_incumbent in EPSG:3577 (jarrah site_meta.parquet)."
    ),
)
WindowOption = typer.Option(
    3, "--window", help="Sampling window in pixels, square, centred (jarrah w1 = 3)."
)
SpectralTolOption = typer.Option(
    1e-6, "--spectral-abs", help="Absolute tolerance for NBR/NDMI (D13 E5 default 1e-6)."
)
FcTolOption = typer.Option(
    0.1, "--fc-abs", help="Absolute tolerance for FC metrics, percentage points (D13 E5: 0.1)."
)
RequireCountsOption = typer.Option(
    True,
    "--require-pixel-counts/--no-require-pixel-counts",
    help=(
        "D13 E5 requires exact member/valid pixel agreement. The jarrah reference table "
        "(HUNTLY_REFERENCE_SCHEMA) carries no pixel-count columns, so this honest default "
        "refuses until a counts-bearing reference is supplied, or the requirement is "
        "explicitly waived with --no-require-pixel-counts."
    ),
)

#: `--site-meta`'s required columns and their rename onto the `x`/`y`
#: `huntly_validation.sample_pilot_cube` takes -- jarrah's own `site_meta.
#: parquet` (`scripts/probes/detection_estimand/build_base.py` in
#: `~/Documents/jarrah-rehab`) carries `x_incumbent`/`y_incumbent`, not
#: `x`/`y`.
_SITE_META_COLUMNS: dict[str, str] = {
    "site_id": "site_id",
    "x_incumbent": "x",
    "y_incumbent": "y",
}


@app.command("validate-huntly")
def validate_huntly_cmd(
    config: Path = ConfigOption,
    date: str = DateOption,
    reference_cube: Path = ReferenceCubeOption,
    composites_dir: Path = CompositesDirOption,
    site_meta: Path = SiteMetaOption,
    window: int = WindowOption,
    spectral_abs: float = SpectralTolOption,
    fc_abs: float = FcTolOption,
    require_pixel_counts: bool = RequireCountsOption,
) -> None:
    """Validate the monitor's own zonal engine against the jarrah Huntly
    pilot cube (D13 E5, engine-parity re-scope: `docs/decisions/
    2026-08-25-e5-engine-parity-rescope.md`), and write the verdict
    `curated/huntly-validation/<date>/validation.json` that
    `extract-trajectories --scope statewide` gates on
    (`trajectory_extract.require_huntly_gate`).

    The comparison is engine parity, not a product test: `--composites-dir`
    is the SAME pilot cube `--reference-cube` was built from, sampled with
    the monitor's OWN zonal engine (`huntly_validation.sample_pilot_cube`)
    at jarrah's own site points (`--site-meta`), and compared against the
    reference at the D13 tolerances (`--spectral-abs`/`--fc-abs`). There is
    no cross-project site mapping and no fractional-cover rescaling -- both
    sides key on jarrah's own `site_id` and share the same rasters and
    units.

    The command writes a `passed: false` verdict just as readily as a
    `passed: true` one: a failing comparison is a RESULT with a manifest,
    never a crash and never a silent nothing. Only a malformed comparison
    (`HuntlyValidationError` -- e.g. a reference cube that cannot be read,
    or `--require-pixel-counts` against a reference carrying no counts) is
    a refusal.
    """
    resolved: ProjectConfig = _load_config_or_exit(config)
    resolved_config = resolved.model_dump(mode="json")
    git_state = _collect_git_state_disclosing_gaps(_REPO_ROOT)
    data_root = resolved.run.data_root

    try:
        site_meta_table = pq.read_table(site_meta)
    except (OSError, pa.ArrowInvalid) as exc:
        typer.echo(
            json.dumps(
                {"refusal": f"cannot read {site_meta}: {exc}", "site_meta": str(site_meta)},
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1) from None
    missing_site_meta_columns = [
        name for name in _SITE_META_COLUMNS if name not in site_meta_table.column_names
    ]
    if missing_site_meta_columns:
        typer.echo(
            json.dumps(
                {
                    "refusal": (
                        f"{site_meta} is missing column(s) {missing_site_meta_columns} -- "
                        f"jarrah site_meta must carry {list(_SITE_META_COLUMNS)}"
                    ),
                    "site_meta": str(site_meta),
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(1)
    sites = site_meta_table.select(list(_SITE_META_COLUMNS)).to_pandas()
    sites = sites.rename(columns=_SITE_META_COLUMNS)
    sites["site_id"] = sites["site_id"].astype(str)

    try:
        sampled = huntly_validation.sample_pilot_cube(composites_dir, sites, window=window)
        extracted = huntly_validation.melt_sampled_frame(sampled)
        reference = huntly_validation.read_reference_cube(reference_cube)
        tolerances = huntly_validation.Tolerances(
            spectral_abs=spectral_abs, fc_abs=fc_abs, require_pixel_counts=require_pixel_counts
        )
        report = huntly_validation.compare(extracted, reference, tolerances)
    except huntly_validation.HuntlyValidationError as exc:
        typer.echo(json.dumps({"refusal": str(exc)}, indent=2, sort_keys=True))
        raise typer.Exit(1) from None

    out_dir = data_root / "curated" / "huntly-validation" / date
    output_path = out_dir / "validation.json"
    _refuse_if_curated_output_already_exists(
        output_path, config=resolved_config, git_state=git_state
    )

    payload: dict[str, object] = {
        **report.as_dict(),
        "reference_cube": str(reference_cube),
        "composites_dir": str(composites_dir),
        "site_meta": str(site_meta),
        "window": window,
        "date": date,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    input_assets = [
        SourceAsset(
            uri=str(reference_cube),
            sha256=sha256_file(reference_cube),
            collection=None,
            snapshot_date=None,
            licence=None,
            redistribute_public=False,
        ),
        SourceAsset(
            uri=str(site_meta),
            sha256=sha256_file(site_meta),
            collection=None,
            snapshot_date=None,
            licence=None,
            redistribute_public=False,
        ),
    ]
    manifests.write_run_manifest(
        output=output_path,
        inputs=input_assets,
        config=resolved_config,
        git_state=git_state,
        resolved_args={
            "date": date,
            "reference_cube": str(reference_cube),
            "composites_dir": str(composites_dir),
            "site_meta": str(site_meta),
            "window": window,
            "spectral_abs": spectral_abs,
            "fc_abs": fc_abs,
            "require_pixel_counts": require_pixel_counts,
        },
    )
    manifest_path = str(output_path) + manifests.MANIFEST_SUFFIX
    typer.echo(
        json.dumps(
            {**payload, "output_path": str(output_path), "manifest_path": manifest_path},
            indent=2,
            sort_keys=True,
        )
    )


def _yaml_marked_error_detail(exc: yaml.MarkedYAMLError) -> dict[str, object]:
    """Structural-only detail for a `yaml.MarkedYAMLError`.

    Renders `problem` (the parser's short description) and the 1-indexed
    `line`/`column` the parser stopped at -- never `str(exc)` or `repr(exc)`,
    both of which include `Mark.get_snippet()`, the offending source line
    itself. `problem_mark` can be `None` on some `YAMLError` subclasses, so
    `line`/`column` fall back to `None` rather than raising.
    """
    mark = exc.problem_mark
    return {
        "error_type": type(exc).__name__,
        "problem": exc.problem,
        "line": (mark.line + 1) if mark is not None else None,
        "column": (mark.column + 1) if mark is not None else None,
    }


if __name__ == "__main__":
    app()
