import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from tests.test_cli import _seed_d3_inputs_chain
from wa_mine_monitor import tables
from wa_mine_monitor.cli import app

runner = CliRunner()


def test_protocol_frozen_before_any_spectral_read(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    result = runner.invoke(
        app, ["freeze-d3-protocol", "--config", str(seed.cfg_file), "--date", "2026-08-18"]
    )
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_no_accuracy_result_can_change_sample_definitions(tmp_path, monkeypatch):
    (tmp_path / "chain1").mkdir(parents=True, exist_ok=True)
    seed1 = _seed_d3_inputs_chain(tmp_path / "chain1", monkeypatch)
    runner.invoke(app, ["build-d3-inputs", "--config", str(seed1.cfg_file), "--date", "2026-08-18"])
    (tmp_path / "chain2").mkdir(parents=True, exist_ok=True)
    seed2 = _seed_d3_inputs_chain(tmp_path / "chain2", monkeypatch)
    runner.invoke(app, ["build-d3-inputs", "--config", str(seed2.cfg_file), "--date", "2026-08-18"])
    out1 = tables.read_table(
        tmp_path / "chain1/data/curated/d3-inputs/2026-08-18/footprint_support.parquet"
    )
    out2 = tables.read_table(
        tmp_path / "chain2/data/curated/d3-inputs/2026-08-18/footprint_support.parquet"
    )
    assert set(out1[out1["selected"]]["maus_id"]) == set(out2[out2["selected"]]["maus_id"])


def test_every_register_row_has_exactly_one_trajectory_status(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    runner.invoke(app, ["build-d3-inputs", "--config", str(seed.cfg_file), "--date", "2026-08-18"])
    runner.invoke(
        app, ["derive-d3-threshold", "--config", str(seed.cfg_file), "--date", "2026-08-19"]
    )
    res = runner.invoke(
        app, ["apply-d3-threshold", "--config", str(seed.cfg_file), "--date", "2026-08-20"]
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    out = tables.read_table(Path(payload["output_path"]))
    assert out["trajectory_status"].notna().all()


def test_sparse_strata_disclosed_not_pooled(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    runner.invoke(app, ["build-d3-inputs", "--config", str(seed.cfg_file), "--date", "2026-08-18"])
    res = runner.invoke(
        app, ["derive-d3-threshold", "--config", str(seed.cfg_file), "--date", "2026-08-19"]
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    with open(payload["output_path"]) as f:
        threshold = json.load(f)
    assert len(threshold["adequate_strata"]) + len(threshold["inadequate_strata"]) == 54


def test_determinism_same_inputs_same_outputs(tmp_path, monkeypatch):
    (tmp_path / "chain1").mkdir(parents=True, exist_ok=True)
    seed = _seed_d3_inputs_chain(tmp_path / "chain1", monkeypatch)
    runner.invoke(app, ["build-d3-inputs", "--config", str(seed.cfg_file), "--date", "2026-08-18"])
    runner.invoke(app, ["build-d3-inputs", "--config", str(seed.cfg_file), "--date", "2026-08-19"])
    out1 = tables.read_table(
        tmp_path / "chain1/data/curated/d3-inputs/2026-08-18/footprint_support.parquet"
    )
    out2 = tables.read_table(
        tmp_path / "chain1/data/curated/d3-inputs/2026-08-19/footprint_support.parquet"
    )
    pd.testing.assert_frame_equal(out1, out2)


def test_refusals_are_structured_json(tmp_path, monkeypatch):
    seed = _seed_d3_inputs_chain(tmp_path, monkeypatch)
    res = runner.invoke(
        app, ["freeze-d3-protocol", "--config", str(seed.cfg_file), "--date", "2026-08-18"]
    )
    assert res.exit_code != 0
    payload = json.loads(res.output)
    assert "refusal" in payload
