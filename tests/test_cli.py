import json
import subprocess
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from wa_mine_monitor import register, snapshots
from wa_mine_monitor.cli import (
    _collect_git_state_disclosing_gaps,
    _latest_curated_dated_dir,
    _verify_snapshot_or_refuse,
    app,
)

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
