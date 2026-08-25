import json
import math
import subprocess
from pathlib import Path
from typing import NamedTuple

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import rasterio
import typer
import yaml
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from typer.testing import CliRunner

from wa_mine_monitor import (
    cli,
    d3_inputs,
    d3_protocol,
    huntly_validation,
    manifests,
    register,
    snapshots,
    tables,
    trajectories,
)
from wa_mine_monitor.cli import (
    _collect_git_state_disclosing_gaps,
    _latest_curated_dated_dir,
    _verify_snapshot_or_refuse,
    app,
)
from wa_mine_monitor.maus_footprints import MAUS_FOOTPRINT_STATS_SCHEMA
from wa_mine_monitor.provenance import SourceAsset

runner = CliRunner()


def _init_git_repo(repo_root: Path) -> None:
    """Initialise a throwaway git repo, WITHOUT committing anything.

    Deliberately leaves the repo at the exact state
    `test_collect_git_state_disclosing_gaps_...` below drives:
    `git init` alone, matching this project's own real build-phase repo per
    `CLAUDE.md`'s cross-task ledger ("zero commits exist").
    """
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True)


def test_config_check_echoes_data_root(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        'run:\n  data_root: "~/data/wa-mine-monitor"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    result = runner.invoke(app, ["config-check", "--config", str(cfg_file)])
    assert result.exit_code == 0
    expected_data_root = str(Path("~/data/wa-mine-monitor").expanduser())
    assert expected_data_root in result.output


def test_config_check_scrubs_secret_shaped_keys(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        'run:\n  data_root: "~/data/wa-mine-monitor"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
        'api_token: "shhh"\n'
    )
    result = runner.invoke(app, ["config-check", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert "shhh" not in result.output
    assert "REDACTED" in result.output


def test_config_check_scrubs_a_credential_carried_by_value(tmp_path):
    """Regression for the finding that the SUCCESS path scrubbed with
    `redact_secrets` alone, which is key-name based: a userinfo-bearing URL
    under a non-credential-shaped key (`slip_endpoint`) was echoed verbatim,
    making the terminal echo strictly weaker than the manifest persisted from
    the same mapping (`manifests` runs `scrub_string_leaves(redact_secrets(
    ...))`). `ProjectConfig` sets `extra="allow"`, so such a key resolves."""
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        'run:\n  data_root: "~/data/wa-mine-monitor"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
        'slip_endpoint: "https://fetchbot:SUPERSECRETVALUE123@slip.wa.gov.au/download?bbox=1,2"\n'
    )
    result = runner.invoke(app, ["config-check", "--config", str(cfg_file)])
    assert result.exit_code == 0
    assert "SUPERSECRETVALUE123" not in result.output
    assert "fetchbot" not in result.output
    # The key itself is not credential-shaped, so it survives; only the
    # userinfo half of its value is removed.
    assert "slip_endpoint" in result.output
    assert "slip.wa.gov.au" in result.output


def test_config_check_does_not_leak_secret_on_validation_error(tmp_path):
    """Regression for the finding that a validation error (here, a missing
    required `sources:` key) let pydantic's own `ValidationError` -- which
    embeds the whole offending input dict -- escape to the terminal
    unscrubbed, printing a credential-shaped value verbatim."""
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        'run:\n  data_root: "~/data/wa-mine-monitor"\n  redistribute_public: false\n'
        'api_token: "SUPERSECRETVALUE123"\n'
    )
    result = runner.invoke(app, ["config-check", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "SUPERSECRETVALUE123" not in result.output
    assert (
        "sources" in result.output
    )  # names the missing field, doesn't hide the shape of the error


def test_config_check_does_not_leak_secret_on_malformed_yaml(tmp_path):
    """Regression for the finding that a malformed-YAML error (here, an
    unterminated quoted scalar around a credential-shaped key) let
    `yaml.MarkedYAMLError`'s own message -- which embeds the offending
    SOURCE LINE via `Mark.get_snippet()` -- escape to the terminal
    unscrubbed, printing a credential-shaped value verbatim. `scrub_text_
    secrets` does not close this: the unterminated quote defeats the
    assignment-shaped pattern it matches on."""
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text('api_token: "SUPERSECRETVALUE123\n')
    result = runner.invoke(app, ["config-check", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "SUPERSECRETVALUE123" not in result.output
    assert "line" in result.output  # structural location is still reported


def test_config_check_missing_file_is_a_clean_error_not_a_traceback(tmp_path):
    """A missing config path must not dump a pydantic/pathlib traceback --
    `ConfigOption`'s `exists=True` turns it into a normal Click usage error."""
    missing = tmp_path / "does-not-exist.yaml"
    result = runner.invoke(app, ["config-check", "--config", str(missing)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


# --- `_collect_git_state_disclosing_gaps` -----------------------------------
#
# THE DEFECT this section closes: `collect_git_state` shells out to `git
# rev-parse HEAD` with `check=True`, which raises `subprocess.CalledProcessError`
# (git exit 128) on a repo with no commits -- exactly this project's own
# current build-phase repo state (`CLAUDE.md`'s cross-task ledger: "zero
# commits exist"). Every fetch-command CLI test in this batch monkeypatches
# `collect_git_state` itself (see `tests/sources/test_tenements.py`,
# `test_minedex.py`, `test_maus.py`), so none of them can see this -- these
# tests drive the REAL, UNPATCHED `collect_git_state` (via the guard that
# wraps it) against a real git repo built in-test, in both states.


def test_collect_git_state_disclosing_gaps_reports_unborn_head_on_zero_commits(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)

    state = _collect_git_state_disclosing_gaps(tmp_path)

    assert state["sha"] is None
    assert state["dirty"] is None
    assert state["diff"] == ""
    assert state["unborn_head"] is True
    assert state["git_available"] is True
    assert state["git_state_error"]


def test_collect_git_state_disclosing_gaps_passes_through_a_real_commit(
    tmp_path: Path,
) -> None:
    """The counterfactual: once a commit exists, the real `collect_git_state`
    succeeds outright and this guard is a pure pass-through -- it must never
    disclose a gap that is not there."""
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("content\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True
    )

    state = _collect_git_state_disclosing_gaps(tmp_path)

    assert isinstance(state["sha"], str) and len(state["sha"]) == 40
    assert state["dirty"] is False
    assert state["diff"] == ""
    assert "unborn_head" not in state
    assert "git_available" not in state


def test_collect_git_state_disclosing_gaps_reports_not_a_git_repo(tmp_path: Path) -> None:
    """A repo root that is not a git checkout at all -- e.g. a non-editable
    wheel install, whose computed `_REPO_ROOT` can sit several directories
    short of any `.git` -- is a DIFFERENT gap from "zero commits" and is
    disclosed as such rather than crash."""
    state = _collect_git_state_disclosing_gaps(tmp_path)

    assert state["sha"] is None
    assert state["git_available"] is False
    assert state["unborn_head"] is False
    assert state["git_state_error"]


# --- shared config-loading redaction, across ALL commands -------------------
#
# THE DEFECT this section closes: `config_check` carried the whole
# config-error redaction discipline in its own body, while the three fetch
# commands each called `load_config(config)` unguarded -- so the identical
# invalid config that `config-check` reported as type/loc/msg alone leaked
# verbatim through `fetch-tenements`/`fetch-minedex`/`fetch-maus-extract`.
# Both leak shapes were live: pydantic's `ValidationError` embeds the whole
# offending input (`input_value={'api_token': 'hunter3SECRET'}`), and
# `yaml.MarkedYAMLError.__str__` embeds the offending SOURCE LINE via
# `Mark.get_snippet()`. `app`'s `pretty_exceptions_show_locals=False` closes
# neither -- both live in the exception's own `str()`, not in the locals
# frame. There is now exactly one loading path (`_load_config_or_exit`), and
# these tests drive it through every command that has one, so a future
# command that reintroduces a bare `load_config` call is caught here rather
# than by inspection.

_LEAK_SENTINEL = "SUPERSECRETVALUE123"


def _command_argv(command: str, cfg_file: Path, tmp_path: Path) -> list[str]:
    """The minimal argv for `command` against `cfg_file`.

    Every required option is supplied, so the invocation reaches the command
    BODY -- and therefore the config load -- rather than dying in Click's own
    option parsing, which would pass these assertions vacuously. The
    `--source-gpkg` file is created empty and is never read: the config load
    fails first, which is the point.
    """
    argv = [command, "--config", str(cfg_file)]
    if command != "config-check":
        argv += ["--date", "2026-08-15"]
    if command == "fetch-maus-extract":
        source_gpkg = tmp_path / "source.gpkg"
        source_gpkg.touch()
        argv += ["--source-gpkg", str(source_gpkg)]
    return argv


ALL_CONFIG_LOADING_COMMANDS = [
    "config-check",
    "fetch-tenements",
    "fetch-minedex",
    "fetch-maus-extract",
    "build-register",
    "adjudicate-minedex-licence",
]


@pytest.mark.parametrize("command", ALL_CONFIG_LOADING_COMMANDS)
def test_no_command_leaks_a_credential_on_a_validation_error(command: str, tmp_path: Path) -> None:
    """An invalid config (missing required `sources:`) must never print the
    credential-shaped value pydantic embeds in its own error."""
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        'run:\n  data_root: "~/data/wa-mine-monitor"\n  redistribute_public: false\n'
        f'api_token: "{_LEAK_SENTINEL}"\n'
    )

    result = runner.invoke(app, _command_argv(command, cfg_file, tmp_path))

    assert result.exit_code == 1
    assert _LEAK_SENTINEL not in result.output
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert "config_error" in payload
    # The shape of the error is still reported -- redaction, not silence.
    assert "sources" in result.output


@pytest.mark.parametrize("command", ALL_CONFIG_LOADING_COMMANDS)
def test_no_command_leaks_a_credential_on_malformed_yaml(command: str, tmp_path: Path) -> None:
    """A malformed config (unterminated quoted scalar on a credential-shaped
    key) must never print the offending SOURCE LINE that
    `yaml.MarkedYAMLError.__str__` embeds via `Mark.get_snippet()`."""
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        'run:\n  data_root: "~/data/wa-mine-monitor"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
        f'api_token: "{_LEAK_SENTINEL}\n'
    )

    result = runner.invoke(app, _command_argv(command, cfg_file, tmp_path))

    assert result.exit_code == 1
    assert _LEAK_SENTINEL not in result.output
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert "config_error" in payload
    # Structural location survives; source text does not.
    assert payload["config_error"]["line"] is not None


@pytest.mark.parametrize("command", ALL_CONFIG_LOADING_COMMANDS)
def test_no_command_creates_a_snapshot_directory_on_a_config_error(
    command: str, tmp_path: Path
) -> None:
    """The counterfactual to the two leak tests: refusing on a config error
    must also happen BEFORE any snapshot directory is created, so a leaked
    config never buys a half-built snapshot either."""
    data_root = tmp_path / "data"
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{data_root}"\n  redistribute_public: false\n'
        f'api_token: "{_LEAK_SENTINEL}"\n'
    )

    result = runner.invoke(app, _command_argv(command, cfg_file, tmp_path))

    assert result.exit_code == 1
    assert not (data_root / "raw").exists()


# =============================================================================
# _latest_curated_dated_dir
#
# Pins the same selection behaviour as `register.latest_snapshot`
# (`tests/test_register.py`'s `latest_snapshot` section) -- both now call
# the one shared `snapshots.latest_dated_subdir` scan -- plus the
# curated-vs-raw finalization difference: a curated directory is selected
# with no `SHA256SUMS.txt` at all, while a raw snapshot directory in the
# same shape is refused by `_verify_snapshot_or_refuse`, the gate build
# commands apply only to raw snapshot inputs.
# =============================================================================


def test_latest_curated_dated_dir_returns_the_most_recent_dated_dir(tmp_path: Path) -> None:
    base_dir = tmp_path / "curated" / "register"
    for date_str in ["2026-07-01", "2026-08-15", "2026-01-01"]:
        (base_dir / date_str).mkdir(parents=True)

    result = _latest_curated_dated_dir(base_dir, label="curated/register")

    assert result == base_dir / "2026-08-15"


def test_latest_curated_dated_dir_ignores_non_date_directories(tmp_path: Path) -> None:
    base_dir = tmp_path / "curated" / "register"
    (base_dir / "2026-01-01").mkdir(parents=True)
    (base_dir / "scratch").mkdir(parents=True)

    result = _latest_curated_dated_dir(base_dir, label="curated/register")

    assert result.name == "2026-01-01"


def test_latest_curated_dated_dir_raises_naming_the_label_when_none_exists(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "curated" / "register"
    with pytest.raises(register.NoSnapshotFoundError, match="curated/register"):
        _latest_curated_dated_dir(base_dir, label="curated/register")


def test_latest_curated_dated_dir_raises_when_the_base_directory_itself_is_absent(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "curated" / "crosswalk"
    with pytest.raises(register.NoSnapshotFoundError, match="curated/crosswalk"):
        _latest_curated_dated_dir(base_dir, label="curated/crosswalk")


def test_latest_curated_dated_dir_selects_a_dated_dir_with_no_sha256sums(
    tmp_path: Path,
) -> None:
    """A curated directory carries a run manifest, not a raw snapshot's
    `SHA256SUMS.txt` -- selection must succeed on one with no checksum file
    at all, the state a real `curated/register/<date>/` directory is
    always in."""
    base_dir = tmp_path / "curated" / "register"
    dated_dir = base_dir / "2026-08-14"
    dated_dir.mkdir(parents=True)
    (dated_dir / "register.parquet").write_bytes(b"not a real parquet file")

    result = _latest_curated_dated_dir(base_dir, label="curated/register")

    assert result == dated_dir
    assert not (result / snapshots.SHA256SUMS_FILENAME).exists()


def test_latest_curated_dated_dir_and_verify_snapshot_or_refuse_diverge_on_sha256sums(
    tmp_path: Path,
) -> None:
    """The distinction that makes these two loops different callers, pinned
    directly: the SAME dated-dir shape (a directory with no `SHA256SUMS.
    txt`) is selected without complaint as a curated directory, and refused
    by `_verify_snapshot_or_refuse` as a raw snapshot -- the raw-snapshot
    integrity gate `build-register`/`build-crosswalk` apply only to their
    RAW inputs, never to a curated one selected via
    `_latest_curated_dated_dir`."""
    curated_dir = tmp_path / "curated" / "register" / "2026-08-14"
    curated_dir.mkdir(parents=True)
    raw_dir = tmp_path / "raw" / "dmirs_001_minedex" / "2026-08-14"
    raw_dir.mkdir(parents=True)  # identical shape: dated, no SHA256SUMS.txt

    # Curated: selection alone, no integrity gate applied -- passes.
    selected = _latest_curated_dated_dir(curated_dir.parent, label="curated/register")
    assert selected == curated_dir

    # Raw: the same directory shape, run through the gate a build command
    # actually applies to a raw snapshot -- refused, naming "never finalized".
    with pytest.raises(typer.Exit):
        _verify_snapshot_or_refuse(raw_dir, source_id="dmirs_001_minedex")


def _dea_fixture_pages():
    fixtures = Path(__file__).resolve().parent / "fixtures" / "dea"

    def load(name):
        return json.loads((fixtures / name).read_text(encoding="utf-8"))

    from wa_mine_monitor.source_catalogue import DEA_COLLECTIONS
    from wa_mine_monitor.sources.dea import collection_url, items_url

    pages = {}
    for spec in DEA_COLLECTIONS:
        collection = load("collection_ga_ls5t_gm_cyear_3.json")
        collection["id"] = spec.collection_id
        page = load("items_page_2.json")  # single page, no next link
        for feature in page["features"]:
            feature["id"] = f"{spec.collection_id}-x11y22-1991"
            if spec.source_id == "dea_fc_pc":
                feature["assets"] = {role: {"href": "s3://x/a.tif"} for role in spec.asset_roles}
        pages[collection_url(spec.collection_id)] = collection
        pages[items_url(spec.collection_id)] = page
    return pages


class _FakeCatalogueClient:
    def __init__(self, pages):
        self._pages = pages

    def get_json(self, url, *, params=None):
        payload = self._pages[url]
        if isinstance(payload, BaseException):
            raise payload
        return payload


def _write_monitor_config(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{tmp_path / "data"}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg_file


def test_fetch_dea_catalogue_writes_snapshot_and_manifest(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    fake = _FakeCatalogueClient(_dea_fixture_pages())
    monkeypatch.setattr("wa_mine_monitor.cli.new_dea_client", lambda: fake)

    result = runner.invoke(
        app,
        ["fetch-dea-catalogue", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert result.exit_code == 0, result.output
    snapshot_dir = tmp_path / "data" / "raw" / "dea_stac" / "2026-08-16"
    assert (snapshot_dir / "ga_ls5t_gm_cyear_3" / "collection.json").exists()
    assert (snapshot_dir / "ga_ls5t_gm_cyear_3" / "items_page_0001.json").exists()
    assert (snapshot_dir / "catalogue_summary.json").exists()
    assert (snapshot_dir / "SHA256SUMS.txt").exists()
    manifest_path = snapshot_dir / "SHA256SUMS.txt.run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["inputs"]) == 4
    payload = json.loads(result.output)
    assert payload["verify"] == {"ok": payload["verify"]["ok"], "bad": 0, "missing": 0}
    summary = json.loads((snapshot_dir / "catalogue_summary.json").read_text(encoding="utf-8"))
    for entry in summary["collections"]:
        assert entry["n_items"] > 0
        # D13 C2's recorded snapshot fields, all three present per collection:
        assert entry["required_assets"]
        assert entry["reported_item_count_disclosure"] in {
            "reported-by-source",
            "absent-from-source",
        }
        assert len(entry["collection_response_sha256"]) == 64


def test_fetch_dea_catalogue_refuses_when_one_collection_fails(tmp_path, monkeypatch):
    """One collection failing refuses the WHOLE catalogue -- no partial,
    no finalized snapshot (the completeness-sensitive caller, end to end)."""
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    pages = _dea_fixture_pages()
    from wa_mine_monitor.sources.dea import collection_url

    pages[collection_url("ga_ls_fc_pc_cyear_3")] = RuntimeError("transport died")
    fake = _FakeCatalogueClient(pages)
    monkeypatch.setattr("wa_mine_monitor.cli.new_dea_client", lambda: fake)

    result = runner.invoke(
        app,
        ["fetch-dea-catalogue", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert result.exit_code == 1
    assert "refusal" in result.output
    snapshot_dir = tmp_path / "data" / "raw" / "dea_stac" / "2026-08-16"
    assert not (snapshot_dir / "SHA256SUMS.txt").exists()


def test_fetch_dea_catalogue_refuses_overwrite_of_finalized_snapshot(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    fake = _FakeCatalogueClient(_dea_fixture_pages())
    monkeypatch.setattr("wa_mine_monitor.cli.new_dea_client", lambda: fake)
    first = runner.invoke(
        app,
        ["fetch-dea-catalogue", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        app,
        ["fetch-dea-catalogue", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert second.exit_code == 1


# --- build-maus-footprint-areas CLI command --------------------------------
#
# Seeding follows `tests/test_crosswalk.py::_seed_maus_extract` (the
# established `raw/maus_v2/<date>/wa_extract.gpkg` seeding technique for
# `build-crosswalk`'s Maus input): a toy GeoDataFrame in EPSG:4326, written
# via `gdf.to_file(..., driver="GPKG", layer="wa_extract")`, then
# `snapshots.write_snapshot_metadata` + `snapshots.finalize_snapshot`.


def _seed_maus_extract(data_root, date_str, *, finalize=True):
    """`finalize=False` leaves the snapshot WITHOUT a `SHA256SUMS.txt` --
    the state an interrupted `fetch-maus-extract` leaves behind."""
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "maus_v2", date_str)
    gdf = gpd.GeoDataFrame(
        {"maus_id": ["MAUS001", "MAUS002"]},
        geometry=[
            Polygon(
                [
                    (115.995, -32.005),
                    (116.005, -32.005),
                    (116.005, -31.995),
                    (115.995, -31.995),
                ]
            ),
            Polygon(
                [
                    (121.490, -30.705),
                    (121.510, -30.705),
                    (121.510, -30.695),
                    (121.490, -30.695),
                ]
            ),
        ],
        crs="EPSG:4326",
    )
    gdf.to_file(snapshot_dir / "wa_extract.gpkg", driver="GPKG", layer="wa_extract")
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="Maus et al. v2 WA extract",
        endpoint="https://example.test/maus",
        licence_note="CC-BY-SA-4.0",
        purpose="test fixture",
    )
    if finalize:
        snapshots.finalize_snapshot(snapshot_dir)
    return snapshot_dir


def test_build_maus_footprint_areas_writes_scalars_and_manifest(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"
    _seed_maus_extract(data_root, "2026-08-15")

    result = runner.invoke(
        app,
        ["build-maus-footprint-areas", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert result.exit_code == 0, result.output

    out_path = (
        data_root / "curated" / "maus_footprint_areas" / "2026-08-16" / "footprint_areas.parquet"
    )
    stats = tables.read_table(out_path)
    assert list(stats.columns) == MAUS_FOOTPRINT_STATS_SCHEMA.names
    assert "geometry" not in stats.columns
    assert sorted(stats["maus_id"]) == ["MAUS001", "MAUS002"]

    manifest = json.loads(Path(str(out_path) + ".run_manifest.json").read_text(encoding="utf-8"))
    args = manifest["resolved_args"]
    assert args["crs"] == "EPSG:3577"
    assert args["output_share_alike"] is True
    assert len(args["maus_gpkg_sha256"]) == 64
    assert manifest["inputs"][0]["licence"] == "CC-BY-SA-4.0"


def test_build_maus_footprint_areas_refuses_an_unverifiable_snapshot(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"
    snapshot_dir = _seed_maus_extract(data_root, "2026-08-15")
    # Corrupt the GeoPackage AFTER finalization -- standing in for tampered
    # input, the same reproduction `build-crosswalk`'s tests use.
    with (snapshot_dir / "wa_extract.gpkg").open("ab") as fh:
        fh.write(b"tampered after finalize")

    result = runner.invoke(
        app,
        ["build-maus-footprint-areas", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_build_maus_footprint_areas_refuses_existing_output(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"
    _seed_maus_extract(data_root, "2026-08-15")

    first = runner.invoke(
        app,
        ["build-maus-footprint-areas", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        app,
        ["build-maus-footprint-areas", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert second.exit_code == 1
    assert "refusal" in second.output
    assert "refusal" in second.output


# --- build-dea-coverage CLI command -----------------------------------------
#
# The catalogue snapshot is seeded via the REAL `fetch-dea-catalogue` command
# against the Task 7 fake client (`_FakeCatalogueClient`/`_dea_fixture_pages`,
# already used above) -- the same finalized `raw/dea_stac/<date>/` layout a
# real fetch produces, `items_page_*.json` files included. No CLI command
# builds a curated register from a two-row toy MINEDEX extract cheaply, so
# the source register is constructed directly with `tables.write_table` /
# `manifests.write_run_manifest`, mirroring `_seed_maus_extract`'s
# directness for the OTHER curated input.


def _seed_curated_register(data_root, date_str):
    """Write a minimal, schema-conforming `curated/register/<date_str>/
    register.parquet` plus its own immutable run manifest.

    `site-a` sits at (116.5, -32.5), inside every DEA fixture item's bbox
    (`[116.0, -33.0, 117.0, -32.0]`, see `_dea_fixture_pages`); `site-b`
    carries no coordinates at all, exercising the located/not-computed
    split `count_site_epochs` distinguishes. `n_tenements_intersecting` is
    null for the coordinate-less site (D12.2's not-computed-vs-zero
    semantic), giving the column its real, nullable-`Int64` round-trip
    dtype.
    """
    register_dir = data_root / "curated" / "register" / date_str
    register_dir.mkdir(parents=True)
    register_path = register_dir / "register.parquet"
    df = pd.DataFrame(
        {
            "site_id": ["site-a", "site-b"],
            "site_name": ["Site A", "Site B"],
            "commodity": ["Au", "Fe"],
            "stage": ["Operating", "Shut"],
            "owners_at_snapshot": ["Owner A", "Owner B"],
            "snapshot_date": [date_str, date_str],
            "lon": [116.5, None],
            "lat": [-32.5, None],
            "n_tenements_intersecting": pd.array([0, None], dtype="Int64"),
            "inclusion_status": ["operating", "closed"],
        }
    )
    tables.write_table(df, register_path, register.REGISTER_SCHEMA)
    manifests.write_run_manifest(
        output=register_path,
        inputs=[SourceAsset(uri="minedex://Sites.csv", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "deadbeef", "dirty": False, "diff": ""},
    )
    return register_dir


def _seed_dea_catalogue_snapshot(cfg_file, catalogue_date, monkeypatch):
    """Fetch a real, finalized `raw/dea_stac/<catalogue_date>/` snapshot via
    the Task 7 fake client -- the seam `test_fetch_dea_catalogue_writes_
    snapshot_and_manifest` already exercises."""
    fake = _FakeCatalogueClient(_dea_fixture_pages())
    monkeypatch.setattr("wa_mine_monitor.cli.new_dea_client", lambda: fake)
    result = runner.invoke(
        app,
        ["fetch-dea-catalogue", "--config", str(cfg_file), "--date", catalogue_date],
    )
    assert result.exit_code == 0, result.output


def test_build_dea_coverage_writes_new_versioned_register(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"
    _seed_curated_register(data_root, "2026-08-15")
    _seed_dea_catalogue_snapshot(cfg_file, "2026-08-16", monkeypatch)

    result = runner.invoke(
        app,
        [
            "build-dea-coverage",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-17",
            "--catalogue-date",
            "2026-08-16",
        ],
    )
    assert result.exit_code == 0, result.output
    out_dir = tmp_path / "data" / "curated" / "register" / "2026-08-17"
    assert (out_dir / "register.parquet").exists()
    manifest = json.loads(
        (out_dir / "register.parquet.run_manifest.json").read_text(encoding="utf-8")
    )
    args = manifest["resolved_args"]
    assert set(args) >= {
        "source_register_manifest",
        "source_catalogue_manifest",
        "dea_coverage_disclosure",
        "minedex_public_export_blocked",
        "register_rows_before",
        "register_rows_after",
    }
    assert args["register_rows_before"] == args["register_rows_after"]
    assert args["minedex_public_export_blocked"] is True
    # Batch B artefact untouched:
    source_dir = tmp_path / "data" / "curated" / "register" / "2026-08-15"
    assert (source_dir / "register.parquet").exists()
    # Columns preserved + four appended, dtypes nullable:
    from wa_mine_monitor.register import DEA_COVERAGE_COLUMNS, REGISTER_SCHEMA

    enriched = tables.read_table(out_dir / "register.parquet")
    assert list(enriched.columns) == REGISTER_SCHEMA.names + list(DEA_COVERAGE_COLUMNS)
    assert str(enriched["n_tenements_intersecting"].dtype) == "Int64"
    # `site-a` is located inside the fixture bbox: a genuine computed count;
    # `site-b` is coordinate-less: not computed (null), never a fabricated
    # zero.
    by_site = enriched.set_index("site_id")
    assert by_site.loc["site-a", "n_dea_gm_ls5t_epochs"] == 1
    assert pd.isna(by_site.loc["site-b", "n_dea_gm_ls5t_epochs"])


def test_build_dea_coverage_refuses_tampered_source_register(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"
    register_dir = _seed_curated_register(data_root, "2026-08-15")
    _seed_dea_catalogue_snapshot(cfg_file, "2026-08-16", monkeypatch)

    # Tamper AFTER the manifest was written.
    with (register_dir / "register.parquet").open("ab") as fh:
        fh.write(b"tampered after manifest")

    result = runner.invoke(
        app,
        [
            "build-dea-coverage",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-17",
            "--catalogue-date",
            "2026-08-16",
        ],
    )
    assert result.exit_code == 1
    assert "digest" in result.output


def test_build_dea_coverage_refuses_existing_output(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"
    _seed_curated_register(data_root, "2026-08-15")
    _seed_dea_catalogue_snapshot(cfg_file, "2026-08-16", monkeypatch)

    first = runner.invoke(
        app,
        [
            "build-dea-coverage",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-17",
            "--catalogue-date",
            "2026-08-16",
        ],
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        app,
        [
            "build-dea-coverage",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-17",
            "--catalogue-date",
            "2026-08-16",
        ],
    )
    assert second.exit_code == 1
    assert "refusal" in second.output


# --- derive-dea-volume CLI command -------------------------------------------
#
# Chains the already-tested seams end to end: seed register -> fetch
# catalogue (fake client) -> build-dea-coverage -> seed a Maus extract ->
# build-maus-footprint-areas -> build-crosswalk. The Maus extract is seeded
# ONCE, so `build-maus-footprint-areas` and `build-crosswalk` (`register.
# latest_snapshot`) both read the SAME `raw/maus_v2/<date>/` snapshot -- the
# guarantee the Maus-digest equality refusal test below relies on to build a
# MATCHING pair, then tampers with it by hand.


def _seed_maus_extract_over_site_a(data_root, date_str):
    """A single Maus polygon covering `_seed_curated_register`'s `site-a`
    (116.5, -32.5) -- `derive-dea-volume`'s Tier 1 population needs a
    high-confidence crosswalk match AND a footprint linked to it."""
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "maus_v2", date_str)
    gdf = gpd.GeoDataFrame(
        {"maus_id": ["MAUSVOL1"]},
        geometry=[
            Polygon(
                [
                    (116.4, -32.6),
                    (116.6, -32.6),
                    (116.6, -32.4),
                    (116.4, -32.4),
                ]
            )
        ],
        crs="EPSG:4326",
    )
    gdf.to_file(snapshot_dir / "wa_extract.gpkg", driver="GPKG", layer="wa_extract")
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="Maus et al. v2 WA extract",
        endpoint="https://example.test/maus",
        licence_note="CC-BY-SA-4.0",
        purpose="test fixture",
    )
    snapshots.finalize_snapshot(snapshot_dir)
    return snapshot_dir


def _seed_derive_dea_volume_chain(tmp_path, monkeypatch):
    """Arrange the full input chain `derive-dea-volume` reads, via the
    already-tested CLI seams: `build-dea-coverage` enriches the register,
    then `build-maus-footprint-areas` and `build-crosswalk` both read the
    SAME (only) Maus snapshot seeded here, so their manifests record
    identical Maus GeoPackage digests by construction. Returns
    `(cfg_file, data_root)`."""
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"

    _seed_curated_register(data_root, "2026-08-14")
    _seed_dea_catalogue_snapshot(cfg_file, "2026-08-15", monkeypatch)

    coverage_result = runner.invoke(
        app,
        [
            "build-dea-coverage",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-16",
            "--catalogue-date",
            "2026-08-15",
        ],
    )
    assert coverage_result.exit_code == 0, coverage_result.output

    _seed_maus_extract_over_site_a(data_root, "2026-08-16")

    footprints_result = runner.invoke(
        app,
        ["build-maus-footprint-areas", "--config", str(cfg_file), "--date", "2026-08-17"],
    )
    assert footprints_result.exit_code == 0, footprints_result.output

    crosswalk_result = runner.invoke(
        app,
        ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-18"],
    )
    assert crosswalk_result.exit_code == 0, crosswalk_result.output

    return cfg_file, data_root


def test_derive_dea_volume_writes_estimate_with_manifest_digests(tmp_path, monkeypatch):
    cfg_file, data_root = _seed_derive_dea_volume_chain(tmp_path, monkeypatch)

    result = runner.invoke(
        app, ["derive-dea-volume", "--config", str(cfg_file), "--date", "2026-08-19"]
    )
    assert result.exit_code == 0, result.output
    estimate_path = data_root / "reports" / "dea-volume" / "2026-08-19" / "estimate.json"
    estimate = json.loads(estimate_path.read_text(encoding="utf-8"))
    for key in (
        "population",
        "windows",
        "tiles",
        "site_year_windows",
        "selections",
        "window_policy",
        "year_ranges",
        "bytes",
        "expected_range_requests",
        "asset_metadata_disclosure",
        "assumptions",
        "formulas",
        "provisional_figures_comparison_only",
        "source_manifest_digests",
    ):
        assert key in estimate
    assert set(estimate["source_manifest_digests"]) == {
        "register",
        "crosswalk",
        "footprints",
        "catalogue",
    }
    assert (Path(str(estimate_path) + ".run_manifest.json")).exists()
    # `site-a` is the one high-confidence, footprint-linked site; `site-b`
    # (no coordinates) was excluded from the crosswalk's input population.
    assert estimate["population"]["n_sites_eligible"] == 1


def test_derive_dea_volume_refuses_an_unenriched_register(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"

    # ONLY the Batch B register -- no build-dea-coverage run.
    _seed_curated_register(data_root, "2026-08-14")
    _seed_maus_extract_over_site_a(data_root, "2026-08-16")

    footprints_result = runner.invoke(
        app,
        ["build-maus-footprint-areas", "--config", str(cfg_file), "--date", "2026-08-17"],
    )
    assert footprints_result.exit_code == 0, footprints_result.output

    crosswalk_result = runner.invoke(
        app,
        ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-18"],
    )
    assert crosswalk_result.exit_code == 0, crosswalk_result.output

    result = runner.invoke(
        app, ["derive-dea-volume", "--config", str(cfg_file), "--date", "2026-08-19"]
    )
    assert result.exit_code == 1
    assert "build-dea-coverage" in result.output


def test_derive_dea_volume_refuses_mismatched_maus_digests(tmp_path, monkeypatch):
    """Crosswalk and footprints built from DIFFERENT Maus snapshots: the ids
    can still join, so only the digests catch it."""
    cfg_file, data_root = _seed_derive_dea_volume_chain(tmp_path, monkeypatch)

    footprint_manifest_path = (
        data_root
        / "curated"
        / "maus_footprint_areas"
        / "2026-08-17"
        / "footprint_areas.parquet.run_manifest.json"
    )
    manifest = json.loads(footprint_manifest_path.read_text(encoding="utf-8"))
    manifest["resolved_args"]["maus_gpkg_sha256"] = "0" * 64
    footprint_manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = runner.invoke(
        app, ["derive-dea-volume", "--config", str(cfg_file), "--date", "2026-08-19"]
    )
    assert result.exit_code == 1
    assert "maus" in result.output.lower()


# --- fetch-region-boundaries CLI command ------------------------------------


def _rdc_fixture_geojson_bytes() -> bytes:
    """Nine-region synthetic DPIRD-020 stand-in as GeoJSON bytes."""
    names = [
        "Pilbara",
        "Goldfields-Esperance",
        "Kimberley",
        "Gascoyne",
        "Mid West",
        "Wheatbelt",
        "Peel",
        "South West",
        "Great Southern",
    ]
    gdf = gpd.GeoDataFrame(
        {"dpird_region_name": names},
        geometry=[Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)]) for i in range(len(names))],
        crs="EPSG:4283",
    )
    return gdf.to_json().encode("utf-8")


def test_fetch_region_boundaries_writes_finalized_snapshot(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    payload = _rdc_fixture_geojson_bytes()
    monkeypatch.setattr("wa_mine_monitor.cli._fetch_region_boundaries_bytes", lambda: payload)
    result = runner.invoke(
        app,
        ["fetch-region-boundaries", "--config", str(cfg_file), "--date", "2026-08-16"],
    )
    assert result.exit_code == 0, result.output
    snapshot_dir = tmp_path / "data" / "raw" / "wa_rdc_regions" / "2026-08-16"
    assert (snapshot_dir / "regions.geojson").exists()
    assert not (snapshot_dir / "regions.gpkg").exists()
    assert (snapshot_dir / "SHA256SUMS.txt").exists()
    manifest = json.loads((snapshot_dir / "SHA256SUMS.txt.run_manifest.json").read_text())
    assert manifest["resolved_args"]["source_url"].startswith(
        "https://public-services.slip.wa.gov.au/public/rest/services/"
        "SLIP_Public_Services/Boundaries/MapServer/25/query?"
    )
    payload_out = json.loads(result.output)
    assert payload_out["region_count"] == 9


def test_fetch_region_boundaries_refuses_non_geojson_body(tmp_path, monkeypatch):
    """A login page (what the old download portal now returns) must be
    refused by shape, never handed to GDAL."""
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    monkeypatch.setattr(
        "wa_mine_monitor.cli._fetch_region_boundaries_bytes",
        lambda: b"<!DOCTYPE html><html><body>Sign in</body></html>",
    )
    result = runner.invoke(
        app, ["fetch-region-boundaries", "--config", str(cfg_file), "--date", "2026-08-21"]
    )
    assert result.exit_code == 1
    assert "not a GeoJSON FeatureCollection" in json.loads(result.output)["refusal"]
    assert not (tmp_path / "data" / "raw" / "wa_rdc_regions" / "2026-08-21").exists()


def test_fetch_region_boundaries_refuses_truncated_rest_response(tmp_path, monkeypatch):
    """ArcGIS REST flags a page-limited result with exceededTransferLimit;
    a partial region set must never be snapshotted."""
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    body = json.loads(_rdc_fixture_geojson_bytes())
    body["exceededTransferLimit"] = True
    monkeypatch.setattr(
        "wa_mine_monitor.cli._fetch_region_boundaries_bytes",
        lambda: json.dumps(body).encode("utf-8"),
    )
    result = runner.invoke(
        app, ["fetch-region-boundaries", "--config", str(cfg_file), "--date", "2026-08-21"]
    )
    assert result.exit_code == 1
    assert "exceededTransferLimit" in json.loads(result.output)["refusal"]
    assert not (tmp_path / "data" / "raw" / "wa_rdc_regions" / "2026-08-21").exists()


def test_fetch_region_boundaries_refuses_extract_missing_required_region(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)

    gdf = gpd.GeoDataFrame(
        {"dpird_region_name": ["Pilbara", "Kimberley"]},
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
        ],
        crs="EPSG:4283",
    )
    payload = gdf.to_json().encode("utf-8")

    monkeypatch.setattr(
        "wa_mine_monitor.cli._fetch_region_boundaries_bytes",
        lambda: payload,
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


# --- freeze-d3-protocol CLI command -----------------------------------------


def _write_d3_config(tmp_path) -> Path:
    import shutil

    src = Path(__file__).resolve().parents[1] / "config" / "d3.yaml"
    dst = tmp_path / "d3.yaml"
    shutil.copy(src, dst)
    return dst


def test_freeze_d3_protocol_writes_digest_artifact(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    d3_file = _write_d3_config(tmp_path)
    result = runner.invoke(
        app,
        [
            "freeze-d3-protocol",
            "--config",
            str(cfg_file),
            "--protocol-config",
            str(d3_file),
            "--date",
            "2026-08-16",
        ],
    )
    assert result.exit_code == 0, result.output
    out_dir = tmp_path / "data" / "curated" / "d3-protocol" / "2026-08-16"
    frozen = json.loads((out_dir / "protocol.json").read_text())
    from wa_mine_monitor import d3_protocol

    expected = d3_protocol.protocol_digest(d3_protocol.load_protocol(d3_file))
    assert frozen["protocol_digest"] == expected
    manifest = json.loads((out_dir / "protocol.json.run_manifest.json").read_text())
    assert manifest["resolved_args"]["protocol_digest"] == expected


def test_freeze_d3_protocol_refuses_existing_output(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    d3_file = _write_d3_config(tmp_path)
    argv = [
        "freeze-d3-protocol",
        "--config",
        str(cfg_file),
        "--protocol-config",
        str(d3_file),
        "--date",
        "2026-08-16",
    ]
    assert runner.invoke(app, argv).exit_code == 0
    result = runner.invoke(app, argv)
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_freeze_d3_protocol_refuses_drifted_config(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    d3_file = _write_d3_config(tmp_path)
    raw = yaml.safe_load(d3_file.read_text())
    raw["d3"]["replicates"] = 5
    d3_file.write_text(yaml.safe_dump(raw))
    result = runner.invoke(
        app,
        [
            "freeze-d3-protocol",
            "--config",
            str(cfg_file),
            "--protocol-config",
            str(d3_file),
            "--date",
            "2026-08-16",
        ],
    )
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_freeze_d3_protocol_refuses_any_dated_dir(tmp_path, monkeypatch):
    """Single lineage: refuse if ANY dated directory exists, not just same date."""
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    d3_file = _write_d3_config(tmp_path)

    # First freeze with date 2026-08-18
    argv1 = [
        "freeze-d3-protocol",
        "--config",
        str(cfg_file),
        "--protocol-config",
        str(d3_file),
        "--date",
        "2026-08-18",
    ]
    assert runner.invoke(app, argv1).exit_code == 0

    # Try to freeze with different date 2026-08-19 - should refuse
    argv2 = [
        "freeze-d3-protocol",
        "--config",
        str(cfg_file),
        "--protocol-config",
        str(d3_file),
        "--date",
        "2026-08-19",
    ]
    result = runner.invoke(app, argv2)
    assert result.exit_code == 1
    assert "refusal" in result.output
    assert "Single lineage" in result.output or "already has dated directory" in result.output


# --- build-d3-inputs CLI command ---------------------------------------------
#
# Chains the already-tested seams: seed a Batch B register (10 sites, all
# high-confidence point-in-polygon matches to 10 distinct Maus footprints,
# same commodity/region/shape so all 10 land in ONE stratum -- adequacy needs
# >=10 footprints in a SINGLE stratum, not 10 spread across the 54-space) ->
# fetch-dea-catalogue (fake client, hrefs pointing at real local GeoTIFFs) ->
# build-dea-coverage -> seed a Maus extract -> build-maus-footprint-areas ->
# build-crosswalk -> fetch-region-boundaries (real CLI, fetch seam
# monkeypatched) -> freeze-d3-protocol (real CLI, captures the digest).
#
# 10 square footprints (~470m x 452m each, Polsby-Popper compactness ~0.785
# -> "compact"), arranged 5 columns x 2 rows in EPSG:4326 degrees, spaced
# apart so no two overlap. A SINGLE shared raster tile ("tile-a", chosen NOT
# to match the DEA `xNyN` lattice-id pattern so `pixel_support._validate_grid`
# never applies the 96,000 m tile-origin check to it) covers the whole
# reprojected footprint field, sized from the ACTUAL EPSG:3577 bounds so
# pixel indexing never needs to be hand-aligned. Ten synthetic years
# (2010-2019) of geomedian (nbart_nir/swir_1/swir_2) and FC
# (bs_pc_50/pv_pc_50/npv_pc_50) bands are written as real local GeoTIFFs;
# every OTHER required asset role (the collection health check in
# `sources/dea.py::validate_items` requires every declared role present) is
# a harmless placeholder `s3://` href that resolve_band_hrefs never reads.

#: A single shared tile id for every synthetic item -- deliberately NOT of
#: the DEA `xNyN` shape, so `pixel_support._validate_grid`'s tile-lattice
#: origin check (which only applies when the id matches that pattern) never
#: constrains this fixture's freely-chosen raster origin.
_D3_TILE_ID = "tile-a"
#: Ten fixture years -- `d3_protocol.REQUIRED_ADEQUACY["min_full_support_years"]`
#: is 10, so this is the minimum that can make a stratum adequate at all.
_D3_YEARS: tuple[int, ...] = tuple(range(2010, 2020))


def _d3_footprint_specs() -> list[dict]:
    """Ten distinct footprint specs: maus_id, site_id, lon/lat (the site's
    point, at the footprint's centroid -- a point-in-polygon match, i.e.
    `confidence == "high"`), and an EPSG:4326 square polygon.

    Square side ~0.005 deg (~470m x 452m at this latitude) covers ~230
    pixel centres on a 30m grid, comfortably over the 144-pixel minimum
    full-support threshold. All 10 sit in a fixed grid of 5 columns x 2
    rows spaced 0.01 deg apart -- no two footprints' bounding boxes touch.
    """
    specs = []
    base_lon, base_lat = 116.40, -32.60
    half = 0.0025
    step = 0.01
    idx = 0
    for row in range(2):
        for col in range(5):
            center_lon = base_lon + col * step
            center_lat = base_lat + row * step
            specs.append(
                {
                    "idx": idx,
                    "maus_id": f"D3FP{idx:02d}",
                    "site_id": f"site-d3-{idx:02d}",
                    "lon": center_lon,
                    "lat": center_lat,
                    "geometry": box(
                        center_lon - half, center_lat - half, center_lon + half, center_lat + half
                    ),
                }
            )
            idx += 1
    return specs


def _d3_raster_grid(specs: list[dict]) -> tuple[float, float, int, int]:
    """`(origin_x, origin_y, width, height)` for a single 30m EPSG:3577 tile
    covering every footprint's REPROJECTED bounds plus a 10-pixel buffer,
    origin snapped to the 30m lattice (`pixel_support._validate_grid`
    requires `c % 30 == 0` / `f % 30 == 0`).

    Computed from the ACTUAL reprojected geometry rather than hand-picked,
    so no fixture pixel needs manual alignment against the Albers
    projection: whatever pixel indices the footprints land on, this tile
    covers them.
    """
    gdf = gpd.GeoDataFrame(geometry=[s["geometry"] for s in specs], crs="EPSG:4326").to_crs(
        "EPSG:3577"
    )
    minx, miny, maxx, maxy = gdf.total_bounds
    buffer_m = 300.0  # 10 px
    origin_x = math.floor((minx - buffer_m) / 30.0) * 30.0
    origin_y = math.ceil((maxy + buffer_m) / 30.0) * 30.0
    width = math.ceil((maxx + buffer_m - origin_x) / 30.0)
    height = math.ceil((origin_y - (miny - buffer_m)) / 30.0)
    return origin_x, origin_y, width, height


#: (band asset key -> value-at-year fn). Geomedian values vary by year on
#: `nbart_nir` only (so NBR/NDMI's full-support series is non-constant
#: across years -- otherwise `d3_inputs.spearman` returns None for EVERY
#: row, undermining what the end-to-end test can observe); FC's
#: `bs_pc_50` varies the same way. `swir_1`/`swir_2`/`pv_pc_50`/`npv_pc_50`
#: stay constant -- nothing requires them to vary too.
_D3_BAND_VALUE_FNS: dict[str, "object"] = {
    "nbart_nir": lambda year: 3000 + (year - 2010) * 50,
    "nbart_swir_1": lambda _year: 1500,
    "nbart_swir_2": lambda _year: 1000,
    "bs_pc_50": lambda year: 20 + (year - 2010) * 2,
    "pv_pc_50": lambda _year: 50,
    "npv_pc_50": lambda _year: 30,
}


def _seed_d3_rasters(
    tmp_path: Path,
    origin_x: float,
    origin_y: float,
    width: int,
    height: int,
    n_uncomputable_years: int = 0,
) -> dict[tuple[str, int], str]:
    """Write one small GeoTIFF per (band asset key, fixture year), filled
    UNIFORMLY over the whole tile (no nodata anywhere) so every footprint's
    pixel support is valid for every year. Returns `(band, year) -> href`,
    `href` a plain local filesystem path string -- the form `rasterio.open`
    accepts directly for a local file, no `file://` prefix needed.
    """
    from tests.test_d3_inputs import _write_geotiff

    raster_dir = tmp_path / "d3_rasters"
    raster_dir.mkdir(exist_ok=True)
    hrefs: dict[tuple[str, int], str] = {}
    for band, value_fn in _D3_BAND_VALUE_FNS.items():
        years = list(_D3_YEARS) + [2020 + i for i in range(n_uncomputable_years)]
        for year in years:
            array = np.full((height, width), value_fn(year), dtype=np.int16)
            if n_uncomputable_years > 0 and year >= 2020 and band == "bs_pc_50":
                array[0, 0] = 255
            path = raster_dir / f"{band}_{year}.tif"
            _write_geotiff(path, array, origin=(origin_x, origin_y))
            hrefs[(band, year)] = str(path)
    return hrefs


def _d3_dea_fixture_pages(hrefs: dict[tuple[str, int], str], n_uncomputable_years: int = 0) -> dict:
    """Ten-year, single-tile STAC fixture pages for every `DEA_COLLECTIONS`
    spec: one item per (collection, year), all on `_D3_TILE_ID`. The three
    geomedian band assets (or the three FC `_pc_50` assets) resolve to real
    local GeoTIFFs; every OTHER required asset role is a placeholder
    `s3://` href `resolve_band_hrefs` never reads, present only to satisfy
    `sources/dea.py::validate_items`'s "every required asset role present"
    health check.
    """
    from wa_mine_monitor.source_catalogue import DEA_COLLECTIONS
    from wa_mine_monitor.sources.dea import collection_url, items_url

    pages: dict = {}
    for spec in DEA_COLLECTIONS:
        collection_json = {
            "type": "Collection",
            "id": spec.collection_id,
            "stac_version": "1.0.0",
            "description": "Synthetic D3 test fixture, not DEA data.",
            "license": "CC-BY-4.0",
            "extent": {
                "spatial": {"bbox": [[112.0, -36.0, 130.0, -13.0]]},
                "temporal": {"interval": [["2010-01-01T00:00:00Z", "2019-12-31T23:59:59Z"]]},
            },
            "links": [],
        }
        features = []
        years = list(_D3_YEARS) + [2020 + i for i in range(n_uncomputable_years)]
        for year in years:
            if spec.source_id == "dea_fc_pc":
                band_keys = ("bs_pc_50", "pv_pc_50", "npv_pc_50")
            else:
                band_keys = ("nbart_nir", "nbart_swir_1", "nbart_swir_2")
            assets = {role: {"href": f"s3://x/{role}.tif"} for role in spec.asset_roles}
            for band_key in band_keys:
                assets[band_key] = {"href": hrefs[(band_key, year)]}
            features.append(
                {
                    "type": "Feature",
                    "id": f"{spec.collection_id}-{_D3_TILE_ID}-{year}",
                    "bbox": [110.0, -35.0, 130.0, -13.0],
                    "properties": {
                        "datetime": f"{year}-07-02T00:00:00Z",
                        "odc:region_code": _D3_TILE_ID,
                        "odc:dataset_version": "4.0.0",
                    },
                    "assets": assets,
                }
            )
        pages[collection_url(spec.collection_id)] = collection_json
        pages[items_url(spec.collection_id)] = {
            "type": "FeatureCollection",
            "features": features,
            "links": [],
        }
    return pages


def _seed_d3_register(
    data_root: Path, date_str: str, specs: list[dict], *, extra_rows: list[dict] | None = None
) -> Path:
    """A minimal, schema-conforming `curated/register/<date_str>/
    register.parquet` -- one row per `_d3_footprint_specs` entry, all
    `commodity="Au"` (-> `d3_protocol.classify_commodity` == "gold" for
    every footprint, so all 10 land in the SAME commodity-group stratum;
    see this section's module comment).

    `extra_rows` (Task 15's `apply-d3-threshold` fixtures) are appended
    verbatim as ADDITIONAL register rows, over and above the one-per-spec
    rows above -- e.g. a site carrying no usable `lon`/`lat`, which
    `crosswalk.filter_register_for_crosswalk` excludes from `build-
    crosswalk`'s input population entirely, landing it outside
    `crosswalk.parquet` altogether (`REGISTER_SCHEMA`'s own column set;
    every column must be supplied)."""
    register_dir = data_root / "curated" / "register" / date_str
    register_dir.mkdir(parents=True)
    register_path = register_dir / "register.parquet"
    df = pd.DataFrame(
        {
            "site_id": [s["site_id"] for s in specs],
            "site_name": [f"Site {s['idx']}" for s in specs],
            "commodity": ["Au"] * len(specs),
            "stage": ["Operating"] * len(specs),
            "owners_at_snapshot": [f"Owner {s['idx']}" for s in specs],
            "snapshot_date": [date_str] * len(specs),
            "lon": [s["lon"] for s in specs],
            "lat": [s["lat"] for s in specs],
            "n_tenements_intersecting": pd.array([0] * len(specs), dtype="Int64"),
            "inclusion_status": ["operating"] * len(specs),
        }
    )
    if extra_rows:
        df = pd.concat([df, pd.DataFrame(extra_rows)], ignore_index=True)
        df["n_tenements_intersecting"] = df["n_tenements_intersecting"].astype("Int64")
    tables.write_table(df, register_path, register.REGISTER_SCHEMA)
    manifests.write_run_manifest(
        output=register_path,
        inputs=[SourceAsset(uri="minedex://Sites.csv", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "deadbeef", "dirty": False, "diff": ""},
    )
    return register_dir


def _seed_d3_maus_extract(data_root: Path, date_str: str, specs: list[dict]) -> Path:
    """A `raw/maus_v2/<date_str>/wa_extract.gpkg` carrying one polygon per
    `_d3_footprint_specs` entry -- the same shape `_seed_maus_extract`/
    `_seed_maus_extract_over_site_a` use, generalised to N footprints.

    UNLIKE those two (which never write a run manifest beside
    `SHA256SUMS.txt`, fine for `derive-dea-volume`'s narrower
    `source_manifest_digests`, which never reads a Maus manifest at all),
    `build-d3-inputs` hashes `SHA256SUMS.txt.run_manifest.json` itself into
    `input_manifest_digests["maus"]` -- exactly what the REAL
    `fetch-maus-extract` CLI writes beside its own `SHA256SUMS.txt`. This
    helper writes the same sidecar so a hand-seeded snapshot matches that
    real shape.
    """
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "maus_v2", date_str)
    gdf = gpd.GeoDataFrame(
        {"maus_id": [s["maus_id"] for s in specs]},
        geometry=[s["geometry"] for s in specs],
        crs="EPSG:4326",
    )
    gdf.to_file(snapshot_dir / "wa_extract.gpkg", driver="GPKG", layer="wa_extract")
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="Maus et al. v2 WA extract",
        endpoint="https://example.test/maus",
        licence_note="CC-BY-SA-4.0",
        purpose="test fixture",
    )
    sums_path = snapshots.finalize_snapshot(snapshot_dir)
    manifests.write_run_manifest(
        output=sums_path,
        inputs=[SourceAsset(uri="maus://wa_extract.gpkg", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "deadbeef", "dirty": False, "diff": ""},
    )
    return snapshot_dir


def _d3_regions_geojson_bytes(specs: list[dict], margin: float = 0.5) -> bytes:
    """A two-region DPIRD-020 stand-in: "Pilbara" is the union of a
    `margin`-radius square around each spec's point (every representative
    point falls inside its own square, so every footprint's `region`
    stratum is "pilbara" -- all 10 in ONE stratum when `specs` is the full
    fixture set); "Goldfields-Esperance" sits well away, satisfying
    `wa_regions.load_regions`'s `REQUIRED_REGIONS` check without
    overlapping it.

    Built as a union of per-point squares rather than one bounding box so
    that a caller can pass a SUBSET of `specs` (dropping footprints meant
    to fall outside every RDC region) and have "Pilbara" genuinely exclude
    them: the fixture grid is 5 cols x 2 rows at 0.01 deg spacing, so any
    single point's lon/lat is shared by another row/column, and a bounding
    box over the remaining points would still reach the dropped one's
    corner. `margin` must stay below the 0.005 deg half-step for a dropped
    point to land outside every kept point's square.
    """
    pilbara = unary_union(
        [
            box(s["lon"] - margin, s["lat"] - margin, s["lon"] + margin, s["lat"] + margin)
            for s in specs
        ]
    )
    goldfields = box(120.0, -31.0, 121.0, -30.0)
    gdf = gpd.GeoDataFrame(
        {"dpird_region_name": ["Pilbara", "Goldfields-Esperance"]},
        geometry=[pilbara, goldfields],
        crs="EPSG:4283",
    )
    return gdf.to_json().encode("utf-8")


class D3Seed(NamedTuple):
    cfg_file: Path
    protocol_digest: str
    d3_yaml_path: Path


def _seed_d3_inputs_chain(
    tmp_path: Path,
    monkeypatch,
    *,
    build_coverage: bool = True,
    extra_register_rows: list[dict] | None = None,
    extra_maus_specs: list[dict] | None = None,
    n_uncomputable_years: int = 0,
    n_outside_region: int = 0,
) -> D3Seed:
    """Arrange the full input chain `build-d3-inputs` reads, via the
    already-tested CLI seams, ending with a frozen D3 protocol. Returns a
    `D3Seed(cfg_file, protocol_digest, d3_yaml_path)`.

    `build_coverage=False` skips `build-dea-coverage` entirely, leaving the
    bare Batch B register as the ONLY (and therefore latest) curated
    register -- the state `test_build_d3_inputs_refuses_bare_batch_b_
    register` needs.

    `extra_register_rows` (Task 15) is passed straight through to `_seed_d3_
    register`.

    `extra_maus_specs` are appended to the Maus extract's polygons ONLY
    (never to the register/raster/region fixtures) -- a `_d3_footprint_
    specs`-shaped dict whose `geometry` overlaps an EXISTING spec's site
    point, so `build-crosswalk`'s real point-in-polygon matching produces a
    second `confidence == "high"` row for that site (a multi-maus_id Tier 1
    site, per the D13 E4 fix this fixture exists to exercise).

    `n_outside_region` (Decision 2026-08-21) shrinks the "Pilbara" RDC
    polygon to exclude the LAST `n_outside_region` footprint specs (e.g.
    `n=1` drops spec idx 9 = maus_id "D3FP09"): they stay in the register,
    crosswalk and Maus extract, so they are still Tier-1 candidates, but
    their representative point is covered by no RDC region.
    """
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"

    specs = _d3_footprint_specs()
    origin_x, origin_y, width, height = _d3_raster_grid(specs)
    hrefs = _seed_d3_rasters(tmp_path, origin_x, origin_y, width, height, n_uncomputable_years)

    _seed_d3_register(data_root, "2026-08-10", specs, extra_rows=extra_register_rows)

    fake = _FakeCatalogueClient(_d3_dea_fixture_pages(hrefs, n_uncomputable_years))
    monkeypatch.setattr("wa_mine_monitor.cli.new_dea_client", lambda: fake)
    catalogue_result = runner.invoke(
        app, ["fetch-dea-catalogue", "--config", str(cfg_file), "--date", "2026-08-11"]
    )
    assert catalogue_result.exit_code == 0, catalogue_result.output

    if build_coverage:
        coverage_result = runner.invoke(
            app,
            [
                "build-dea-coverage",
                "--config",
                str(cfg_file),
                "--date",
                "2026-08-12",
                "--catalogue-date",
                "2026-08-11",
            ],
        )
        assert coverage_result.exit_code == 0, coverage_result.output

    _seed_d3_maus_extract(data_root, "2026-08-13", specs + (extra_maus_specs or []))

    footprints_result = runner.invoke(
        app, ["build-maus-footprint-areas", "--config", str(cfg_file), "--date", "2026-08-14"]
    )
    assert footprints_result.exit_code == 0, footprints_result.output

    crosswalk_result = runner.invoke(
        app, ["build-crosswalk", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert crosswalk_result.exit_code == 0, crosswalk_result.output

    regions_specs = specs[: len(specs) - n_outside_region] if n_outside_region else specs
    regions_margin = 0.002 if n_outside_region else 0.5
    regions_payload = _d3_regions_geojson_bytes(regions_specs, margin=regions_margin)
    monkeypatch.setattr(
        "wa_mine_monitor.cli._fetch_region_boundaries_bytes", lambda: regions_payload
    )
    regions_result = runner.invoke(
        app, ["fetch-region-boundaries", "--config", str(cfg_file), "--date", "2026-08-16"]
    )
    assert regions_result.exit_code == 0, regions_result.output

    d3_yaml_path = _write_d3_config(tmp_path)
    freeze_result = runner.invoke(
        app,
        [
            "freeze-d3-protocol",
            "--config",
            str(cfg_file),
            "--protocol-config",
            str(d3_yaml_path),
            "--date",
            "2026-08-17",
        ],
    )
    assert freeze_result.exit_code == 0, freeze_result.output
    protocol_digest = json.loads(freeze_result.output)["protocol_digest"]

    return D3Seed(cfg_file=cfg_file, protocol_digest=protocol_digest, d3_yaml_path=d3_yaml_path)


def test_run_reads_in_serial_order_preserves_order_and_first_error():
    from wa_mine_monitor import cli as cli_mod

    calls: list[int] = []

    def make(i):
        def job():
            calls.append(i)
            if i in (2, 4):
                raise d3_inputs.D3InputsError(f"job {i} failed")
            return i * 10

        return job

    # All succeed: results come back in submission order regardless of workers.
    out = cli_mod._run_reads_in_serial_order([make(0), make(1), make(3)], workers=4)
    assert out == [0, 10, 30]

    # Two failures: the FIRST IN SERIAL ORDER (job 2) is raised, not job 4.
    with pytest.raises(d3_inputs.D3InputsError, match="job 2 failed"):
        cli_mod._run_reads_in_serial_order([make(0), make(2), make(4)], workers=4)

    # workers=1 uses the same path.
    assert cli_mod._run_reads_in_serial_order([make(5)], workers=1) == [50]
    with pytest.raises(ValueError, match="read_workers"):
        cli_mod._run_reads_in_serial_order([make(5)], workers=0)


def test_build_d3_inputs_end_to_end_over_fixtures(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    data_root = tmp_path / "data"

    result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    out_dir = data_root / "curated" / "d3-inputs" / "2026-08-18"
    support_inputs = tables.read_table(out_dir / "support_inputs.parquet")
    assert (support_inputs["protocol_digest"] == seed.protocol_digest).all()
    assert (support_inputs["n_replicates"] == 100).all()
    assert set(support_inputs["support_px"].unique()).issubset({9, 16, 25, 36, 49, 64, 100, 144})

    stratum_summary = tables.read_table(out_dir / "stratum_summary.parquet")
    assert len(stratum_summary) == 54

    footprint_support = tables.read_table(out_dir / "footprint_support.parquet")
    assert int(footprint_support["selected"].sum()) >= 10

    assert payload["n_selected_footprints"] >= 10
    assert payload["n_candidate_footprints"] >= payload["n_selected_footprints"]


def test_build_d3_inputs_parallel_reads_are_byte_identical_to_serial(tmp_path, monkeypatch):
    """--read-workers changes wall-clock only: every output table must be
    byte-identical between 1 and 4 workers, and the manifest discloses it.

    A single seed is reused for both runs (rather than one seed per root):
    the raster hrefs `_seed_d3_rasters` writes are absolute paths under
    `tmp_path`, so two independently-seeded roots would embed different
    `href` strings in `extraction_assets.parquet` and fail this comparison
    for a reason that has nothing to do with `--read-workers`. Reusing one
    seed and varying only `--date` (no output column encodes `date`, per
    `d3_inputs.D3_EXTRACTION_ASSETS_SCHEMA` et al.) isolates the read-worker
    count as the only variable.
    """
    import hashlib

    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    dates = {1: "2026-08-18", 4: "2026-08-19"}
    digests: dict[int, dict[str, str]] = {}
    for workers, date in dates.items():
        result = runner.invoke(
            app,
            [
                "build-d3-inputs",
                "--config",
                str(seed.cfg_file),
                "--protocol-config",
                str(seed.d3_yaml_path),
                "--date",
                date,
                "--read-workers",
                str(workers),
            ],
        )
        assert result.exit_code == 0, result.output
        out_dir = tmp_path / "data" / "curated" / "d3-inputs" / date
        digests[workers] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(out_dir.glob("*.parquet"))
        }
        manifest = json.loads(
            (out_dir / "footprint_support.parquet.run_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["resolved_args"]["read_workers"] == workers
    assert len(digests[1]) == 5
    assert digests[1] == digests[4]


def test_build_d3_inputs_refuses_read_workers_below_one(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
            "--read-workers",
            "0",
        ],
    )
    assert result.exit_code != 0
    assert not (tmp_path / "data" / "curated" / "d3-inputs" / "2026-08-18").exists()


def test_build_d3_inputs_excludes_footprints_outside_rdc_regions(tmp_path, monkeypatch):
    """Decision 2026-08-21: a Tier-1 footprint covered by no RDC polygon is
    excluded with disclosure, not a refusal. 1 of 10 fixtures is outside
    (10%), so lift the 5% ceiling for this happy path."""
    monkeypatch.setattr("wa_mine_monitor.d3_protocol.MAX_UNCOVERED_FRACTION", 0.5)
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch, n_outside_region=1)
    data_root = tmp_path / "data"
    result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["region_ambiguity"]["n_footprints_outside_rdc_regions"] == 1
    assert payload["region_ambiguity"]["footprints_outside_rdc_regions"] == ["D3FP09"]

    out_dir = data_root / "curated" / "d3-inputs" / "2026-08-18"
    footprint_support = tables.read_table(out_dir / "footprint_support.parquet")
    row = footprint_support[footprint_support["maus_id"] == "D3FP09"].iloc[0]
    assert row["region"] is None or pd.isna(row["region"])
    assert row["support_not_computed_reason"] == d3_protocol.OUTSIDE_RDC_REGIONS_REASON
    assert not bool(row["selected"])
    manifest = json.loads((out_dir / "footprint_support.parquet.run_manifest.json").read_text())
    assert manifest["resolved_args"]["region_ambiguity"]["n_footprints_outside_rdc_regions"] == 1


def test_build_d3_inputs_refuses_when_too_many_footprints_outside_rdc_regions(
    tmp_path, monkeypatch
):
    """1 of 10 outside = 10% > MAX_UNCOVERED_FRACTION (5%): refuse, naming the ids."""
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch, n_outside_region=1)
    result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert "outside" in payload["refusal"] and "D3FP09" in payload["refusal"]
    assert not (tmp_path / "data" / "curated" / "d3-inputs" / "2026-08-18").exists()


def test_build_d3_inputs_refuses_existing_output_before_any_read(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    data_root = tmp_path / "data"
    output_dir = data_root / "curated" / "d3-inputs" / "2026-08-18"
    output_dir.mkdir(parents=True)

    calls: list[object] = []

    def _boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("rasterio.open must not be called")

    monkeypatch.setattr("rasterio.open", _boom)

    result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "refusal" in result.output
    assert calls == []


def test_build_d3_inputs_refuses_drifted_protocol(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    raw = yaml.safe_load(seed.d3_yaml_path.read_text())
    raw["d3"]["commodity_code_rules"].append({"group": "gold", "codes": ["Aurum"]})
    seed.d3_yaml_path.write_text(yaml.safe_dump(raw))

    result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
    )
    assert result.exit_code == 1
    assert "drift" in result.output


def test_build_d3_inputs_refuses_bare_batch_b_register(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch, build_coverage=False)

    result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
    )
    assert result.exit_code == 1
    assert "build-dea-coverage" in result.output


def test_build_d3_inputs_refuses_maus_digest_mismatch(tmp_path, monkeypatch):
    """Crosswalk and footprint-areas built from DIFFERENT Maus snapshots:
    the ids can still join, so only the digests catch it (mirrors
    `test_derive_dea_volume_refuses_mismatched_maus_digests`'s intent, but
    by RE-SEEDING footprint-areas from a genuinely different Maus snapshot
    rather than hand-tampering a manifest field)."""
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    data_root = tmp_path / "data"

    other_specs = _d3_footprint_specs()
    for spec in other_specs:  # shift the whole field -- guaranteed different bytes
        spec["lon"] += 1.0
        spec["geometry"] = box(
            spec["lon"] - 0.0025, spec["lat"] - 0.0025, spec["lon"] + 0.0025, spec["lat"] + 0.0025
        )
    _seed_d3_maus_extract(data_root, "2026-08-18", other_specs)

    footprints_result = runner.invoke(
        app, ["build-maus-footprint-areas", "--config", str(seed.cfg_file), "--date", "2026-08-19"]
    )
    assert footprints_result.exit_code == 0, footprints_result.output

    result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-20",
        ],
    )
    assert result.exit_code == 1
    assert "sha256" in result.output


def test_build_d3_inputs_refuses_second_frozen_protocol(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    data_root = tmp_path / "data"
    (data_root / "curated" / "d3-protocol" / "2026-08-99").mkdir(parents=True)

    result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
    )
    assert result.exit_code == 1
    assert "protocol" in result.output


def test_derive_d3_threshold_end_to_end(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    data_root = tmp_path / "data"

    build_result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
    )
    assert build_result.exit_code == 0, build_result.output

    result = runner.invoke(
        app,
        [
            "derive-d3-threshold",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-19",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["n_star"] in {9, 16, 25, 36, 49, 64, 100, 144}

    output_path = data_root / "curated" / "d3-threshold" / "2026-08-19" / "threshold.json"
    threshold = json.loads(output_path.read_text())
    assert threshold["nominal_area_m2"] == 900 * payload["n_star"]
    assert threshold["protocol_digest"] == seed.protocol_digest
    assert len(threshold["adequate_strata"]) + len(threshold["inadequate_strata"]) == 54


def test_derive_d3_threshold_refuses_digest_mismatch(tmp_path, monkeypatch):
    import shutil

    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    data_root = tmp_path / "data"

    build_result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
    )
    assert build_result.exit_code == 0, build_result.output

    shutil.rmtree(data_root / "curated" / "d3-protocol" / "2026-08-17")
    raw = yaml.safe_load(seed.d3_yaml_path.read_text())
    raw["d3"]["commodity_code_rules"].append({"group": "gold", "codes": ["Aurum"]})
    seed.d3_yaml_path.write_text(yaml.safe_dump(raw))

    refreeze_result = runner.invoke(
        app,
        [
            "freeze-d3-protocol",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-19",
        ],
    )
    assert refreeze_result.exit_code == 0, refreeze_result.output

    result = runner.invoke(
        app,
        [
            "derive-d3-threshold",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-20",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "different protocol" in result.output


def test_derive_d3_threshold_refuses_missing_inputs(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        [
            "derive-d3-threshold",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "d3-inputs" in result.output


# --- apply-d3-threshold CLI command -----------------------------------------
#
# Chains `_seed_d3_inputs_chain` through the two already-tested commands
# `build-d3-inputs` and `derive-d3-threshold` (dates 2026-08-18/-19), then
# exercises `apply-d3-threshold` at 2026-08-20 -- the D13 D5 trajectory-
# status/eligibility columns joined onto the register.


def _run_d3_threshold_chain(tmp_path, monkeypatch, **seed_kwargs) -> D3Seed:
    """`_seed_d3_inputs_chain` -> `build-d3-inputs` (2026-08-18) ->
    `derive-d3-threshold` (2026-08-19), asserting each step succeeds.
    Returns the `D3Seed` unchanged, so a caller invokes `apply-d3-threshold`
    itself at whatever `--date` it needs."""
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch, **seed_kwargs)

    build_result = runner.invoke(
        app,
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
    )
    assert build_result.exit_code == 0, build_result.output

    threshold_result = runner.invoke(
        app,
        [
            "derive-d3-threshold",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-19",
        ],
    )
    assert threshold_result.exit_code == 0, threshold_result.output
    return seed


def test_apply_d3_threshold_assigns_every_site_exactly_one_status(tmp_path, monkeypatch):
    seed = _run_d3_threshold_chain(tmp_path, monkeypatch)
    data_root = tmp_path / "data"
    register_path = data_root / "curated" / "register" / "2026-08-10" / "register.parquet"
    n_register_rows = len(tables.read_table(register_path))

    result = runner.invoke(
        app,
        ["apply-d3-threshold", "--config", str(seed.cfg_file), "--date", "2026-08-20"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    out = tables.read_table(Path(payload["output_path"]))
    assert len(out) == n_register_rows
    from wa_mine_monitor.register import _TRAJECTORY_STATUSES

    assert set(out["trajectory_status"].unique()) <= set(_TRAJECTORY_STATUSES)
    register.validate_eligible_register(out)  # must not raise

    eligible_mask = out["trajectory_status"] == "eligible"
    assert (out.loc[eligible_mask, "d3_eligible"] == True).all()
    assert not out.loc[~eligible_mask, "d3_eligible"].fillna(False).any()

    assert sum(payload["n_by_status"].values()) == len(out)
    assert payload["rows"] == len(out) == n_register_rows


def test_apply_d3_threshold_unmatched_site_is_no_usable_footprint(tmp_path, monkeypatch):
    unmatched_site_id = "site-unmatched-00"
    extra_row = {
        "site_id": unmatched_site_id,
        "site_name": "Unmatched Site",
        "commodity": "Gold",
        "stage": "Operating",
        "owners_at_snapshot": "Owner Unmatched",
        "snapshot_date": "2026-08-10",
        "lon": float("nan"),
        "lat": float("nan"),
        "n_tenements_intersecting": pd.NA,
        "inclusion_status": "operating",
    }
    seed = _run_d3_threshold_chain(tmp_path, monkeypatch, extra_register_rows=[extra_row])

    result = runner.invoke(
        app,
        ["apply-d3-threshold", "--config", str(seed.cfg_file), "--date", "2026-08-20"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    out = tables.read_table(Path(payload["output_path"]))
    row = out.loc[out["site_id"] == unmatched_site_id].iloc[0]
    assert row["trajectory_status"] == "no_usable_footprint"
    assert pd.isna(row["d3_eligible"])
    assert pd.isna(row["d3_threshold_px"])


def test_apply_d3_threshold_refuses_unverified_support_table(tmp_path, monkeypatch):
    seed = _run_d3_threshold_chain(tmp_path, monkeypatch)
    data_root = tmp_path / "data"
    support_path = data_root / "curated" / "d3-inputs" / "2026-08-18" / "footprint_support.parquet"
    # Corrupt AFTER its manifest is written -- a tampered support table.
    support_path.write_bytes(support_path.read_bytes() + b"\x00corrupt")

    result = runner.invoke(
        app,
        ["apply-d3-threshold", "--config", str(seed.cfg_file), "--date", "2026-08-20"],
    )
    assert result.exit_code == 1, result.output


def test_apply_d3_threshold_refuses_protocol_mismatch(tmp_path, monkeypatch):
    import shutil

    seed = _run_d3_threshold_chain(tmp_path, monkeypatch)
    data_root = tmp_path / "data"

    # Re-freeze under a DIFFERENT protocol (single lineage: the old dated
    # dir must be removed first, mirroring test_derive_d3_threshold_refuses_
    # digest_mismatch), so the frozen digest no longer matches the one the
    # threshold artefact and footprint_support table were built under.
    shutil.rmtree(data_root / "curated" / "d3-protocol" / "2026-08-17")
    raw = yaml.safe_load(seed.d3_yaml_path.read_text())
    raw["d3"]["commodity_code_rules"].append({"group": "gold", "codes": ["Aurum"]})
    seed.d3_yaml_path.write_text(yaml.safe_dump(raw))
    refreeze_result = runner.invoke(
        app,
        [
            "freeze-d3-protocol",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-21",
        ],
    )
    assert refreeze_result.exit_code == 0, refreeze_result.output

    result = runner.invoke(
        app,
        ["apply-d3-threshold", "--config", str(seed.cfg_file), "--date", "2026-08-20"],
    )
    assert result.exit_code == 1, result.output
    assert "protocol" in result.output


def test_apply_d3_threshold_forced_144_discloses(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from wa_mine_monitor import tables

    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch, n_uncomputable_years=2)

    result = runner.invoke(
        app, ["build-d3-inputs", "--config", str(seed.cfg_file), "--date", "2026-08-18"]
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app, ["derive-d3-threshold", "--config", str(seed.cfg_file), "--date", "2026-08-19"]
    )
    assert result.exit_code == 0, result.output

    threshold_path = (
        tmp_path / "data" / "curated" / "d3-threshold" / "2026-08-19" / "threshold.json"
    )
    with open(threshold_path) as f:
        threshold = json.load(f)
    assert threshold["criteria_passed"] is False

    result = runner.invoke(
        app, ["apply-d3-threshold", "--config", str(seed.cfg_file), "--date", "2026-08-20"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    out = tables.read_table(Path(payload["output_path"]))
    judged_mask = ~out["trajectory_status"].isin(
        ["no_usable_footprint", "crosswalk_not_high_confidence"]
    )
    judged = out[judged_mask]

    assert (judged["trajectory_status"] == "threshold_not_computed").all()
    assert (judged["d3_threshold_px"] == 144).all()
    assert (judged["d3_eligible"] == False).all()

    manifest_path = Path(payload["manifest_path"])
    with open(manifest_path) as f:
        manifest = json.load(f)
    print("MANIFEST:", manifest.keys())
    assert "failed_criteria" in manifest.get("resolved_args", {})
    assert len(manifest["resolved_args"]["failed_criteria"]) > 0


def test_apply_d3_threshold_refuses_forced_threshold_without_decision_record(tmp_path, monkeypatch):
    seed = _run_d3_threshold_chain(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        [
            "apply-d3-threshold",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-20",
            "--forced-threshold",
        ],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert "decision-record" in payload["refusal"] or "decision_record" in payload["refusal"]


def test_apply_d3_threshold_refuses_forced_threshold_with_missing_decision_record_file(
    tmp_path, monkeypatch
):
    seed = _run_d3_threshold_chain(tmp_path, monkeypatch)
    missing_record = tmp_path / "no-such-decision.md"

    result = runner.invoke(
        app,
        [
            "apply-d3-threshold",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-20",
            "--forced-threshold",
            "--decision-record",
            str(missing_record),
        ],
    )
    assert result.exit_code == 1, result.output


def test_apply_d3_threshold_forced_threshold_makes_sites_eligible_with_decision_record(
    tmp_path, monkeypatch
):
    seed = _run_d3_threshold_chain(tmp_path, monkeypatch)
    decision_record = tmp_path / "decision.md"
    decision_record.write_text("# Forced-threshold entry, authorised 2026-08-25\n")

    result = runner.invoke(
        app,
        [
            "apply-d3-threshold",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-20",
            "--forced-threshold",
            "--decision-record",
            str(decision_record),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["criteria_passed"] is False

    out = tables.read_table(Path(payload["output_path"]))
    judged_mask = ~out["trajectory_status"].isin(
        ["no_usable_footprint", "crosswalk_not_high_confidence"]
    )
    judged = out[judged_mask]
    assert judged["trajectory_status"].isin(["eligible", "insufficient_pixel_support"]).all()
    assert judged["d3_forced_threshold"].all()
    register.validate_eligible_register(out)  # must not raise

    manifest_path = Path(payload["manifest_path"])
    with open(manifest_path) as f:
        manifest = json.load(f)
    # `criteria_passed` itself is not re-checked from the manifest here: the
    # manifest's `resolved_args` run through `secrets.redact_secrets`
    # (`manifests.write_run_manifest`'s own documented discipline), which is
    # key-name-based and redacts `criteria_passed` as a `pass`-substring
    # match -- an existing, pre-dated behaviour of that redaction, not
    # something this task changes. The echoed `payload["criteria_passed"]`
    # above (unredacted) is the authoritative check that it stayed `False`.
    resolved_args = manifest["resolved_args"]
    assert resolved_args["forced_threshold"] is True
    assert resolved_args["decision_record"] == str(decision_record)


# --- extract-trajectories CLI command ---------------------------------------
#
# Chains `_seed_d3_inputs_chain` through `build-d3-inputs` (2026-08-18),
# `derive-d3-threshold` (2026-08-19) and `apply-d3-threshold` (2026-08-20),
# leaving an ELIGIBLE register as the latest curated register, then
# exercises `extract-trajectories` at 2026-08-21 -- the D13 E4 resumable
# Parquet-partition extraction.


def _seed_through_apply_d3_threshold(tmp_path, monkeypatch, **seed_kwargs) -> tuple[D3Seed, Path]:
    """`_seed_d3_inputs_chain` plus build-d3-inputs (2026-08-18),
    derive-d3-threshold (2026-08-19) and apply-d3-threshold (2026-08-20),
    leaving an ELIGIBLE register as the latest curated register. Returns
    `(seed, data_root)`. `seed_kwargs` are passed straight through to
    `_seed_d3_inputs_chain` (e.g. `extra_register_rows`).

    Applies via `--forced-threshold` (Batch E Task 0, docs/decisions/
    2026-08-25-batch-e-forced-threshold-entry.md): the D3 fixture's `pv_pc_50`/
    `npv_pc_50` bands are deliberately CONSTANT across years (`_D3_BAND_VALUE_
    FNS`'s own docstring), so their Spearman correlation is never computable
    and `criteria_passed` is always `False` for this fixture -- the forced
    path is the only way this fixture chain ever reaches `eligible` rows,
    exactly like `test_apply_d3_threshold_forced_threshold_makes_sites_
    eligible_with_decision_record` already relies on.
    """
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch, **seed_kwargs)
    data_root = tmp_path / "data"
    decision_record = tmp_path / "forced-threshold-decision.md"
    decision_record.write_text("# Forced-threshold entry, authorised 2026-08-25\n")
    for argv in (
        [
            "build-d3-inputs",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-18",
        ],
        [
            "derive-d3-threshold",
            "--config",
            str(seed.cfg_file),
            "--protocol-config",
            str(seed.d3_yaml_path),
            "--date",
            "2026-08-19",
        ],
        [
            "apply-d3-threshold",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-20",
            "--forced-threshold",
            "--decision-record",
            str(decision_record),
        ],
    ):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, result.output
    return seed, data_root


def test_extract_trajectories_writes_collection_year_partitions(tmp_path, monkeypatch):
    seed, data_root = _seed_through_apply_d3_threshold(tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        [
            "extract-trajectories",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-21",
            "--scope",
            "sites",
            "--site-id",
            "site-d3-00",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    out_dir = data_root / "curated" / "trajectories" / "2026-08-21"
    assert payload["n_partitions_written"] > 0
    assert payload["inserted"] > 0
    parts = sorted(out_dir.glob("collection_id=*/year=*/part-0000.parquet"))
    assert parts, sorted(p.name for p in out_dir.rglob("*"))
    for part in parts:
        assert Path(str(part) + manifests.MANIFEST_SUFFIX).exists()
    frame = tables.read_table(parts[0])
    assert set(frame.columns) == set(trajectories.TRAJECTORY_SCHEMA.names)
    assert (frame["site_id"] == "site-d3-00").all()
    assert (frame["computable"] | frame["not_computable_reason"].notna()).all()
    assert frame["d3_forced_threshold"].notna().all()
    assert frame["d3_forced_threshold"].dtype == bool
    # No other eligible site shares site-d3-00's footprint in this seed.
    assert (frame["shared_footprint_site_count"] == 1).all()
    # This fixture's FC bands are deliberately constant across years (see
    # `_seed_through_apply_d3_threshold`'s docstring), so `criteria_passed`
    # is always False and the forced-144 path is the only way it reaches
    # `eligible` rows -- every row here is forced, never passing-criteria.
    assert frame["d3_forced_threshold"].eq(True).all()
    assert (out_dir / "extraction_summary.json").exists()
    assert (out_dir / ("extraction_summary.json" + manifests.MANIFEST_SUFFIX)).exists()


def test_extract_trajectories_skips_already_verified_partitions(tmp_path, monkeypatch):
    seed, data_root = _seed_through_apply_d3_threshold(tmp_path, monkeypatch)
    argv = [
        "extract-trajectories",
        "--config",
        str(seed.cfg_file),
        "--scope",
        "sites",
        "--site-id",
        "site-d3-00",
    ]
    first = runner.invoke(app, [*argv, "--date", "2026-08-21"])
    assert first.exit_code == 0, first.output
    n_written = json.loads(first.output)["n_partitions_written"]

    # Simulate an INTERRUPTED first run: the partitions completed (each
    # digest-verified by its own manifest) but the run never reached the
    # batch summary. `extraction_summary.json` (and its manifest) is what
    # `_refuse_if_curated_output_already_exists` gates on -- it is the
    # ONLY thing standing between this dated directory and a resume, per
    # the docstring's "resuming into an existing dated directory is the
    # point of E4": a run that already finished (summary present) has
    # nothing left to resume.
    summary_path = data_root / "curated" / "trajectories" / "2026-08-21" / "extraction_summary.json"
    summary_path.unlink()
    Path(str(summary_path) + manifests.MANIFEST_SUFFIX).unlink()

    # Same dated directory: every partition is already covered, so the
    # second run inserts nothing and reports them all as `existing`.
    second = runner.invoke(app, [*argv, "--date", "2026-08-21"])
    assert second.exit_code == 0, second.output
    payload = json.loads(second.output)
    assert payload["existing"] == n_written
    assert payload["inserted"] == 0
    parts = list((data_root / "curated" / "trajectories" / "2026-08-21").rglob("part-*.parquet"))
    assert all(p.name == "part-0000.parquet" for p in parts)


def test_extract_trajectories_refuses_resume_with_a_different_site_scope(tmp_path, monkeypatch):
    """A partition written under one `--site-id` scope must never be
    silently absorbed by a resume under a DIFFERENT scope: the skip
    decision in `verified_parts` binds only on digest+schema, never on
    scope, so the resume-binding gate is what must catch this."""
    extra_row = {
        "site_id": "site-d3-00b",
        "site_name": "Site 0 (second entry)",
        "commodity": "Au",
        "stage": "Operating",
        "owners_at_snapshot": "Owner 0b",
        "snapshot_date": "2026-08-10",
        "lon": 116.40,
        "lat": -32.60,
        "n_tenements_intersecting": 0,
        "inclusion_status": "operating",
    }
    seed, data_root = _seed_through_apply_d3_threshold(
        tmp_path, monkeypatch, extra_register_rows=[extra_row]
    )
    argv_base = [
        "extract-trajectories",
        "--config",
        str(seed.cfg_file),
        "--date",
        "2026-08-21",
        "--scope",
        "sites",
    ]
    first = runner.invoke(app, [*argv_base, "--site-id", "site-d3-00"])
    assert first.exit_code == 0, first.output

    # Simulate an INTERRUPTED first run, exactly as
    # `test_extract_trajectories_skips_already_verified_partitions` does:
    # the partitions completed (each digest- and schema-verified by its own
    # manifest) but the run never reached the batch summary.
    out_dir = data_root / "curated" / "trajectories" / "2026-08-21"
    summary_path = out_dir / "extraction_summary.json"
    summary_path.unlink()
    Path(str(summary_path) + manifests.MANIFEST_SUFFIX).unlink()

    parts_before = sorted(out_dir.rglob("part-*.parquet"))
    assert parts_before

    # Resume with a DIFFERENT --site-id scope: every already-verified
    # partition was written for `site_ids == ["site-d3-00"]`, not
    # `["site-d3-00", "site-d3-00b"]`.
    second = runner.invoke(app, [*argv_base, "--site-id", "site-d3-00", "--site-id", "site-d3-00b"])
    assert second.exit_code == 1, second.output
    payload = json.loads(second.output)
    assert payload["differing_fields"] == ["site_ids"]
    assert "site_ids" in payload["refusal"]
    assert payload["partition"] in {str(p.parent) for p in parts_before}
    # Nothing was rewritten -- still only the version-0 part per partition.
    assert sorted(out_dir.rglob("part-*.parquet")) == parts_before


def test_extract_trajectories_refuses_a_stray_partition_outside_the_catalogue(
    tmp_path, monkeypatch
):
    seed, data_root = _seed_through_apply_d3_threshold(tmp_path, monkeypatch)
    argv = [
        "extract-trajectories",
        "--config",
        str(seed.cfg_file),
        "--scope",
        "sites",
        "--site-id",
        "site-d3-00",
    ]
    first = runner.invoke(app, [*argv, "--date", "2026-08-21"])
    assert first.exit_code == 0, first.output

    # Simulate the same interrupted-first-run resume scenario as
    # `test_extract_trajectories_skips_already_verified_partitions`, but
    # this time the dated directory ALSO carries a partition for a
    # (collection, year) that this run's catalogue snapshot does not
    # cover -- e.g. left behind by an earlier run against a different
    # catalogue snapshot. It must never be silently finalized: the
    # partition loop only visits (collection, year) pairs the CURRENT
    # catalogue derives, so an out-of-set partition would otherwise never
    # be digest- or schema-checked yet still end up inside the finalized
    # dataset.
    out_dir = data_root / "curated" / "trajectories" / "2026-08-21"
    summary_path = out_dir / "extraction_summary.json"
    summary_path.unlink()
    Path(str(summary_path) + manifests.MANIFEST_SUFFIX).unlink()

    collection_dir = next(out_dir.glob("collection_id=*"))
    stray_partition = collection_dir / "year=1900"
    stray_partition.mkdir()
    (stray_partition / "part-0000.parquet").write_bytes(b"not a real parquet file")

    second = runner.invoke(app, [*argv, "--date", "2026-08-21"])
    assert second.exit_code == 1, second.output
    refusal = json.loads(second.output)["refusal"]
    assert collection_dir.name.split("=", 1)[1] in refusal
    assert "1900" in refusal


def test_extract_trajectories_refuses_an_ineligible_site(tmp_path, monkeypatch):
    seed, _data_root = _seed_through_apply_d3_threshold(tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        [
            "extract-trajectories",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-21",
            "--scope",
            "sites",
            "--site-id",
            "site-does-not-exist",
        ],
    )
    assert result.exit_code == 1
    assert "not D3-eligible" in json.loads(result.output)["refusal"]


def test_extract_trajectories_refuses_statewide_without_the_huntly_gate(tmp_path, monkeypatch):
    seed, _data_root = _seed_through_apply_d3_threshold(tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        [
            "extract-trajectories",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-21",
            "--scope",
            "statewide",
        ],
    )
    assert result.exit_code == 1
    assert "validate-huntly" in json.loads(result.output)["refusal"]


# --- validate-huntly CLI command (D13 E5, engine-parity re-scope) ----------
#
# `sample_pilot_cube` (huntly_validation.py) is the left-hand side, sampled
# from a tiny synthetic pilot cube built here -- deliberately NOT imported
# from `tests/test_huntly_validation.py` (each test file builds its own
# fixtures). The grid mirrors that module's own fixture: a 4x4, 30 m grid
# with its north-west corner at (0, 120), so a site at (45, 75) sits
# squarely in the interior and a 3x3 window around it never clips.

_HUNTLY_TRANSFORM = from_origin(0, 120, 30, 30)


def _write_huntly_cog(path: Path, band_arrays: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = next(iter(band_arrays.values())).shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=len(band_arrays),
        dtype="float64",
        crs="EPSG:3577",
        transform=_HUNTLY_TRANSFORM,
    ) as dst:
        for i, (name, array) in enumerate(band_arrays.items(), start=1):
            dst.write(array, i)
            dst.set_band_description(i, name)


def _write_huntly_pilot_cube(composites_dir: Path, year: int) -> None:
    """A single-year pilot cube, uniform-valued so every pixel in the
    sampling window agrees, matching the exact-agreement reference the
    passing tests build against `nbr=0.5, ndmi=0.3, ndvi=0.2,
    bare=10.0, pv=40.0, npv=50.0`."""
    _write_huntly_cog(
        composites_dir / "nbart" / f"nbart_{year}.tif",
        {
            "nbr": np.full((4, 4), 0.5, dtype=np.float64),
            "ndmi": np.full((4, 4), 0.3, dtype=np.float64),
            "ndvi": np.full((4, 4), 0.2, dtype=np.float64),
        },
    )
    _write_huntly_cog(
        composites_dir / "fractional_cover" / f"fractional_cover_{year}.tif",
        {
            "bare": np.full((4, 4), 10.0, dtype=np.float64),
            "pv": np.full((4, 4), 40.0, dtype=np.float64),
            "npv": np.full((4, 4), 50.0, dtype=np.float64),
        },
    )


def _write_huntly_site_meta(path: Path, site_id: str = "H0001") -> None:
    """jarrah's own `site_meta.parquet` column names
    (`x_incumbent`/`y_incumbent`, per `scripts/probes/detection_estimand/
    build_base.py` in `~/Documents/jarrah-rehab`), not `x`/`y`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"site_id": [site_id], "x_incumbent": [45.0], "y_incumbent": [75.0]}).to_parquet(
        path, index=False
    )


def _write_huntly_reference(path: Path, *, site_id: str = "H0001", year: int, nbr: float) -> None:
    """A `HUNTLY_REFERENCE_SCHEMA` reference table that agrees exactly with
    `_write_huntly_pilot_cube` on every metric except `nbr`, which callers
    control directly (to build both the passing and the failing fixture
    from one helper)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(
        [
            {
                "site_id": site_id,
                "year": year,
                "bare": 10.0,
                "pv": 40.0,
                "npv": 50.0,
                "nbr": nbr,
                "ndmi": 0.3,
                "ndvi": 0.2,
            }
        ],
        schema=huntly_validation.HUNTLY_REFERENCE_SCHEMA,
    )
    pq.write_table(table, path)


def _write_huntly_reference_with_counts(
    path: Path,
    *,
    site_id: str = "H0001",
    year: int,
    nbr: float,
    n_member_pixels: int = 9,
    n_valid_pixels: int = 9,
) -> None:
    """Like `_write_huntly_reference`, but ALSO carries `n_member_pixels`/
    `n_valid_pixels` -- the shape a counts-bearing jarrah reference is
    expected to take, and the only shape that can satisfy
    `--require-pixel-counts` (the CLI default) against `_write_huntly_pilot_cube`'s
    interior site, whose 3x3 window is 9 member / 9 valid pixels."""
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = huntly_validation.HUNTLY_REFERENCE_SCHEMA.append(
        pa.field("n_member_pixels", pa.int64(), nullable=False)
    ).append(pa.field("n_valid_pixels", pa.int64(), nullable=False))
    table = pa.Table.from_pylist(
        [
            {
                "site_id": site_id,
                "year": year,
                "bare": 10.0,
                "pv": 40.0,
                "npv": 50.0,
                "nbr": nbr,
                "ndmi": 0.3,
                "ndvi": 0.2,
                "n_member_pixels": n_member_pixels,
                "n_valid_pixels": n_valid_pixels,
            }
        ],
        schema=schema,
    )
    pq.write_table(table, path)


def test_validate_huntly_writes_a_passing_verdict(tmp_path, monkeypatch):
    """Sample an agreeing pilot cube and confirm the verdict artefact +
    manifest land where `require_huntly_gate` reads them.

    Uses a counts-bearing reference under the DEFAULT `--require-pixel-counts`
    flag: `read_reference_cube` now keeps `n_member_pixels`/`n_valid_pixels`
    as optional passthrough columns when the file carries them, so the
    honest, on-by-default gate is exercised here rather than waived (test
    (b) below still pins the refusal against a COUNTLESS reference by
    default).
    """
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    composites_dir = tmp_path / "composites"
    _write_huntly_pilot_cube(composites_dir, 2011)
    site_meta_path = tmp_path / "site_meta.parquet"
    _write_huntly_site_meta(site_meta_path)
    reference_path = tmp_path / "reference.parquet"
    _write_huntly_reference_with_counts(
        reference_path, year=2011, nbr=0.5, n_member_pixels=9, n_valid_pixels=9
    )

    result = runner.invoke(
        app,
        [
            "validate-huntly",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-22",
            "--reference-cube",
            str(reference_path),
            "--composites-dir",
            str(composites_dir),
            "--site-meta",
            str(site_meta_path),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["n_failures"] == 0

    output_path = (
        tmp_path / "data" / "curated" / "huntly-validation" / "2026-08-22" / "validation.json"
    )
    assert output_path.exists()
    assert Path(str(output_path) + manifests.MANIFEST_SUFFIX).exists()
    assert payload["output_path"] == str(output_path)


def test_validate_huntly_refuses_without_pixel_counts_by_default(tmp_path, monkeypatch):
    """The D13 default refuses against a reference carrying no counts --
    the honest state until a counts-bearing reference exists, and NOT to be
    weakened."""
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    composites_dir = tmp_path / "composites"
    _write_huntly_pilot_cube(composites_dir, 2011)
    site_meta_path = tmp_path / "site_meta.parquet"
    _write_huntly_site_meta(site_meta_path)
    reference_path = tmp_path / "reference.parquet"
    _write_huntly_reference(reference_path, year=2011, nbr=0.5)

    result = runner.invoke(
        app,
        [
            "validate-huntly",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-22",
            "--reference-cube",
            str(reference_path),
            "--composites-dir",
            str(composites_dir),
            "--site-meta",
            str(site_meta_path),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "pixel count" in json.loads(result.output)["refusal"]


def test_validate_huntly_writes_a_failing_verdict_and_exits_zero(tmp_path, monkeypatch):
    """A metric outside tolerance is a RESULT, not a crash."""
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    composites_dir = tmp_path / "composites"
    _write_huntly_pilot_cube(composites_dir, 2011)
    site_meta_path = tmp_path / "site_meta.parquet"
    _write_huntly_site_meta(site_meta_path)
    reference_path = tmp_path / "reference.parquet"
    # nbr off by 0.1, far outside the 1e-6 default tolerance.
    _write_huntly_reference(reference_path, year=2011, nbr=0.6)

    result = runner.invoke(
        app,
        [
            "validate-huntly",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-22",
            "--reference-cube",
            str(reference_path),
            "--composites-dir",
            str(composites_dir),
            "--site-meta",
            str(site_meta_path),
            "--no-require-pixel-counts",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is False
    assert payload["failures"][0]["reason"] == "value_outside_tolerance"


def test_extract_trajectories_statewide_runs_once_the_verdict_passes(tmp_path, monkeypatch):
    """The E4<->E5 interlock, end to end: statewide is refused, then
    validate-huntly passes, then statewide is permitted. ONE seed."""
    seed, data_root = _seed_through_apply_d3_threshold(tmp_path, monkeypatch)

    refused = runner.invoke(
        app,
        [
            "extract-trajectories",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-21",
            "--scope",
            "statewide",
        ],
    )
    assert refused.exit_code == 1
    assert "validate-huntly" in json.loads(refused.output)["refusal"]

    composites_dir = tmp_path / "composites"
    _write_huntly_pilot_cube(composites_dir, 2011)
    site_meta_path = tmp_path / "site_meta.parquet"
    _write_huntly_site_meta(site_meta_path)
    reference_path = tmp_path / "reference.parquet"
    _write_huntly_reference(reference_path, year=2011, nbr=0.5)

    validated = runner.invoke(
        app,
        [
            "validate-huntly",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-22",
            "--reference-cube",
            str(reference_path),
            "--composites-dir",
            str(composites_dir),
            "--site-meta",
            str(site_meta_path),
            "--no-require-pixel-counts",
        ],
    )
    assert validated.exit_code == 0, validated.output
    assert json.loads(validated.output)["passed"] is True

    permitted = runner.invoke(
        app,
        [
            "extract-trajectories",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-23",
            "--scope",
            "statewide",
        ],
    )
    assert permitted.exit_code == 0, permitted.output
    payload = json.loads(permitted.output)
    assert payload["n_partitions_written"] > 0
    assert (
        data_root / "curated" / "trajectories" / "2026-08-23" / "extraction_summary.json"
    ).exists()


def test_extract_trajectories_refuses_a_bare_register(tmp_path, monkeypatch):
    """A register that never went through apply-d3-threshold carries no
    `trajectory_status`, so nothing can be known to be eligible."""
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    result = runner.invoke(
        app,
        [
            "extract-trajectories",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-21",
            "--scope",
            "sites",
            "--site-id",
            "site-d3-00",
        ],
    )
    assert result.exit_code == 1
    assert "apply-d3-threshold" in json.loads(result.output)["refusal"]


def test_extract_trajectories_records_item_missing_rather_than_dropping(tmp_path, monkeypatch):
    """Delete one collection's catalogue items for one year and confirm the
    site-year still produces rows -- carrying `item_missing`, never absent."""
    seed, data_root = _seed_through_apply_d3_threshold(tmp_path, monkeypatch)
    # Blank the FC item for 2011 in the catalogue snapshot BEFORE extraction.
    # (Monkeypatch `cli._load_dea_items` to drop that item from the returned
    # mapping -- the snapshot's own digest gate must not be broken by
    # editing the file on disk.)
    real_loader = cli._load_dea_items

    def _drop_fc_2011(catalogue_dir):
        items = real_loader(catalogue_dir)
        items["dea_fc_pc"] = [
            item
            for item in items["dea_fc_pc"]
            if not str(item["properties"]["datetime"]).startswith("2011")
        ]
        return items

    monkeypatch.setattr("wa_mine_monitor.cli._load_dea_items", _drop_fc_2011)
    result = runner.invoke(
        app,
        [
            "extract-trajectories",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-21",
            "--scope",
            "sites",
            "--site-id",
            "site-d3-00",
        ],
    )
    assert result.exit_code == 0, result.output
    out_dir = data_root / "curated" / "trajectories" / "2026-08-21"
    fc_2011 = out_dir / "collection_id=ga_ls_fc_pc_cyear_3" / "year=2011"
    # No FC item exists for 2011 at all (dropped from the catalogue), so no
    # (source_id, tile, year) key exists in `item_index` for it either --
    # the whole partition is absent, while every OTHER FC year is present.
    assert not fc_2011.exists()
    other_fc_years = sorted(
        p.parent.name
        for p in out_dir.glob("collection_id=ga_ls_fc_pc_cyear_3/year=*/part-*.parquet")
    )
    assert other_fc_years
    assert "year=2011" not in other_fc_years


def test_extract_trajectories_reads_each_footprint_once_for_sites_that_share_it(
    tmp_path, monkeypatch
):
    """Two eligible sites on ONE footprint => ONE raster read per
    (collection, year), TWO rows.

    A per-site read loop would re-read the same pixels once per site
    sharing a footprint, for byte-identical values -- nothing else in the
    suite would notice that regression.
    """
    extra_row = {
        "site_id": "site-d3-00b",
        "site_name": "Site 0 (second entry)",
        "commodity": "Au",
        "stage": "Operating",
        "owners_at_snapshot": "Owner 0b",
        "snapshot_date": "2026-08-10",
        "lon": 116.40,
        "lat": -32.60,
        "n_tenements_intersecting": 0,
        "inclusion_status": "operating",
    }
    seed, data_root = _seed_through_apply_d3_threshold(
        tmp_path, monkeypatch, extra_register_rows=[extra_row]
    )

    calls: list[str] = []
    real = cli._read_footprint_year_bands

    def _tracking_read(**kw):
        calls.append(f"{kw['source_id']}:{kw['year']}")
        return real(**kw)

    monkeypatch.setattr(cli, "_read_footprint_year_bands", _tracking_read)

    result = runner.invoke(
        app,
        [
            "extract-trajectories",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-21",
            "--scope",
            "sites",
            "--site-id",
            "site-d3-00",
            "--site-id",
            "site-d3-00b",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls, "no raster reads were recorded"
    assert len(calls) == len(set(calls))  # one read per (collection, year)

    out_dir = data_root / "curated" / "trajectories" / "2026-08-21"
    parts = sorted(out_dir.glob("collection_id=*/year=*/part-0000.parquet"))
    assert parts
    rows = tables.read_table(parts[0])
    assert set(rows["site_id"]) == {"site-d3-00", "site-d3-00b"}
    a = rows[rows["site_id"] == "site-d3-00"].set_index("metric")["value"]
    b = rows[rows["site_id"] == "site-d3-00b"].set_index("metric")["value"]
    assert a.equals(b)  # identical, not merely close

    # Sharing disclosure (decision 2026-08-25): site-d3-00/00b sit on the
    # SAME maus_id footprint (site-d3-00b's lon/lat is site-d3-00's), so
    # both carry shared_footprint_site_count == 2.
    counts = rows.drop_duplicates("site_id").set_index("site_id")["shared_footprint_site_count"]
    assert counts["site-d3-00"] == 2
    assert counts["site-d3-00b"] == 2
    assert rows["d3_forced_threshold"].notna().all()


def test_extract_trajectories_uses_eligibilitys_maus_id_for_a_multi_match_site(
    tmp_path, monkeypatch
):
    """site-d3-00's point sits inside TWO overlapping Maus polygons
    (`D3FP00`, its regular footprint, and `D3AAA00`, an extra polygon this
    test adds) -- `build-crosswalk`'s real point-in-polygon matching (see
    `crosswalk._containment_rows`) therefore gives it TWO `confidence ==
    "high"` crosswalk rows, never silently reduced to one.

    `register.apply_d3_threshold_to_register` (~1373) resolves that
    ambiguity with a stable sort by `["site_id", "maus_id"]` then
    `drop_duplicates(keep="first")` -- the LEXICOGRAPHICALLY SMALLEST
    `maus_id` ("D3AAA00" < "D3FP00") is the footprint whose support
    site-d3-00's D3 eligibility was actually judged against. Before the E4
    fix, `extract_trajectories_cmd` built `maus_id_by_site` with a bare
    `dict(zip(tier1_df["site_id"], tier1_df["maus_id"]))`: since `build_
    crosswalk`'s matched rows for one site come out `maus_id`-sorted
    ASCENDING (verified directly against `crosswalk.build_crosswalk`), the
    LAST row processed for a repeated key -- and so the value a plain dict
    keeps -- is the LARGEST `maus_id`, "D3FP00". That is the OPPOSITE of
    eligibility's choice: the site would be extracted on a footprint that
    never passed its own D3 eligibility comparison.

    `site-d3-00b` (a second register row, same commodity/snapshot, offset
    just far enough east to sit inside `D3FP00` but OUTSIDE the smaller,
    west-shifted `D3AAA00`) isolates the effect: pre-fix, both sites
    resolve to "D3FP00" and wrongly appear to share it
    (`shared_footprint_site_count == 2`); post-fix, site-d3-00 resolves to
    its OWN footprint "D3AAA00" and site-d3-00b keeps "D3FP00" alone --
    neither actually shared (`shared_footprint_site_count == 1` for both),
    matching `register.py`'s own per-site tie-break.
    """
    base_lon, base_lat = 116.40, -32.60
    half = 0.0025
    extra_maus_spec = {
        "idx": "extra",
        "maus_id": "D3AAA00",
        "site_id": "site-d3-00",  # unused by `_seed_d3_maus_extract`; kept for shape parity
        "lon": base_lon - 0.0015,
        "lat": base_lat,
        # West-shifted by 0.0015 deg (~14 m at this latitude) from D3FP00's
        # own box: still covers site-d3-00's point (116.40) at its EAST
        # edge, but its west shift pushes its own east edge (116.401) short
        # of site-d3-00b's point (116.4020) below.
        "geometry": box(
            base_lon - 0.0015 - half,
            base_lat - half,
            base_lon - 0.0015 + half,
            base_lat + half,
        ),
    }
    extra_register_row = {
        "site_id": "site-d3-00b",
        "site_name": "Site 0 (second entry, distinct point)",
        "commodity": "Au",
        "stage": "Operating",
        "owners_at_snapshot": "Owner 0b",
        "snapshot_date": "2026-08-10",
        "lon": base_lon + 0.0020,  # inside D3FP00's box, outside D3AAA00's
        "lat": base_lat,
        "n_tenements_intersecting": 0,
        "inclusion_status": "operating",
    }
    seed, data_root = _seed_through_apply_d3_threshold(
        tmp_path,
        monkeypatch,
        extra_register_rows=[extra_register_row],
        extra_maus_specs=[extra_maus_spec],
    )

    # Ground truth: exactly what `build-crosswalk` actually matched, read
    # straight from the curated crosswalk this run produced.
    crosswalk_dir = _latest_curated_dated_dir(
        data_root / "curated" / "crosswalk", label="curated/crosswalk"
    )
    crosswalk_df = tables.read_table(crosswalk_dir / "crosswalk.parquet")
    site_00_high = crosswalk_df.loc[
        (crosswalk_df["site_id"] == "site-d3-00") & (crosswalk_df["confidence"] == "high")
    ]
    assert sorted(site_00_high["maus_id"]) == ["D3AAA00", "D3FP00"]
    site_00b_high = crosswalk_df.loc[
        (crosswalk_df["site_id"] == "site-d3-00b") & (crosswalk_df["confidence"] == "high")
    ]
    assert list(site_00b_high["maus_id"]) == ["D3FP00"]

    result = runner.invoke(
        app,
        [
            "extract-trajectories",
            "--config",
            str(seed.cfg_file),
            "--date",
            "2026-08-21",
            "--scope",
            "sites",
            "--site-id",
            "site-d3-00",
            "--site-id",
            "site-d3-00b",
        ],
    )
    assert result.exit_code == 0, result.output

    out_dir = data_root / "curated" / "trajectories" / "2026-08-21"
    parts = sorted(out_dir.glob("collection_id=*/year=*/part-0000.parquet"))
    assert parts
    rows = tables.read_table(parts[0])
    maus_id_by_site = rows.drop_duplicates("site_id").set_index("site_id")["maus_id"]
    # The lexicographically-first maus_id -- register.py's own tie-break --
    # is the footprint site-d3-00 was actually judged eligible against.
    assert maus_id_by_site["site-d3-00"] == "D3AAA00"
    assert maus_id_by_site["site-d3-00b"] == "D3FP00"

    counts = rows.drop_duplicates("site_id").set_index("site_id")["shared_footprint_site_count"]
    assert counts["site-d3-00"] == 1
    assert counts["site-d3-00b"] == 1
