"""DEA collection-3 decode rules (D13 Batch E task E1, built for Batch D).

Batch D needs exactly two rules for the D3 simulation; Batch E EXTENDS
this module rather than re-declaring them. Rules are D13-frozen:
geomedian nodata is -999 and valid values scale by 1/10_000; FC nodata is
255 and values above 100 are real measurements -- retained and counted,
never clipped.
"""

from __future__ import annotations

import numpy as np

GEOMEDIAN_NODATA = -999
GEOMEDIAN_SCALE = 10_000.0
FC_NODATA = 255
FC_DOCUMENTED_MAX = 100.0


def decode_geomedian(values: np.ndarray) -> np.ndarray:
    """-999 -> NaN; everything else / 10_000. Returns float64, input unchanged."""
    out = values.astype(np.float64, copy=True)
    out[values == GEOMEDIAN_NODATA] = np.nan
    return out / GEOMEDIAN_SCALE


def decode_fc(values: np.ndarray) -> tuple[np.ndarray, int]:
    """255 -> NaN; >100 retained and counted. Returns (float64, n_out_of_range)."""
    out = values.astype(np.float64, copy=True)
    out[values == FC_NODATA] = np.nan
    n_out_of_range = int(np.sum(out > FC_DOCUMENTED_MAX))
    return out, n_out_of_range
