"""Encodes Task 1 Step 6 as real, regression-guarded behaviour.

Task 1 Step 6 of the implementation plan says: "`uv sync` then `uv run
python -c "import wa_mine_monitor"`. Expected: no error." Nothing else in
the repo turned that into a test, so nothing regression-guards the package
layout, the src-layout wheel config, or the editable install. These tests
close that gap and additionally pin the fail-closed defaults declared in
`config/base.yaml`.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import yaml


def test_wa_mine_monitor_package_resolves_from_installed_src_layout() -> None:
    """`import wa_mine_monitor` must succeed and resolve under `src/`.

    This is Task 1 Step 6 turned into an assertion: the package must be
    importable (the editable install / src-layout wheel config actually
    works), and the resolved module file must live under this repo's
    `src/wa_mine_monitor/` directory rather than some other copy that
    happens to be on the path.
    """
    module = importlib.import_module("wa_mine_monitor")
    module_file = Path(module.__file__).resolve()

    repo_root = Path(__file__).resolve().parents[1]
    expected_src_dir = (repo_root / "src" / "wa_mine_monitor").resolve()

    assert module_file.parent == expected_src_dir
    assert module_file.name == "__init__.py"


def test_wa_mine_monitor_spec_origin_is_src_layout() -> None:
    """The import machinery's own spec must point at `src/`, not site-packages.

    A stray `wa_mine_monitor` copied into site-packages (rather than an
    editable install of this src layout) would satisfy a bare
    `import wa_mine_monitor` while silently testing the wrong tree; this
    checks the resolved spec origin directly.
    """
    spec = importlib.util.find_spec("wa_mine_monitor")
    assert spec is not None
    assert spec.origin is not None

    origin = Path(spec.origin).resolve()
    repo_root = Path(__file__).resolve().parents[1]
    assert origin == (repo_root / "src" / "wa_mine_monitor" / "__init__.py").resolve()


def test_base_config_loads_as_yaml_with_fail_closed_defaults() -> None:
    """`config/base.yaml` parses and pins the fail-closed defaults.

    `run.redistribute_public` and `sources.minedex_public_export_blocked`
    are the two fail-closed licence-gate defaults this project depends on
    everywhere downstream; this pins them so a scaffold edit cannot flip
    either silently.
    """
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "config" / "base.yaml"

    assert config_path.is_file()

    loaded = yaml.safe_load(config_path.read_text())

    assert loaded["run"]["redistribute_public"] is False
    assert loaded["sources"]["minedex_public_export_blocked"] is True
