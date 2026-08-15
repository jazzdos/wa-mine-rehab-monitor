# Ported from jarrah-rehab src/jarrah_rehab/reporting/manifests.py at commit cf1743e202d367fcb2016eca0a1563b4f9db240c (2026-08-15); MIT-relicensed by the same author.
"""Deterministic, immutable run manifests.

Every artefact this pipeline writes is accompanied by a
`<artefact>.run_manifest.json`, co-located with the artefact, that pins:

- the exact command (`argv`) and fully-resolved arguments that produced it;
- installed package versions (the tool's own version and its dependencies);
- the source assets consumed (URIs, collections, snapshot dates, local
  hashes) -- so a rerun that silently picks up new scenes is not a rerun;
- the sha256 of the output artefact itself;
- the git state (`sha`, `dirty`, `diff`) of the code that produced it, with
  the full working-tree diff embedded when dirty, so a dirty-tree run can be
  reconstructed -- subject to the scrubbing caveat below.

The embedded diff is secret-scrubbed like every other string in the manifest.
Scrubbing can rewrite a line, and a rewritten diff is not an appliable patch,
so the manifest records `git.diff_scrubbed` and `git.diff_scrub_count`: a
reader can see whether the recorded diff is verbatim and how many lines were
altered if not. A leaked credential is worse than a lossy diff, so scrubbing
wins the trade-off and the loss is disclosed rather than hidden.

The manifest JSON is written with sorted keys and stable (compact)
separators, terminated by a single trailing newline, so two runs with
identical inputs and an identical timestamp are byte-identical. Secret-shaped
keys in `config` and `resolved_args` are redacted via
`wa_mine_monitor.secrets.redact_secrets` before anything is serialised; every
string *value* surviving that pass is also run through
`wa_mine_monitor.secrets.scrub_text_secrets`, so a secret carried by value under
a non-secret-shaped key (e.g. a `stac_endpoint` URL with an `api_key` query
parameter) is still caught. Secrets carried by value elsewhere -- a token
following a `--flag` in `argv`, credential-shaped query parameters or
userinfo in a `SourceAsset.uri`, and anything of either shape embedded in the
git diff -- are scrubbed via `wa_mine_monitor.secrets.scrub_argv_secrets` (then
`scrub_url_secrets` on each surviving token, to catch a credential-shaped
query parameter behind a non-secret-shaped flag), `scrub_url_secrets` and
`scrub_text_secrets` respectively. None of this redaction logic is
reimplemented here.

Once written, a manifest is immutable: a second `write_run_manifest` call for
the same output path either no-ops (identical *provenance*) or raises
`FileExistsError` (differing provenance) -- it never silently overwrites a
provenance record already on disk, and the first write's timestamp is the one
that survives.

Provenance is the manifest minus `timestamp` (see `manifest_identity`), because
`timestamp` defaults to `now()`: comparing whole manifests would make two runs
with identical inputs always differ, so the no-op branch would be unreachable
outside tests. `manifest_matches` exposes that comparison for stage reuse --
deciding whether an already-computed stage output can be kept.

Governing rule: a number whose exact command cannot be re-run does not go in
the log.

Every filesystem path this module records -- `output.path`, each local-path
`SourceAsset.uri` under `inputs`, `config.data_root`, `argv[0]`, and every
LATER `argv` token that resolves to an existing local path (a flag VALUE,
e.g. `--source-gpkg /home/<account>/.../global.gpkg`) -- is made
ROOT-RELATIVE before it is written, never scrubbed after the fact, because
neither `redact_secrets` (key-name based) nor `scrub_string_leaves`
(credential-pattern based) has any reason to touch `/home/<account>/...` --
it is not credential-shaped. A scrubber that must be remembered is a filter;
a relative path cannot leak the account it was written under in the first
place. So `output.path` is recorded relative to the run's own `data_root`
(read out of the caller-supplied `config` mapping; see `_extract_data_root`,
which checks both the flat `config["data_root"]` shape and this project's
own nested `config["run"]["data_root"]` shape -- `config/base.yaml` declares
`run.data_root`, and a `ProjectConfig.model_dump()` preserves that nesting),
with a sibling `output.path_root` naming WHICH root as a string
(`"data_root"`, or `"unrooted"` when `data_root` is unset or does not
contain it) -- never the root's own absolute location. Each `inputs[].uri`
that is a bare local path (no URL scheme+netloc -- a `dea://` scene
reference or an `https://` URL is left alone, since neither carries an
account's home directory) gets the identical treatment via a sibling
`uri_root`, and the top-level `argv0_root`/`argv_paths_root` disclose the
same thing for `argv[0]` and every later `argv` token respectively (one
entry per `argv` position; `"not_local"` for a token that was not an
existing local path, so it is distinguishable from one that was reduced to
a bare filename because it sat outside `data_root`). `data_root`, wherever
it was found (`config["data_root"]` or
`config["run"]["data_root"]`), is replaced with its symbolic name outright,
in place, via `_relativize_config_data_root`. This is the same disclosure
principle as
`git.diff_scrubbed` above: a transformation applied to a provenance record
must say that it was applied, so every one of these fields names the root
rather than silently dropping the path down to a bare filename.

This module has no coupling to any project's config class: `config`
parameters are typed as `Mapping[str, Any]` throughout (the resolved/dumped
form of whatever config object a caller has), and `ProjectConfigLike` below
names the one attribute (`data_root`) this module actually cares about,
without importing a concrete config type to get it. jarrah-rehab's version
of this module additionally fell back to a hardcoded `REPO_ROOT` constant
(that project's own checkout root) as a second declared root; this module
carries no equivalent constant, so a path outside `data_root` is recorded as
`"unrooted"` rather than measured against a second, undeclared root.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib import metadata
from itertools import zip_longest
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from wa_mine_monitor.provenance import SourceAsset, sha256_file
from wa_mine_monitor.secrets import (
    redact_secrets,
    scrub_argv_secrets,
    scrub_string_leaves,
    scrub_url_secrets,
)


class ProjectConfigLike(Protocol):
    """Minimal structural shape a project config object needs for this
    module: just a `data_root` attribute.

    Defined locally (not imported from any concrete config class) so this
    module never couples to a project-specific config type. Nothing in this
    module currently takes a `ProjectConfigLike` directly -- every real call
    site passes `config.model_dump()` (a plain `Mapping[str, Any]`) -- but
    the type is named here so the one attribute this module actually reads
    off a config-shaped object (`data_root`) has a single, explicit
    definition rather than being an implicit assumption about dict keys.
    """

    data_root: Path


#: Symbolic name an emitted path is recorded relative to -- never the root's
#: own absolute location. `"unrooted"` is not a failure: it discloses that a
#: path fell outside the declared root, so only its final path component
#: survives (see `_root_relative`), rather than silently truncating it with
#: no trace that a truncation happened.
_ROOT_DATA = "data_root"
_ROOT_NONE = "unrooted"

#: Value written in place of `config["data_root"]`'s absolute location.
#: A resolved `data_root` names an account's home directory, and a manifest
#: carrying it verbatim is exactly the forward risk this module closes. A
#: reader who has their own `data_root` configured substitutes it back in
#: for this name.
_DATA_ROOT_MARKER = "<data_root>"


def _extract_data_root(config: Mapping[str, Any]) -> Path | None:
    """Pull `data_root` out of `config` as a `Path`, if present and path-like.

    Checks the FLAT shape (`config["data_root"]`) first, then this project's
    NESTED shape (`config["run"]["data_root"]`) -- `config/base.yaml` declares
    `run.data_root`, and the real call path is a `ProjectConfig.model_dump()`
    that preserves that nesting, so `{"run": {"data_root": ...}, ...}` is what
    every live caller actually passes. Checking the flat key first keeps this
    test suite's flat fixtures (`{"data_root": ...}`) working unchanged.

    Read BEFORE `config["data_root"]` / `config["run"]["data_root"]` is
    replaced with `_DATA_ROOT_MARKER` (see `_relativize_config_data_root`), so
    `output`'s own path can still be expressed relative to it.
    """
    raw = config.get("data_root")
    if raw is None:
        run_section = config.get("run")
        if isinstance(run_section, Mapping):
            raw = run_section.get("data_root")
    if raw is None:
        return None
    try:
        return Path(raw)
    except TypeError:
        return None


def _relativize_config_data_root(config: Mapping[str, Any]) -> dict[str, Any]:
    """Replace an absolute `data_root` with its symbolic name, wherever
    `_extract_data_root` found it.

    Handles both the flat shape (`config["data_root"]`) and this project's
    nested shape (`config["run"]["data_root"]`) -- see `_extract_data_root`.
    Leaves every other key, including `run`'s other fields, untouched; a
    config with neither shape passes through unchanged.
    """
    out = dict(config)
    if "data_root" in out:
        out["data_root"] = _DATA_ROOT_MARKER
    run_section = out.get("run")
    if isinstance(run_section, Mapping) and "data_root" in run_section:
        run_out = dict(run_section)
        run_out["data_root"] = _DATA_ROOT_MARKER
        out["run"] = run_out
    return out


def _root_relative(path: Path, *, data_root: Path | None) -> tuple[str, str]:
    """Express `path` relative to the declared `data_root`, naming which root
    rather than recording its absolute location.

    When `data_root` is unset, or `path` sits outside it -- most of this
    test suite's `tmp_path` fixtures, for instance -- the absolute location
    is never returned: only the final path component is kept, paired with
    `"unrooted"` so a reader can tell the directory prefix was deliberately
    elided rather than lost.

    Shared by `_root_relative_output` (`output.path`), `_root_relative_uri`
    (a local-filesystem `SourceAsset.uri`) and the `argv[0]` reduction in
    `_preflight_known_fields`, so the "which root, if any" logic lives in
    exactly one place rather than being re-derived at each call site.
    """
    if data_root is not None:
        try:
            relative = path.relative_to(data_root)
        except ValueError:
            pass
        else:
            return relative.as_posix(), _ROOT_DATA
    return path.name, _ROOT_NONE


def _root_relative_output(output: Path, *, data_root: Path | None) -> dict[str, Any]:
    """Express `output`'s location relative to the declared `data_root`,
    naming which root rather than recording its absolute location. See
    `_root_relative`.
    """
    value, root_name = _root_relative(output, data_root=data_root)
    return {"path": value, "path_root": root_name}


#: Value of `uri_root` / `argv0_root` for a value that was NOT treated as a
#: local filesystem path, so no root-relativisation applies to it at all --
#: distinct from `_ROOT_NONE` ("treated as local, but outside the declared
#: root"), because collapsing the two would hide that a `dea://` or
#: `https://` reference was recognised and deliberately left untouched
#: rather than silently matched by the local-path fallback.
_URI_NOT_LOCAL = "not_local"


def _root_relative_uri(uri: str, *, data_root: Path | None) -> dict[str, Any]:
    """Express a `SourceAsset.uri` relative to the declared `data_root`, when
    it is a bare local filesystem path -- exactly the shape `scrub_url_secrets`
    documents itself as leaving untouched (no URL scheme+netloc), and
    exactly the shape every real CLI call site actually builds
    (`SourceAsset(uri=str(path))` under `cfg.data_root`, resolved to an
    absolute path by config loading). A remote or scheme-qualified reference
    (`dea://...`, `https://...`) carries no account path to leak and is
    returned unchanged, with `uri_root` naming that it was not treated as a
    local path at all.
    """
    parsed = urlsplit(uri)
    if parsed.scheme and parsed.netloc:
        return {"uri": uri, "uri_root": _URI_NOT_LOCAL}
    try:
        path = Path(uri)
    except (TypeError, ValueError):
        return {"uri": uri, "uri_root": _URI_NOT_LOCAL}
    value, root_name = _root_relative(path, data_root=data_root)
    return {"uri": value, "uri_root": root_name}


def root_relative_path(path: Path, *, config: Mapping[str, Any]) -> tuple[str, str]:
    """Express `path` relative to `config`'s declared `data_root`, naming
    which root (`"data_root"` or `"unrooted"`) rather than recording its
    absolute location -- the public form of the reduction `write_run_manifest`
    already applies to `output.path`, `inputs[].uri` and `argv[0]` (see
    `_root_relative`).

    For a caller (e.g. `cli.py`'s `fetch-maus-extract`) that must record
    ANOTHER local path inside `resolved_args` -- `write_run_manifest` scrubs
    `resolved_args` for secrets but does not root-relativise it, because
    `resolved_args` is caller-defined free-form content, not one of this
    module's own known path-shaped fields. Returns `(reduced_value,
    root_name)`; the caller is responsible for naming both under whatever
    keys suit its own `resolved_args` shape (e.g. `source_local_path` /
    `source_local_path_root`), the same disclosure principle this module's
    docstring states for every other path it reduces: a transformation
    applied to a provenance record must say that it was applied.
    """
    data_root = _extract_data_root(config)
    return _root_relative(Path(path), data_root=data_root)


def _root_relative_argv0(argv0: str, *, data_root: Path | None) -> tuple[str, str]:
    """Express `argv[0]` relative to the declared `data_root`, the same way
    `_root_relative_output` treats `output.path`.

    On the real call path `argv[0]` is the interpreter or console-script
    path under the active venv (e.g. `/home/<account>/.../.venv/bin/wa-mine-monitor`),
    which is as account-identifying as `output.path` -- neither
    `scrub_argv_secrets` nor `scrub_url_secrets` is credential-pattern based
    and neither has any reason to touch it. A synthetic `argv[0]` such as
    this suite's `"wa-mine-monitor"` is not absolute and sits outside
    `data_root`, so it falls through to the `"unrooted"` branch unchanged --
    the same no-op `_root_relative` already guarantees for an `output`
    outside `data_root`.
    """
    return _root_relative(Path(argv0), data_root=data_root)


def _root_relative_argv_token(token: str, *, data_root: Path | None) -> tuple[str, str]:
    """Express one argv token AFTER `argv[0]` relative to the declared
    `data_root`, but ONLY when it resolves to an EXISTING local path.

    THE DEFECT this closes: before this function existed, `_preflight_known_fields`
    root-relativised `argv[0]` alone (see `_root_relative_argv0`) and left
    every later token untouched, so an absolute path passed as a flag VALUE
    -- `--source-gpkg /home/<account>/data/jarrah-rehab/raw/maus-v2/global.gpkg`,
    exactly what `fetch-maus-extract` receives -- was written into the
    manifest's `argv` verbatim, even though the SAME path was already being
    root-relativised three other places in that same manifest
    (`inputs[0].uri`, and `resolved_args.source_local_path`/`output.path`
    via `root_relative_path`). This module's own docstring states "every
    filesystem path this module records ... is made ROOT-RELATIVE before it
    is written"; a verbatim `argv` token was the one path this module forgot.

    Unlike `argv[0]` (always a real filesystem path on the live call path)
    or a `SourceAsset.uri` (always caller-typed as a path/URI reference), a
    later argv token is heterogeneous -- a subcommand name, a date, a flag,
    a secret-shaped value, or a genuine local path. Reducing every token
    unconditionally would relativise ordinary non-path values that merely
    happen to resolve under `_root_relative`'s fallback; gating on
    `Path(token).exists()` keeps the reduction to tokens that are actually
    filesystem references, the same criterion this module's own public
    `root_relative_path` helper exists for a caller to apply itself (see
    `cli.py`'s `fetch-maus-extract`, which now passes its real `argv`
    through this path instead of duplicating the check there).

    A URL-shaped token (scheme + netloc, e.g. `--stac-url
    https://x.gov.au/wfs?...`) is left alone -- `Path(url).exists()` is
    always `False` for one, so this is a documentation of that fact rather
    than a separate branch, matching `_root_relative_uri`'s explicit
    `_URI_NOT_LOCAL` treatment of the identical shape.

    Returns `(token, _URI_NOT_LOCAL)` for anything not treated as an
    existing local path, so a reader can tell "not reduced because there
    was nothing to reduce" apart from "reduced to a bare filename because it
    sat outside `data_root`" (`_ROOT_NONE`).
    """
    try:
        exists = Path(token).exists()
    except OSError:
        exists = False
    if not exists:
        return token, _URI_NOT_LOCAL
    return _root_relative(Path(token), data_root=data_root)


def _scrub_git_state(git_state: Mapping[str, Any]) -> dict[str, Any]:
    """Scrub `git_state` and disclose whether the diff survived it verbatim.

    Adds `diff_scrubbed` and `diff_scrub_count` so a reader is never left
    believing a rewritten diff is the exact working tree.
    """
    scrubbed = scrub_string_leaves(redact_secrets(dict(git_state)))

    original_diff = git_state.get("diff")
    scrubbed_diff = scrubbed.get("diff")
    if isinstance(original_diff, str) and isinstance(scrubbed_diff, str):
        original_lines = original_diff.splitlines()
        scrubbed_lines = scrubbed_diff.splitlines()
        altered = sum(
            1
            for before, after in zip_longest(original_lines, scrubbed_lines, fillvalue=None)
            if before != after
        )
    else:
        # No diff to compare (a clean tree omits it, or a caller supplied a
        # non-string). Report honestly rather than asserting "not scrubbed".
        altered = 0

    scrubbed["diff_scrubbed"] = altered > 0
    scrubbed["diff_scrub_count"] = altered
    return scrubbed


#: Keys excluded from a manifest's provenance identity. `timestamp` is wall
#: clock, not provenance: two runs of the same code over the same inputs
#: producing the same bytes are the same run for reuse and immutability
#: purposes, however far apart they happened.
_IDENTITY_EXCLUDED_KEYS = frozenset({"timestamp"})


def manifest_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The provenance-bearing subset of a manifest: everything but `timestamp`."""
    return {key: value for key, value in manifest.items() if key not in _IDENTITY_EXCLUDED_KEYS}


def manifest_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """True when two manifests record the same provenance, ignoring `timestamp`.

    This is the reuse test: a stage whose manifest matches the one already on
    disk produced its output from the same code, config, inputs and package
    versions, and need not be recomputed.
    """
    return manifest_identity(left) == manifest_identity(right)


#: Suffix appended to an output artefact's full path to derive its manifest
#: path, e.g. `result.parquet` -> `result.parquet.run_manifest.json`.
MANIFEST_SUFFIX = ".run_manifest.json"


def _manifest_path(output: Path) -> Path:
    return Path(str(output) + MANIFEST_SUFFIX)


def _installed_package_versions() -> dict[str, str]:
    """Snapshot of every installed distribution's version, sorted by name.

    Captures the full runtime environment (not just this package), since a
    rerun with a silently upgraded dependency is exactly the kind of drift
    a manifest exists to catch.
    """
    versions: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if not name:
            continue
        versions[name] = dist.version
    return dict(sorted(versions.items()))


#: The manifest fields a caller knows BEFORE it does any work, in the order
#: `write_run_manifest` writes them. Everything else in a manifest --
#: `resolved_args` (a run reports what it resolved), `inputs` (hashed as they
#: are read) and `output.sha256` (the artefact must exist) -- is knowable only
#: afterwards, which is what bounds `preflight_manifest_conflict` below.
_PREFLIGHT_KNOWN_FIELDS: tuple[str, ...] = (
    "argv",
    "argv0_root",
    "argv_paths_root",
    "config",
    "git",
    "package_versions",
)


def _preflight_known_fields(
    *,
    config: Mapping[str, Any],
    git_state: Mapping[str, Any],
    argv: Sequence[str] | None,
    package_versions: Mapping[str, str],
) -> dict[str, Any]:
    """Build the `_PREFLIGHT_KNOWN_FIELDS` subset of a manifest.

    Shared verbatim by `write_run_manifest` and `preflight_manifest_conflict`
    so the two cannot drift: a pre-flight that scrubbed or ordered a field
    differently from the writer would compare two different normalisations and
    report a conflict that the real write does not have, or miss one it does.
    """
    argv_tokens = list(argv) if argv is not None else list(sys.argv)
    # `argv[0]` is root-relativised BEFORE the secret scrub, mirroring
    # `output.path` -- it is an absolute interpreter/console-script path on
    # the real call path, not credential-shaped, so nothing else in this
    # pipeline would otherwise touch it. See `_root_relative_argv0`. Every
    # LATER token that resolves to an existing local path (a flag VALUE such
    # as `--source-gpkg /home/<account>/.../global.gpkg`) gets the identical
    # treatment via `_root_relative_argv_token`, also before the secret
    # scrub -- see that function's docstring for the defect this closes.
    # `argv_paths_root` discloses which root (if any) each token, including
    # `argv[0]`, was measured from, one entry per `argv` position.
    data_root = _extract_data_root(config)
    argv0_root = _ROOT_NONE
    argv_paths_root: list[str] = []
    if argv_tokens:
        reduced_argv0, argv0_root = _root_relative_argv0(argv_tokens[0], data_root=data_root)
        argv_paths_root.append(argv0_root)
        reduced_rest: list[str] = []
        for token in argv_tokens[1:]:
            reduced_token, token_root = _root_relative_argv_token(token, data_root=data_root)
            reduced_rest.append(reduced_token)
            argv_paths_root.append(token_root)
        argv_tokens = [reduced_argv0, *reduced_rest]
    return {
        "argv": [scrub_url_secrets(token) for token in scrub_argv_secrets(argv_tokens)],
        "argv0_root": argv0_root,
        "argv_paths_root": argv_paths_root,
        "config": scrub_string_leaves(redact_secrets(_relativize_config_data_root(config))),
        # Scrubbed as a whole rather than `diff`-only: `git_state` is an open
        # mapping (`collect_git_state` is the expected place to add fields such
        # as `remote_url`, and an HTTPS remote routinely embeds a PAT), so every
        # string leaf gets the same treatment `config` and `resolved_args` get.
        "git": _scrub_git_state(git_state),
        "package_versions": dict(package_versions),
    }


def preflight_manifest_conflict(
    output: Path,
    *,
    config: Mapping[str, Any],
    git_state: Mapping[str, Any],
    argv: Sequence[str] | None = None,
    package_versions: Mapping[str, str] | None = None,
) -> str | None:
    """Name the immutable-manifest refusal at `output` that is already CERTAIN,
    or return `None`.

    `write_run_manifest` can only refuse after the artefact exists, because it
    hashes it -- so the refusal lands at the END of a run. The provenance in
    `_PREFLIGHT_KNOWN_FIELDS` is fixed before the first byte is read, so a run
    doomed by any of it can be refused in milliseconds instead of after a
    full, wasted compute.

    The check is deliberately ONE-SIDED: it fires only where a difference
    already guarantees refusal, and stays silent otherwise. A `None` is
    therefore NOT a promise that the write will succeed -- `resolved_args`,
    `inputs` and the artefact's own `sha256` are all unknowable here, and any
    of them can still differ. Guessing at them would refuse legitimate reruns,
    including the byte-identical rerun `write_run_manifest` treats as a no-op.
    """
    manifest_path = _manifest_path(output)
    if not manifest_path.exists():
        return None

    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    prospective = json.loads(
        json.dumps(
            _preflight_known_fields(
                config=config,
                git_state=git_state,
                argv=argv,
                package_versions=(
                    dict(package_versions)
                    if package_versions is not None
                    else _installed_package_versions()
                ),
            ),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )
    differing = [
        field
        for field in _PREFLIGHT_KNOWN_FIELDS
        if field in existing and existing[field] != prospective[field]
    ]
    if not differing:
        return None
    return (
        f"a run manifest already sits at {manifest_path} and its {', '.join(differing)} "
        f"differ(s) from this run's, so the write at the END of this run WILL be refused "
        f"(manifests are immutable). Move the existing artefact and manifest aside together, "
        f"or write to a new path, before starting the work. Output: {output}"
    )


def write_run_manifest(
    output: Path,
    inputs: Sequence[SourceAsset],
    config: Mapping[str, Any],
    git_state: Mapping[str, Any],
    *,
    argv: Sequence[str] | None = None,
    resolved_args: Mapping[str, Any] | None = None,
    timestamp: datetime | None = None,
    package_versions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Write an immutable, deterministic run manifest next to `output`.

    Refuses to write a manifest for an output that does not exist -- a
    manifest describing an artefact that was never produced would
    misrepresent the run.

    `git_state` is always caller-supplied (see `collect_git_state`) so this
    function never shells out to git itself and tests never depend on the
    live repo.
    """
    if not output.exists():
        raise FileNotFoundError(
            f"cannot write a run manifest for {output}: output artefact does not exist"
        )

    ts = timestamp if timestamp is not None else datetime.now(UTC)
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(
            "timestamp must be timezone-aware -- a naive datetime would be silently "
            "reinterpreted as machine-local time, corrupting the provenance record"
        )
    ts_str = ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    resolved_versions = (
        dict(package_versions) if package_versions is not None else _installed_package_versions()
    )

    data_root = _extract_data_root(config)
    scrubbed_inputs = []
    for asset in inputs:
        asset_dict = json.loads(asset.model_dump_json())
        # Root-relativise a bare local-path `uri` BEFORE the secret scrub,
        # the same order `argv0_root` uses -- `scrub_url_secrets` leaves a
        # schemeless/netloc-less string (a bare local path) unchanged by its
        # own contract, so this is the only pass that touches it. See
        # `_root_relative_uri`.
        relativized = _root_relative_uri(asset_dict["uri"], data_root=data_root)
        asset_dict["uri"] = scrub_url_secrets(relativized["uri"])
        asset_dict["uri_root"] = relativized["uri_root"]
        scrubbed_inputs.append(asset_dict)

    manifest: dict[str, Any] = {
        "timestamp": ts_str,
        **_preflight_known_fields(
            config=config,
            git_state=git_state,
            argv=argv,
            package_versions=resolved_versions,
        ),
        "resolved_args": (
            scrub_string_leaves(redact_secrets(dict(resolved_args)))
            if resolved_args is not None
            else {}
        ),
        "inputs": scrubbed_inputs,
        "output": {
            **_root_relative_output(output, data_root=data_root),
            "sha256": sha256_file(output),
        },
    }

    # `default=str` coerces anything `json` cannot natively serialise --
    # notably `Path` (e.g. a config's `data_root`), `date`/`datetime`, and
    # `Decimal`/`UUID` values that can appear in `config` or `resolved_args`
    # -- deterministically, since `str()` on these types is stable across
    # runs. Without it, `write_run_manifest` crashes on a real config's
    # `.model_dump()`, its primary intended caller.
    manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str) + "\n"
    manifest_path = _manifest_path(output)
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        # Compare against the JSON-normalised form of the manifest just built,
        # not the in-memory `manifest` dict itself. `existing` came off disk
        # via `json.loads`, so any Path/date/Decimal leaf it once held is
        # already a plain `str` (per the `default=str` coercion above);
        # `manifest` in memory still holds the original non-JSON-native
        # object. Comparing the two directly makes the no-op branch
        # unreachable for byte-identical provenance, so round-trip `manifest`
        # through the same `default=str` serialisation first.
        if manifest_matches(existing, json.loads(manifest_json)):
            # Same provenance, later clock. The record on disk is the one that
            # stands, so return it rather than the manifest just built -- the
            # caller must not be handed a timestamp that was never written.
            return existing
        raise FileExistsError(
            f"refusing to overwrite existing run manifest at {manifest_path}: its provenance "
            "differs from the manifest just built. Manifests are immutable provenance "
            "records -- write the artefact and manifest to a new path instead."
        )
    manifest_path.write_text(manifest_json, encoding="utf-8")

    return manifest
