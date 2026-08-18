"""Tests for d3_threshold.evaluate_threshold (D13 Batch D task D4)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from wa_mine_monitor import d3_protocol, d3_threshold

PROTOCOL = d3_protocol.load_protocol(Path("config/d3.yaml"))
DIGEST = d3_protocol.protocol_digest(PROTOCOL)
OTHER_DIGEST = "0" * 64

ADEQUATE_STRATUM = ("pilbara", "iron_ore", "compact")
INADEQUATE_STRATUM = ("goldfields_esperance", "gold", "elongated")

SUPPORTS = PROTOCOL.supports  # (9, 16, 25, 36, 49, 64, 100, 144)


# --- helper constructors -----------------------------------------------


def _inputs_row(
    *,
    maus_id: str,
    year: int,
    source_id: str,
    metric_id: str,
    support_px: int,
    errors: list[float],
    stratum: tuple[str, str, str] = ADEQUATE_STRATUM,
    protocol_digest: str = DIGEST,
) -> dict[str, object]:
    region, commodity_group, shape_class = stratum
    return {
        "maus_id": maus_id,
        "region": region,
        "commodity_group": commodity_group,
        "shape_class": shape_class,
        "year": year,
        "source_id": source_id,
        "metric_id": metric_id,
        "support_px": support_px,
        "full_support_px": 144,
        "valid_support_px": 144,
        "full_value": 0.5,
        "replicate_abs_errors": list(errors),
        "n_replicates": len(errors),
        "protocol_digest": protocol_digest,
        "input_manifest_digests": "{}",
    }


def _inputs_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _spearman_row(
    *,
    maus_id: str,
    source_id: str,
    metric_id: str,
    support_px: int,
    replicate: int,
    spearman: float,
    protocol_digest: str = DIGEST,
) -> dict[str, object]:
    return {
        "maus_id": maus_id,
        "source_id": source_id,
        "metric_id": metric_id,
        "support_px": support_px,
        "replicate": replicate,
        "spearman": spearman,
        "n_years": 5,
        "protocol_digest": protocol_digest,
        "input_manifest_digests": "{}",
    }


def _spearman_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _support_row(
    *,
    maus_id: str,
    n_full_support_years: int,
    n_epoch_covered_years: int,
    stratum: tuple[str, str, str] = ADEQUATE_STRATUM,
    selected: bool = True,
    candidate: bool = True,
    protocol_digest: str = DIGEST,
) -> dict[str, object]:
    region, commodity_group, shape_class = stratum
    return {
        "maus_id": maus_id,
        "region": region,
        "commodity_group": commodity_group,
        "shape_class": shape_class,
        "effective_pixel_support_px": 200,
        "support_not_computed_reason": None,
        "n_epoch_covered_years": n_epoch_covered_years,
        "n_full_support_years": n_full_support_years,
        "candidate": candidate,
        "selected": selected,
        "protocol_digest": protocol_digest,
        "input_manifest_digests": "{}",
    }


def _support_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _stratum_summary_row(
    *,
    stratum: tuple[str, str, str],
    adequate: bool,
    n_footprints: int = 12,
    n_adequate_footprints: int = 12,
    n_selected: int = 2,
    protocol_digest: str = DIGEST,
) -> dict[str, object]:
    region, commodity_group, shape_class = stratum
    return {
        "region": region,
        "commodity_group": commodity_group,
        "shape_class": shape_class,
        "n_footprints": n_footprints,
        "n_adequate_footprints": n_adequate_footprints,
        "adequate": adequate,
        "n_selected": n_selected,
        "protocol_digest": protocol_digest,
        "input_manifest_digests": "{}",
    }


def _stratum_summary_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


#: Two footprints selected in the adequate stratum, always fully passing
#: the computable-fraction criterion (18/20 = 0.9 >= 0.90).
_DEFAULT_FOOTPRINTS = _support_frame(
    [
        _support_row(maus_id="maus-1", n_full_support_years=9, n_epoch_covered_years=10),
        _support_row(maus_id="maus-2", n_full_support_years=9, n_epoch_covered_years=10),
    ]
)

_DEFAULT_STRATUM_SUMMARY = _stratum_summary_frame(
    [
        _stratum_summary_row(stratum=ADEQUATE_STRATUM, adequate=True),
        _stratum_summary_row(
            stratum=INADEQUATE_STRATUM, adequate=False, n_adequate_footprints=3, n_selected=0
        ),
    ]
)


def _passing_geomedian_rows(
    *, source_id: str = "dea_gm_ls5t", nbr_error: float = 0.01, ndmi_error: float = 0.01
) -> list[dict[str, object]]:
    rows = []
    for metric, error in (("nbr", nbr_error), ("ndmi", ndmi_error)):
        for support in SUPPORTS:
            rows.append(
                _inputs_row(
                    maus_id="maus-1",
                    year=2015,
                    source_id=source_id,
                    metric_id=metric,
                    support_px=support,
                    errors=[error] * 100,
                )
            )
    return rows


def _passing_fc_rows(
    *, source_id: str = "dea_fc_pc", error: float = 1.0
) -> list[dict[str, object]]:
    rows = []
    for metric in (
        "bare_soil",
        "photosynthetic_vegetation",
        "non_photosynthetic_vegetation",
    ):
        for support in SUPPORTS:
            rows.append(
                _inputs_row(
                    maus_id="maus-1",
                    year=2015,
                    source_id=source_id,
                    metric_id=metric,
                    support_px=support,
                    errors=[error] * 100,
                )
            )
    return rows


def _passing_spearman_rows(
    *, source_id: str, metrics: tuple[str, ...], spearman: float = 0.99
) -> list[dict[str, object]]:
    rows = []
    for metric in metrics:
        for support in SUPPORTS:
            for replicate, maus_id in enumerate(("maus-1", "maus-2")):
                rows.append(
                    _spearman_row(
                        maus_id=maus_id,
                        source_id=source_id,
                        metric_id=metric,
                        support_px=support,
                        replicate=replicate,
                        spearman=spearman,
                    )
                )
    return rows


def _cells_for(result: d3_threshold.ThresholdResult, support_px: int) -> tuple[dict, ...]:
    (entry,) = [e for e in result.per_support if e["support_px"] == support_px]
    return entry["cells"]  # type: ignore[return-value]


def _entry_for(result: d3_threshold.ThresholdResult, support_px: int) -> dict[str, object]:
    (entry,) = [e for e in result.per_support if e["support_px"] == support_px]
    return entry


# --- tests ---------------------------------------------------------------


def test_smallest_passing_support_wins() -> None:
    # nbr fails only at support 9; everything else passes at every support.
    geomedian_rows = _passing_geomedian_rows()
    for row in geomedian_rows:
        if row["metric_id"] == "nbr" and row["support_px"] == 9:
            row["replicate_abs_errors"] = [0.05] * 100
    inputs = d3_threshold.ThresholdInputs(
        support_inputs=_inputs_frame(geomedian_rows + _passing_fc_rows()),
        support_spearman=_spearman_frame(
            _passing_spearman_rows(source_id="dea_gm_ls5t", metrics=("nbr", "ndmi"))
            + _passing_spearman_rows(
                source_id="dea_fc_pc",
                metrics=(
                    "bare_soil",
                    "photosynthetic_vegetation",
                    "non_photosynthetic_vegetation",
                ),
            )
        ),
        footprint_support=_DEFAULT_FOOTPRINTS,
        stratum_summary=_DEFAULT_STRATUM_SUMMARY,
    )
    result = d3_threshold.evaluate_threshold(inputs, PROTOCOL)
    assert result.n_star == 16
    assert result.criteria_passed is True
    assert result.nominal_area_m2 == 900 * 16 == 14400
    assert result.failed_criteria == ()
    assert _entry_for(result, 9)["passed"] is False
    assert _entry_for(result, 16)["passed"] is True


def _full_inputs(
    *,
    geomedian_rows: list[dict[str, object]] | None = None,
    fc_rows: list[dict[str, object]] | None = None,
    spearman_rows: list[dict[str, object]] | None = None,
    footprint_support: pd.DataFrame | None = None,
    stratum_summary: pd.DataFrame | None = None,
) -> d3_threshold.ThresholdInputs:
    geomedian_rows = _passing_geomedian_rows() if geomedian_rows is None else geomedian_rows
    fc_rows = _passing_fc_rows() if fc_rows is None else fc_rows
    if spearman_rows is None:
        spearman_rows = _passing_spearman_rows(
            source_id="dea_gm_ls5t", metrics=("nbr", "ndmi")
        ) + _passing_spearman_rows(
            source_id="dea_fc_pc",
            metrics=("bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"),
        )
    return d3_threshold.ThresholdInputs(
        support_inputs=_inputs_frame(geomedian_rows + fc_rows),
        support_spearman=_spearman_frame(spearman_rows),
        footprint_support=footprint_support
        if footprint_support is not None
        else _DEFAULT_FOOTPRINTS,
        stratum_summary=stratum_summary
        if stratum_summary is not None
        else _DEFAULT_STRATUM_SUMMARY,
    )


@pytest.mark.parametrize("failing_criterion", ["p90_error", "spearman", "fraction"])
def test_each_criterion_can_fail_independently(failing_criterion: str) -> None:
    geomedian_rows = _passing_geomedian_rows()
    spearman_rows = _passing_spearman_rows(
        source_id="dea_gm_ls5t", metrics=("nbr", "ndmi")
    ) + _passing_spearman_rows(
        source_id="dea_fc_pc",
        metrics=("bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"),
    )
    footprint_support = _DEFAULT_FOOTPRINTS

    if failing_criterion == "p90_error":
        for row in geomedian_rows:
            if row["metric_id"] == "nbr":
                row["replicate_abs_errors"] = [0.05] * 100
    elif failing_criterion == "spearman":
        spearman_rows = [dict(r) for r in spearman_rows]
        for row in spearman_rows:
            if row["source_id"] == "dea_gm_ls5t" and row["metric_id"] == "nbr":
                row["spearman"] = 0.5
    else:
        footprint_support = _support_frame(
            [
                _support_row(maus_id="maus-1", n_full_support_years=1, n_epoch_covered_years=10),
                _support_row(maus_id="maus-2", n_full_support_years=1, n_epoch_covered_years=10),
            ]
        )

    inputs = _full_inputs(
        geomedian_rows=geomedian_rows,
        spearman_rows=spearman_rows,
        footprint_support=footprint_support,
    )
    result = d3_threshold.evaluate_threshold(inputs, PROTOCOL)
    assert result.n_star == 144
    assert result.criteria_passed is False


def test_no_passing_support_falls_back_to_144() -> None:
    geomedian_rows = _passing_geomedian_rows()
    for row in geomedian_rows:
        if row["metric_id"] == "nbr":
            row["replicate_abs_errors"] = [0.05] * 100
    inputs = _full_inputs(geomedian_rows=geomedian_rows)
    result = d3_threshold.evaluate_threshold(inputs, PROTOCOL)
    assert result.criteria_passed is False
    assert result.n_star == 144
    assert len(result.failed_criteria) > 0
    assert all(name.endswith("/p90_abs_error") for name in result.failed_criteria)


def test_only_144_passing_is_not_criteria_passed() -> None:
    # nbr error is too high for every reduced support but drops away at 144
    # (the full-support cell), which is trivially error-free.
    geomedian_rows = _passing_geomedian_rows()
    for row in geomedian_rows:
        if row["metric_id"] == "nbr" and row["support_px"] < 144:
            row["replicate_abs_errors"] = [0.05] * 100
    inputs = _full_inputs(geomedian_rows=geomedian_rows)
    result = d3_threshold.evaluate_threshold(inputs, PROTOCOL)
    assert _entry_for(result, 144)["passed"] is True
    assert result.criteria_passed is False
    assert result.n_star == 144


def test_fc_uses_percentage_point_tolerance() -> None:
    # FC tolerance is 5.0 (percentage points): 4.0 passes.
    # Geomedian tolerance is 0.03: the same raw value 4.0 would fail hard.
    geomedian_rows = _passing_geomedian_rows(nbr_error=4.0, ndmi_error=4.0)
    fc_rows = _passing_fc_rows(error=4.0)
    inputs = _full_inputs(geomedian_rows=geomedian_rows, fc_rows=fc_rows)
    result = d3_threshold.evaluate_threshold(inputs, PROTOCOL)
    cells = _cells_for(result, 16)
    fc_error_cells = [
        c for c in cells if c["collection"] == "dea_fc_pc" and c["criterion"] == "p90_abs_error"
    ]
    geomedian_error_cells = [
        c for c in cells if c["collection"] == "dea_gm_ls5t" and c["criterion"] == "p90_abs_error"
    ]
    assert fc_error_cells and all(c["passed"] for c in fc_error_cells)
    assert geomedian_error_cells and all(not c["passed"] for c in geomedian_error_cells)


def test_sensor_variants_evaluated_separately() -> None:
    ls5t_rows = _passing_geomedian_rows(source_id="dea_gm_ls5t")
    ls7e_rows = _passing_geomedian_rows(source_id="dea_gm_ls7e")
    for row in ls7e_rows:
        if row["metric_id"] == "nbr":
            row["replicate_abs_errors"] = [0.05] * 100
    spearman_rows = (
        _passing_spearman_rows(source_id="dea_gm_ls5t", metrics=("nbr", "ndmi"))
        + _passing_spearman_rows(source_id="dea_gm_ls7e", metrics=("nbr", "ndmi"))
        + _passing_spearman_rows(
            source_id="dea_fc_pc",
            metrics=("bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"),
        )
    )
    inputs = _full_inputs(
        geomedian_rows=ls5t_rows + ls7e_rows,
        spearman_rows=spearman_rows,
    )
    result = d3_threshold.evaluate_threshold(inputs, PROTOCOL)
    cells_16 = _cells_for(result, 16)
    ls5t_nbr = next(
        c
        for c in cells_16
        if c["collection"] == "dea_gm_ls5t"
        and c["metric"] == "nbr"
        and c["criterion"] == "p90_abs_error"
    )
    ls7e_nbr = next(
        c
        for c in cells_16
        if c["collection"] == "dea_gm_ls7e"
        and c["metric"] == "nbr"
        and c["criterion"] == "p90_abs_error"
    )
    assert ls5t_nbr["passed"] is True
    assert ls7e_nbr["passed"] is False
    assert _entry_for(result, 16)["passed"] is False


def test_missing_required_metric_is_refused() -> None:
    geomedian_rows = [row for row in _passing_geomedian_rows() if row["metric_id"] != "ndmi"]
    inputs = _full_inputs(geomedian_rows=geomedian_rows, fc_rows=[])
    with pytest.raises(d3_threshold.D3ThresholdError, match="ndmi"):
        d3_threshold.evaluate_threshold(inputs, PROTOCOL)


def test_inadequate_strata_excluded() -> None:
    # A failing collection planted in the INADEQUATE stratum must not
    # affect the adequate stratum's outcome.
    inadequate_rows = []
    for metric in ("nbr", "ndmi"):
        for support in SUPPORTS:
            inadequate_rows.append(
                _inputs_row(
                    maus_id="maus-99",
                    year=2015,
                    source_id="dea_gm_ls5t",
                    metric_id=metric,
                    support_px=support,
                    errors=[9.0] * 100,
                    stratum=INADEQUATE_STRATUM,
                )
            )
    geomedian_rows = _passing_geomedian_rows()
    for row in geomedian_rows:
        if row["metric_id"] == "nbr" and row["support_px"] == 9:
            row["replicate_abs_errors"] = [0.05] * 100
    inputs = _full_inputs(geomedian_rows=geomedian_rows + inadequate_rows)
    result = d3_threshold.evaluate_threshold(inputs, PROTOCOL)
    assert result.n_star == 16
    assert result.criteria_passed is True


def test_empty_cell_fails_not_passes() -> None:
    # ndmi has support_inputs rows (so the "required metric present" check
    # passes) but zero support_spearman rows anywhere -- every ndmi
    # spearman cell must fail with n_spearman_rows == 0, never pass
    # vacuously.
    spearman_rows = _passing_spearman_rows(source_id="dea_gm_ls5t", metrics=("nbr",))
    spearman_rows += _passing_spearman_rows(
        source_id="dea_fc_pc",
        metrics=("bare_soil", "photosynthetic_vegetation", "non_photosynthetic_vegetation"),
    )
    inputs = _full_inputs(spearman_rows=spearman_rows)
    result = d3_threshold.evaluate_threshold(inputs, PROTOCOL)
    cells = _cells_for(result, 16)
    ndmi_spearman = next(
        c
        for c in cells
        if c["collection"] == "dea_gm_ls5t"
        and c["metric"] == "ndmi"
        and c["criterion"] == "spearman_median"
    )
    assert ndmi_spearman["passed"] is False
    assert ndmi_spearman["n_spearman_rows"] == 0
    assert ndmi_spearman["value"] is None
    assert result.criteria_passed is False


def test_mixed_protocol_digest_is_refused() -> None:
    geomedian_rows = _passing_geomedian_rows()
    geomedian_rows[0] = dict(geomedian_rows[0])
    geomedian_rows[0]["protocol_digest"] = OTHER_DIGEST
    inputs = _full_inputs(geomedian_rows=geomedian_rows)
    with pytest.raises(d3_threshold.D3ThresholdError, match="digest"):
        d3_threshold.evaluate_threshold(inputs, PROTOCOL)


def test_digest_differing_from_protocol_is_refused() -> None:
    rows = [dict(r, protocol_digest=OTHER_DIGEST) for r in _passing_geomedian_rows()]
    fc_rows = [dict(r, protocol_digest=OTHER_DIGEST) for r in _passing_fc_rows()]
    spearman_rows = [
        dict(r, protocol_digest=OTHER_DIGEST)
        for r in (
            _passing_spearman_rows(source_id="dea_gm_ls5t", metrics=("nbr", "ndmi"))
            + _passing_spearman_rows(
                source_id="dea_fc_pc",
                metrics=(
                    "bare_soil",
                    "photosynthetic_vegetation",
                    "non_photosynthetic_vegetation",
                ),
            )
        )
    ]
    footprint_support = _support_frame(
        [
            dict(row, protocol_digest=OTHER_DIGEST)
            for row in [
                _support_row(maus_id="maus-1", n_full_support_years=9, n_epoch_covered_years=10),
                _support_row(maus_id="maus-2", n_full_support_years=9, n_epoch_covered_years=10),
            ]
        ]
    )
    stratum_summary = _stratum_summary_frame(
        [
            dict(row, protocol_digest=OTHER_DIGEST)
            for row in [
                _stratum_summary_row(stratum=ADEQUATE_STRATUM, adequate=True),
                _stratum_summary_row(stratum=INADEQUATE_STRATUM, adequate=False),
            ]
        ]
    )
    inputs = d3_threshold.ThresholdInputs(
        support_inputs=_inputs_frame(rows + fc_rows),
        support_spearman=_spearman_frame(spearman_rows),
        footprint_support=footprint_support,
        stratum_summary=stratum_summary,
    )
    with pytest.raises(d3_threshold.D3ThresholdError, match="digest"):
        d3_threshold.evaluate_threshold(inputs, PROTOCOL)


def test_no_adequate_strata_is_refused() -> None:
    stratum_summary = _stratum_summary_frame(
        [_stratum_summary_row(stratum=ADEQUATE_STRATUM, adequate=False)]
    )
    inputs = _full_inputs(stratum_summary=stratum_summary)
    with pytest.raises(d3_threshold.D3ThresholdError, match="adequate"):
        d3_threshold.evaluate_threshold(inputs, PROTOCOL)


def test_per_support_detail_records_counts() -> None:
    inputs = _full_inputs()
    result = d3_threshold.evaluate_threshold(inputs, PROTOCOL)
    assert len(result.per_support) == len(SUPPORTS)
    required_keys = {
        "stratum",
        "collection",
        "metric",
        "criterion",
        "value",
        "passed",
        "n_footprint_years",
        "n_error_values",
        "n_spearman_rows",
        "fraction_numerator",
        "fraction_denominator",
    }
    for entry in result.per_support:
        assert entry["cells"]
        for cell in entry["cells"]:  # type: ignore[union-attr]
            assert required_keys <= set(cell)
