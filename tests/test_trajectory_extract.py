"""D13 E4: resumable partition extraction of the Tier 1 trajectory table."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from wa_mine_monitor import manifests, tables, trajectories, trajectory_extract
from wa_mine_monitor.provenance import SourceAsset


def test_partition_dir_uses_collection_and_year_keys(tmp_path):
    path = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    assert path == tmp_path / "collection_id=ga_ls5t_gm_cyear_3" / "year=2011"


def test_partition_result_adds_componentwise():
    a = trajectory_extract.PartitionResult(
        existing=1, inserted=10, refused_empty=0, not_computable=2
    )
    b = trajectory_extract.PartitionResult(
        existing=0, inserted=5, refused_empty=3, not_computable=1
    )
    assert a + b == trajectory_extract.PartitionResult(
        existing=1, inserted=15, refused_empty=3, not_computable=3
    )


def test_partition_result_as_dict_is_json_ready():
    result = trajectory_extract.PartitionResult(
        existing=1, inserted=2, refused_empty=3, not_computable=4
    )
    assert result.as_dict() == {
        "existing": 1,
        "inserted": 2,
        "refused_empty": 3,
        "not_computable": 4,
    }


def _write_verified_part(partition: Path, version: int, df: pd.DataFrame) -> Path:
    """Write `df` as `part-<version>.parquet` with a real run manifest
    beside it -- the exact shape `write_partition` produces."""
    partition.mkdir(parents=True, exist_ok=True)
    path = partition / trajectory_extract.PART_FILENAME_TEMPLATE.format(version=version)
    trajectories.write_trajectories(df, path)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(partition)}},
        git_state={"sha": "deadbeef", "dirty": False, "diff": ""},
    )
    return path


def _one_trajectory_row(**overrides) -> pd.DataFrame:
    row = {
        "site_id": "site-d3-00",
        "maus_id": "D3FP00",
        "year": 2011,
        "metric": "nbr",
        "value": 0.5,
        "sensor": "ls5t",
        "collection_id": "ga_ls5t_gm_cyear_3",
        "item_id": "ga_ls5t_gm_cyear_3-x-2011",
        "product_version": "4.0.0",
        "geomad_count": None,
        "n_member_pixels": 230,
        "n_valid_pixels": 230,
        "effective_pixel_support_px": 230,
        "computable": True,
        "not_computable_reason": None,
        "value_out_of_documented_range": None,
        "transition_adjacent": False,
        "shared_footprint_site_count": 1,
        "d3_forced_threshold": False,
        "source_snapshot_date": "2026-08-13",
        "geometry": b"\x00\x01",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_partition_is_incomplete_when_absent(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    assert trajectory_extract.verified_parts(partition) == []


def test_partition_is_complete_when_part_digest_matches_its_manifest(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    path = _write_verified_part(partition, 0, _one_trajectory_row())
    assert trajectory_extract.verified_parts(partition) == [path]


def test_part_without_a_manifest_is_refused_not_counted(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    partition.mkdir(parents=True)
    path = partition / "part-0000.parquet"
    trajectories.write_trajectories(_one_trajectory_row(), path)
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="no run manifest"):
        trajectory_extract.verified_parts(partition)


def test_part_altered_after_its_manifest_is_refused(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    path = _write_verified_part(partition, 0, _one_trajectory_row())
    trajectories.write_trajectories(_one_trajectory_row(value=0.9), path)
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="changed after"):
        trajectory_extract.verified_parts(partition)


def test_next_part_version_is_one_past_the_highest_present(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    assert trajectory_extract.next_part_version(partition) == 0
    _write_verified_part(partition, 0, _one_trajectory_row())
    assert trajectory_extract.next_part_version(partition) == 1


def test_write_partition_refuses_an_empty_frame(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    empty = _one_trajectory_row().iloc[0:0]
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="refusing to write"):
        trajectory_extract.write_partition(empty, partition)


def test_write_partition_counts_rows_and_not_computable(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    rows = pd.concat(
        [
            _one_trajectory_row(),
            _one_trajectory_row(
                metric="ndmi",
                value=None,
                computable=False,
                not_computable_reason="read_failed",
                n_valid_pixels=None,
            ),
        ],
        ignore_index=True,
    )
    path, result = trajectory_extract.write_partition(rows, partition)
    assert path.name == "part-0000.parquet"
    assert result == trajectory_extract.PartitionResult(inserted=2, not_computable=1)
    assert len(tables.read_table(path)) == 2


def test_write_partition_writes_the_next_version_never_mutating_the_old(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    first = _write_verified_part(partition, 0, _one_trajectory_row())
    first_bytes = first.read_bytes()
    second, result = trajectory_extract.write_partition(_one_trajectory_row(value=0.75), partition)
    assert second.name == "part-0001.parquet"
    assert first.read_bytes() == first_bytes
    assert result.inserted == 1


def test_select_eligible_sites_keeps_only_eligible_rows():
    register_df = pd.DataFrame(
        {
            "site_id": ["a", "b", "c", "d"],
            "trajectory_status": [
                "eligible",
                "insufficient_pixel_support",
                "eligible",
                "no_usable_footprint",
            ],
            "d3_eligible": [True, False, True, None],
        }
    )
    assert trajectory_extract.select_eligible_sites(register_df) == ["a", "c"]


def test_select_eligible_sites_refuses_a_register_without_the_column():
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="trajectory_status"):
        trajectory_extract.select_eligible_sites(pd.DataFrame({"site_id": ["a"]}))


def test_sensor_for_source_is_none_for_fractional_cover():
    assert trajectory_extract.sensor_for_source("dea_gm_ls5t") == "ls5t"
    assert trajectory_extract.sensor_for_source("dea_gm_ls8cls9c") == "ls8cls9c"
    assert trajectory_extract.sensor_for_source("dea_fc_pc") is None


def test_sensor_for_source_refuses_an_unknown_collection():
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="unknown"):
        trajectory_extract.sensor_for_source("dea_gm_ls99")


def test_transition_adjacent_years_flags_overlap_and_both_edges():
    # ls5t covers 2009-2011, ls7e covers 2011-2013: 2011 is a genuine
    # overlap year and 2010/2012 each sit one year from a coverage change.
    covered = {"dea_gm_ls5t": {2009, 2010, 2011}, "dea_gm_ls7e": {2011, 2012, 2013}}
    flags = trajectory_extract.transition_adjacent_years(covered)
    assert flags[2009] is False
    assert flags[2010] is True
    assert flags[2011] is True
    assert flags[2012] is True
    assert flags[2013] is False


def test_transition_adjacent_years_is_all_false_for_one_uninterrupted_sensor():
    covered = {"dea_gm_ls5t": {2009, 2010, 2011}}
    flags = trajectory_extract.transition_adjacent_years(covered)
    assert set(flags.values()) == {False}


def _write_huntly_verdict(data_root: Path, date_str: str, payload: dict) -> Path:
    out_dir = data_root / "curated" / "huntly-validation" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "validation.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://huntly-cube", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "deadbeef", "dirty": False, "diff": ""},
    )
    return path


def test_statewide_gate_refuses_when_no_verdict_exists(tmp_path):
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="validate-huntly"):
        trajectory_extract.require_huntly_gate(tmp_path)


def test_statewide_gate_refuses_a_failed_verdict(tmp_path):
    _write_huntly_verdict(tmp_path, "2026-08-25", {"passed": False, "n_compared": 40})
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="did not pass"):
        trajectory_extract.require_huntly_gate(tmp_path)


def test_statewide_gate_refuses_a_verdict_altered_after_its_manifest(tmp_path):
    path = _write_huntly_verdict(tmp_path, "2026-08-25", {"passed": False, "n_compared": 40})
    path.write_text(json.dumps({"passed": True, "n_compared": 40}), encoding="utf-8")
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="changed after"):
        trajectory_extract.require_huntly_gate(tmp_path)


def test_statewide_gate_accepts_a_verified_passing_verdict(tmp_path):
    _write_huntly_verdict(
        tmp_path, "2026-08-25", {"passed": True, "n_compared": 40, "checkpoint_digest": "abc"}
    )
    verdict = trajectory_extract.require_huntly_gate(tmp_path)
    assert verdict["passed"] is True
    assert verdict["n_compared"] == 40
