import numpy as np
from rasterio.transform import Affine
from shapely.geometry import Polygon

from wa_mine_monitor.core import zonal


def test_zonal_stats_parity_and_zero_valid():
    # 3x3 grid, 10m pixels. EPSG:28350 or local CRS.
    transform = Affine(10.0, 0.0, 100.0, 0.0, -10.0, 200.0)
    ulx, uly, lrx, lry = zonal.bounds_from_affine(transform, 3, 3)

    # Polygon that covers the top-left pixel (col 0, row 0, center at 105, 195)
    poly_tl = Polygon([(100, 200), (110, 200), (110, 190), (100, 190)])
    # Polygon that is empty/small, covers no center
    poly_empty = Polygon([(100.1, 199.9), (100.2, 199.9), (100.2, 199.8), (100.1, 199.8)])
    # Polygon that covers bottom-right pixel (col 2, row 2, center at 125, 175)
    poly_br = Polygon([(120, 180), (130, 180), (130, 170), (120, 170)])

    polygons = {"tl": poly_tl, "empty": poly_empty, "br": poly_br}

    assign = zonal.build_assignment(ulx, uly, lrx, lry, 3, 3, polygons)

    # poly_empty is too small to cover the pixel center
    assert "empty" not in assign
    assert "tl" in assign
    assert "br" in assign

    values = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]])

    # Run stats without valid mask
    stats = zonal.zonal_stats(values, assign, stat="mean")
    assert stats["tl"] == (1.0, 1)
    assert stats["br"] == (9.0, 1)

    # Now run with valid mask that masks out 'tl' completely
    valid = np.array([[False, True, True], [True, True, True], [True, True, True]])
    stats_masked = zonal.zonal_stats(values, assign, valid=valid, stat="mean")

    # 'tl' should be completely missing because no valid pixels!
    assert "tl" not in stats_masked
    assert "br" in stats_masked
