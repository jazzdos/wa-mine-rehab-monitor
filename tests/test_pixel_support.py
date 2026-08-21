"""EPSG:3577 pixel-CENTRE support assignments (D13 Batch D task D2).

Partial-pixel weighting and all_touched are prohibited: membership is
"pixel centre covered by the polygon", nothing else. Effective support is
a measured centre count, never area/900.
"""

import pytest
from shapely.geometry import Polygon

from wa_mine_monitor import pixel_support

# A grid whose origin sits on the 30 m lattice: 10x10 pixels, top-left
# corner at (0, 300), pixel size 30 x -30 (north-up).
# Fixture tile_id doesn't match DEA xNyN pattern, so lattice validation is skipped.
GRID = pixel_support.GridSpec(
    crs="EPSG:3577",
    transform=(30.0, 0.0, 0.0, 0.0, -30.0, 300.0),
    width=10,
    height=10,
    tile_id="fixture_tile",
)

# Larger grid for testing 144-centre support.
GRID_144 = pixel_support.GridSpec(
    crs="EPSG:3577",
    transform=(30.0, 0.0, 0.0, 0.0, -30.0, 360.0),
    width=20,
    height=20,
    tile_id="fixture_large",
)


def _square(x0: float, y0: float, side: float) -> Polygon:
    return Polygon([(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)])


def test_exact_nine_centre_square():
    # Covers centres of cols 0-2, rows 7-9 (y in [0, 90]): 3x3 = 9.
    result = pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", GRID)
    assert result.effective_pixel_support_px == 9
    assert len(result.member_indices) == 9


def test_exact_sixteen_centre_square():
    result = pixel_support.build_pixel_support(_square(0, 0, 120), "EPSG:3577", GRID)
    assert result.effective_pixel_support_px == 16


def test_exact_144_centre_square():
    # 12x12 pixels at 30 m = 360 m square. Covers all 144 centres.
    result = pixel_support.build_pixel_support(_square(0, 0, 360), "EPSG:3577", GRID_144)
    assert result.effective_pixel_support_px == 144


def test_boundary_centre_is_a_member():
    # Polygon edge passing exactly through a centre: covered_by includes
    # the boundary, so the centre at (15, 15) belongs to a polygon whose
    # edge is x in [15, 45], y in [15, 45].
    polygon = Polygon([(15, 15), (45, 15), (45, 45), (15, 45)])
    result = pixel_support.build_pixel_support(polygon, "EPSG:3577", GRID)
    assert result.effective_pixel_support_px == 4  # centres 15,45 x 15,45


def test_effective_support_is_not_area_over_900():
    # A thin sliver with area 900*4 m^2 that covers NO pixel centre.
    sliver = Polygon([(1, 0), (2, 0), (2, 3600), (1, 3600)])
    result = pixel_support.build_pixel_support(sliver, "EPSG:3577", GRID)
    assert result.effective_pixel_support_px == 0  # computed zero, not null


def test_crs_mismatch_refused():
    with pytest.raises(pixel_support.PixelSupportError, match="CRS"):
        pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:4326", GRID)


def test_wrong_crs_even_if_30m_lattice():
    # Even a 30m EPSG:4326 grid is refused: only EPSG:3577 is valid.
    bad_crs_grid = pixel_support.GridSpec(
        crs="EPSG:4326",
        transform=(30.0, 0.0, 0.0, 0.0, -30.0, 300.0),
        width=10,
        height=10,
        tile_id="fixture_tile",
    )
    with pytest.raises(pixel_support.PixelSupportError, match="CRS"):
        pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:4326", bad_crs_grid)


def test_shifted_grid_refused():
    shifted = pixel_support.GridSpec(
        crs="EPSG:3577",
        transform=(30.0, 0.0, 7.0, 0.0, -30.0, 300.0),
        width=10,
        height=10,
        tile_id="fixture_tile",
    )
    with pytest.raises(pixel_support.PixelSupportError, match="lattice"):
        pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", shifted)


def test_rotated_grid_refused():
    rotated = pixel_support.GridSpec(
        crs="EPSG:3577",
        transform=(30.0, 0.5, 0.0, 0.5, -30.0, 300.0),
        width=10,
        height=10,
        tile_id="fixture_tile",
    )
    with pytest.raises(pixel_support.PixelSupportError, match="rotat"):
        pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", rotated)


def test_wrong_pixel_size_refused():
    coarse = pixel_support.GridSpec(
        crs="EPSG:3577",
        transform=(60.0, 0.0, 0.0, 0.0, -60.0, 600.0),
        width=10,
        height=10,
        tile_id="fixture_tile",
    )
    with pytest.raises(pixel_support.PixelSupportError, match="30"):
        pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", coarse)


def test_dea_tile_lattice_validation_positive_indices():
    # DEA tile x5y10: origin (-4416000 + 5*96000, -6912000 + (10+1)*96000)
    # = (-3936000, -5856000) on the collection-3 `au-30` lattice.
    grid = pixel_support.GridSpec(
        crs="EPSG:3577",
        transform=(30.0, 0.0, -3936000.0, 0.0, -30.0, -5856000.0),
        width=3200,
        height=3200,
        tile_id="x5y10",
    )
    # Should not raise.
    pixel_support._validate_grid(grid)


@pytest.mark.parametrize(
    ("tile_id", "origin_x", "origin_y"),
    [
        # Read live from dea-public-data ga_ls5t_gm_cyear_3/4-0-0 on 2026-08-21:
        # .../x31/y37/1986--P1Y/..._x31y37_..._nbart_nir.tif and
        # .../x34/y37/1986--P1Y/..._x34y37_..._nbart_nir.tif (both 3200x3200, 30 m).
        ("x31y37", -1440000.0, -3264000.0),
        ("x34y37", -1152000.0, -3264000.0),
    ],
)
def test_dea_tile_lattice_validation_live_wa_tiles(tile_id, origin_x, origin_y):
    grid = pixel_support.GridSpec(
        crs="EPSG:3577",
        transform=(30.0, 0.0, origin_x, 0.0, -30.0, origin_y),
        width=3200,
        height=3200,
        tile_id=tile_id,
    )
    # Should not raise.
    pixel_support._validate_grid(grid)


def test_dea_tile_lattice_refuses_zero_origin_convention():
    # The pre-2026-08-21 formula (origin at 0,0) placed x34y37 at (3264000, 3648000);
    # that is a different tile and must be refused.
    wrong = pixel_support.GridSpec(
        crs="EPSG:3577",
        transform=(30.0, 0.0, 3264000.0, 0.0, -30.0, 3648000.0),
        width=3200,
        height=3200,
        tile_id="x34y37",
    )
    with pytest.raises(pixel_support.PixelSupportError, match="inconsistent"):
        pixel_support._validate_grid(wrong)


def test_dea_tile_lattice_refused_wrong_origin():
    # Tile x5y10 but origin is off by 1 pixel.
    wrong = pixel_support.GridSpec(
        crs="EPSG:3577",
        transform=(30.0, 0.0, -3935970.0, 0.0, -30.0, -5856000.0),
        width=3200,
        height=3200,
        tile_id="x5y10",
    )
    with pytest.raises(pixel_support.PixelSupportError, match="inconsistent"):
        pixel_support._validate_grid(wrong)


def test_non_dea_tile_id_skips_lattice_check():
    # A fixture tile_id that doesn't match xNyN pattern should skip the
    # lattice check but still validate CRS and 30m lattice.
    grid = pixel_support.GridSpec(
        crs="EPSG:3577",
        transform=(30.0, 0.0, 123450.0, 0.0, -30.0, 654300.0),
        width=10,
        height=10,
        tile_id="fixture_tile",
    )
    # Should not raise: non-matching tile_id means no DEA lattice check.
    pixel_support._validate_grid(grid)


def test_missing_or_invalid_geometry_is_not_computed_not_zero():
    assert pixel_support.build_pixel_support(None, "EPSG:3577", GRID) is None
    bowtie = Polygon([(0, 0), (30, 30), (30, 0), (0, 30)])
    assert pixel_support.build_pixel_support(bowtie, "EPSG:3577", GRID) is None


def test_assignment_digest_binds_grid_identity_and_members():
    a = pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", GRID)
    b = pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", GRID)
    assert a.assignment_digest == b.assignment_digest
    other_tile = pixel_support.GridSpec(
        crs=GRID.crs,
        transform=GRID.transform,
        width=GRID.width,
        height=GRID.height,
        tile_id="other_fixture",
    )
    c = pixel_support.build_pixel_support(_square(0, 0, 90), "EPSG:3577", other_tile)
    assert c.assignment_digest != a.assignment_digest
