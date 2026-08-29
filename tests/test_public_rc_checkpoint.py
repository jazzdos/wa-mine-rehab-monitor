"""Tests for the D13 §8 P6 public-flip checkpoint (D13 §8 P6).

The checkpoint doc (`docs/checkpoints/tier0-public-rc.md`) carries one fenced
YAML block: `fields` (the closed 16-field D13 schema) and `evidence`
(`d7_exclusion` note + `artefact_digests` map). Machine authorization
(`checkpoint_authorizes_flip`) is deliberately stricter than "every bool is
truthy" -- it also refuses a D7 exclusion note that reads as a grant of
permission, and refuses a non-list aggregate-clearances value. Digest
verification (`verify_checkpoint_digests`) is a SEPARATE function: booleans
alone can never authorize a flip if the artefacts they claim to cover don't
actually match on disk.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from wa_mine_monitor import public_rc

REPO_ROOT = Path(__file__).resolve().parents[1]


def _all_true_doc() -> dict:
    return {
        "fields": {
            **{name: True for name in public_rc.CHECKPOINT_BOOL_FIELDS},
            "public_aggregate_clearances": [],
        },
        "evidence": {
            "d7_exclusion": "D7 adjudication closed (contrary_notice: true)",
            "artefact_digests": {},
        },
    }


def test_the_d13_field_schema_is_exactly_sixteen():
    assert len(public_rc.CHECKPOINT_BOOL_FIELDS) == 15
    assert "public_flip_authorized" in public_rc.CHECKPOINT_BOOL_FIELDS


def test_fully_true_checkpoint_authorizes():
    assert public_rc.checkpoint_authorizes_flip(_all_true_doc()) is True


@pytest.mark.parametrize("field_name", sorted(public_rc.CHECKPOINT_BOOL_FIELDS))
@pytest.mark.parametrize("bad_value", [False, None, "yes", 1])
def test_any_non_true_bool_field_blocks_authorization(field_name, bad_value):
    doc = _all_true_doc()
    doc["fields"][field_name] = bad_value
    assert public_rc.checkpoint_authorizes_flip(doc) is False


@pytest.mark.parametrize("field_name", sorted(public_rc.CHECKPOINT_BOOL_FIELDS))
def test_deleting_any_bool_field_blocks_authorization(field_name):
    doc = _all_true_doc()
    del doc["fields"][field_name]
    assert public_rc.checkpoint_authorizes_flip(doc) is False


def test_d7_exclusion_cannot_be_permission():
    doc = _all_true_doc()
    doc["evidence"]["d7_exclusion"] = "permission granted to publish MINEDEX"
    assert public_rc.checkpoint_authorizes_flip(doc) is False


@pytest.mark.parametrize(
    "d7_note",
    [
        # The real committed evidence text (docs/checkpoints/tier0-public-rc.md)
        # -- an honest exclusion note that happens to contain "permission"
        # only as part of a negation, not a grant.
        (
            "D7 adjudication closed: licence conflict (contrary_notice: true), "
            "recorded in docs/checkpoints/tier0-result.md — exclusion evidence, "
            "not permission."
        ),
        "D7 adjudication closed -- this is exclusion, never permission.",
        "D7 adjudication closed: no permission was granted here.",
    ],
)
def test_d7_exclusion_honest_negation_of_permission_is_not_blocked(d7_note):
    doc = _all_true_doc()
    doc["evidence"]["d7_exclusion"] = d7_note
    assert public_rc.checkpoint_authorizes_flip(doc) is True


def test_aggregate_clearances_must_be_a_list():
    doc = _all_true_doc()
    doc["fields"]["public_aggregate_clearances"] = "none shipped"
    assert public_rc.checkpoint_authorizes_flip(doc) is False


def test_ci_green_without_reviewed_logs_blocks():
    doc = _all_true_doc()
    doc["fields"]["actions_logs_reviewed"] = False
    assert public_rc.checkpoint_authorizes_flip(doc) is False


def test_digest_verification_catches_a_changed_artefact(tmp_path):
    artefact = tmp_path / "docs" / "reviews" / "audit.md"
    artefact.parent.mkdir(parents=True)
    artefact.write_text("clean report\n")
    digest = hashlib.sha256(artefact.read_bytes()).hexdigest()

    doc = _all_true_doc()
    doc["evidence"]["artefact_digests"] = {"docs/reviews/audit.md": digest}

    result = public_rc.verify_checkpoint_digests(doc, tmp_path)
    assert result["failed"] == 0
    assert result["verified"] == 1

    artefact.write_text("tampered report\n")
    result = public_rc.verify_checkpoint_digests(doc, tmp_path)
    assert result["failed"] == 1


def test_digest_verification_fails_on_missing_repo_artefact(tmp_path):
    doc = _all_true_doc()
    doc["evidence"]["artefact_digests"] = {"docs/reviews/gone.md": "0" * 64}
    result = public_rc.verify_checkpoint_digests(doc, tmp_path)
    assert result["failed"] == 1


def test_data_root_artefacts_skip_with_disclosure_when_root_absent(tmp_path):
    doc = _all_true_doc()
    doc["evidence"]["artefact_digests"] = {
        "data_root:releases/tier0-public-rc/2026.08.29/"
        "tier0-tenements.parquet.run_manifest.json": "0" * 64
    }
    result = public_rc.verify_checkpoint_digests(doc, tmp_path, data_root=None)
    assert result["skipped_offline"] == 1
    assert result["failed"] == 0


def test_committed_checkpoint_parses_verifies_and_authorizes():
    # The three OWNER-ONLY fields were set on the owner's explicit
    # 2026-08-29 instruction (evidence notes in the checkpoint), so the
    # committed checkpoint now authorizes the flip.
    doc = public_rc.load_checkpoint(REPO_ROOT / "docs" / "checkpoints" / "tier0-public-rc.md")
    result = public_rc.verify_checkpoint_digests(doc, REPO_ROOT)
    assert result["failed"] == 0
    assert public_rc.checkpoint_authorizes_flip(doc) is True


def test_load_checkpoint_refuses_missing_or_extra_fields(tmp_path):
    too_few = tmp_path / "too_few.md"
    too_few.write_text(
        "```yaml\n"
        "fields:\n"
        "  d7_exclusion_passed: false\n"
        "evidence:\n"
        "  d7_exclusion: 'closed'\n"
        "  artefact_digests: {}\n"
        "```\n"
    )
    with pytest.raises(public_rc.PublicRcError):
        public_rc.load_checkpoint(too_few)

    all_true = _all_true_doc()
    fields_lines = "\n".join(
        f"  {name}: {'true' if value is True else value}"
        for name, value in all_true["fields"].items()
        if name != "public_aggregate_clearances"
    )
    too_many = tmp_path / "too_many.md"
    too_many.write_text(
        "```yaml\n"
        "fields:\n"
        f"{fields_lines}\n"
        "  public_aggregate_clearances: []\n"
        "  seventeenth_field: true\n"
        "evidence:\n"
        "  d7_exclusion: 'D7 adjudication closed'\n"
        "  artefact_digests: {}\n"
        "```\n"
    )
    with pytest.raises(public_rc.PublicRcError):
        public_rc.load_checkpoint(too_many)
