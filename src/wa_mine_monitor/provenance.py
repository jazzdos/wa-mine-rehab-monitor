# Ported from jarrah-rehab src/jarrah_rehab/provenance.py at commit cf1743e202d367fcb2016eca0a1563b4f9db240c (2026-08-15); MIT-relicensed by the same author.
"""Source-asset provenance primitives.

Data identity matters as much as code identity here: every input this
project consumes (DEA scenes, MINEDEX/tenement extracts, Maus polygons)
comes from outside the repo and can change underneath a rerun. A rerun that
silently picks up new scenes is not a rerun -- so every source asset
consumed by a pipeline run is modelled explicitly (`SourceAsset`) and hashed
where a local copy exists (`sha256_file`), and the exact git state of the
code that produced a run is captured (`collect_git_state`) so a dirty
tree's text content can be reconstructed -- the diff is textual, so changes
to binary files are not captured and such a tree is not reconstructible from
it.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

#: Default streaming chunk size for `sha256_file`. These artefacts (rasters,
#: STAC downloads) can be multi-gigabyte, so the whole file must never be
#: read into memory at once.
_DEFAULT_CHUNK_SIZE = 1_048_576  # 1 MiB


class SourceAsset(BaseModel):
    """One external input consumed by a pipeline run.

    `sha256` is nullable because a remote STAC item referenced by URI may
    never be hashed locally (e.g. a composite built directly from a
    streamed read). Every other data-identity field defaults to unset or
    closed rather than assuming a licence permits redistribution.
    """

    model_config = ConfigDict(extra="forbid")

    #: Locator for the source -- a STAC item URI, DEA `dea://` scene
    #: reference, or a local path/URL for non-STAC sources (MINEDEX,
    #: tenements, Maus).
    uri: str
    #: sha256 of a local copy of this asset, if one was hashed for this run.
    sha256: str | None = None
    #: Source collection name, e.g. `ga_ls8c_ard_3`.
    collection: str | None = None
    #: The date this asset was fetched/extracted, e.g. a MINEDEX snapshot
    #: date or a tenement extract date. This is what pins "which version of
    #: the outside world" a run saw.
    snapshot_date: date | None = None
    #: Licence under which the source is distributed, e.g. `CC-BY-4.0`.
    licence: str | None = None
    #: Whether this asset may be redistributed publicly. Defaults closed --
    #: enforced again at the export boundary (see the licence gate in
    #: `export_gate.py`).
    redistribute_public: bool = False


def sha256_file(path: Path, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> str:
    """Compute the sha256 hex digest of `path`, streaming in chunks.

    Never reads the whole file into memory -- these artefacts can be
    multi-gigabyte rasters.
    """
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def collect_git_state(repo_root: Path) -> dict[str, Any]:
    """Collect the real git state (`sha`, `dirty`, `diff`) of `repo_root`.

    `diff` is the full working-tree diff against `HEAD` (staged and
    unstaged) plus a per-file `--no-index` diff against `/dev/null` for every
    untracked (`??`) path, embedded whenever the tree is dirty, so a
    dirty-tree run -- including one whose only change is a new untracked
    file -- can be reconstructed, subject to one caveat: the diff is textual,
    so changes to binary files are not captured and such a tree is not
    reconstructible from it.
    Callers that need this to be injectable for tests should call this once
    and pass the resulting dict to `write_run_manifest`'s `git_state`
    argument -- `write_run_manifest` itself never calls git.
    """
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    dirty = bool(status.strip())
    diff = ""
    if dirty:
        diff = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        ).stdout
        # `git diff HEAD` only covers tracked content -- an untracked file
        # (a `??` line in porcelain status) contributes to `dirty` but is
        # invisible to that diff. Without this, a dirty tree whose only
        # change is a new untracked file records `dirty=True` with an empty
        # diff, capturing none of the code that produced the run. Diff each
        # untracked path against /dev/null and append.
        #
        # Deliberately not parsed from `status` porcelain output: a `??`
        # line collapses an entire untracked *directory* into one entry
        # (`?? newpkg/`), and `git diff --no-index -- /dev/null newpkg/`
        # errors without writing anything, silently dropping that content.
        # Porcelain also quotes and octal-escapes non-ASCII paths (e.g.
        # `?? "r\303\251swm\303\251.py"`), so slicing `line[3:]` yields a
        # quoted path that never resolves on disk. `git ls-files` enumerates
        # individual untracked files (recursing into directories) and, with
        # `-z`, emits raw NUL-separated unquoted paths.
        untracked_listing = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        ).stdout
        untracked_paths = [path for path in untracked_listing.split("\0") if path]
        for rel_path in untracked_paths:
            untracked_diff = subprocess.run(
                # `-c core.quotepath=false` keeps non-ASCII paths in the
                # diff's own `a/`/`b/` headers as literal UTF-8 rather than
                # octal-escaped and quoted (git's default), matching the
                # unquoted paths `git ls-files -z` already gave us above.
                [
                    "git",
                    "-c",
                    "core.quotepath=false",
                    "diff",
                    "--no-index",
                    "--",
                    "/dev/null",
                    rel_path,
                ],
                cwd=repo_root,
                # `git diff --no-index` exits 1 when it finds a difference
                # (the expected, common case here) and only a genuine
                # invocation error should be treated as a failure -- so this
                # deliberately does not raise on a nonzero exit status.
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            ).stdout
            diff += untracked_diff
    return {"sha": sha, "dirty": dirty, "diff": diff}
