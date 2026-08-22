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
import pyogrio
import rasterio  # type: ignore[import-untyped]
import typer
import yaml
from pydantic import ValidationError

from wa_mine_monitor import (
    crosswalk,
    d3_inputs,
    d3_protocol,
    d3_threshold,
    dea_coverage,
    dea_raster,
    dea_volume,
    licence,
    manifests,
    maus_footprints,
    pixel_support,
    register,
    snapshots,
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
from wa_mine_monitor.sources import wa_regions
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
    # intersecting tile's ACTUAL grid, read from a catalogue item asset. ---
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
                    decoded, kind=kind
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
def apply_d3_threshold_cmd(config: Path = ConfigOption, date: str = DateOption) -> None:
    """Apply the derived D3 reduced-support threshold to the latest register
    (D13 D5): every register row gets exactly one `trajectory_status`
    (`register._TRAJECTORY_STATUSES`) plus `effective_pixel_support_px`/
    `d3_threshold_px`/`d3_eligible` (`register.assign_trajectory_
    eligibility`).

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
            n_star=n_star,
            criteria_passed=criteria_passed,
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
