"""CLI tests for build-trajectory-summary (Batch G). Fixture world:
tests/test_context_join.py's `_seed_full_world` -- three sites S1/S2/S3
over trajectory years {2000, 2001}, context year {2001} -- with the F6
context join built on top."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from tests.test_context_join import _seed_full_world
from tests.test_trajectory_qa import _seed_register
from wa_mine_monitor import manifests, trajectory_summary
from wa_mine_monitor.cli import app

runner = CliRunner()


def _seed_world_with_join(tmp_path: Path) -> tuple[Path, Path]:
    cfg, data_root = _seed_full_world(tmp_path)
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 0, result.output
    return cfg, data_root


def _build(cfg: Path, date: str = "2026-08-30"):
    return runner.invoke(app, ["build-trajectory-summary", "--config", str(cfg), "--date", date])


def test_refuses_without_a_context_join(tmp_path: Path) -> None:
    cfg, _data_root = _seed_full_world(tmp_path)  # no build-context-join
    result = _build(cfg)
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_refuses_without_an_acceptance_verdict(tmp_path: Path) -> None:
    cfg, data_root = _seed_world_with_join(tmp_path)
    import shutil

    shutil.rmtree(data_root / "curated" / "trajectories-acceptance")
    result = _build(cfg)
    assert result.exit_code == 1
    assert "accept-trajectories" in result.output


def test_refuses_when_parts_changed_after_acceptance(tmp_path: Path) -> None:
    # Same TOCTOU discipline as build-context-join: a part rewritten
    # after acceptance -- with a fresh self-consistent sidecar -- is
    # refused on parts_digest.
    from wa_mine_monitor import trajectories
    from wa_mine_monitor.provenance import SourceAsset

    cfg, data_root = _seed_world_with_join(tmp_path)
    troot = data_root / "curated" / "trajectories" / "2026-08-29"
    part = next(troot.glob("collection_id=*/year=*/part-0000.parquet"))
    df = pd.read_parquet(part)
    trajectories.write_trajectories(df.iloc[::-1].reset_index(drop=True), part)
    Path(str(part) + manifests.MANIFEST_SUFFIX).unlink()
    manifests.write_run_manifest(
        output=part,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    result = _build(cfg)
    assert result.exit_code == 1
    assert "parts_digest" in result.output or "part bytes" in result.output


def test_refuses_existing_output(tmp_path: Path) -> None:
    # The dated directory itself is the sentinel (design section 2 gate 1):
    # an existing -- even empty -- `curated/trajectory-summary/<date>/`
    # must refuse; checking only the .gpkg would let a stale or partial
    # directory be built into (codex plan-attack, 2026-08-30, finding 2).
    cfg, data_root = _seed_world_with_join(tmp_path)
    out_dir = data_root / "curated" / "trajectory-summary" / "2026-08-30"
    out_dir.mkdir(parents=True)  # deliberately empty: no gpkg inside
    result = _build(cfg)
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_refuses_context_join_built_from_different_trajectories(tmp_path: Path) -> None:
    # Version-skew gate: the context join's manifest must cite the SAME
    # trajectories directory this build consumes.
    cfg, data_root = _seed_world_with_join(tmp_path)
    join_path = data_root / "curated" / "context-join" / "2026-08-30" / "context_join.parquet"
    manifest_path = Path(str(join_path) + manifests.MANIFEST_SUFFIX)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["resolved_args"]["trajectories_dir"] = str(
        data_root / "curated" / "trajectories" / "1999-01-01"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = _build(cfg)
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_refuses_a_register_newer_than_the_acceptance(tmp_path: Path) -> None:
    # Register-binding gate (codex plan-attack, 2026-08-30, finding 1):
    # the acceptance verdict records `register_dir` (accept-trajectories
    # payload, cli.py); the summary consumes the register DIRECTLY, so a
    # register snapshot newer than the accepted one would let coordinates
    # or `d3_forced_threshold` drift out from under the accepted
    # trajectories. Seed a newer register after acceptance; refuse.
    cfg, data_root = _seed_world_with_join(tmp_path)
    _seed_register(data_root, "2026-08-30", [("S1", True), ("S2", True), ("S3", True)])
    result = _build(cfg)
    assert result.exit_code == 1
    assert "refusal" in result.output
    assert "register" in result.output


def test_build_trajectory_summary_writes_gpkg_and_manifest(tmp_path: Path) -> None:
    import geopandas as gpd
    import pyogrio

    cfg, _data_root = _seed_world_with_join(tmp_path)
    result = _build(cfg)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    out = Path(payload["output_path"])
    assert out.exists()
    manifest = json.loads(Path(payload["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["resolved_args"]["n_register_sites_unlocated"] == 0
    assert {"register_sites", "site_summary"} == {name for name, _ in pyogrio.list_layers(out)}
    summary = gpd.read_file(out, layer=trajectory_summary.SUMMARY_LAYER)
    assert sorted(summary["site_id"]) == ["S1", "S2", "S3"]
    # EXACT pinned column set (design section 3): nothing extra may ride
    # along, nothing pinned may be dropped by the gpkg round trip.
    assert set(summary.columns) == {*trajectory_summary.SUMMARY_COLUMNS, "geometry"}
    # L4/L17 disclosures survive the round trip on every row.
    assert summary["shared_footprint_site_count"].notna().all()
    assert summary["d3_forced_threshold"].notna().all()


def test_second_run_refuses_the_existing_output(tmp_path: Path) -> None:
    cfg, _data_root = _seed_world_with_join(tmp_path)
    assert _build(cfg).exit_code == 0
    result = _build(cfg)
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_refuses_a_truthy_but_non_boolean_passed_verdict(tmp_path: Path) -> None:
    # The gate demands the literal boolean True (design section 2; codex
    # diff review, 2026-08-30): a digest-valid verdict whose "passed" is
    # a truthy non-boolean (e.g. the string "true") must refuse, not
    # authorize the summary.
    from wa_mine_monitor.provenance import SourceAsset

    cfg, data_root = _seed_world_with_join(tmp_path)
    verdict_path = (
        data_root / "curated" / "trajectories-acceptance" / "2026-08-29" / "acceptance.json"
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    verdict["passed"] = "true"
    verdict_path.write_text(json.dumps(verdict, indent=2, sort_keys=True), encoding="utf-8")
    Path(str(verdict_path) + manifests.MANIFEST_SUFFIX).unlink()
    manifests.write_run_manifest(
        output=verdict_path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    result = _build(cfg)
    assert result.exit_code == 1
    assert "did not pass" in result.output
