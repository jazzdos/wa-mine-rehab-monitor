"""Deterministic reduced-support simulation inputs (D13 Batch D task D3).

Sampling is WITHOUT replacement, deterministic, nested and input-order
free: members are ranked by sha256("{seed_material}|{replicate}|{member}")
and a sample of size n is the first n of that ranking, so the n=9 sample
is always a prefix-subset of the n=100 sample for the same replicate. The
seed material is pre-registered by the caller (protocol digest + maus_id +
collection + year) and never derived from a clock or process state.

The statistical unit throughout is the footprint (maus_id); register
sites never enter this module.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

Member = tuple[str, int, int]  # (tile_id, row, col)

#: Geomedian metric -> (numerator-plus band, numerator-minus band).
GEOMEDIAN_METRIC_BANDS: dict[str, tuple[str, str]] = {
    "nbr": ("nbart_nir", "nbart_swir_2"),
    "ndmi": ("nbart_nir", "nbart_swir_1"),
}
FC_METRIC_ASSETS: dict[str, str] = {
    "bare_soil": "bs_pc_50",
    "photosynthetic_vegetation": "pv_pc_50",
    "non_photosynthetic_vegetation": "npv_pc_50",
}
MIN_SPEARMAN_YEARS = 2


class D3InputsError(ValueError):
    """Simulation-input construction violated the frozen protocol -- refused."""


def _rank_key(member: Member, replicate: int, seed_material: str) -> str:
    token = f"{seed_material}|{replicate}|{member[0]},{member[1]},{member[2]}"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sample_support(
    members: Sequence[Member], n: int, *, replicate: int, seed_material: str
) -> tuple[Member, ...]:
    distinct = sorted(set(members))
    if n > len(distinct):
        raise D3InputsError(f"requested support {n} exceeds available {len(distinct)} members")
    ranked = sorted(distinct, key=lambda m: _rank_key(m, replicate, seed_material))
    return tuple(ranked[:n])


def geomedian_valid_mask(bands: Mapping[str, np.ndarray]) -> np.ndarray:
    """Pixel validity for geomedian metrics: every band finite AND every
    metric denominator nonzero (design decision 11)."""
    stacked = np.vstack([bands[b] for b in sorted(bands)])
    valid = np.isfinite(stacked).all(axis=0)
    for plus, minus in GEOMEDIAN_METRIC_BANDS.values():
        valid &= (bands[plus] + bands[minus]) != 0
    return valid


def fc_valid_mask(values: Mapping[str, np.ndarray]) -> np.ndarray:
    stacked = np.vstack([values[a] for a in sorted(values)])
    return np.isfinite(stacked).all(axis=0)


def geomedian_metrics(bands: Mapping[str, np.ndarray]) -> dict[str, float]:
    """Spatial mean of the per-pixel index over the given pixel arrays.
    Caller guarantees validity (geomedian_valid_mask all-True)."""
    out: dict[str, float] = {}
    for metric, (plus, minus) in GEOMEDIAN_METRIC_BANDS.items():
        numerator = bands[plus] - bands[minus]
        denominator = bands[plus] + bands[minus]
        out[metric] = float(np.mean(numerator / denominator))
    return out


def fc_metrics(values: Mapping[str, np.ndarray]) -> dict[str, float]:
    return {metric: float(np.mean(values[asset])) for metric, asset in FC_METRIC_ASSETS.items()}


def spearman(full: pd.Series, reduced: pd.Series) -> float | None:
    if len(full) < MIN_SPEARMAN_YEARS or len(full) != len(reduced):
        raise D3InputsError(
            f"spearman needs >= {MIN_SPEARMAN_YEARS} paired years, got "
            f"{len(full)} vs {len(reduced)}"
        )
    if full.nunique() < 2 or reduced.nunique() < 2:
        return None  # undefined for a constant series -- caller discloses
    return float(full.rank().corr(reduced.rank()))
