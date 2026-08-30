"""Batch F acceptance-level tests (D13 §6): the cross-product contracts
and the committed record, independent of any one module's unit tests."""

from __future__ import annotations

from pathlib import Path

from wa_mine_monitor import climate_context, context_join, fire_context

REPO_ROOT = Path(__file__).resolve().parents[1]
E4_CHECKPOINT = REPO_ROOT / "docs" / "checkpoints" / "e4-statewide-extraction.md"
F_CHECKPOINT = REPO_ROOT / "docs" / "checkpoints" / "batch-f-result.md"


def test_schema_carries_no_causal_column_names() -> None:
    for name in context_join.CONTEXT_JOIN_SCHEMA.names:
        assert not any(
            fragment in name.lower() for fragment in context_join.FORBIDDEN_NAME_FRAGMENTS
        ), name


def test_payload_columns_enumerate_every_context_column() -> None:
    keys = {
        "site_id",
        "maus_id",
        "year",
        "context_row_status",
        "context_complete",
        "no_context_row_reason",
    }
    assert (
        set(context_join.CONTEXT_PAYLOAD_COLUMNS)
        == set(context_join.CONTEXT_JOIN_SCHEMA.names) - keys
    )


def test_source_versions_travel_onto_every_joined_row() -> None:
    from tests.test_context_join import _small_world

    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    joined = df[df["context_row_status"] == context_join.CONTEXT_ROW_JOINED]
    assert joined["fire_source_version"].notna().all()
    assert joined["silo_source_version"].notna().all()
    assert joined["fire_snapshot_date"].notna().all()
    assert joined["silo_snapshot_date"].notna().all()


def test_status_vocabularies_are_carried_verbatim_not_widened() -> None:
    from tests.test_context_join import _small_world

    fire, climate, years = _small_world()
    df = context_join.assemble_rows(fire_df=fire, climate_df=climate, years=years)
    fire_values = set(df["fire_status"].dropna())
    assert fire_values <= {
        fire_context.FIRE_STATUS_RECORDED,
        fire_context.FIRE_STATUS_NOT_RECORDED,
        fire_context.FIRE_STATUS_UNKNOWN,
    }
    climate_values = set(df["climate_status"].dropna())
    assert climate_values <= {
        climate_context.CLIMATE_STATUS_COMPUTED,
        climate_context.CLIMATE_STATUS_NOT_COMPUTABLE,
    }


def test_e4_checkpoint_exists_with_required_sections() -> None:
    text = E4_CHECKPOINT.read_text(encoding="utf-8")
    for heading in (
        "## D13 E4 acceptance clauses, adjudicated",
        "## Claim boundary",
        "## Honesty flags",
    ):
        assert heading in text
    assert "serial-vs-concurrent" in text  # the missing-test disclosure
    assert "E6" in text and "E7" in text


def test_batch_f_checkpoint_exists_with_required_sections() -> None:
    text = F_CHECKPOINT.read_text(encoding="utf-8")
    for heading in ("## D13 §6 acceptance, adjudicated", "## Claim boundary", "## Honesty flags"):
        assert heading in text
    assert "REMAINED DECLINED" in text  # A10 mirror decision
    assert "no causal attribution is" in text.lower()
    assert "cause not determined" in text
