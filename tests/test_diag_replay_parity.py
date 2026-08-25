"""Pins O8: the diag replay and the production eligibility function must
produce identical trajectory_status counts on a population that contains
the never-judged divergence class (a low-confidence match whose maus_id
carries no computed support)."""

from __future__ import annotations

import pandas as pd
from scripts import diag_batch_e_readiness

from wa_mine_monitor import register


def _fixture_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    register_df = pd.DataFrame({"site_id": [f"s{i}" for i in range(6)]})
    crosswalk_df = pd.DataFrame(
        {
            "site_id": ["s0", "s1", "s2", "s3", "s4"],
            "maus_id": ["m0", "m1", "m2", "m3", "m4"],
            "confidence": ["high", "high", "high", "low", "low"],
            # s0: high-confidence match, support computed, >=144  -> eligible
            # s1: high-confidence match, support computed, <144   -> insufficient_pixel_support
            # s2: high-confidence match, maus_id has NO support row -> no_usable_footprint (rule 1)
            # s3: LOW-confidence match, maus_id has NO support row  -> the O8 class:
            #     production must bucket it identically on both paths
            # s4: low-confidence match, support computed            -> crosswalk_not_high_confidence
            # s5: absent from crosswalk entirely                    -> no_usable_footprint
        }
    )
    footprint_support_df = pd.DataFrame(
        {
            "maus_id": ["m0", "m1", "m4"],
            "effective_pixel_support_px": [200, 50, 100],
            "region": ["Goldfields", "Pilbara", "Kimberley"],
        }
    )
    return register_df, crosswalk_df, footprint_support_df


def test_replay_counts_equal_production_counts() -> None:
    register_df, crosswalk_df, support_df = _fixture_frames()
    production = (
        register.assign_trajectory_eligibility(
            register_df,
            crosswalk_df,
            support_df,
            n_star=144,
            criteria_passed=False,
            forced_threshold=True,
        )["trajectory_status"]
        .value_counts()
        .to_dict()
    )
    replay = (
        diag_batch_e_readiness.replay_eligibility(register_df, crosswalk_df, support_df)[
            "trajectory_status"
        ]
        .value_counts()
        .to_dict()
    )
    assert replay == production


def test_replay_frame_carries_the_diagnostic_columns() -> None:
    # The production function returns the register plus the four
    # eligibility columns ONLY (register.py:1352-1357) -- it does not carry
    # `maus_id` or `region`, which `check_eligibility`/`check_sharing`
    # read. `replay_eligibility` must append them, or the diagnostics
    # KeyError at runtime while a counts-only parity test stays green.
    register_df, crosswalk_df, support_df = _fixture_frames()
    replay = diag_batch_e_readiness.replay_eligibility(register_df, crosswalk_df, support_df)
    for column in (
        "trajectory_status",
        "effective_pixel_support_px",
        "maus_id",
        "region",
    ):
        assert column in replay.columns, column
    # The appended columns are lookups, never judgements: the maus_id for a
    # judged site must be the same one production's dedup rule picked.
    judged = replay["trajectory_status"] == "eligible"
    assert replay.loc[judged, "maus_id"].notna().all()
