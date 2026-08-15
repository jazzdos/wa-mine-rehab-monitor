# Ported from jarrah-rehab tests/test_provenance.py (manifest-only slice) at
# commit cf1743e202d367fcb2016eca0a1563b4f9db240c (2026-08-15); MIT-relicensed
# by the same author. Split out of the original combined file to match this
# project's module layout (`wa_mine_monitor.manifests`, not
# `jarrah_rehab.reporting.manifests`). One test was dropped rather than
# ported: `test_manifest_writes_from_real_project_config`, which exercised a
# real `jarrah_rehab.config.load_config` / `config/pilot.yaml` round-trip --
# a jarrah-domain fixture this project has no equivalent of, and no
# `ProjectConfig` class exists yet in this repo (T3 plan step 3, unbuilt as
# of this file's authoring). Its replacement here,
# `test_manifest_relativizes_data_root_from_nested_run_config_shape`, drives
# the real writer over a hand-built mapping matching this project's actual
# nested config shape (`config/base.yaml`'s `run.data_root`) instead -- not a
# `ProjectConfig` instance, but the same nested-mapping contract one would
# `model_dump()` into. Every other behavioural test in the original file is
# retained.
"""Tests for deterministic, immutable run manifests.

Covers `wa_mine_monitor.manifests` (write_run_manifest, manifest_matches,
preflight_manifest_conflict).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wa_mine_monitor.manifests import (
    manifest_matches,
    preflight_manifest_conflict,
    write_run_manifest,
)
from wa_mine_monitor.provenance import SourceAsset

# --- write_run_manifest ----------------------------------------------------


def test_manifest_records_source_hash_scene_ids_and_git_state(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    manifest = write_run_manifest(
        output=output,
        inputs=[SourceAsset(uri="dea://scene/LS8_001", sha256=None)],
        config={"end_year": 2025},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
    )
    assert manifest["inputs"][0]["uri"] == "dea://scene/LS8_001"
    assert manifest["git"]["sha"] == "abc123"


def test_manifest_path_is_output_path_with_suffix_appended(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
    )
    expected_path = tmp_path / "result.parquet.run_manifest.json"
    assert expected_path.exists()


def test_manifest_refuses_to_write_for_missing_output(tmp_path: Path) -> None:
    output = tmp_path / "does_not_exist.parquet"
    with pytest.raises(FileNotFoundError):
        write_run_manifest(
            output=output,
            inputs=[],
            config={},
            git_state={"sha": "abc123", "dirty": False, "diff": ""},
        )


def test_manifest_records_output_sha256(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    manifest = write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
    )
    assert manifest["output"]["sha256"] == hashlib.sha256(b"result").hexdigest()
    # `output` sits under `tmp_path`, which is not a declared `data_root` (no
    # `data_root` key in this test's config) -- so its absolute location must
    # NOT be recorded, only its own filename, with `path_root` disclosing
    # that the directory prefix was elided. See
    # `test_manifest_never_contains_an_absolute_home_path` for the pattern
    # that generalises this.
    assert manifest["output"]["path"] == "result.parquet"
    assert manifest["output"]["path_root"] == "unrooted"


def test_manifest_never_contains_an_absolute_home_path(tmp_path: Path) -> None:
    """THE DEFECT, closed: `output.path` was `str(output)` and
    `config["data_root"]` (an ABSOLUTE path) was carried through unscrubbed,
    since neither `redact_secrets` (key-name based) nor `scrub_string_leaves`
    (credential-pattern based) has any reason to touch a home-directory path.
    A committed manifest would have carried `/home/<account>/...` twice over.

    Pattern-based (`/home/`, `/Users/`) rather than asserting against this
    developer's own account name, so it catches ANY account the pipeline
    might run under, not just the one that produced this test run -- and it
    fabricates a fake home tree under `tmp_path` so the assertion holds
    regardless of where pytest's own tmp dir happens to sit on this machine.

    Driven through the REAL writer, not a helper -- a unit test on a
    path-relativising helper cannot see whether `write_run_manifest` is
    actually calling it.
    """
    fake_home = tmp_path / "home" / "someaccount"
    data_root = fake_home / "data" / "wa-mine-monitor"
    output_dir = data_root / "interim" / "pilot"
    output_dir.mkdir(parents=True)
    output = output_dir / "result.parquet"
    output.write_bytes(b"result")

    # THE case this test previously missed: a `SourceAsset` whose `uri` is a
    # bare local path under `data_root` -- exactly what every real CLI call
    # site builds (`SourceAsset(uri=str(path))`), and an `argv[0]` shaped
    # like the venv console-script path a real invocation carries. Neither
    # channel could leak a home path via the fixture this test used before
    # (`dea://...` and a synthetic `"wa-mine-monitor"` argv[0]), so the
    # guard passed while production still wrote the account's home
    # directory.
    minedex_csv = data_root / "raw" / "minedex" / "sites.csv"
    minedex_csv.parent.mkdir(parents=True)
    minedex_csv.write_text("site_id,x,y\n")
    venv_argv0 = str(
        fake_home / "projects" / "wa-mine-monitor" / ".venv" / "bin" / "wa-mine-monitor"
    )

    manifest = write_run_manifest(
        output=output,
        inputs=[
            SourceAsset(uri="dea://scene/LS8_001", sha256=None),
            SourceAsset(uri=str(minedex_csv), sha256=None),
        ],
        config={"data_root": str(data_root), "end_year": 2025},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
        argv=[venv_argv0, "build-register", "--config", "base.yaml"],
    )

    # The in-memory structure handed back to the caller...
    in_memory_text = json.dumps(manifest, default=str)
    assert "/home/" not in in_memory_text
    assert "/Users/" not in in_memory_text

    # ...and the bytes actually committed to disk, which is what a report
    # package would ship.
    manifest_path = output_dir / "result.parquet.run_manifest.json"
    on_disk_text = manifest_path.read_text(encoding="utf-8")
    assert "/home/" not in on_disk_text
    assert "/Users/" not in on_disk_text

    # The transformation is disclosed, not silent: a reader can see which
    # root each relative path is measured from.
    assert manifest["output"]["path"] == "interim/pilot/result.parquet"
    assert manifest["output"]["path_root"] == "data_root"
    assert manifest["config"]["data_root"] == "<data_root>"

    # The remote reference is untouched (nothing to relativise)...
    assert manifest["inputs"][0]["uri"] == "dea://scene/LS8_001"
    assert manifest["inputs"][0]["uri_root"] == "not_local"
    # ...and the local-path input -- the dominant real-world case, per every
    # `SourceAsset(uri=str(path))` call site in a real CLI -- is relativised
    # and disclosed the same way `output.path` is.
    assert manifest["inputs"][1]["uri"] == "raw/minedex/sites.csv"
    assert manifest["inputs"][1]["uri_root"] == "data_root"

    # `argv[0]` gets the identical treatment: relativised (falling back to
    # its basename here, since it sits outside the declared `data_root`) and
    # disclosed via a sibling, never the account's venv path verbatim.
    assert manifest["argv"][0] == "wa-mine-monitor"
    assert manifest["argv0_root"] == "unrooted"


def test_manifest_relativizes_an_existing_local_path_carried_as_a_later_argv_token(
    tmp_path: Path,
) -> None:
    """THE DEFECT, closed: `argv[0]` was root-relativised, but a LATER argv
    token was not -- so an absolute local path passed as a FLAG VALUE (e.g.
    `fetch-maus-extract --source-gpkg /home/<account>/.../global.gpkg`) was
    written into `argv` verbatim, even though the identical path is already
    reduced three other places in the same manifest (`inputs[].uri`,
    `resolved_args.source_local_path` via `manifests.root_relative_path`).
    Reproduced here directly against `write_run_manifest`, not a helper --
    the vacuous regression this closes (`tests/sources/test_maus.py`) came
    from asserting against a `sys.argv` that never carried the real path at
    all, not from the reduction working.

    A token that is NOT an existing local path (a subcommand name, a date,
    a bare relative filename that happens not to exist under `tmp_path`) is
    left untouched, with `argv_paths_root` disclosing `"not_local"` for it
    -- distinct from `"unrooted"` (treated as a path, but outside
    `data_root`) and from `"data_root"` (reduced to a `data_root`-relative
    form).
    """
    fake_home = tmp_path / "home" / "someaccount"
    data_root = fake_home / "data" / "wa-mine-monitor"
    output_dir = data_root / "interim" / "pilot"
    output_dir.mkdir(parents=True)
    output = output_dir / "result.parquet"
    output.write_bytes(b"result")

    source_gpkg = fake_home / "data" / "jarrah-rehab" / "raw" / "maus-v2" / "global.gpkg"
    source_gpkg.parent.mkdir(parents=True)
    source_gpkg.write_bytes(b"gpkg-bytes")

    under_data_root_arg = data_root / "raw" / "minedex" / "sites.csv"
    under_data_root_arg.parent.mkdir(parents=True)
    under_data_root_arg.write_text("site_id,x,y\n")

    manifest = write_run_manifest(
        output=output,
        inputs=[],
        config={"data_root": str(data_root)},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
        argv=[
            "wa-mine-monitor",
            "fetch-maus-extract",
            "--date",
            "2026-08-15",
            "--source-gpkg",
            str(source_gpkg),
            "--extra-local-arg",
            str(under_data_root_arg),
        ],
    )

    # Neither absolute local path survives, in memory or on disk.
    manifest_text = json.dumps(manifest, default=str)
    assert str(source_gpkg) not in manifest_text
    assert str(under_data_root_arg) not in manifest_text
    manifest_path = output_dir / "result.parquet.run_manifest.json"
    on_disk_text = manifest_path.read_text(encoding="utf-8")
    assert str(source_gpkg) not in on_disk_text
    assert str(under_data_root_arg) not in on_disk_text

    # Positive assertions: each existing local path is reduced, disclosed,
    # and every OTHER token (subcommand, flag names, the date) is untouched.
    assert manifest["argv"] == [
        "wa-mine-monitor",
        "fetch-maus-extract",
        "--date",
        "2026-08-15",
        "--source-gpkg",
        "global.gpkg",
        "--extra-local-arg",
        "raw/minedex/sites.csv",
    ]
    assert manifest["argv_paths_root"] == [
        "unrooted",  # argv[0]: outside data_root, reduced to its basename
        "not_local",  # subcommand name: never an existing path
        "not_local",  # --date flag name
        "not_local",  # the date value itself
        "not_local",  # --source-gpkg flag name
        "unrooted",  # the source gpkg: exists, but outside data_root
        "not_local",  # --extra-local-arg flag name
        "data_root",  # the second local path: exists, under data_root
    ]


def test_manifest_relativizes_data_root_from_nested_run_config_shape(
    tmp_path: Path,
) -> None:
    """THE DEFECT this test closes: `_extract_data_root` and
    `_relativize_config_data_root` read a FLAT `config["data_root"]`, but this
    project's real config shape is NESTED -- `config/base.yaml` declares
    `run.data_root`, and `ProjectConfig.model_dump()` (per the T3 plan step 3)
    yields `{"run": {"data_root": ..., "redistribute_public": ...},
    "sources": {...}}`. Driven through that exact nested mapping shape (not a
    real `ProjectConfig` instance, since one does not exist yet in this repo)
    and through the REAL `write_run_manifest`, not a helper -- a unit test on
    a path-extraction helper alone cannot see whether the writer actually
    calls it, per the same discipline
    `test_manifest_never_contains_an_absolute_home_path` applies to the flat
    shape.

    Before the fix: `output.path` degraded to `"unrooted"` with only its
    basename recorded, `config["run"]["data_root"]` was carried into the
    manifest bytes verbatim as the absolute path, and neither guarantee this
    module's own docstring states -- "every filesystem path ... is made
    ROOT-RELATIVE" and `config["data_root"]` replaced with `<data_root>` --
    held for the nested shape. No error, no warning.
    """
    fake_home = tmp_path / "home" / "someaccount"
    data_root = fake_home / "data" / "wa-mine-monitor"
    output_dir = data_root / "interim" / "pilot"
    output_dir.mkdir(parents=True)
    output = output_dir / "result.parquet"
    output.write_bytes(b"result")

    nested_config = {
        "run": {
            "data_root": str(data_root),
            "redistribute_public": False,
        },
        "sources": {"minedex_public_export_blocked": True},
    }

    manifest = write_run_manifest(
        output=output,
        inputs=[],
        config=nested_config,
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
    )

    # The absolute home path never reaches the manifest, in memory or on disk.
    on_disk_text = (output_dir / "result.parquet.run_manifest.json").read_text(encoding="utf-8")
    assert "/home/" not in json.dumps(manifest, default=str)
    assert "/home/" not in on_disk_text

    # `output.path` is expressed relative to the NESTED `data_root`, exactly
    # as it already is for the flat shape.
    assert manifest["output"]["path"] == "interim/pilot/result.parquet"
    assert manifest["output"]["path_root"] == "data_root"

    # `data_root` is replaced with its symbolic name IN PLACE, inside `run`,
    # and the rest of `run` (and `sources`) is left untouched.
    assert manifest["config"]["run"]["data_root"] == "<data_root>"
    assert manifest["config"]["run"]["redistribute_public"] is False
    assert manifest["config"]["sources"]["minedex_public_export_blocked"] is True


def test_manifest_json_is_sorted_stable_and_newline_terminated(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    write_run_manifest(
        output=output,
        inputs=[SourceAsset(uri="dea://scene/LS8_001", sha256=None)],
        config={"end_year": 2025},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
        timestamp=datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC),
    )
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    text = manifest_path.read_text(encoding="utf-8")

    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    # Stable (compact) separators: no ", " or ": " padding.
    assert ", " not in text
    assert ": " not in text
    # Top-level keys must appear in sorted order in the serialised text.
    top_level_keys = [
        "argv",
        "argv0_root",
        "config",
        "git",
        "inputs",
        "output",
        "package_versions",
        "resolved_args",
        "timestamp",
    ]
    assert top_level_keys == sorted(top_level_keys)
    positions = [text.index(f'"{key}"') for key in top_level_keys]
    assert positions == sorted(positions)
    # Round-trips as valid JSON.
    json.loads(text)


def test_manifest_json_is_byte_identical_across_runs_with_frozen_timestamp(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    frozen = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    fixed_versions = {"wa-mine-monitor": "0.1.0", "pydantic": "2.0.0"}

    write_run_manifest(
        output=output,
        inputs=[SourceAsset(uri="dea://scene/LS8_001", sha256=None, collection="ga_ls8c_ard_3")],
        config={"end_year": 2025},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
        argv=["wa-mine-monitor", "run", "--config", "base.yaml"],
        timestamp=frozen,
        package_versions=fixed_versions,
    )
    first_run_bytes = manifest_path.read_bytes()

    write_run_manifest(
        output=output,
        inputs=[SourceAsset(uri="dea://scene/LS8_001", sha256=None, collection="ga_ls8c_ard_3")],
        config={"end_year": 2025},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
        argv=["wa-mine-monitor", "run", "--config", "base.yaml"],
        timestamp=frozen,
        package_versions=fixed_versions,
    )
    second_run_bytes = manifest_path.read_bytes()

    assert first_run_bytes == second_run_bytes


def test_manifest_rejects_naive_timestamp(tmp_path: Path) -> None:
    """A naive datetime would otherwise be silently reinterpreted as
    machine-local time by `astimezone`, recording a wrong, host-dependent
    timestamp as authoritative provenance."""
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    with pytest.raises(ValueError):
        write_run_manifest(
            output=output,
            inputs=[],
            config={},
            git_state={"sha": "abc123", "dirty": False, "diff": ""},
            timestamp=datetime(2026, 7, 20, 12, 0, 0),  # naive, no tzinfo  # noqa: DTZ001
        )


def test_manifest_timestamp_is_utc_iso8601(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    manifest = write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
        timestamp=datetime(2026, 7, 20, 3, 4, 5, tzinfo=UTC),
    )
    assert manifest["timestamp"] == "2026-07-20T03:04:05.000000Z"


def test_manifest_embeds_full_working_tree_diff_when_dirty(tmp_path: Path) -> None:
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "index abc123..def456 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-old line\n"
        "+new line\n"
    )
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    manifest = write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={"sha": "abc123", "dirty": True, "diff": diff_text},
    )
    assert manifest["git"]["dirty"] is True
    assert manifest["git"]["diff"] == diff_text


# --- diff scrub disclosure ---------------------------------------------------


def test_manifest_reports_no_scrubbing_for_a_clean_diff(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    manifest = write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={"sha": "abc123", "dirty": True, "diff": "+new line\n-old line\n"},
    )
    assert manifest["git"]["diff_scrubbed"] is False
    assert manifest["git"]["diff_scrub_count"] == 0


def test_manifest_discloses_that_the_diff_was_altered_by_scrubbing(tmp_path: Path) -> None:
    """A scrubbed diff is not an appliable patch, and the manifest must say so.

    Without this, a reader takes `git.diff` to be the exact working tree and
    silently gets a corrupted patch.
    """
    diff_text = (
        "diff --git a/config/base.yaml b/config/base.yaml\n"
        "+minedex_token=SUPERSECRETTOKEN\n"
        "+bbox=115.9,-33.2,116.4,-32.4\n"
    )
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    manifest = write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={"sha": "abc123", "dirty": True, "diff": diff_text},
    )
    assert manifest["git"]["diff_scrubbed"] is True
    assert manifest["git"]["diff_scrub_count"] == 1
    assert "SUPERSECRETTOKEN" not in manifest["git"]["diff"]


def test_manifest_does_not_corrupt_a_diff_containing_sort_keys(tmp_path: Path) -> None:
    """Regression: `sort_keys=True` was rewritten to `sort_keys=***REDACTED***`."""
    diff_text = "+    manifest_json = json.dumps(manifest, sort_keys=True)\n"
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    manifest = write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={"sha": "abc123", "dirty": True, "diff": diff_text},
    )
    assert manifest["git"]["diff"] == diff_text
    assert manifest["git"]["diff_scrubbed"] is False


# --- manifest immutability --------------------------------------------------


def test_manifest_second_write_with_identical_provenance_but_later_timestamp_is_a_noop(
    tmp_path: Path,
) -> None:
    """Immutability compares provenance, not the wall clock.

    `timestamp` defaults to `now()`, so comparing whole manifests made the
    no-op branch unreachable outside tests -- and resumable stages need
    exactly this comparison to succeed.
    """
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    kwargs = {
        "output": output,
        "inputs": [],
        "config": {"run": "SAME"},
        "git_state": {"sha": "abc123", "dirty": False, "diff": ""},
        "package_versions": {"wa-mine-monitor": "0.1.0"},
    }
    first = write_run_manifest(timestamp=datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC), **kwargs)  # type: ignore[arg-type]
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    first_bytes = manifest_path.read_bytes()

    second = write_run_manifest(timestamp=datetime(2026, 7, 21, 9, 30, 0, tzinfo=UTC), **kwargs)  # type: ignore[arg-type]

    assert manifest_path.read_bytes() == first_bytes
    assert second["timestamp"] == first["timestamp"]


def test_manifest_second_write_with_path_valued_config_is_a_noop(
    tmp_path: Path,
) -> None:
    """The no-op check must compare like-for-like, not native leaves vs JSON.

    `config` on the real call path is a config object's `.model_dump()` and
    can carry a `Path` leaf (e.g. `data_root`). `manifest` in memory keeps
    that `Path` object; the on-disk manifest, read back via `json.loads`, has
    it coerced to `str` by `default=str`. Comparing the two directly makes
    the no-op branch unreachable for byte-identical provenance whenever a
    config leaf needs `default=str` coercion -- exactly a resumable-stage
    reuse path.
    """
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    kwargs = {
        "output": output,
        "inputs": [],
        "config": {"run": "SAME", "data_root": Path("/data/wa-mine-monitor")},
        "git_state": {"sha": "abc123", "dirty": False, "diff": ""},
        "package_versions": {"wa-mine-monitor": "0.1.0"},
    }
    first = write_run_manifest(timestamp=datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC), **kwargs)  # type: ignore[arg-type]
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    first_bytes = manifest_path.read_bytes()

    second = write_run_manifest(timestamp=datetime(2026, 7, 21, 9, 30, 0, tzinfo=UTC), **kwargs)  # type: ignore[arg-type]

    assert manifest_path.read_bytes() == first_bytes
    assert second["timestamp"] == first["timestamp"]


def test_manifest_matches_ignores_timestamp_but_not_provenance(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    kwargs = {
        "output": output,
        "inputs": [],
        "config": {"run": "SAME"},
        "git_state": {"sha": "abc123", "dirty": False, "diff": ""},
        "package_versions": {"wa-mine-monitor": "0.1.0"},
    }
    first = write_run_manifest(timestamp=datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC), **kwargs)  # type: ignore[arg-type]
    later = dict(first, timestamp="2026-07-21T09:30:00.000000Z")
    assert manifest_matches(first, later)

    different_code = dict(first, git=dict(first["git"], sha="def456"))
    assert not manifest_matches(first, different_code)


def test_manifest_second_write_with_identical_content_is_a_noop(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    frozen = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    kwargs = {
        "output": output,
        "inputs": [],
        "config": {"run": "SAME"},
        "git_state": {"sha": "abc123", "dirty": False, "diff": ""},
        "timestamp": frozen,
        "package_versions": {"wa-mine-monitor": "0.1.0"},
    }
    write_run_manifest(**kwargs)  # type: ignore[arg-type]
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    first_bytes = manifest_path.read_bytes()

    write_run_manifest(**kwargs)  # type: ignore[arg-type]
    assert manifest_path.read_bytes() == first_bytes


def test_manifest_write_raises_on_differing_rewrite_and_preserves_original(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"first-bytes")
    write_run_manifest(
        output=output,
        inputs=[],
        config={"run": "FIRST"},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
        timestamp=datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC),
    )
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    original_bytes = manifest_path.read_bytes()

    output.write_bytes(b"second-bytes-that-differ")
    with pytest.raises(FileExistsError):
        write_run_manifest(
            output=output,
            inputs=[],
            config={"run": "SECOND"},
            git_state={"sha": "abc123", "dirty": False, "diff": ""},
            timestamp=datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC),
        )

    assert manifest_path.read_bytes() == original_bytes


# --- pre-flight: the refusal that is knowable BEFORE the work ---------------
#
# `write_run_manifest` refuses AFTER the artefact has been computed, because
# the write happens last. Some of the provenance a refusal turns on --
# config, git state, argv, package versions -- is fully known before a
# single byte of the artefact is computed, so a run doomed by any of those
# can be refused instantly.


def _preflight_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "config": {"run": "FIRST"},
        "git_state": {"sha": "abc123", "dirty": False, "diff": ""},
        "argv": ["wa-mine-monitor", "build-register"],
        "package_versions": {"wa-mine-monitor": "0.1.0"},
    }
    base.update(overrides)
    return base


def _write_baseline_manifest(output: Path, **overrides: object) -> None:
    output.write_bytes(b"first-bytes")
    write_run_manifest(
        output=output,
        inputs=[],
        timestamp=datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC),
        **_preflight_kwargs(**overrides),  # type: ignore[arg-type]
    )


def test_preflight_reports_no_conflict_when_no_manifest_exists(tmp_path: Path) -> None:
    assert (
        preflight_manifest_conflict(tmp_path / "result.parquet", **_preflight_kwargs())  # type: ignore[arg-type]
        is None
    )


def test_preflight_names_a_git_state_that_guarantees_refusal(tmp_path: Path) -> None:
    """The previous manifest was written at a different commit, so the write
    was already doomed before the work started."""
    output = tmp_path / "result.parquet"
    _write_baseline_manifest(output)

    reason = preflight_manifest_conflict(
        output,
        **_preflight_kwargs(git_state={"sha": "def456", "dirty": False, "diff": ""}),  # type: ignore[arg-type]
    )

    assert reason is not None
    assert "git" in reason
    assert str(output) in reason


def test_preflight_names_a_differing_config(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    _write_baseline_manifest(output)

    reason = preflight_manifest_conflict(output, **_preflight_kwargs(config={"run": "SECOND"}))  # type: ignore[arg-type]

    assert reason is not None
    assert "config" in reason


def test_preflight_stays_silent_when_only_the_output_bytes_could_differ(
    tmp_path: Path,
) -> None:
    """The check is ONE-SIDED and must be: it fires only where refusal is
    CERTAIN. The artefact's `sha256` and the run's `resolved_args` are not
    knowable until the work is done, so a run that matches on everything else
    is left to proceed -- a pre-flight PASS is not a promise the write will
    succeed, and a pre-flight that guessed would refuse legitimate reruns."""
    output = tmp_path / "result.parquet"
    _write_baseline_manifest(output)

    assert preflight_manifest_conflict(output, **_preflight_kwargs()) is None  # type: ignore[arg-type]


def test_preflight_agrees_with_the_write_it_predicts(tmp_path: Path) -> None:
    """The counterfactual: whatever the pre-flight refuses, the real writer
    must also refuse. A pre-flight scored only against itself proves nothing
    about the boundary it stands in for."""
    output = tmp_path / "result.parquet"
    _write_baseline_manifest(output)
    changed = _preflight_kwargs(git_state={"sha": "def456", "dirty": False, "diff": ""})

    assert preflight_manifest_conflict(output, **changed) is not None  # type: ignore[arg-type]
    with pytest.raises(FileExistsError):
        write_run_manifest(
            output=output,
            inputs=[],
            timestamp=datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC),
            **changed,  # type: ignore[arg-type]
        )


# --- value-level secret scrubbing -------------------------------------------


def test_manifest_scrubs_secret_shaped_query_param_from_input_uri_on_disk(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    write_run_manifest(
        output=output,
        inputs=[SourceAsset(uri="https://maps.slip.wa.gov.au/wfs?api_key=SUPERSECRETTOKEN")],
        config={},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
    )
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    text = manifest_path.read_text(encoding="utf-8")

    assert "SUPERSECRETTOKEN" not in text
    assert "REDACTED" in text


def test_manifest_scrubs_secret_shaped_argv_token_on_disk(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
        argv=["wa-mine-monitor", "detect", "--slip-token", "ANOTHERSECRET"],
    )
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    text = manifest_path.read_text(encoding="utf-8")

    assert "ANOTHERSECRET" not in text
    assert "REDACTED" in text


def test_manifest_scrubs_secret_shaped_url_value_under_non_secret_config_key(
    tmp_path: Path,
) -> None:
    """A token embedded in a URL value survives `redact_secrets` (which is
    key-name based) whenever the surrounding key -- here `stac_endpoint` --
    isn't itself credential-shaped. This is the realistic DEA/SLIP endpoint
    case."""
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    write_run_manifest(
        output=output,
        inputs=[],
        config={"stac_endpoint": "https://dea.ga.gov.au/stac?api_key=LEAKED_CONFIG_TOKEN"},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
    )
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    text = manifest_path.read_text(encoding="utf-8")

    assert "LEAKED_CONFIG_TOKEN" not in text
    assert "REDACTED" in text


def test_manifest_scrubs_secret_shaped_url_value_behind_non_secret_argv_flag(
    tmp_path: Path,
) -> None:
    """`scrub_argv_secrets` only redacts a token following a credential-shaped
    flag name; a token that is itself a URL carrying a credential-shaped
    query parameter (behind an innocuous flag like `--stac-url`) survives it
    untouched."""
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
        argv=[
            "wa-mine-monitor",
            "detect",
            "--stac-url",
            "https://x.gov.au/wfs?token=LEAKED_ARGV_TOKEN",
        ],
    )
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    text = manifest_path.read_text(encoding="utf-8")

    assert "LEAKED_ARGV_TOKEN" not in text
    assert "REDACTED" in text


def test_manifest_scrubs_secret_shaped_value_from_git_diff_on_disk(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    diff_text = '+    "slip_api_key": "YETANOTHERSECRET",\n'
    write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={"sha": "abc123", "dirty": True, "diff": diff_text},
    )
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    text = manifest_path.read_text(encoding="utf-8")

    assert "YETANOTHERSECRET" not in text
    assert "REDACTED" in text


def test_manifest_scrubs_credential_in_non_diff_git_state_key_on_disk(tmp_path: Path) -> None:
    """`git_state` is an open mapping -- `remote_url` from
    `git remote get-url origin` is the obvious next field and an HTTPS remote
    routinely embeds a PAT -- so every leaf must be scrubbed, not just
    `diff`."""
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={
            "sha": "abc123",
            "dirty": True,
            "diff": "",
            "remote_url": "https://u:PASSWORDLEAK@github.com/x.git",
        },
    )
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    text = manifest_path.read_text(encoding="utf-8")

    assert "PASSWORDLEAK" not in text
    # The rest of git_state still survives -- scrubbing must not drop fields.
    assert "abc123" in text
    assert "github.com/x.git" in text


# --- secret redaction ------------------------------------------------------


def test_manifest_redacts_secret_shaped_config_keys_on_disk(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    write_run_manifest(
        output=output,
        inputs=[],
        config={"end_year": 2025, "slip_api_token": "super-secret-value-do-not-print"},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
    )
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    text = manifest_path.read_text(encoding="utf-8")

    assert "super-secret-value-do-not-print" not in text
    assert "REDACTED" in text


def test_manifest_redacts_secret_shaped_resolved_args_on_disk(tmp_path: Path) -> None:
    output = tmp_path / "result.parquet"
    output.write_bytes(b"result")
    write_run_manifest(
        output=output,
        inputs=[],
        config={},
        git_state={"sha": "abc123", "dirty": False, "diff": ""},
        resolved_args={"slip_password": "hunter2-do-not-print"},
    )
    manifest_path = tmp_path / "result.parquet.run_manifest.json"
    text = manifest_path.read_text(encoding="utf-8")

    assert "hunter2-do-not-print" not in text
    assert "REDACTED" in text
