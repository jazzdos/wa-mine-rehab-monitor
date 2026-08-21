"""Per-site-year spectral metric rows (D13 E3).

Reuses the frozen D3 formulas in `d3_inputs`; adds the E3 contract that a
metric is either a value with its pixel counts or an explicit
not_computable_reason. Nothing here fabricates a zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from wa_mine_monitor import d3_inputs, dea_raster

GEOMEDIAN_BANDS: tuple[str, ...] = tuple(
    sorted({b for pair in d3_inputs.GEOMEDIAN_METRIC_BANDS.values() for b in pair})
)
FC_ASSETS: tuple[str, ...] = tuple(sorted(d3_inputs.FC_METRIC_ASSETS.values()))

#: Closed vocabulary for `not_computable_reason`.
NOT_COMPUTABLE_REASONS: tuple[str, ...] = (
    "zero_member_pixels",
    "zero_valid_pixels",
    "read_failed",
    "item_missing",
)


class SpectralMetricsError(ValueError):
    """A band set the frozen formulas cannot be applied to."""


@dataclass(frozen=True)
class MetricRow:
    metric: str
    value: float | None
    n_member_pixels: int
    n_valid_pixels: int
    computable: bool
    not_computable_reason: str | None
    value_out_of_documented_range: int | None = None


def _require_keys(arrays: Mapping[str, np.ndarray], required: tuple[str, ...]) -> None:
    missing = [k for k in required if k not in arrays]
    if missing:
        raise SpectralMetricsError(f"missing band array(s): {missing}")
    lengths = {arrays[k].shape for k in required}
    if len(lengths) != 1:
        raise SpectralMetricsError(f"band arrays differ in shape: {sorted(lengths)}")


def _not_computable(metric: str, n_member: int, reason: str) -> MetricRow:
    return MetricRow(metric, None, n_member, 0, False, reason)


def geomedian_site_year_metrics(bands: Mapping[str, np.ndarray]) -> list[MetricRow]:
    """NBR and NDMI spatial means over the valid members of one site-year."""
    _require_keys(bands, GEOMEDIAN_BANDS)
    n_member = int(bands[GEOMEDIAN_BANDS[0]].size)
    metrics = list(d3_inputs.GEOMEDIAN_METRIC_BANDS)
    if n_member == 0:
        return [_not_computable(m, 0, "zero_member_pixels") for m in metrics]
    valid = d3_inputs.geomedian_valid_mask({k: bands[k] for k in GEOMEDIAN_BANDS})
    n_valid = int(valid.sum())
    if n_valid == 0:
        return [_not_computable(m, n_member, "zero_valid_pixels") for m in metrics]
    values = d3_inputs.geomedian_metrics({k: bands[k][valid] for k in GEOMEDIAN_BANDS})
    return [MetricRow(m, values[m], n_member, n_valid, True, None) for m in metrics]


def fc_site_year_metrics(values: Mapping[str, np.ndarray]) -> list[MetricRow]:
    """Bare-soil / PV / NPV spatial means of decoded `_pc_50` assets; values
    above 100 are retained and counted per metric (decode_rules)."""
    _require_keys(values, FC_ASSETS)
    n_member = int(values[FC_ASSETS[0]].size)
    metric_to_asset = d3_inputs.FC_METRIC_ASSETS
    if n_member == 0:
        return [_not_computable(m, 0, "zero_member_pixels") for m in metric_to_asset]
    valid = d3_inputs.fc_valid_mask({k: values[k] for k in FC_ASSETS})
    n_valid = int(valid.sum())
    if n_valid == 0:
        return [_not_computable(m, n_member, "zero_valid_pixels") for m in metric_to_asset]
    masked = {k: values[k][valid] for k in FC_ASSETS}
    means = d3_inputs.fc_metrics(masked)
    return [
        MetricRow(
            metric,
            means[metric],
            n_member,
            n_valid,
            True,
            None,
            value_out_of_documented_range=int(np.sum(masked[asset] > dea_raster.FC_DOCUMENTED_MAX)),
        )
        for metric, asset in metric_to_asset.items()
    ]
