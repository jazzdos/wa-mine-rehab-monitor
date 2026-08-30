"""Tests for the F6 context join (D13 §6).

The five D13-named behaviours are tested under their own names:
one context record per Tier 1 site-year; fire and climate missingness
independent; no trajectory row dropped for unknown context; the rendering
contract requires both contexts; "cause not determined" when context is
absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from tests.test_trajectory_qa import (
    _seed_register,
    _seed_trajectories,
    _write_config,
)
from wa_mine_monitor import (
    climate_context,
    context_join,
    fire_context,
    manifests,
    tables,
    trajectories,
)
from wa_mine_monitor.cli import app
from wa_mine_monitor.provenance import SourceAsset

runner = CliRunner()


def _fire_df(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=list(fire_context.FIRE_CONTEXT_SCHEMA.names))
    frame["year"] = frame["year"].astype("int32")
    frame["fire_record_count"] = frame["fire_record_count"].astype("Int32")
    return frame


def _climate_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(climate_context.CLIMATE_CONTEXT_SCHEMA.names))


def _fire_row(site: str, maus: str, year: int, **over: object) -> dict:
    row = {
        "site_id": site,
        "maus_id": maus,
        "year": year,
        "fire_status": fire_context.FIRE_STATUS_NOT_RECORDED,
        "fire_record_count": 0,
        "fire_source_version": "DBCA-060 v1",
        "fire_coverage_status": fire_context.COVERAGE_COVERED,
        "fire_snapshot_date": "2026-08-29",
        "not_computable_reason": None,
    }
    row.update(over)
    return row


def _climate_row(site: str, maus: str, year: int, **over: object) -> dict:
    row = {
        "site_id": site,
        "maus_id": maus,
        "year": year,
        "silo_cell_id": "-32.000_116.000",
        "annual_rainfall_mm": 650.0,
        "rain_days_ge_1mm": 80,
        "rainfall_anomaly_mm": 12.5,
        "rainfall_baseline_start_year": climate_context.BASELINE_START_YEAR,
        "rainfall_baseline_end_year": climate_context.BASELINE_END_YEAR,
        "climate_status": climate_context.CLIMATE_STATUS_COMPUTED,
        "not_computable_reason": None,
        "silo_source_version": "SILO v1",
        "silo_snapshot_date": "2026-08-29",
    }
    row.update(over)
    return row


def _small_world() -> tuple[pd.DataFrame, pd.DataFrame, list[int]]:
    """Two sites, context years 1987-1988, trajectory years 1986-1988 --
    the smallest domain with a pre-context (no_context_row) year."""
    fire = _fire_df(
        [_fire_row(s, m, y) for (s, m) in [("S1", "M1"), ("S2", "M2")] for y in (1987, 1988)]
    )
    climate = _climate_df(
        [_climate_row(s, m, y) for (s, m) in [("S1", "M1"), ("S2", "M2")] for y in (1987, 1988)]
    )
    return fire, climate, [1986, 1987, 1988]


def test_one_context_record_per_tier1_site_year() -> None:
    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    assert len(df) == 2 * 3
    assert not df.duplicated(["site_id", "year"]).any()
    assert set(zip(df["site_id"], df["year"].astype(int))) == {
        (s, y) for s in ("S1", "S2") for y in years
    }


def test_fire_and_climate_missingness_are_independent() -> None:
    fire, climate, years = _small_world()
    # S1/1987: fire unknown (outside window), climate computed.
    fire.loc[
        (fire["site_id"] == "S1") & (fire["year"] == 1987),
        ["fire_status", "fire_record_count", "fire_coverage_status", "not_computable_reason"],
    ] = [fire_context.FIRE_STATUS_UNKNOWN, None, fire_context.COVERAGE_OUTSIDE_WINDOW, "window"]
    # S2/1988: climate not computable, fire untouched.
    climate.loc[
        (climate["site_id"] == "S2") & (climate["year"] == 1988),
        [
            "annual_rainfall_mm",
            "rain_days_ge_1mm",
            "rainfall_anomaly_mm",
            "climate_status",
            "not_computable_reason",
        ],
    ] = [None, None, None, climate_context.CLIMATE_STATUS_NOT_COMPUTABLE, "gap"]
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    s1_1987 = df[(df["site_id"] == "S1") & (df["year"] == 1987)].iloc[0]
    assert s1_1987["fire_status"] == fire_context.FIRE_STATUS_UNKNOWN
    assert s1_1987["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED
    assert s1_1987["annual_rainfall_mm"] == 650.0
    s2_1988 = df[(df["site_id"] == "S2") & (df["year"] == 1988)].iloc[0]
    assert s2_1988["climate_status"] == climate_context.CLIMATE_STATUS_NOT_COMPUTABLE
    assert s2_1988["fire_status"] == fire_context.FIRE_STATUS_NOT_RECORDED


def test_no_trajectory_row_dropped_for_unknown_context() -> None:
    # 1986 has NO context rows at all; the join still emits one explicit
    # row per site for it -- absence is a state, never a dropped row.
    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    absent = df[df["year"] == 1986]
    assert len(absent) == 2
    assert (absent["context_row_status"] == context_join.CONTEXT_ROW_NO_CONTEXT).all()
    assert absent["fire_status"].isna().all()
    assert absent["climate_status"].isna().all()
    # And the absent state is never expressed through fire's vocabulary.
    assert fire_context.FIRE_STATUS_UNKNOWN not in set(absent["fire_status"].dropna())
    joined = df[df["year"] != 1986]
    assert (joined["context_row_status"] == context_join.CONTEXT_ROW_JOINED).all()


def test_no_context_row_reason_names_the_context_start() -> None:
    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    reasons = set(df.loc[df["year"] == 1986, "no_context_row_reason"])
    assert len(reasons) == 1
    reason = reasons.pop()
    assert "1986" in reason and "1987" in reason
    assert df.loc[df["year"] != 1986, "no_context_row_reason"].isna().all()


def test_collision_columns_are_prefixed() -> None:
    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    assert "fire_not_computable_reason" in df.columns
    assert "climate_not_computable_reason" in df.columns
    assert "not_computable_reason" not in df.columns


def test_context_complete_requires_joined_covered_and_computed() -> None:
    fire, climate, years = _small_world()
    fire.loc[
        (fire["site_id"] == "S1") & (fire["year"] == 1987),
        ["fire_status", "fire_record_count", "fire_coverage_status", "not_computable_reason"],
    ] = [fire_context.FIRE_STATUS_UNKNOWN, None, fire_context.COVERAGE_OUTSIDE_WINDOW, "window"]
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    by_key = df.set_index(["site_id", "year"])
    assert bool(by_key.loc[("S1", 1988), "context_complete"]) is True
    assert bool(by_key.loc[("S1", 1987), "context_complete"]) is False  # fire not covered
    assert bool(by_key.loc[("S1", 1986), "context_complete"]) is False  # no context row
    assert df["context_complete"].notna().all()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda fire, climate: fire.drop(fire.index[:1]),  # domain mismatch
        lambda fire, climate: pd.concat([fire, fire.iloc[[0]]], ignore_index=True),  # dup
    ],
)
def test_inconsistent_context_inputs_are_refused(mutate) -> None:
    fire, climate, years = _small_world()
    bad_fire = mutate(fire, climate)
    with pytest.raises(context_join.ContextJoinError):
        context_join.assemble_rows(fire_df=bad_fire, climate_df=climate, years=years)


def test_maus_disagreement_between_contexts_is_refused() -> None:
    fire, climate, years = _small_world()
    climate.loc[climate["site_id"] == "S1", "maus_id"] = "M9"
    with pytest.raises(context_join.ContextJoinError):
        context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)


def _assembled() -> tuple[pd.DataFrame, dict, dict, list[int]]:
    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    fire_counts = fire["fire_status"].value_counts().to_dict()
    climate_counts = climate["climate_status"].value_counts().to_dict()
    return df, fire_counts, climate_counts, years


def test_rendering_contract_requires_both_contexts_beside_any_interpretation() -> None:
    # The schema-level rendering contract: context_complete is non-null
    # bool everywhere, and True ONLY where the row is joined AND fire
    # coverage is covered AND climate is computed. The data dictionary
    # wording is asserted at the acceptance level (test_batch_f_acceptance).
    df, _fire_counts, _climate_counts, _years = _assembled()
    recomputed = (
        (df["context_row_status"] == context_join.CONTEXT_ROW_JOINED)
        & (df["fire_coverage_status"] == fire_context.COVERAGE_COVERED)
        & (df["climate_status"] == climate_context.CLIMATE_STATUS_COMPUTED)
    )
    assert (df["context_complete"] == recomputed).all()


def test_cause_not_determined_when_context_absent() -> None:
    # Either context absent/incomplete => context_complete False, and no
    # column name in the product implies causation.
    fire, climate, years = _small_world()
    climate.loc[
        (climate["site_id"] == "S1") & (climate["year"] == 1987),
        [
            "annual_rainfall_mm",
            "rain_days_ge_1mm",
            "rainfall_anomaly_mm",
            "climate_status",
            "not_computable_reason",
        ],
    ] = [None, None, None, climate_context.CLIMATE_STATUS_NOT_COMPUTABLE, "gap"]
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    by_key = df.set_index(["site_id", "year"])
    assert bool(by_key.loc[("S1", 1987), "context_complete"]) is False
    for name in df.columns:
        assert not any(
            fragment in name.lower() for fragment in context_join.FORBIDDEN_NAME_FRAGMENTS
        ), name


def test_validate_context_join_accepts_the_assembled_product() -> None:
    df, fire_counts, climate_counts, years = _assembled()
    context_join.validate_context_join(
        df,
        site_ids=["S1", "S2"],
        years=years,
        fire_status_counts=fire_counts,
        climate_status_counts=climate_counts,
    )


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda df: df.drop(df.index[:1]),
        lambda df: df.assign(context_complete=True),
        lambda df: df.assign(
            fire_status=df["fire_status"].where(df["year"] != 1986, "not_recorded")
        ),
        lambda df: df.rename(columns={"fire_status": "fire_cause"}),
        lambda df: df.assign(no_context_row_reason=None),
    ],
)
def test_validate_context_join_catches_each_violation(corrupt) -> None:
    df, fire_counts, climate_counts, years = _assembled()
    with pytest.raises(context_join.ContextJoinError):
        context_join.validate_context_join(
            corrupt(df),
            site_ids=["S1", "S2"],
            years=years,
            fire_status_counts=fire_counts,
            climate_status_counts=climate_counts,
        )


def test_validate_context_join_catches_status_count_drift() -> None:
    df, fire_counts, climate_counts, years = _assembled()
    fire_counts[fire_context.FIRE_STATUS_RECORDED] = 99
    with pytest.raises(context_join.ContextJoinError):
        context_join.validate_context_join(
            df,
            site_ids=["S1", "S2"],
            years=years,
            fire_status_counts=fire_counts,
            climate_status_counts=climate_counts,
        )


def _seed_context(data_root: Path, kind: str, date_str: str, df: pd.DataFrame, schema) -> None:
    output_dir = data_root / "curated" / kind / date_str
    output_dir.mkdir(parents=True)
    filename = "fire_context.parquet" if kind == "fire-context" else "climate_context.parquet"
    path = output_dir / filename
    tables.write_table(df, path, schema)
    manifests.write_run_manifest(
        output=path,
        inputs=[SourceAsset(uri="test://fixture", sha256=None)],
        config={"run": {"data_root": str(data_root)}},
        git_state={"sha": "testsha", "dirty": False, "diff": ""},
    )


def _seed_full_world(tmp_path: Path, *, accept: bool = True) -> tuple[Path, Path]:
    """Trajectories + register + (optionally) a passing acceptance verdict
    + both context products, all consistent with `_good_world`'s three
    sites over trajectory years {2000, 2001} and context year {2001}."""
    data_root = tmp_path / "data"
    cfg = _write_config(tmp_path, data_root)
    _seed_trajectories(data_root, "2026-08-29", tmp_path)
    _seed_register(data_root, "2026-08-29", [("S1", True), ("S2", True), ("S3", True)])
    if accept:
        result = runner.invoke(
            app,
            [
                "accept-trajectories",
                "--config",
                str(cfg),
                "--date",
                "2026-08-29",
                "--expected-partitions",
                "2",
            ],
        )
        assert result.exit_code == 0, result.output
    pairs = [("S1", "M1"), ("S2", "M1"), ("S3", "M2")]
    fire = _fire_df([_fire_row(s, m, 2001) for s, m in pairs])
    climate = _climate_df([_climate_row(s, m, 2001) for s, m in pairs])
    _seed_context(data_root, "fire-context", "2026-08-29", fire, fire_context.FIRE_CONTEXT_SCHEMA)
    _seed_context(
        data_root,
        "climate-context",
        "2026-08-29",
        climate,
        climate_context.CLIMATE_CONTEXT_SCHEMA,
    )
    return cfg, data_root


def test_build_context_join_refuses_without_an_acceptance_verdict(tmp_path: Path) -> None:
    cfg, _data_root = _seed_full_world(tmp_path, accept=False)
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 1
    assert "refusal" in result.output
    assert "accept-trajectories" in result.output


def test_build_context_join_refuses_a_failed_acceptance_verdict(tmp_path: Path) -> None:
    cfg, data_root = _seed_full_world(tmp_path, accept=False)
    # Produce a FAILED verdict by making the register disagree, then
    # restoring it: run accept against a register with an extra site.
    import shutil

    register_dir = data_root / "curated" / "register" / "2026-08-29"
    backup = tmp_path / "register-backup"
    shutil.copytree(register_dir, backup)
    shutil.rmtree(register_dir)
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
            "2026-08-29",
            "--expected-partitions",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    build = runner.invoke(app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"])
    assert build.exit_code == 1
    assert "did not pass" in build.output


def test_build_context_join_refuses_when_parts_changed_after_acceptance(tmp_path: Path) -> None:
    # A part rewritten AFTER acceptance -- with a fresh, self-consistent
    # sidecar so digest/schema re-verification passes -- must still be
    # refused: the verdict is bound to the part bytes it accepted.
    cfg, data_root = _seed_full_world(tmp_path)
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
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 1
    assert "parts_digest" in result.output


def test_build_context_join_happy_path_writes_product_and_manifest(tmp_path: Path) -> None:
    cfg, data_root = _seed_full_world(tmp_path)
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 0, result.output
    out = data_root / "curated" / "context-join" / "2026-08-30" / "context_join.parquet"
    assert out.exists()
    assert Path(str(out) + manifests.MANIFEST_SUFFIX).exists()
    df = tables.read_table(out)
    # 3 sites x trajectory years {2000, 2001}; 2000 has no context rows.
    assert len(df) == 6
    absent = df[df["year"] == 2000]
    assert (absent["context_row_status"] == context_join.CONTEXT_ROW_NO_CONTEXT).all()
    payload = json.loads(result.output)
    assert payload["rows"] == 6
    assert payload["n_sites"] == 3
    assert payload["n_no_context_rows"] == 3
    # The manifest cites all four inputs.
    manifest = json.loads(Path(str(out) + manifests.MANIFEST_SUFFIX).read_text())
    assert len(manifest["inputs"]) == 4


def test_build_context_join_refuses_site_set_mismatch_with_contexts(tmp_path: Path) -> None:
    cfg, data_root = _seed_full_world(tmp_path)
    # Rebuild fire context missing S3.
    import shutil

    fire_dir = data_root / "curated" / "fire-context" / "2026-08-29"
    shutil.rmtree(fire_dir)
    pairs = [("S1", "M1"), ("S2", "M1")]
    fire = _fire_df([_fire_row(s, m, 2001) for s, m in pairs])
    _seed_context(data_root, "fire-context", "2026-08-29", fire, fire_context.FIRE_CONTEXT_SCHEMA)
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 1
    assert "refusal" in result.output


def test_build_context_join_refuses_maus_disagreement_with_trajectories(tmp_path: Path) -> None:
    cfg, data_root = _seed_full_world(tmp_path)
    import shutil

    for kind, filename, schema, mk_rows in (
        ("fire-context", "fire_context.parquet", fire_context.FIRE_CONTEXT_SCHEMA, _fire_row),
        (
            "climate-context",
            "climate_context.parquet",
            climate_context.CLIMATE_CONTEXT_SCHEMA,
            _climate_row,
        ),
    ):
        shutil.rmtree(data_root / "curated" / kind / "2026-08-29")
        pairs = [("S1", "M9"), ("S2", "M1"), ("S3", "M2")]  # S1's maus diverges
        rows = [mk_rows(s, m, 2001) for s, m in pairs]
        df = _fire_df(rows) if kind == "fire-context" else _climate_df(rows)
        _seed_context(data_root, kind, "2026-08-29", df, schema)
    result = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert result.exit_code == 1
    assert "maus_id" in result.output


def test_build_context_join_refuses_a_second_run(tmp_path: Path) -> None:
    cfg, _data_root = _seed_full_world(tmp_path)
    first = runner.invoke(app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"])
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        app, ["build-context-join", "--config", str(cfg), "--date", "2026-08-30"]
    )
    assert second.exit_code == 1
    assert "refusal" in second.output
