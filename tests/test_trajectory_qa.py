"""Tests for the E4 acceptance battery (trajectory_qa).

Fixtures forge tiny trajectories trees with the SAME production writers the
extractor uses (`trajectories.write_trajectories` + a real
`manifests.write_run_manifest` sidecar), so the QA module is exercised
against artefacts byte-shaped like the real thing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from wa_mine_monitor import manifests, tables, trajectories, trajectory_extract, trajectory_qa
from wa_mine_monitor import register as register_mod
from wa_mine_monitor.cli import app
from wa_mine_monitor.provenance import SourceAsset

runner = CliRunner()

GM_COLLECTION = "ga_ls5t_gm_cyear_3"
FC_COLLECTION = "ga_ls_fc_pc_cyear_3"


def test_expected_metric_set_derives_from_production_constants() -> None:
    assert trajectory_qa.expected_metric_set(GM_COLLECTION) == frozenset({"nbr", "ndmi"})
    assert trajectory_qa.expected_metric_set(FC_COLLECTION) == frozenset(
        {"bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"}
    )
    with pytest.raises(trajectory_qa.TrajectoryQaError):
        trajectory_qa.expected_metric_set("ga_not_a_collection")


def _trajectory_rows(
    *,
    sites_maus: list[tuple[str, str]],
    year: int,
    collection_id: str,
    metrics: list[str],
    forced: bool = True,
    value: float | None = 0.5,
    reason: str | None = None,
) -> pd.DataFrame:
    shared: dict[str, int] = {}
    for _s, m in sites_maus:
        shared[m] = shared.get(m, 0) + 1
    rows = []
    for site_id, maus_id in sites_maus:
        for metric in metrics:
            rows.append(
                {
                    "site_id": site_id,
                    "maus_id": maus_id,
                    "year": year,
                    "metric": metric,
                    "value": value,
                    "sensor": "ls5t" if collection_id == GM_COLLECTION else None,
                    "collection_id": collection_id,
                    "item_id": f"{collection_id}-x-{year}",
                    "product_version": "4.0.0",
                    "geomad_count": 5 if collection_id == GM_COLLECTION else None,
                    "n_member_pixels": 10,
                    "n_valid_pixels": 9,
                    "effective_pixel_support_px": 9,
                    "computable": value is not None,
                    "not_computable_reason": reason,
                    "value_out_of_documented_range": 0,
                    "transition_adjacent": False,
                    "shared_footprint_site_count": shared[maus_id],
                    "d3_forced_threshold": forced,
                    "source_snapshot_date": "2026-08-29",
                    "geometry": b"\x01\x02",
                }
            )
    return pd.DataFrame(rows)


def _write_partition(root: Path, collection_id: str, year: int, df: pd.DataFrame) -> Path:
    partition = trajectory_extract.partition_dir(root, collection_id, year)
    partition.mkdir(parents=True, exist_ok=True)
    path = partition / trajectory_extract.PART_FILENAME_TEMPLATE.format(version=0)
    trajectories.write_trajectories(df, path)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    return path


def _summary_for(root: Path, partitions: list[tuple[str, int]], **totals: object) -> dict:
    return {
        "date": "2026-08-29",
        "scope": "statewide",
        "existing": 0,
        "refused_empty": 0,
        "protocol_digest": "0" * 64,
        "partitions": [
            {
                "collection_id": c,
                "year": y,
                "path": str(trajectory_extract.partition_dir(root, c, y) / "part-0000.parquet"),
            }
            for c, y in partitions
        ],
        **totals,
    }


def _register_frame(sites_forced: list[tuple[str, bool]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "site_id": [s for s, _f in sites_forced],
            "trajectory_status": ["eligible"] * len(sites_forced),
            "d3_forced_threshold": [f for _s, f in sites_forced],
        }
    )


def test_partition_inventory_mismatch_is_a_reported_failure(tmp_path: Path) -> None:
    df = _trajectory_rows(
        sites_maus=[("S1", "M1")], year=2000, collection_id=GM_COLLECTION, metrics=["nbr", "ndmi"]
    )
    _write_partition(tmp_path, GM_COLLECTION, 2000, df)
    # Summary claims a partition that is not on disk.
    summary = _summary_for(
        tmp_path,
        [(GM_COLLECTION, 2000), (GM_COLLECTION, 2001)],
        inserted=2,
        not_computable=0,
        site_ids=["S1"],
    )
    report = trajectory_qa.accept_trajectories(
        tmp_path,
        summary=summary,
        register_df=_register_frame([("S1", True)]),
        expected_partition_count=2,
    )
    assert report.passed is False
    assert any("partition_inventory" == c.name and not c.passed for c in report.checks)


def test_partition_count_below_protocol_expectation_fails(tmp_path: Path) -> None:
    # On-disk and summary AGREE (1 partition each) but the protocol
    # expects 2 -- agreement with its own summary must not be enough.
    df = _trajectory_rows(
        sites_maus=[("S1", "M1")], year=2000, collection_id=GM_COLLECTION, metrics=["nbr", "ndmi"]
    )
    _write_partition(tmp_path, GM_COLLECTION, 2000, df)
    summary = _summary_for(
        tmp_path, [(GM_COLLECTION, 2000)], inserted=2, not_computable=0, site_ids=["S1"]
    )
    report = trajectory_qa.accept_trajectories(
        tmp_path,
        summary=summary,
        register_df=_register_frame([("S1", True)]),
        expected_partition_count=2,
    )
    assert report.passed is False
    assert any(c.name == "partition_count" and not c.passed for c in report.checks)
    assert any(c.name == "partition_inventory" and c.passed for c in report.checks)


def _good_world(tmp_path: Path) -> tuple[Path, dict, pd.DataFrame]:
    """Two partitions (one GM year, one FC year), two sites sharing one
    footprint plus one solo site -- the smallest tree that exercises the
    shared-footprint (L17), metric-set and accounting checks at once."""
    sites_maus = [("S1", "M1"), ("S2", "M1"), ("S3", "M2")]
    gm = _trajectory_rows(
        sites_maus=sites_maus, year=2000, collection_id=GM_COLLECTION, metrics=["nbr", "ndmi"]
    )
    fc = _trajectory_rows(
        sites_maus=sites_maus,
        year=2001,
        collection_id=FC_COLLECTION,
        metrics=["bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"],
    )
    fc.loc[fc["site_id"] == "S3", ["value"]] = None
    fc.loc[fc["site_id"] == "S3", "computable"] = False
    fc.loc[fc["site_id"] == "S3", "not_computable_reason"] = "zero_valid_pixels"
    _write_partition(tmp_path, GM_COLLECTION, 2000, gm)
    _write_partition(tmp_path, FC_COLLECTION, 2001, fc)
    summary = _summary_for(
        tmp_path,
        [(GM_COLLECTION, 2000), (FC_COLLECTION, 2001)],
        inserted=15,
        not_computable=3,
        site_ids=["S1", "S2", "S3"],
    )
    register = _register_frame([("S1", True), ("S2", True), ("S3", True)])
    return tmp_path, summary, register


def test_good_tree_passes_with_full_accounting(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is True, report.failures
    assert report.counts["rows"] == 15
    assert report.counts["not_computable_rows"] == 3
    assert report.counts["n_sites"] == 3
    assert report.counts["n_forced_threshold_true_rows"] == 15
    assert report.not_computable_by_reason == {"zero_valid_pixels": 3}


def test_tampered_part_is_a_written_failure_not_a_crash(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    part = next(root.glob("collection_id=*/year=*/part-0000.parquet"))
    part.write_bytes(part.read_bytes() + b"tamper")
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is False
    assert any(c.name == "parts_digest_and_schema" and not c.passed for c in report.checks)


def test_row_count_drift_fails_totals(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    summary["inserted"] = 999
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is False
    assert any(c.name == "total_rows_match_summary" and not c.passed for c in report.checks)


def test_missing_site_in_a_partition_fails_site_sets(tmp_path: Path) -> None:
    sites_maus = [("S1", "M1"), ("S2", "M2")]
    gm = _trajectory_rows(
        sites_maus=sites_maus, year=2000, collection_id=GM_COLLECTION, metrics=["nbr", "ndmi"]
    )
    _write_partition(tmp_path, GM_COLLECTION, 2000, gm)
    summary = _summary_for(
        tmp_path,
        [(GM_COLLECTION, 2000)],
        inserted=4,
        not_computable=0,
        site_ids=["S1", "S2", "S3"],
    )
    register = _register_frame([("S1", True), ("S2", True), ("S3", True)])
    report = trajectory_qa.accept_trajectories(
        tmp_path, summary=summary, register_df=register, expected_partition_count=1
    )
    assert report.passed is False
    failed = {c.name for c in report.checks if not c.passed}
    assert "partition_site_sets" in failed
    assert "summary_site_ids_match_register" in failed


def test_forced_threshold_divergence_from_register_fails(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    register.loc[register["site_id"] == "S1", "d3_forced_threshold"] = False
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is False
    assert any(
        c.name == "forced_threshold_register_consistency" and not c.passed for c in report.checks
    )


def test_non_forced_lineage_fails_even_when_register_agrees(tmp_path: Path) -> None:
    # Rows and register CONSISTENTLY say forced=False -- consistency alone
    # must not accept it: L4 requires the forced threshold on every
    # statewide row (design: d3_forced_threshold true everywhere).
    sites_maus = [("S1", "M1")]
    gm = _trajectory_rows(
        sites_maus=sites_maus,
        year=2000,
        collection_id=GM_COLLECTION,
        metrics=["nbr", "ndmi"],
        forced=False,
    )
    _write_partition(tmp_path, GM_COLLECTION, 2000, gm)
    summary = _summary_for(
        tmp_path, [(GM_COLLECTION, 2000)], inserted=2, not_computable=0, site_ids=["S1"]
    )
    report = trajectory_qa.accept_trajectories(
        tmp_path,
        summary=summary,
        register_df=_register_frame([("S1", False)]),
        expected_partition_count=1,
    )
    assert report.passed is False
    failed = {c.name for c in report.checks if not c.passed}
    assert "forced_threshold_all_true" in failed
    assert "forced_threshold_register_consistency" not in failed


def test_shared_footprint_value_divergence_fails_l17(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    partition = trajectory_extract.partition_dir(root, GM_COLLECTION, 2000)
    part = partition / "part-0000.parquet"
    df = pd.read_parquet(part)
    # S1 and S2 share M1 -- give S2 a different nbr value than S1.
    df.loc[(df["site_id"] == "S2") & (df["metric"] == "nbr"), "value"] = 0.9
    trajectories.write_trajectories(df, part)
    manifest_path = Path(str(part) + manifests.MANIFEST_SUFFIX)
    manifest_path.unlink()
    manifests.write_run_manifest(
        output=part,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is False
    assert any(c.name == "shared_footprint_consistency" and not c.passed for c in report.checks)


def test_shared_footprint_count_divergence_fails_l17(tmp_path: Path) -> None:
    root, summary, register = _good_world(tmp_path)
    partition = trajectory_extract.partition_dir(root, GM_COLLECTION, 2000)
    part = partition / "part-0000.parquet"
    df = pd.read_parquet(part)
    df["shared_footprint_site_count"] = 7
    trajectories.write_trajectories(df, part)
    manifest_path = Path(str(part) + manifests.MANIFEST_SUFFIX)
    manifest_path.unlink()
    manifests.write_run_manifest(
        output=part,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is False
    assert any(c.name == "shared_footprint_consistency" and not c.passed for c in report.checks)


def _write_config(tmp_path: Path, data_root: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f'run:\n  data_root: "{data_root}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg


def _seed_register(data_root: Path, date_str: str, sites_forced: list[tuple[str, bool]]) -> None:
    output_dir = data_root / "curated" / "register" / date_str
    output_dir.mkdir(parents=True)
    rows = []
    for site_id, forced in sites_forced:
        rows.append(
            {
                "site_id": site_id,
                "site_name": site_id,
                "commodity": "GOLD",
                "stage": "x",
                "owners_at_snapshot": "o",
                "snapshot_date": "2026-08-15",
                "lon": 116.0,
                "lat": -32.0,
                "n_tenements_intersecting": 1,
                "inclusion_status": "included",
                "n_dea_gm_ls5t_epochs": 1,
                "n_dea_gm_ls7e_epochs": 1,
                "n_dea_gm_ls8cls9c_epochs": 1,
                "n_dea_fc_pc_epochs": 1,
                "effective_pixel_support_px": 200,
                "d3_threshold_px": 144,
                "d3_eligible": True,
                "trajectory_status": "eligible",
                "d3_forced_threshold": forced,
            }
        )
    df = pd.DataFrame(rows)[list(register_mod.ELIGIBLE_REGISTER_SCHEMA.names)]
    path = output_dir / "register.parquet"
    tables.write_table(df, path, register_mod.ELIGIBLE_REGISTER_SCHEMA)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )


def _seed_trajectories(data_root: Path, date_str: str, tmp_path: Path) -> dict:
    """Seed a good two-partition trajectories tree under the data root and
    write its digest-manifested extraction summary."""
    root = data_root / "curated" / "trajectories" / date_str
    root.mkdir(parents=True)
    _root, summary, _register = _good_world(root)
    summary_path = root / "extraction_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
    manifests.write_run_manifest(
        output=summary_path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )
    return summary


def test_accept_trajectories_cli_writes_a_passing_verdict(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cfg = _write_config(tmp_path, data_root)
    _seed_trajectories(data_root, "2026-08-29", tmp_path)
    _seed_register(data_root, "2026-08-29", [("S1", True), ("S2", True), ("S3", True)])
    result = runner.invoke(
        app,
        [
            "accept-trajectories",
            "--config",
            str(cfg),
            "--date",
            "2026-08-30",
            "--expected-partitions",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    verdict_path = (
        data_root / "curated" / "trajectories-acceptance" / "2026-08-30" / "acceptance.json"
    )
    verdict = json.loads(verdict_path.read_text())
    assert verdict["passed"] is True
    assert verdict["counts"]["rows"] == 15
    assert verdict["extraction_summary_sha256"]
    assert len(verdict["parts_digest"]) == 64  # binds the verdict to the part bytes
    assert Path(str(verdict_path) + manifests.MANIFEST_SUFFIX).exists()


def test_accept_trajectories_cli_writes_a_failing_verdict_not_a_crash(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cfg = _write_config(tmp_path, data_root)
    _seed_trajectories(data_root, "2026-08-29", tmp_path)
    # Register disagrees with the tree: an extra eligible site.
    _seed_register(
        data_root, "2026-08-29", [("S1", True), ("S2", True), ("S3", True), ("S9", True)]
    )
    result = runner.invoke(
        app,
        [
            "accept-trajectories",
            "--config",
            str(cfg),
            "--date",
            "2026-08-30",
            "--expected-partitions",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    verdict_path = (
        data_root / "curated" / "trajectories-acceptance" / "2026-08-30" / "acceptance.json"
    )
    verdict = json.loads(verdict_path.read_text())
    assert verdict["passed"] is False
    assert verdict["failures"]


def test_accept_trajectories_cli_refuses_a_second_run(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    cfg = _write_config(tmp_path, data_root)
    _seed_trajectories(data_root, "2026-08-29", tmp_path)
    _seed_register(data_root, "2026-08-29", [("S1", True), ("S2", True), ("S3", True)])
    first = runner.invoke(
        app,
        [
            "accept-trajectories",
            "--config",
            str(cfg),
            "--date",
            "2026-08-30",
            "--expected-partitions",
            "2",
        ],
    )
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        app,
        [
            "accept-trajectories",
            "--config",
            str(cfg),
            "--date",
            "2026-08-30",
            "--expected-partitions",
            "2",
        ],
    )
    assert second.exit_code == 1
    assert "refusal" in second.output


def test_register_with_na_forced_flag_on_ineligible_rows_is_accepted(tmp_path: Path) -> None:
    # The live register carries NA d3_forced_threshold on ineligible rows
    # (no_usable_footprint, crosswalk_not_high_confidence); the forced-
    # threshold checks are defined against the ELIGIBLE set only, so those
    # NAs must not crash or fail acceptance.
    root, summary, register = _good_world(tmp_path)
    register["d3_forced_threshold"] = register["d3_forced_threshold"].astype("boolean")
    extra = pd.DataFrame(
        {
            "site_id": ["S8"],
            "trajectory_status": ["no_usable_footprint"],
            "d3_forced_threshold": pd.array([pd.NA], dtype="boolean"),
        }
    )
    register = pd.concat([register, extra], ignore_index=True)
    report = trajectory_qa.accept_trajectories(
        root, summary=summary, register_df=register, expected_partition_count=2
    )
    assert report.passed is True, report.failures


def test_eligible_site_with_null_forced_flag_is_a_refusal(tmp_path: Path) -> None:
    # An ELIGIBLE site without the flag is an unusable register (the
    # extraction lineage cannot be adjudicated), not a failed check.
    root, summary, register = _good_world(tmp_path)
    register["d3_forced_threshold"] = register["d3_forced_threshold"].astype("boolean")
    register.loc[register["site_id"] == "S1", "d3_forced_threshold"] = pd.NA
    with pytest.raises(trajectory_qa.TrajectoryQaError):
        trajectory_qa.accept_trajectories(
            root, summary=summary, register_df=register, expected_partition_count=2
        )
