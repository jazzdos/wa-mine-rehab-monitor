"""DPIRD-020 regional-boundary extract validation (D13 Batch D task D1)."""

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from wa_mine_monitor.sources import wa_regions


def _regions_gdf(names):
    geoms = [Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)]) for i in range(len(names))]
    return gpd.GeoDataFrame({"dpird_region_name": list(names)}, geometry=geoms, crs="EPSG:4283")


def _write_gpkg(tmp_path, gdf) -> Path:
    path = tmp_path / "regions.gpkg"
    gdf.to_file(path, driver="GPKG")
    return path


def test_load_regions_returns_named_frame_in_target_crs(tmp_path):
    path = _write_gpkg(tmp_path, _regions_gdf(["Pilbara", "Goldfields-Esperance", "Kimberley"]))
    out = wa_regions.load_regions(path)
    assert list(out.columns) == ["region_name", "geometry"]
    assert str(out.crs) == "EPSG:3577"
    assert set(out["region_name"]) == {
        "Pilbara",
        "Goldfields-Esperance",
        "Kimberley",
    }


def test_load_regions_refuses_when_a_required_region_is_missing(tmp_path):
    path = _write_gpkg(tmp_path, _regions_gdf(["Pilbara", "Kimberley"]))
    with pytest.raises(wa_regions.RegionExtractError, match="Goldfields-Esperance"):
        wa_regions.load_regions(path)


def test_load_regions_refuses_null_or_duplicate_region_names(tmp_path):
    gdf = _regions_gdf(["Pilbara", "Goldfields-Esperance", "Pilbara"])
    with pytest.raises(wa_regions.RegionExtractError, match="duplicate"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf))
    gdf2 = _regions_gdf(["Pilbara", "Goldfields-Esperance", None])
    with pytest.raises(wa_regions.RegionExtractError, match="null"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf2))


def test_load_regions_refuses_missing_name_column(tmp_path):
    gdf = _regions_gdf(["Pilbara", "Goldfields-Esperance"]).rename(
        columns={"dpird_region_name": "unexpected"}
    )
    with pytest.raises(wa_regions.RegionExtractError, match="name column"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf))


def test_load_regions_refuses_null_geometries(tmp_path):
    gdf = _regions_gdf(["Pilbara", "Goldfields-Esperance", "Kimberley"])
    gdf.loc[1, "geometry"] = None
    with pytest.raises(wa_regions.RegionExtractError, match="null|invalid"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf))


def test_load_regions_refuses_empty_geometries(tmp_path):
    gdf = _regions_gdf(["Pilbara", "Goldfields-Esperance", "Kimberley"])
    gdf.loc[1, "geometry"] = Polygon()
    with pytest.raises(wa_regions.RegionExtractError, match="empty|invalid"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf))


def test_load_regions_refuses_invalid_geometries(tmp_path):
    gdf = _regions_gdf(["Pilbara", "Goldfields-Esperance", "Kimberley"])
    # Create an invalid self-intersecting polygon (bowtie)
    gdf.loc[1, "geometry"] = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    with pytest.raises(wa_regions.RegionExtractError, match="invalid"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf))


def test_load_regions_refuses_non_polygon_geometry_types(tmp_path):
    from shapely.geometry import Point

    gdf = gpd.GeoDataFrame(
        {"dpird_region_name": ["Pilbara", "Goldfields-Esperance", "Kimberley"]},
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Point(1.5, 0.5),
            Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
        ],
        crs="EPSG:4283",
    )
    with pytest.raises(wa_regions.RegionExtractError, match="Polygon"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf))


def test_load_regions_refuses_missing_crs(tmp_path):
    gdf = _regions_gdf(["Pilbara", "Goldfields-Esperance"])
    gdf = gdf.set_crs(None, allow_override=True)
    with pytest.raises(wa_regions.RegionExtractError, match="CRS"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf))


def test_load_regions_refuses_multiple_name_columns(tmp_path):
    gdf = _regions_gdf(["Pilbara", "Goldfields-Esperance"])
    # Add a second name column candidate
    gdf["region_name"] = ["P", "G"]
    with pytest.raises(wa_regions.RegionExtractError, match="multiple|ambiguous"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf))


def test_load_regions_validates_exactly_one_name_column_exists(tmp_path):
    path = _write_gpkg(tmp_path, _regions_gdf(["Pilbara", "Goldfields-Esperance"]))
    out = wa_regions.load_regions(path)
    # Should succeed with exactly one name column present
    assert "region_name" in out.columns
    assert "dpird_region_name" not in out.columns


def test_load_regions_refuses_overlapping_region_interiors(tmp_path):
    # Create two regions with overlapping interiors
    gdf = gpd.GeoDataFrame(
        {"dpird_region_name": ["Pilbara", "Goldfields-Esperance"]},
        geometry=[
            Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]),
            Polygon([(1, 1), (3, 1), (3, 3), (1, 3)]),
        ],
        crs="EPSG:4283",
    )
    with pytest.raises(wa_regions.RegionExtractError, match="overlaps|overlap"):
        wa_regions.load_regions(_write_gpkg(tmp_path, gdf))


def test_load_regions_allows_touching_boundaries(tmp_path):
    # Create two regions that touch at a boundary but don't overlap
    gdf = gpd.GeoDataFrame(
        {"dpird_region_name": ["Pilbara", "Goldfields-Esperance", "Kimberley"]},
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
            Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
        ],
        crs="EPSG:4283",
    )
    path = _write_gpkg(tmp_path, gdf)
    out = wa_regions.load_regions(path)
    # Should succeed with touching boundaries
    assert len(out) == 3
