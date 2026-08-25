"""D13 E4: resumable partition extraction of the Tier 1 trajectory table."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from wa_mine_monitor import manifests, tables, trajectories, trajectory_extract
from wa_mine_monitor.provenance import SourceAsset

_OLD_SCHEMA_DROPPED_COLUMNS = ("shared_footprint_site_count", "d3_forced_threshold")


def test_partition_dir_uses_collection_and_year_keys(tmp_path):
    path = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    assert path == tmp_path / "collection_id=ga_ls5t_gm_cyear_3" / "year=2011"


def test_existing_partitions_is_empty_when_out_dir_is_absent(tmp_path):
    assert trajectory_extract.existing_partitions(tmp_path / "does-not-exist") == []


def test_existing_partitions_parses_real_partition_layouts(tmp_path):
    trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011).mkdir(parents=True)
    trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2012).mkdir(parents=True)
    trajectory_extract.partition_dir(tmp_path, "ga_ls_fc_pc_cyear_3", 2011).mkdir(parents=True)
    assert sorted(trajectory_extract.existing_partitions(tmp_path)) == [
        ("ga_ls5t_gm_cyear_3", 2011),
        ("ga_ls5t_gm_cyear_3", 2012),
        ("ga_ls_fc_pc_cyear_3", 2011),
    ]


def test_existing_partitions_ignores_top_level_summary_and_manifest_files(tmp_path):
    trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011).mkdir(parents=True)
    (tmp_path / "extraction_summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / ("extraction_summary.json" + manifests.MANIFEST_SUFFIX)).write_text(
        "{}", encoding="utf-8"
    )
    assert trajectory_extract.existing_partitions(tmp_path) == [("ga_ls5t_gm_cyear_3", 2011)]


def test_existing_partitions_refuses_a_malformed_collection_directory(tmp_path):
    (tmp_path / "not-a-collection-dir").mkdir()
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="not-a-collection-dir"):
        trajectory_extract.existing_partitions(tmp_path)


def test_existing_partitions_refuses_a_malformed_year_directory(tmp_path):
    (tmp_path / "collection_id=ga_ls5t_gm_cyear_3" / "not-a-year-dir").mkdir(parents=True)
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="not-a-year-dir"):
        trajectory_extract.existing_partitions(tmp_path)


def test_existing_partitions_refuses_a_non_canonical_year_directory_name(tmp_path):
    trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011).mkdir(parents=True)
    (tmp_path / "collection_id=ga_ls5t_gm_cyear_3" / "year=02011").mkdir()
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="year=02011"):
        trajectory_extract.existing_partitions(tmp_path)


def test_existing_partitions_refuses_a_unicode_digit_year_directory(tmp_path):
    """`year=2٠١١` mixes an ASCII leading digit with Arabic-Indic digits,
    which Python's `\\d` (and `int()`) both accept as equal to ASCII
    '2011'. The partition-directory regex must use `[0-9]` so this is
    refused as malformed rather than silently parsed to the integer 2011
    -- an unverified Unicode-named directory sitting inside a dataset the
    verification loop believes it fully checked would defeat the
    stray-partition gate this module exists to enforce."""
    collection_dir = tmp_path / "collection_id=ga_ls5t_gm_cyear_3"
    collection_dir.mkdir(parents=True)
    (collection_dir / "year=2٠١١").mkdir()
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="year=2"):
        trajectory_extract.existing_partitions(tmp_path)


def test_existing_partitions_refuses_a_stray_file_inside_a_collection_directory(tmp_path):
    collection_dir = tmp_path / "collection_id=ga_ls5t_gm_cyear_3"
    collection_dir.mkdir(parents=True)
    (collection_dir / "stray.txt").write_text("", encoding="utf-8")
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="stray.txt"):
        trajectory_extract.existing_partitions(tmp_path)


def test_part_files_refuses_a_unicode_digit_part_filename(tmp_path):
    """`part-٠٠١١.parquet` mixes Arabic-Indic digits into the part-filename
    slot. `_PART_FILENAME_RE` is ASCII-digit-only (`[0-9]`, not `\\d`), so
    this file matches neither `_PART_FILENAME_RE` nor
    `_PART_MANIFEST_FILENAME_RE` and must be refused -- silently skipping it
    would let it finalize, unverified, inside a partition directory the
    ledger's fail-closed convention requires to hold nothing the run did not
    check."""
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    partition.mkdir(parents=True)
    (partition / "part-٠٠١١.parquet").write_bytes(b"")
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="part-٠٠١١.parquet"):
        trajectory_extract.part_files(partition)


def test_part_files_refuses_a_stray_file_inside_a_partition_directory(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    partition.mkdir(parents=True)
    (partition / "stray.txt").write_text("", encoding="utf-8")
    with pytest.raises(trajectory_extract.TrajectoryExtractError, match="stray.txt"):
        trajectory_extract.part_files(partition)


def test_part_files_accepts_a_part_and_its_manifest_sidecar(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    path = _write_verified_part(partition, 0, _one_trajectory_row())
    assert trajectory_extract.part_files(partition) == [path]


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


def _write_old_schema_verified_part(partition: Path, version: int) -> Path:
    """Write a `part-NNNN.parquet` conforming to the schema TRAJECTORY_SCHEMA
    had BEFORE `shared_footprint_site_count` and `d3_forced_threshold` were
    added, with a real (correct) run manifest beside it -- reproducing a
    partition an older build of this branch finished and digest-verifies
    cleanly today, even though its row contract is stale."""
    old_schema = pa.schema(
        [f for f in trajectories.TRAJECTORY_SCHEMA if f.name not in _OLD_SCHEMA_DROPPED_COLUMNS]
    )
    df = _one_trajectory_row().drop(columns=list(_OLD_SCHEMA_DROPPED_COLUMNS))
    partition.mkdir(parents=True, exist_ok=True)
    path = partition / trajectory_extract.PART_FILENAME_TEMPLATE.format(version=version)
    tables.write_table(df[old_schema.names], path, old_schema)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(partition)}},
        git_state={"sha": "deadbeef", "dirty": False, "diff": ""},
    )
    return path


def test_verified_parts_accepts_a_part_matching_the_expected_schema(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    path = _write_verified_part(partition, 0, _one_trajectory_row())
    assert trajectory_extract.verified_parts(
        partition, expected_schema=trajectories.TRAJECTORY_SCHEMA
    ) == [path]


def test_verified_parts_refuses_a_part_predating_the_current_schema(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    path = _write_old_schema_verified_part(partition, 0)
    with pytest.raises(trajectory_extract.TrajectoryExtractError) as excinfo:
        trajectory_extract.verified_parts(partition, expected_schema=trajectories.TRAJECTORY_SCHEMA)
    message = str(excinfo.value)
    assert str(path) in message
    assert "shared_footprint_site_count" in message
    assert "d3_forced_threshold" in message
    assert "re-extract" in message


def _write_relaxed_nullable_part(partition: Path, version: int, field_name: str) -> Path:
    """Write a `part-NNNN.parquet` whose schema matches `TRAJECTORY_SCHEMA`
    field-for-field in name and type, except that `field_name` (a non-null
    field in `TRAJECTORY_SCHEMA`) is relaxed to nullable -- reproducing a
    legacy part that would pass a name/type-only schema gate while
    violating the declared trajectory row contract's nullability. Row
    values are the same valid, non-null values `_one_trajectory_row`
    writes, with a real (correct) run manifest beside it."""
    relaxed_schema = pa.schema(
        [
            f.with_nullable(True) if f.name == field_name else f
            for f in trajectories.TRAJECTORY_SCHEMA
        ]
    )
    df = _one_trajectory_row()
    partition.mkdir(parents=True, exist_ok=True)
    path = partition / trajectory_extract.PART_FILENAME_TEMPLATE.format(version=version)
    tables.write_table(df[relaxed_schema.names], path, relaxed_schema)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(partition)}},
        git_state={"sha": "deadbeef", "dirty": False, "diff": ""},
    )
    return path


def test_verified_parts_accepts_a_part_with_round_tripped_nullability(tmp_path):
    """A part written straight through `TRAJECTORY_SCHEMA` must round-trip
    its nullability through Parquet's footer unchanged -- this pins that
    assumption so a future pyarrow/format change that broke it would fail
    loudly here rather than silently weakening the nullability gate below."""
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    path = _write_verified_part(partition, 0, _one_trajectory_row())
    assert trajectory_extract.verified_parts(
        partition, expected_schema=trajectories.TRAJECTORY_SCHEMA
    ) == [path]


def test_verified_parts_refuses_a_part_that_relaxes_a_non_null_field(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    path = _write_relaxed_nullable_part(partition, 0, "site_id")
    with pytest.raises(trajectory_extract.TrajectoryExtractError) as excinfo:
        trajectory_extract.verified_parts(partition, expected_schema=trajectories.TRAJECTORY_SCHEMA)
    message = str(excinfo.value)
    assert str(path) in message
    assert "site_id" in message
    assert "nullable" in message
    assert "re-extract" in message


def _write_duplicate_field_part(partition: Path, version: int) -> Path:
    """Write a `part-NNNN.parquet` whose Parquet schema carries the field
    `site_id` TWICE -- legal in Parquet, and something a foreign tool could
    write, but never something `write_trajectories`/`tables.write_table`
    produce themselves. Built via `Table.append_column`, the one pyarrow
    entry point that tolerates the resulting duplicate name (`pa.table({...})`
    and a `dict`-keyed schema both collapse duplicates first), with a
    manifest that correctly digests the resulting bytes -- reproducing a
    part that would pass the digest gate cleanly while the dict-keyed
    schema comparison in `_schema_field_mismatches` silently collapses the
    duplicate to one entry and reports no mismatch at all."""
    df = _one_trajectory_row()
    ordered = df.loc[:, trajectories.TRAJECTORY_SCHEMA.names]
    table = pa.Table.from_pandas(
        ordered, schema=trajectories.TRAJECTORY_SCHEMA, preserve_index=False
    )
    # `append_column` is pyarrow's one entry point that tolerates a
    # resulting duplicate name -- `pa.table({...})` and a dict-keyed
    # schema both collapse duplicates before they ever reach a schema.
    # Pass the ORIGINAL field (not just a bare name) so the appended
    # column's nullability matches TRAJECTORY_SCHEMA's `site_id` exactly
    # -- otherwise a relaxed-nullability mismatch would mask the
    # duplicate-name bug this test exists to catch.
    table = table.append_column(
        trajectories.TRAJECTORY_SCHEMA.field("site_id"), table.column("site_id")
    )
    partition.mkdir(parents=True, exist_ok=True)
    path = partition / trajectory_extract.PART_FILENAME_TEMPLATE.format(version=version)
    pq.write_table(table, path)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(partition)}},
        git_state={"sha": "deadbeef", "dirty": False, "diff": ""},
    )
    return path


def test_verified_parts_refuses_a_part_with_a_duplicate_field_name(tmp_path):
    partition = trajectory_extract.partition_dir(tmp_path, "ga_ls5t_gm_cyear_3", 2011)
    path = _write_duplicate_field_part(partition, 0)
    with pytest.raises(trajectory_extract.TrajectoryExtractError) as excinfo:
        trajectory_extract.verified_parts(partition, expected_schema=trajectories.TRAJECTORY_SCHEMA)
    message = str(excinfo.value)
    assert str(path) in message
    assert "site_id" in message
    assert "2" in message


# --- resume_binding_mismatches -----------------------------------------


def _write_part_manifest_for_resume(
    tmp_path: Path,
    *,
    date: str = "2026-08-21",
    scope: str = "sites",
    site_ids: list[str] | None = None,
    input_sha256: str = "aaa",
    config: dict | None = None,
    git_state: dict | None = None,
) -> dict:
    """Write a real run manifest (via `manifests.write_run_manifest`) for a
    throwaway output file and return it parsed from disk, exactly as
    `resume_binding_mismatches` receives one read off a real partition."""
    output = tmp_path / "part-0000.parquet"
    output.write_bytes(b"resume-binding fixture -- only its sha256 matters here")
    manifest = manifests.write_run_manifest(
        output=output,
        inputs=[SourceAsset(uri="test://fixture", sha256=input_sha256)],
        config=config if config is not None else {"run": {"data_root": str(tmp_path)}},
        git_state=(
            git_state if git_state is not None else {"sha": "deadbeef", "dirty": False, "diff": ""}
        ),
        resolved_args={
            "date": date,
            "scope": scope,
            "site_ids": site_ids if site_ids is not None else ["site-d3-00"],
        },
    )
    return json.loads(json.dumps(manifest, default=str))


def test_resume_binding_mismatches_is_empty_when_everything_matches(tmp_path):
    config = {"run": {"data_root": str(tmp_path)}}
    git_state = {"sha": "deadbeef", "dirty": False, "diff": ""}
    manifest = _write_part_manifest_for_resume(tmp_path, config=config, git_state=git_state)
    assert (
        trajectory_extract.resume_binding_mismatches(
            manifest,
            date="2026-08-21",
            scope="sites",
            site_ids=["site-d3-00"],
            input_sha256s={"aaa"},
            config=config,
            git_state=git_state,
        )
        == []
    )


def test_resume_binding_mismatches_flags_a_different_date(tmp_path):
    config = {"run": {"data_root": str(tmp_path)}}
    git_state = {"sha": "deadbeef", "dirty": False, "diff": ""}
    manifest = _write_part_manifest_for_resume(tmp_path, config=config, git_state=git_state)
    assert trajectory_extract.resume_binding_mismatches(
        manifest,
        date="2026-08-22",
        scope="sites",
        site_ids=["site-d3-00"],
        input_sha256s={"aaa"},
        config=config,
        git_state=git_state,
    ) == ["date"]


def test_resume_binding_mismatches_flags_a_different_scope(tmp_path):
    config = {"run": {"data_root": str(tmp_path)}}
    git_state = {"sha": "deadbeef", "dirty": False, "diff": ""}
    manifest = _write_part_manifest_for_resume(tmp_path, config=config, git_state=git_state)
    assert trajectory_extract.resume_binding_mismatches(
        manifest,
        date="2026-08-21",
        scope="statewide",
        site_ids=["site-d3-00"],
        input_sha256s={"aaa"},
        config=config,
        git_state=git_state,
    ) == ["scope"]


def test_resume_binding_mismatches_flags_different_site_ids(tmp_path):
    config = {"run": {"data_root": str(tmp_path)}}
    git_state = {"sha": "deadbeef", "dirty": False, "diff": ""}
    manifest = _write_part_manifest_for_resume(
        tmp_path, site_ids=["site-d3-00"], config=config, git_state=git_state
    )
    assert trajectory_extract.resume_binding_mismatches(
        manifest,
        date="2026-08-21",
        scope="sites",
        site_ids=["site-d3-00", "site-d3-00b"],
        input_sha256s={"aaa"},
        config=config,
        git_state=git_state,
    ) == ["site_ids"]


def test_resume_binding_mismatches_flags_different_inputs(tmp_path):
    config = {"run": {"data_root": str(tmp_path)}}
    git_state = {"sha": "deadbeef", "dirty": False, "diff": ""}
    manifest = _write_part_manifest_for_resume(
        tmp_path, input_sha256="aaa", config=config, git_state=git_state
    )
    assert trajectory_extract.resume_binding_mismatches(
        manifest,
        date="2026-08-21",
        scope="sites",
        site_ids=["site-d3-00"],
        input_sha256s={"bbb"},
        config=config,
        git_state=git_state,
    ) == ["inputs"]


def test_resume_binding_mismatches_flags_a_different_config(tmp_path):
    config = {"run": {"data_root": str(tmp_path)}}
    git_state = {"sha": "deadbeef", "dirty": False, "diff": ""}
    manifest = _write_part_manifest_for_resume(tmp_path, config=config, git_state=git_state)
    assert trajectory_extract.resume_binding_mismatches(
        manifest,
        date="2026-08-21",
        scope="sites",
        site_ids=["site-d3-00"],
        input_sha256s={"aaa"},
        config={"run": {"data_root": str(tmp_path)}, "extra_field": "changed"},
        git_state=git_state,
    ) == ["config"]


def test_resume_binding_mismatches_flags_a_different_git_state(tmp_path):
    config = {"run": {"data_root": str(tmp_path)}}
    git_state = {"sha": "deadbeef", "dirty": False, "diff": ""}
    manifest = _write_part_manifest_for_resume(tmp_path, config=config, git_state=git_state)
    assert trajectory_extract.resume_binding_mismatches(
        manifest,
        date="2026-08-21",
        scope="sites",
        site_ids=["site-d3-00"],
        input_sha256s={"aaa"},
        config=config,
        git_state={"sha": "cafebabe", "dirty": False, "diff": ""},
    ) == ["git"]


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
