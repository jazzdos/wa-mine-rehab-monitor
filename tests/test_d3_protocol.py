"""D3 simulation protocol: frozen before any spectral result (D13 task D1)."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
import yaml
from shapely.geometry import Point, Polygon

from wa_mine_monitor import d3_protocol

_CONFIG = Path(__file__).resolve().parents[1] / "config" / "d3.yaml"


def _protocol():
    return d3_protocol.load_protocol(_CONFIG)


def _footprint_frame(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "maus_id",
            "region",
            "commodity_group",
            "shape_class",
            "n_full_support_years",
        ],
    )


# ==============================================================================
# Task 4: Core protocol loading and digest
# ==============================================================================


def test_support_set_is_exactly_the_d13_set():
    protocol = d3_protocol.load_protocol(_CONFIG)
    assert protocol.supports == (9, 16, 25, 36, 49, 64, 100, 144)


def test_criteria_are_the_immutable_d13_values():
    protocol = d3_protocol.load_protocol(_CONFIG)
    assert protocol.criteria.nbr_p90_abs_error_max == 0.03
    assert protocol.criteria.ndmi_p90_abs_error_max == 0.03
    assert protocol.criteria.fc_p90_abs_error_pp_max == 5.0
    assert protocol.criteria.spearman_median_min == 0.95
    assert protocol.criteria.computable_site_year_fraction_min == 0.90
    assert protocol.replicates == 100
    assert protocol.adequacy.min_footprints == 10
    assert protocol.adequacy.min_full_support_years == 10
    assert protocol.selection.use_all_below == 30
    assert protocol.selection.select_n == 30


def test_load_refuses_a_drifted_support_set(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["supports"] = [9, 16, 25]
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="supports"):
        d3_protocol.load_protocol(drifted)


def test_load_refuses_a_relaxed_criterion(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["criteria"]["nbr_p90_abs_error_max"] = 0.05
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="criteria"):
        d3_protocol.load_protocol(drifted)


def test_load_refuses_drifted_shape_classes(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["shape_classes"]["elongated_below"] = 0.25
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="shape_classes"):
        d3_protocol.load_protocol(drifted)


def test_load_refuses_drifted_adequacy_min_footprints(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["adequacy"]["min_footprints"] = 5
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="adequacy"):
        d3_protocol.load_protocol(drifted)


def test_load_refuses_drifted_adequacy_min_years(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["adequacy"]["min_full_support_years"] = 8
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="adequacy"):
        d3_protocol.load_protocol(drifted)


def test_load_refuses_drifted_selection(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["selection"]["use_all_below"] = 25
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="selection"):
        d3_protocol.load_protocol(drifted)


def test_load_refuses_drifted_replicates(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    raw["d3"]["replicates"] = 50
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="replicates"):
        d3_protocol.load_protocol(drifted)


def test_load_requires_all_procedure_keys(tmp_path):
    raw = yaml.safe_load(_CONFIG.read_text())
    del raw["d3"]["procedures"]["boundary_tie"]
    drifted = tmp_path / "d3.yaml"
    drifted.write_text(yaml.safe_dump(raw))
    with pytest.raises(d3_protocol.D3ProtocolError, match="procedures"):
        d3_protocol.load_protocol(drifted)


def test_digest_is_stable_and_key_order_independent(tmp_path):
    protocol = d3_protocol.load_protocol(_CONFIG)
    digest_one = d3_protocol.protocol_digest(protocol)
    # Re-serialize with keys reordered (recursively reverse insertion order)
    raw = yaml.safe_load(_CONFIG.read_text())

    def reverse_dict_order(obj):
        if isinstance(obj, dict):
            return {k: reverse_dict_order(obj[k]) for k in reversed(obj.keys())}
        if isinstance(obj, list):
            return [reverse_dict_order(item) for item in obj]
        return obj

    reordered_raw = reverse_dict_order(raw)
    reordered = tmp_path / "d3.yaml"
    reordered.write_text(yaml.safe_dump(reordered_raw, sort_keys=False))
    digest_two = d3_protocol.protocol_digest(d3_protocol.load_protocol(reordered))
    assert digest_two == digest_one
    assert len(digest_one) == 64


# ==============================================================================
# Task 5: Classification (commodity, shape, region)
# ==============================================================================


def test_classify_commodity_first_rule_wins_and_other_is_catch_all():
    protocol = _protocol()
    assert d3_protocol.classify_commodity("IRON ORE - Hematite", protocol) == "iron_ore"
    assert d3_protocol.classify_commodity("Gold, Nickel", protocol) == "nickel"
    assert d3_protocol.classify_commodity("Zircon; Rutile", protocol) == "mineral_sands"
    assert d3_protocol.classify_commodity("Coal", protocol) == "other"


def test_classify_commodity_refuses_null_or_blank():
    protocol = _protocol()
    for bad in (None, "", "   "):
        with pytest.raises(d3_protocol.D3ProtocolError, match="unclassified"):
            d3_protocol.classify_commodity(bad, protocol)


def test_shape_class_boundaries_match_d13():
    protocol = _protocol()
    assert d3_protocol.shape_class(0.19, protocol) == "elongated"
    assert d3_protocol.shape_class(0.20, protocol) == "intermediate"
    assert d3_protocol.shape_class(0.49, protocol) == "intermediate"
    assert d3_protocol.shape_class(0.50, protocol) == "compact"
    assert d3_protocol.shape_class(1.0, protocol) == "compact"


def test_shape_class_refuses_non_finite():
    protocol = _protocol()
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(d3_protocol.D3ProtocolError, match="compactness"):
            d3_protocol.shape_class(bad, protocol)


def test_shape_class_refuses_out_of_range():
    protocol = _protocol()
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(d3_protocol.D3ProtocolError, match="compactness"):
            d3_protocol.shape_class(bad, protocol)


def test_shape_class_accepts_boundary_values():
    protocol = _protocol()
    # Boundary: just above 0, just below 1+1e-9
    assert d3_protocol.shape_class(0.0001, protocol) == "elongated"
    assert d3_protocol.shape_class(1.0 + 1e-9, protocol) == "compact"


def test_assign_regions_names_strata_and_refuses_uncovered_points():
    protocol = _protocol()
    regions = gpd.GeoDataFrame(
        {"region_name": ["Pilbara", "Goldfields-Esperance", "Kimberley"]},
        geometry=[
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            Polygon([(10, 0), (20, 0), (20, 10), (10, 10)]),
            Polygon([(20, 0), (30, 0), (30, 10), (20, 10)]),
        ],
        crs="EPSG:3577",
    )
    points = gpd.GeoDataFrame(
        {"site_id": ["S1", "S2", "S3"]},
        geometry=[Point(5, 5), Point(15, 5), Point(25, 5)],
        crs="EPSG:3577",
    )
    assigned, disclosure = d3_protocol.assign_regions(points, regions, protocol)
    assert assigned.tolist() == ["pilbara", "goldfields_esperance", "other_wa"]
    assert disclosure["n_ambiguous_boundary_points"] == 0

    outside = gpd.GeoDataFrame({"site_id": ["S4"]}, geometry=[Point(99, 99)], crs="EPSG:3577")
    with pytest.raises(d3_protocol.D3ProtocolError, match="S4"):
        d3_protocol.assign_regions(outside, regions, protocol)


def test_assign_regions_boundary_point_resolves_deterministically():
    protocol = _protocol()
    regions = gpd.GeoDataFrame(
        {"region_name": ["Pilbara", "Goldfields-Esperance"]},
        geometry=[
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            Polygon([(10, 0), (20, 0), (20, 10), (10, 10)]),
        ],
        crs="EPSG:3577",
    )
    on_border = gpd.GeoDataFrame({"site_id": ["S1"]}, geometry=[Point(10, 5)], crs="EPSG:3577")
    assigned, disclosure = d3_protocol.assign_regions(on_border, regions, protocol)
    # Lexicographically smallest source region name wins:
    # "Goldfields-Esperance" < "Pilbara".
    assert assigned.tolist() == ["goldfields_esperance"]
    assert disclosure["n_ambiguous_boundary_points"] == 1


def test_assign_regions_works_with_footprint_representative_points():
    """Test assign_regions on polygon-derived representative_point() geometries."""
    protocol = _protocol()
    regions = gpd.GeoDataFrame(
        {"region_name": ["Pilbara", "Goldfields-Esperance"]},
        geometry=[
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            Polygon([(10, 0), (20, 0), (20, 10), (10, 10)]),
        ],
        crs="EPSG:3577",
    )
    # Create representative points from polygons
    footprint_polygons = gpd.GeoDataFrame(
        {"site_id": ["M1", "M2"]},
        geometry=[
            Polygon([(1, 1), (4, 1), (4, 4), (1, 4)]),
            Polygon([(11, 1), (14, 1), (14, 4), (11, 4)]),
        ],
        crs="EPSG:3577",
    )
    rep_points = footprint_polygons.copy()
    rep_points["geometry"] = footprint_polygons.geometry.apply(lambda g: g.representative_point())
    assigned, _disclosure = d3_protocol.assign_regions(rep_points, regions, protocol)
    assert assigned.tolist() == ["pilbara", "goldfields_esperance"]


# ==============================================================================
# Task 6: Selection (adequacy + stable hash)
# ==============================================================================


def test_adequacy_and_selection_use_all_when_10_to_29():
    protocol = _protocol()
    rows = [(f"m{i:03d}", "pilbara", "iron_ore", "compact", 12) for i in range(15)]
    selected = d3_protocol.select_stratum_footprints(_footprint_frame(rows), protocol)
    stratum = ("pilbara", "iron_ore", "compact")
    assert stratum in selected
    assert len(selected[stratum]) == 15


def test_selection_caps_at_30_by_stable_hash_of_maus_id():
    protocol = _protocol()
    rows = [(f"m{i:03d}", "pilbara", "gold", "compact", 12) for i in range(40)]
    frame = _footprint_frame(rows)
    selected = d3_protocol.select_stratum_footprints(frame, protocol)
    stratum = ("pilbara", "gold", "compact")
    assert len(selected[stratum]) == 30
    import hashlib

    expected = sorted(
        (row[0] for row in rows),
        key=lambda m: hashlib.sha256(m.encode("utf-8")).hexdigest(),
    )[:30]
    assert selected[stratum] == tuple(sorted(expected))


def test_selection_is_stable_under_row_reorder():
    protocol = _protocol()
    rows = [(f"m{i:03d}", "pilbara", "gold", "compact", 12) for i in range(40)]
    forward = d3_protocol.select_stratum_footprints(_footprint_frame(rows), protocol)
    backward = d3_protocol.select_stratum_footprints(
        _footprint_frame(list(reversed(rows))), protocol
    )
    assert forward == backward


def test_sparse_stratum_is_reported_not_selected():
    protocol = _protocol()
    rows = [(f"m{i:03d}", "pilbara", "nickel", "compact", 12) for i in range(9)]
    selected = d3_protocol.select_stratum_footprints(_footprint_frame(rows), protocol)
    assert ("pilbara", "nickel", "compact") not in selected
    adequacy = d3_protocol.stratum_adequacy(_footprint_frame(rows), protocol)
    assert adequacy[("pilbara", "nickel", "compact")] == {
        "n_footprints_meeting_years": 9,
        "adequate": False,
    }


def test_footprints_below_min_years_do_not_count_toward_adequacy():
    protocol = _protocol()
    rows = [(f"m{i:03d}", "pilbara", "nickel", "compact", 12) for i in range(9)]
    rows += [("m_low", "pilbara", "nickel", "compact", 9)]
    adequacy = d3_protocol.stratum_adequacy(_footprint_frame(rows), protocol)
    assert adequacy[("pilbara", "nickel", "compact")]["adequate"] is False


def test_stratum_adequacy_returns_full_54_stratum_space():
    """Stratum adequacy includes all 54 strata (3×6×3), even zero-count ones."""
    protocol = _protocol()
    rows = [(f"m{i:03d}", "pilbara", "iron_ore", "compact", 12) for i in range(15)]
    adequacy = d3_protocol.stratum_adequacy(_footprint_frame(rows), protocol)
    # Full space: 3 regions × 6 commodities × 3 shapes = 54
    assert len(adequacy) == 54
    # Pilbara+iron_ore+compact has 15, so adequate
    assert adequacy[("pilbara", "iron_ore", "compact")]["adequate"] is True
    # All others are inadequate
    for stratum, info in adequacy.items():
        if stratum != ("pilbara", "iron_ore", "compact"):
            assert info["adequate"] is False


def test_stratum_adequacy_with_empty_input_returns_full_space():
    """Empty input frame still yields 54 strata with adequate=False."""
    protocol = _protocol()
    empty_frame = _footprint_frame([])
    adequacy = d3_protocol.stratum_adequacy(empty_frame, protocol)
    assert len(adequacy) == 54
    for info in adequacy.values():
        assert info["adequate"] is False
        assert info["n_footprints_meeting_years"] == 0
