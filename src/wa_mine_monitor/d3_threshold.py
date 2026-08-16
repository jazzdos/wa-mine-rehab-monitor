"""D3 reduced-support threshold evaluation (D13 Batch D task D4).

Pure evaluation core: given the four `build-d3-inputs` tables and the
loaded frozen protocol, decide the smallest reduced pixel support (n*)
strictly below the full-support floor (144) at which every accuracy
criterion passes for every adequate stratum. Criterion VALUES come from
`protocol.criteria` -- this module hard-codes no tolerance constant.

Evaluation runs per stratum x collection x metric: geomedian sensor
variants (`dea_gm_ls5t`/`dea_gm_ls7e`/`dea_gm_ls8cls9c`) are scored
separately so a strong sensor can never mask a weak one, and the required
metric set for whichever collection kind is present (geomedian: nbr AND
ndmi; FC: bare_soil, photosynthetic_vegetation,
non_photosynthetic_vegetation) must be complete wherever that collection
has rows at all -- a missing metric is a refusal, not a silent pass. An
empty or all-non-finite cell FAILS its criterion (recording n=0), never
passes vacuously.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from wa_mine_monitor import d3_inputs, d3_protocol, pixel_support

#: Frozen (sorted) metric sets per collection kind, sourced from d3_inputs
#: so this module never redeclares the metric identities it evaluates.
_GEOMEDIAN_METRICS: tuple[str, ...] = tuple(sorted(d3_inputs.GEOMEDIAN_METRIC_BANDS))
_FC_METRICS: tuple[str, ...] = tuple(sorted(d3_inputs.FC_METRIC_ASSETS))

#: metric_id -> the `D3Protocol.criteria` attribute naming its tolerance.
_METRIC_CRITERION_FIELD: dict[str, str] = {
    "nbr": "nbr_p90_abs_error_max",
    "ndmi": "ndmi_p90_abs_error_max",
    "bare_soil": "fc_p90_abs_error_pp_max",
    "photosynthetic_vegetation": "fc_p90_abs_error_pp_max",
    "non_photosynthetic_vegetation": "fc_p90_abs_error_pp_max",
}

Stratum = tuple[str, str, str]  # (region, commodity_group, shape_class)


class D3ThresholdError(ValueError):
    """Threshold evaluation cannot proceed as specified -- refused."""


@dataclass(frozen=True)
class ThresholdInputs:
    support_inputs: pd.DataFrame
    support_spearman: pd.DataFrame
    footprint_support: pd.DataFrame
    stratum_summary: pd.DataFrame


@dataclass(frozen=True)
class ThresholdResult:
    n_star: int
    criteria_passed: bool
    nominal_area_m2: int
    protocol_digest: str
    per_support: tuple[dict[str, object], ...]
    failed_criteria: tuple[str, ...]


def _stratum_key(region: str, commodity_group: str, shape_class: str) -> str:
    return f"{region}:{commodity_group}:{shape_class}"


def _check_digest(inputs: ThresholdInputs, expected_digest: str) -> None:
    digests: set[str] = set()
    for table in (
        inputs.support_inputs,
        inputs.support_spearman,
        inputs.footprint_support,
        inputs.stratum_summary,
    ):
        if len(table):
            digests.update(str(d) for d in table["protocol_digest"].unique())
    if not digests:
        raise D3ThresholdError("no input rows carry a protocol_digest -- cannot verify digest")
    if len(digests) > 1:
        raise D3ThresholdError(
            f"mixed protocol_digest values across input tables: {sorted(digests)}"
        )
    (actual,) = digests
    if actual != expected_digest:
        raise D3ThresholdError(
            f"input protocol_digest {actual!r} != frozen protocol digest "
            f"{expected_digest!r} -- inputs were built under a different protocol"
        )


def _required_metrics(collection: str) -> tuple[str, ...]:
    kind = d3_inputs.D3_COLLECTION_KIND.get(collection)
    if kind is None:
        raise D3ThresholdError(f"collection {collection!r} is not a recognised D3 collection")
    return _GEOMEDIAN_METRICS if kind == "geomedian" else _FC_METRICS


def _stratum_collections(
    support_inputs: pd.DataFrame, stratum: Stratum
) -> dict[str, tuple[str, ...]]:
    """(collection -> required metrics) for every collection with ANY rows
    in this stratum (any support), refusing a collection that is missing
    one of its kind's required metrics anywhere in the stratum."""
    region, commodity_group, shape_class = stratum
    rows = support_inputs[
        (support_inputs["region"] == region)
        & (support_inputs["commodity_group"] == commodity_group)
        & (support_inputs["shape_class"] == shape_class)
    ]
    collections: dict[str, tuple[str, ...]] = {}
    for collection in sorted(rows["source_id"].unique()):
        required = _required_metrics(collection)
        present = set(rows.loc[rows["source_id"] == collection, "metric_id"].unique())
        missing = sorted(set(required) - present)
        if missing:
            raise D3ThresholdError(
                f"stratum {_stratum_key(*stratum)!r} collection {collection!r} is "
                f"missing required metric(s) {missing}"
            )
        collections[collection] = required
    return collections


def _p90_cell(
    rows: pd.DataFrame, protocol: d3_protocol.D3Protocol, metric: str
) -> dict[str, object]:
    tolerance = getattr(protocol.criteria, _METRIC_CRITERION_FIELD[metric])
    n_footprint_years = len(rows)
    if rows.empty:
        pooled = np.array([], dtype=np.float64)
    else:
        pooled = np.concatenate(
            [np.asarray(errors, dtype=np.float64) for errors in rows["replicate_abs_errors"]]
        )
    finite = pooled[np.isfinite(pooled)]
    n_error_values = int(finite.size)
    if n_error_values == 0:
        return {
            "value": None,
            "passed": False,
            "n_footprint_years": n_footprint_years,
            "n_error_values": 0,
            "n_spearman_rows": 0,
            "fraction_numerator": 0,
            "fraction_denominator": 0,
        }
    value = float(np.percentile(finite, 90, method="linear"))
    return {
        "value": value,
        "passed": bool(value <= tolerance),
        "n_footprint_years": n_footprint_years,
        "n_error_values": n_error_values,
        "n_spearman_rows": 0,
        "fraction_numerator": 0,
        "fraction_denominator": 0,
    }


def _spearman_cell(rows: pd.DataFrame, protocol: d3_protocol.D3Protocol) -> dict[str, object]:
    threshold = protocol.criteria.spearman_median_min
    if rows.empty:
        finite = np.array([], dtype=np.float64)
    else:
        raw = rows["spearman"].to_numpy(dtype=np.float64)
        finite = raw[np.isfinite(raw)]
    n_spearman_rows = int(finite.size)
    if n_spearman_rows == 0:
        return {
            "value": None,
            "passed": False,
            "n_footprint_years": 0,
            "n_error_values": 0,
            "n_spearman_rows": 0,
            "fraction_numerator": 0,
            "fraction_denominator": 0,
        }
    value = float(np.median(finite))
    return {
        "value": value,
        "passed": bool(value >= threshold),
        "n_footprint_years": 0,
        "n_error_values": 0,
        "n_spearman_rows": n_spearman_rows,
        "fraction_numerator": 0,
        "fraction_denominator": 0,
    }


def _fraction_cell(
    selected_footprints: pd.DataFrame,
    stratum: Stratum,
    protocol: d3_protocol.D3Protocol,
) -> dict[str, object]:
    """Decision 12: full-support-computable / epoch-covered footprint-years,
    summed over the stratum's SELECTED footprints -- identical across
    supports (the ratio never depends on support_px)."""
    region, commodity_group, shape_class = stratum
    threshold = protocol.criteria.computable_site_year_fraction_min
    rows = selected_footprints[
        (selected_footprints["region"] == region)
        & (selected_footprints["commodity_group"] == commodity_group)
        & (selected_footprints["shape_class"] == shape_class)
    ]
    numerator = int(rows["n_full_support_years"].sum())
    denominator = int(rows["n_epoch_covered_years"].sum())
    if denominator == 0:
        return {
            "value": None,
            "passed": False,
            "n_footprint_years": 0,
            "n_error_values": 0,
            "n_spearman_rows": 0,
            "fraction_numerator": numerator,
            "fraction_denominator": 0,
        }
    value = numerator / denominator
    return {
        "value": value,
        "passed": bool(value >= threshold),
        "n_footprint_years": 0,
        "n_error_values": 0,
        "n_spearman_rows": 0,
        "fraction_numerator": numerator,
        "fraction_denominator": denominator,
    }


def evaluate_threshold(
    inputs: ThresholdInputs, protocol: d3_protocol.D3Protocol
) -> ThresholdResult:
    expected_digest = d3_protocol.protocol_digest(protocol)
    _check_digest(inputs, expected_digest)

    summary = inputs.stratum_summary
    adequate_rows = summary[summary["adequate"]]
    if adequate_rows.empty:
        raise D3ThresholdError(
            "no adequate strata in stratum_summary -- threshold cannot be evaluated"
        )
    strata: list[Stratum] = sorted(
        set(
            adequate_rows[["region", "commodity_group", "shape_class"]].itertuples(
                index=False, name=None
            )
        )
    )

    selected_footprints = inputs.footprint_support[inputs.footprint_support["selected"]]
    maus_stratum = selected_footprints.set_index("maus_id")[
        ["region", "commodity_group", "shape_class"]
    ]

    # Refuse missing required metrics up front -- a structural data defect,
    # not a per-support criterion outcome. Also fixes the (collection ->
    # metrics) cell set every support will be scored against.
    stratum_collections: dict[Stratum, dict[str, tuple[str, ...]]] = {
        stratum: _stratum_collections(inputs.support_inputs, stratum) for stratum in strata
    }

    support_results: list[tuple[int, bool, tuple[dict[str, object], ...]]] = []
    for support in protocol.supports:
        cells: list[dict[str, object]] = []
        support_passed = True
        for stratum in strata:
            region, commodity_group, shape_class = stratum
            stratum_key = _stratum_key(*stratum)
            stratum_maus_ids = maus_stratum[
                (maus_stratum["region"] == region)
                & (maus_stratum["commodity_group"] == commodity_group)
                & (maus_stratum["shape_class"] == shape_class)
            ].index

            for collection, metrics in stratum_collections[stratum].items():
                for metric in metrics:
                    metric_rows = inputs.support_inputs[
                        (inputs.support_inputs["region"] == region)
                        & (inputs.support_inputs["commodity_group"] == commodity_group)
                        & (inputs.support_inputs["shape_class"] == shape_class)
                        & (inputs.support_inputs["source_id"] == collection)
                        & (inputs.support_inputs["metric_id"] == metric)
                        & (inputs.support_inputs["support_px"] == support)
                    ]
                    error_cell = _p90_cell(metric_rows, protocol, metric)
                    cells.append(
                        {
                            "stratum": stratum_key,
                            "collection": collection,
                            "metric": metric,
                            "criterion": "p90_abs_error",
                            **error_cell,
                        }
                    )
                    support_passed = support_passed and bool(error_cell["passed"])

                    spearman_rows = inputs.support_spearman[
                        inputs.support_spearman["maus_id"].isin(stratum_maus_ids)
                        & (inputs.support_spearman["source_id"] == collection)
                        & (inputs.support_spearman["metric_id"] == metric)
                        & (inputs.support_spearman["support_px"] == support)
                    ]
                    spearman_cell = _spearman_cell(spearman_rows, protocol)
                    cells.append(
                        {
                            "stratum": stratum_key,
                            "collection": collection,
                            "metric": metric,
                            "criterion": "spearman_median",
                            **spearman_cell,
                        }
                    )
                    support_passed = support_passed and bool(spearman_cell["passed"])

            fraction_cell = _fraction_cell(selected_footprints, stratum, protocol)
            cells.append(
                {
                    "stratum": stratum_key,
                    "collection": "-",
                    "metric": "-",
                    "criterion": "computable_fraction",
                    **fraction_cell,
                }
            )
            support_passed = support_passed and bool(fraction_cell["passed"])

        support_results.append((support, support_passed, tuple(cells)))

    full_support = d3_protocol.MIN_FULL_SUPPORT_PX
    passing_below_144 = sorted(
        support for support, passed, _ in support_results if passed and support < full_support
    )
    if passing_below_144:
        n_star = passing_below_144[0]
        criteria_passed = True
        failed_criteria: tuple[str, ...] = ()
    else:
        n_star = full_support
        criteria_passed = False
        below_144 = [entry for entry in support_results if entry[0] < full_support]
        best_entry = (
            min(
                below_144,
                key=lambda entry: (
                    sum(1 for cell in entry[2] if not cell["passed"]),
                    -entry[0],
                ),
            )
            if below_144
            else support_results[-1]
        )
        failed_criteria = tuple(
            f"{cell['stratum']}/{cell['collection']}/{cell['metric']}/{cell['criterion']}"
            for cell in best_entry[2]
            if not cell["passed"]
        )

    per_support = tuple(
        {"support_px": support, "passed": passed, "cells": cells}
        for support, passed, cells in support_results
    )

    return ThresholdResult(
        n_star=n_star,
        criteria_passed=criteria_passed,
        nominal_area_m2=int(pixel_support.PIXEL_METRES**2) * n_star,
        protocol_digest=expected_digest,
        per_support=per_support,
        failed_criteria=failed_criteria,
    )
