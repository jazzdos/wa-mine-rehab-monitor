"""Tests for the DEA epoch-coverage index (D13 Batch C task C3)."""

import pandas as pd
import pyarrow as pa
import pytest

from wa_mine_monitor import tables
from wa_mine_monitor.dea_coverage import (
    ASSET_INDEX_COLUMNS,
    COVERAGE_DISCLOSURE_KEYS,
    DEA_EPOCH_COLUMN_BY_SOURCE,
    ITEM_INDEX_COLUMNS,
    build_asset_index,
    build_item_index,
    count_site_epochs,
)


def _item(item_id, *, bbox, year, tile="x11y22", version="4.0.0", assets=None):
    return {
        "id": item_id,
        "bbox": list(bbox),
        "properties": {
            "datetime": f"{year}-07-02T00:00:00Z",
            "odc:region_code": tile,
            "odc:dataset_version": version,
        },
        "assets": assets
        if assets is not None
        else {"nbart_nir": {"href": "s3://x/n.tif"}, "count": {"href": "s3://x/c.tif"}},
    }


BBOX_A = (116.0, -33.0, 117.0, -32.0)
BBOX_B = (117.0, -33.0, 118.0, -32.0)


def test_build_item_index_columns_and_content():
    items = {"dea_gm_ls5t": [_item("a-1990", bbox=BBOX_A, year=1990)]}
    index, duplicates = build_item_index(items)
    assert tuple(index.columns) == ITEM_INDEX_COLUMNS
    row = index.iloc[0]
    assert row["source_id"] == "dea_gm_ls5t"
    # D13 C3 names COLLECTION identity and ASSET identity as index fields;
    # source_id alone renames the first and drops the second.
    assert row["collection_id"] == "ga_ls5t_gm_cyear_3"
    assert row["asset_identity"] == "count|nbart_nir"
    assert row["item_id"] == "a-1990"
    assert row["year"] == 1990
    assert row["tile_id"] == "x11y22"
    assert row["product_version"] == "4.0.0"
    assert (row["bbox_west"], row["bbox_north"]) == (116.0, -32.0)
    assert duplicates == {"dea_gm_ls5t": 0}


def test_asset_identity_distinguishes_items_with_different_asset_sets():
    items = {
        "dea_gm_ls5t": [
            _item("a-1990", bbox=BBOX_A, year=1990),
            _item(
                "b-1990",
                bbox=BBOX_B,
                year=1990,
                assets={"count": {"href": "s3://x/c.tif"}},
            ),
        ]
    }
    index, _ = build_item_index(items)
    assert sorted(index["asset_identity"]) == ["count", "count|nbart_nir"]


def test_unknown_source_id_is_refused_not_silently_indexed():
    with pytest.raises(KeyError):
        build_item_index({"not_a_pinned_source": [_item("a", bbox=BBOX_A, year=1990)]})


def test_item_without_assets_is_a_refusal():
    broken = _item("a-1990", bbox=BBOX_A, year=1990)
    broken["assets"] = {}
    with pytest.raises(ValueError, match="asset"):
        build_item_index({"dea_gm_ls5t": [broken]})


def test_duplicate_item_ids_are_refused_with_a_count_not_double_counted():
    items = {
        "dea_gm_ls5t": [
            _item("a-1990", bbox=BBOX_A, year=1990),
            _item("a-1990", bbox=BBOX_A, year=1990),
            _item("b-1990", bbox=BBOX_B, year=1990, tile="x12y22"),
        ]
    }
    index, duplicates = build_item_index(items)
    assert len(index) == 2
    assert duplicates == {"dea_gm_ls5t": 1}


def test_sources_stay_separate():
    items = {
        "dea_gm_ls5t": [_item("a-1990", bbox=BBOX_A, year=1990)],
        "dea_gm_ls7e": [_item("a-1990", bbox=BBOX_A, year=1990)],
    }
    index, _ = build_item_index(items)
    assert sorted(index["source_id"]) == ["dea_gm_ls5t", "dea_gm_ls7e"]


def test_item_without_bbox_is_a_refusal_not_a_skip():
    broken = _item("a-1990", bbox=BBOX_A, year=1990)
    del broken["bbox"]
    with pytest.raises(ValueError, match="bbox"):
        build_item_index({"dea_gm_ls5t": [broken]})


# --------------------------------------------------------------- build_asset_index


def _asset_item(item_id, *, assets):
    item = _item(item_id, bbox=BBOX_A, year=1990)
    item["assets"] = assets
    return item


def test_asset_index_reads_observed_metadata_from_the_captured_item():
    items = {
        "dea_gm_ls5t": [
            _asset_item(
                "a-1990",
                assets={
                    "nbart_nir": {
                        "href": "s3://x/n.tif",
                        "file:size": 12345,
                        "proj:shape": [3200, 3200],
                        "raster:bands": [{"data_type": "int16", "block_size": [512, 512]}],
                    }
                },
            )
        ]
    }
    index, disclosure = build_asset_index(items)
    assert tuple(index.columns) == ASSET_INDEX_COLUMNS
    row = index.iloc[0]
    assert row["asset_key"] == "nbart_nir"
    assert row["file_size_bytes"] == 12345
    assert row["raster_width_px"] == 3200
    assert row["block_width_px"] == 512
    assert row["data_type"] == "int16"
    assert row["bytes_per_sample"] == 2
    assert row["metadata_source"] == "stac-item-asset"
    assert disclosure["dea_gm_ls5t"]["n_assets_block_size_missing"] == 0


def test_absent_asset_metadata_stays_null_and_is_counted():
    """No implicit 512-pixel block, no implicit dtype, no implicit size --
    a missing field is null WITH a count (D13's disclosure discipline)."""
    items = {"dea_gm_ls5t": [_asset_item("a-1990", assets={"count": {"href": "s3://x/c.tif"}})]}
    index, disclosure = build_asset_index(items)
    row = index.iloc[0]
    for column in ("file_size_bytes", "block_width_px", "raster_width_px", "bytes_per_sample"):
        assert pd.isna(row[column])
    assert row["metadata_source"] == "absent"
    counts = disclosure["dea_gm_ls5t"]
    assert counts["n_assets"] == 1
    assert counts["n_assets_block_size_missing"] == 1
    assert counts["n_assets_file_size_missing"] == 1
    assert counts["n_assets_data_type_missing"] == 1


def test_an_unmapped_data_type_leaves_bytes_per_sample_null():
    items = {
        "dea_gm_ls5t": [
            _asset_item(
                "a-1990",
                assets={"count": {"raster:bands": [{"data_type": "complex128"}]}},
            )
        ]
    }
    index, _ = build_asset_index(items)
    assert pd.isna(index.iloc[0]["bytes_per_sample"])


# --------------------------------------------------------------- count_site_epochs


def _register(rows):
    return pd.DataFrame(rows, columns=["site_id", "lon", "lat"])


def _coverage_inputs(items_by_source):
    index, duplicates = build_item_index(items_by_source)
    return index, duplicates


def test_coordinate_less_site_gets_null_for_all_four_counts():
    register = _register([{"site_id": "S1", "lon": None, "lat": None}])
    index, dups = _coverage_inputs({"dea_gm_ls5t": [_item("a", bbox=BBOX_A, year=1990)]})
    coverage, _ = count_site_epochs(register, index, duplicates_refused=dups)
    row = coverage.set_index("site_id").loc["S1"]
    for column in DEA_EPOCH_COLUMN_BY_SOURCE.values():
        assert pd.isna(row[column])


def test_located_site_with_no_item_gets_genuine_zero():
    register = _register([{"site_id": "S1", "lon": 150.0, "lat": -20.0}])
    index, dups = _coverage_inputs({"dea_gm_ls5t": [_item("a", bbox=BBOX_A, year=1990)]})
    coverage, disclosures = count_site_epochs(register, index, duplicates_refused=dups)
    assert coverage.set_index("site_id").loc["S1", "n_dea_gm_ls5t_epochs"] == 0
    assert disclosures["dea_gm_ls5t"]["n_sites_coverage_zero"] == 1


def test_multiple_tiles_in_one_year_count_as_one_epoch():
    # Site at the shared corner of two tiles, both 1990: one epoch.
    register = _register([{"site_id": "S1", "lon": 117.0, "lat": -32.5}])
    index, dups = _coverage_inputs(
        {
            "dea_gm_ls5t": [
                _item("a-1990", bbox=BBOX_A, year=1990),
                _item("b-1990", bbox=BBOX_B, year=1990, tile="x12y22"),
                _item("a-1991", bbox=BBOX_A, year=1991),
            ]
        }
    )
    coverage, _ = count_site_epochs(register, index, duplicates_refused=dups)
    assert coverage.set_index("site_id").loc["S1", "n_dea_gm_ls5t_epochs"] == 2


def test_overlapping_sensor_collections_remain_separate():
    register = _register([{"site_id": "S1", "lon": 116.5, "lat": -32.5}])
    index, dups = _coverage_inputs(
        {
            "dea_gm_ls5t": [_item("a-1990", bbox=BBOX_A, year=1990)],
            "dea_gm_ls7e": [
                _item("c-1999", bbox=BBOX_A, year=1999),
                _item("c-2000", bbox=BBOX_A, year=2000),
            ],
        }
    )
    coverage, _ = count_site_epochs(register, index, duplicates_refused=dups)
    row = coverage.set_index("site_id").loc["S1"]
    assert row["n_dea_gm_ls5t_epochs"] == 1
    assert row["n_dea_gm_ls7e_epochs"] == 2


def test_disclosure_reconciles_to_register_rows_and_carries_fixed_keys():
    register = _register(
        [
            {"site_id": "S1", "lon": 116.5, "lat": -32.5},
            {"site_id": "S2", "lon": 150.0, "lat": -20.0},
            {"site_id": "S3", "lon": None, "lat": None},
        ]
    )
    index, dups = _coverage_inputs({"dea_gm_ls5t": [_item("a", bbox=BBOX_A, year=1990)]})
    _, disclosures = count_site_epochs(register, index, duplicates_refused=dups)
    disclosure = disclosures["dea_gm_ls5t"]
    assert tuple(disclosure.keys()) == COVERAGE_DISCLOSURE_KEYS
    assert disclosure["n_sites_coverage_computed"] + disclosure[
        "n_sites_coverage_not_computed"
    ] == len(register)
    assert disclosure["n_sites_coverage_computed"] == 2
    assert disclosure["n_sites_coverage_zero"] == 1
    assert disclosure["n_sites_coverage_not_computed"] == 1
    assert disclosure["n_distinct_items"] == 1
    assert disclosure["n_duplicate_items_refused"] == 0


def test_counts_survive_declared_arrow_write_read(tmp_path):
    register = _register(
        [
            {"site_id": "S1", "lon": 116.5, "lat": -32.5},
            {"site_id": "S2", "lon": None, "lat": None},
        ]
    )
    index, dups = _coverage_inputs({"dea_gm_ls5t": [_item("a", bbox=BBOX_A, year=1990)]})
    coverage, _ = count_site_epochs(register, index, duplicates_refused=dups)
    schema = pa.schema(
        [pa.field("site_id", pa.string())]
        + [
            pa.field(column, pa.int64(), nullable=True)
            for column in DEA_EPOCH_COLUMN_BY_SOURCE.values()
        ]
    )
    path = tmp_path / "coverage.parquet"
    tables.write_table(coverage, path, schema)
    read_back = tables.read_table(path)
    assert read_back["n_dea_gm_ls5t_epochs"].tolist()[0] == 1
    assert pd.isna(read_back["n_dea_gm_ls5t_epochs"].tolist()[1])
