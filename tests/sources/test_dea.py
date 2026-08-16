"""Tests for the DEA STAC catalogue source module (D13 Batch C task C2)."""

import json
from pathlib import Path

import pytest

from wa_mine_monitor.source_catalogue import spec_for_collection
from wa_mine_monitor.sources.dea import (
    MAX_PAGES,
    CatalogueValidationError,
    fetch_collection_catalogue,
    validate_collection_json,
    validate_items,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "dea"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _spec():
    return spec_for_collection("ga_ls5t_gm_cyear_3")


def _items():
    return _load("items_page_1.json")["features"] + _load("items_page_2.json")["features"]


def test_valid_collection_json_passes_and_summarises():
    summary = validate_collection_json(_load("collection_ga_ls5t_gm_cyear_3.json"), _spec())
    assert summary["collection_id"] == "ga_ls5t_gm_cyear_3"
    assert summary["license"] == "CC-BY-4.0"
    assert summary["temporal_extent"] == [
        "1986-01-01T00:00:00Z",
        "2011-12-31T23:59:59Z",
    ]
    # D13 C2 names the required assets among the recorded snapshot fields:
    # the summary carries the SPEC's declared asset roles, so a later reader
    # can see what "required assets" meant at capture time.
    assert summary["required_assets"] == list(_spec().asset_roles)


def test_stub_signature_is_rejected():
    with pytest.raises(CatalogueValidationError, match="stub"):
        validate_collection_json(_load("collection_stub.json"), _spec())


def test_wrong_collection_id_is_rejected():
    payload = _load("collection_ga_ls5t_gm_cyear_3.json")
    payload["id"] = "ga_ls7e_gm_cyear_3"
    with pytest.raises(CatalogueValidationError, match="id"):
        validate_collection_json(payload, _spec())


def test_licence_inconsistent_with_pinned_record_is_rejected():
    payload = _load("collection_ga_ls5t_gm_cyear_3.json")
    payload["license"] = "proprietary"
    with pytest.raises(CatalogueValidationError, match="licen"):
        validate_collection_json(payload, _spec())


def test_missing_temporal_extent_is_rejected():
    payload = _load("collection_ga_ls5t_gm_cyear_3.json")
    del payload["extent"]["temporal"]
    with pytest.raises(CatalogueValidationError, match="temporal"):
        validate_collection_json(payload, _spec())


def test_valid_items_pass_and_summarise():
    summary = validate_items(_items(), _spec())
    assert summary["n_items"] == 3
    assert summary["years"] == [1990, 1991]


def test_zero_items_are_rejected():
    with pytest.raises(CatalogueValidationError, match="0 item"):
        validate_items([], _spec())


def test_duplicate_item_ids_fail_reconciliation():
    items = _items()
    items.append(dict(items[0]))
    with pytest.raises(CatalogueValidationError, match="duplicate"):
        validate_items(items, _spec())


def test_item_missing_a_required_asset_is_rejected():
    items = _items()
    del items[0]["assets"]["nbart_swir_2"]
    with pytest.raises(CatalogueValidationError, match="nbart_swir_2"):
        validate_items(items, _spec())


def test_item_with_null_datetime_is_rejected_not_a_raw_valueerror():
    """STAC allows properties.datetime to be null when start_datetime /
    end_datetime carry the interval instead. That's still a shape this
    validator refuses -- but the refusal must be a CatalogueValidationError
    naming the item, never a raw ValueError from the int() slice."""
    items = _items()
    items[0]["properties"]["datetime"] = None
    with pytest.raises(CatalogueValidationError, match=items[0]["id"]):
        validate_items(items, _spec())


def test_item_with_absent_datetime_is_rejected_not_a_raw_valueerror():
    items = _items()
    del items[0]["properties"]["datetime"]
    with pytest.raises(CatalogueValidationError, match=items[0]["id"]):
        validate_items(items, _spec())


def test_item_with_unparsable_datetime_is_rejected_not_a_raw_valueerror():
    items = _items()
    items[0]["properties"]["datetime"] = "not-a-date"
    with pytest.raises(CatalogueValidationError, match=items[0]["id"]):
        validate_items(items, _spec())


class FakeStacClient:
    """Maps URL -> payload; unknown URL raises. Records requested URLs."""

    def __init__(self, pages: dict):
        self._pages = pages
        self.requested: list[str] = []

    def get_json(self, url, *, params=None):
        self.requested.append(url)
        if url not in self._pages:
            raise AssertionError(f"unexpected URL {url}")
        payload = self._pages[url]
        if isinstance(payload, BaseException):
            raise payload
        return payload


def _fake_client_for_ls5t():
    from wa_mine_monitor.sources.dea import collection_url, items_url

    cid = "ga_ls5t_gm_cyear_3"
    return FakeStacClient(
        {
            collection_url(cid): _load("collection_ga_ls5t_gm_cyear_3.json"),
            items_url(cid): _load("items_page_1.json"),
            "https://example.test/stac/collections/ga_ls5t_gm_cyear_3/items?page=2": _load(
                "items_page_2.json"
            ),
        }
    )


def test_fetch_follows_next_links_and_returns_all_pages():
    client = _fake_client_for_ls5t()
    collection, pages, summary = fetch_collection_catalogue(client, _spec())
    assert collection["id"] == "ga_ls5t_gm_cyear_3"
    assert len(pages) == 2
    assert summary["n_items"] == 3
    assert summary["n_pages"] == 2


def test_reported_item_count_is_the_sources_own_numberMatched():
    """D13 C2 records the SOURCE's reported item count. When the API reports
    one it is captured verbatim -- never the fetched count relabelled."""
    from wa_mine_monitor.sources.dea import items_url

    client = _fake_client_for_ls5t()
    cid = "ga_ls5t_gm_cyear_3"
    client._pages[items_url(cid)] = {
        **client._pages[items_url(cid)],
        "numberMatched": 3,
    }
    _, _, summary = fetch_collection_catalogue(client, _spec())
    assert summary["reported_item_count"] == 3
    assert summary["reported_item_count_disclosure"] == "reported-by-source"


def test_absent_numberMatched_is_null_with_a_disclosure_not_the_fetched_count():
    client = _fake_client_for_ls5t()
    _, _, summary = fetch_collection_catalogue(client, _spec())
    assert summary["reported_item_count"] is None
    assert summary["reported_item_count_disclosure"] == "absent-from-source"
    # The fetched count is a SEPARATE field; the two must never be conflated.
    assert summary["n_items"] == 3


def test_fetch_refuses_a_stub_before_paging_items():
    from wa_mine_monitor.sources.dea import collection_url

    cid = "ga_ls5t_gm_cyear_3"
    client = FakeStacClient({collection_url(cid): _load("collection_stub.json")})
    with pytest.raises(CatalogueValidationError, match="stub"):
        fetch_collection_catalogue(client, _spec())
    # No items request was made -- the stub was refused at the collection.
    assert client.requested == [collection_url(cid)]


def test_fetch_refuses_a_next_link_loop_at_max_pages():
    from wa_mine_monitor.sources.dea import collection_url, items_url

    cid = "ga_ls5t_gm_cyear_3"
    looping_page = _load("items_page_1.json")
    looping_page["links"] = [{"rel": "next", "href": items_url(cid)}]
    client = FakeStacClient(
        {
            collection_url(cid): _load("collection_ga_ls5t_gm_cyear_3.json"),
            items_url(cid): looping_page,
        }
    )
    with pytest.raises(CatalogueValidationError, match=str(MAX_PAGES)):
        fetch_collection_catalogue(client, _spec())


def test_a_failing_page_propagates_no_partial_catalogue():
    """The completeness-sensitive-caller test D13 lists under C1: the
    catalogue fetch never tolerates a missing page -- a None-padded partial
    catalogue would silently understate coverage."""
    from wa_mine_monitor.sources.dea import collection_url, items_url

    cid = "ga_ls5t_gm_cyear_3"
    client = FakeStacClient(
        {
            collection_url(cid): _load("collection_ga_ls5t_gm_cyear_3.json"),
            items_url(cid): RuntimeError("transport died"),
        }
    )
    with pytest.raises(RuntimeError, match="transport died"):
        fetch_collection_catalogue(client, _spec())
