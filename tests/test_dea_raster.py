"""DEA raster decode rules -- Batch E's declared rules, built in Batch D.

D13 lines 511-512: geomedian -999 -> null, valid values / 10_000; FC 255 ->
null, values above 100 RETAINED (measured, counted, never clipped).
"""

import numpy as np

from wa_mine_monitor import dea_raster


def test_decode_geomedian_nodata_and_scale():
    raw = np.array([[-999, 0], [5000, 10000]], dtype=np.int16)
    decoded = dea_raster.decode_geomedian(raw)
    assert np.isnan(decoded[0, 0])
    assert decoded[0, 1] == 0.0
    assert decoded[1, 0] == 0.5
    assert decoded[1, 1] == 1.0


def test_decode_fc_nodata_and_out_of_range_retained_and_counted():
    raw = np.array([[255, 42], [101, 120]], dtype=np.uint8)
    decoded, n_out_of_range = dea_raster.decode_fc(raw)
    assert np.isnan(decoded[0, 0])
    assert decoded[0, 1] == 42.0
    assert decoded[1, 0] == 101.0  # retained, not clipped
    assert decoded[1, 1] == 120.0
    assert n_out_of_range == 2


def test_decode_functions_do_not_mutate_input():
    raw = np.array([-999, 100], dtype=np.int16)
    dea_raster.decode_geomedian(raw)
    assert raw.tolist() == [-999, 100]
