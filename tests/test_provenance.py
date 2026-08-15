# Ported from jarrah-rehab tests/test_provenance.py (provenance-only slice) at
# commit cf1743e202d367fcb2016eca0a1563b4f9db240c (2026-08-15); MIT-relicensed
# by the same author. The original file also covered
# `jarrah_rehab.reporting.manifests`; that half now lives in
# `tests/test_manifests.py`, matching this project's module layout
# (`wa_mine_monitor.manifests`, not a `reporting` subpackage).
"""Tests for source-asset provenance primitives.

Covers `wa_mine_monitor.provenance` (SourceAsset, sha256_file,
collect_git_state).
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from wa_mine_monitor.provenance import SourceAsset, collect_git_state, sha256_file


def _init_git_repo(repo_root: Path) -> None:
    """Initialise a throwaway git repo with one committed file.

    Used so git-state tests never touch the live project repo.
    """
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True)
    (repo_root / "tracked.txt").write_text("original\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=repo_root, check=True, capture_output=True
    )


# --- SourceAsset --------------------------------------------------------


def test_source_asset_carries_data_identity_fields() -> None:
    asset = SourceAsset(
        uri="dea://scene/LS8_001",
        sha256=None,
        collection="ga_ls8c_ard_3",
        snapshot_date="2026-07-20",
        licence="CC-BY-4.0",
        redistribute_public=True,
    )
    assert asset.uri == "dea://scene/LS8_001"
    assert asset.sha256 is None
    assert asset.collection == "ga_ls8c_ard_3"
    assert asset.licence == "CC-BY-4.0"
    assert asset.redistribute_public is True


def test_source_asset_defaults_are_minimal_and_closed() -> None:
    """Only uri is required; unspecified data-identity fields default to
    unset/closed rather than silently assuming a licence permits redistribution."""
    asset = SourceAsset(uri="dea://scene/LS8_001", sha256=None)
    assert asset.collection is None
    assert asset.snapshot_date is None
    assert asset.licence is None
    assert asset.redistribute_public is False


def test_source_asset_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        SourceAsset(uri="dea://scene/LS8_001", sha256=None, bogus_field="x")  # type: ignore[call-arg]


# --- sha256_file ---------------------------------------------------------


def test_sha256_file_matches_hashlib_reference(tmp_path: Path) -> None:
    data = b"wa mine rehab spectral chronology" * 1000
    path = tmp_path / "asset.bin"
    path.write_bytes(data)

    assert sha256_file(path) == hashlib.sha256(data).hexdigest()


def test_sha256_file_streams_in_bounded_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Must call read() with a bounded chunk size across multiple calls,
    never a single unbounded read() -- these artefacts can be multi-gigabyte
    rasters."""
    data = b"x" * (2 * 1024 * 1024 + 17)
    path = tmp_path / "big.bin"
    path.write_bytes(data)

    read_sizes: list[int | None] = []
    real_open = open

    def spy_open(*args: object, **kwargs: object) -> object:
        fh = real_open(*args, **kwargs)  # type: ignore[call-overload]
        real_read = fh.read

        def spy_read(size: int = -1) -> bytes:
            read_sizes.append(size)
            return real_read(size)

        fh.read = spy_read  # type: ignore[method-assign]
        return fh

    monkeypatch.setattr("builtins.open", spy_open)
    digest = sha256_file(path)

    assert digest == hashlib.sha256(data).hexdigest()
    assert read_sizes, "sha256_file must call read() at least once"
    assert all(size not in (-1, None) for size in read_sizes), (
        "sha256_file must pass a bounded chunk size to read(), not read the whole file at once"
    )
    assert len(read_sizes) > 1, "a >2MB file must be streamed across multiple chunks"


# --- collect_git_state -----------------------------------------------------


def test_collect_git_state_reports_clean_repo(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    state = collect_git_state(tmp_path)

    assert state["dirty"] is False
    assert state["diff"] == ""
    assert isinstance(state["sha"], str) and len(state["sha"]) == 40


def test_collect_git_state_embeds_full_diff_when_dirty(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("changed contents\n")

    state = collect_git_state(tmp_path)

    assert state["dirty"] is True
    assert "changed contents" in state["diff"]


def test_collect_git_state_includes_untracked_file_content_in_diff(tmp_path: Path) -> None:
    """A tree whose only change is a new untracked file must not report
    `dirty=True` with an empty `diff` -- `git status --porcelain` (which
    drives `dirty`) counts untracked files, but `git diff HEAD` (which drove
    the old `diff`) does not."""
    _init_git_repo(tmp_path)
    (tmp_path / "new_module.py").write_text("import totally_new_code\n")

    state = collect_git_state(tmp_path)

    assert state["dirty"] is True
    assert "new_module.py" in state["diff"]
    assert "totally_new_code" in state["diff"]


def test_collect_git_state_dirty_from_untracked_file_alone_has_nonempty_diff(
    tmp_path: Path,
) -> None:
    """Regression for the specific silent-drift case: only an untracked file
    present, no tracked-file changes at all."""
    _init_git_repo(tmp_path)
    (tmp_path / "another_new_file.py").write_text("content\n")

    state = collect_git_state(tmp_path)

    assert state["dirty"] is True
    assert state["diff"] != ""


def test_collect_git_state_includes_content_of_untracked_directory(tmp_path: Path) -> None:
    """`git status --porcelain` collapses a whole untracked directory into a
    single `?? newpkg/` entry. Parsing that line naively and diffing the
    directory path itself against /dev/null errors and writes nothing --
    the directory's files must be enumerated individually instead."""
    _init_git_repo(tmp_path)
    (tmp_path / "newpkg").mkdir()
    (tmp_path / "newpkg" / "mod.py").write_text("import totally_new_code\n")

    state = collect_git_state(tmp_path)

    assert state["dirty"] is True
    assert "newpkg/mod.py" in state["diff"]
    assert "totally_new_code" in state["diff"]


def test_collect_git_state_includes_non_ascii_untracked_filename(tmp_path: Path) -> None:
    """`git status --porcelain` quotes and octal-escapes non-ASCII paths
    (e.g. `?? "r\\303\\251swm\\303\\251.py"`), so naively slicing the
    porcelain line yields a quoted path that never resolves on disk and is
    silently dropped from the diff."""
    _init_git_repo(tmp_path)
    (tmp_path / "réswmé.py").write_text("content\n")

    state = collect_git_state(tmp_path)

    assert state["dirty"] is True
    assert "réswmé.py" in state["diff"]
    assert "content" in state["diff"]


def test_collect_git_state_survives_non_utf8_bytes_in_tracked_and_untracked_files(
    tmp_path: Path,
) -> None:
    """Git emits raw bytes for a latin-1 text file (no NULs, so not "binary"),
    so a strict-UTF-8 decode of the diff raises `UnicodeDecodeError` and kills
    provenance capture for the whole run. Undecodable bytes must degrade to
    U+FFFD instead."""
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_bytes(b"caf\xe9 modified latin1\n")
    (tmp_path / "untracked_latin1.txt").write_bytes(b"caf\xe9 untracked latin1\n")

    state = collect_git_state(tmp_path)

    assert state["dirty"] is True
    assert "modified latin1" in state["diff"]
    assert "untracked latin1" in state["diff"]
