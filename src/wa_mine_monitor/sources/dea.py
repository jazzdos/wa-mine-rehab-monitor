"""DEA Explorer STAC catalogue: fetch, validate, and page collections.

Validation exists because collection EXISTENCE is not collection HEALTH:
the ``*_nbart_gm_cyear_3`` family answers HTTP 200 with a stub payload
(global unbounded bbox, null temporal interval, zero items). Every check
here rejects a specific measured failure shape, not a hypothetical.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from wa_mine_monitor.http import HttpClient, RetryPolicy
from wa_mine_monitor.source_catalogue import SourceSpec

#: DEA Explorer STAC root. Pinned; a test asserts the exact value.
DEA_STAC_API_ROOT = "https://explorer.dea.ga.gov.au/stac"

#: WA statewide bbox -- same extent `sources/maus.py` clips to.
WA_BBOX: tuple[float, float, float, float] = (112.5, -35.5, 129.1, -13.5)

#: The stub signature measured live 2026-08-15.
_STUB_BBOX = [-180.0, -90.0, 180.0, 90.0]

#: Source-level transport policy for the DEA Explorer (public, unauthed,
#: modest fan-out: one worker per collection).
DEA_RETRY_POLICY = RetryPolicy(max_workers=4)

_USER_AGENT = "wa-mine-rehab-monitor/0.1 (github.com/jazzdos/wa-mine-rehab-monitor)"

#: Items per page requested from the paged items endpoint.
PAGE_LIMIT = 200

#: Hard page ceiling per collection -- a runaway `next`-link loop must fail
#: loudly, not fetch forever.
MAX_PAGES = 500


class CatalogueValidationError(Exception):
    """A captured collection or item set failed a health check."""


def collection_url(collection_id: str) -> str:
    return f"{DEA_STAC_API_ROOT}/collections/{collection_id}"


def items_url(collection_id: str) -> str:
    return f"{DEA_STAC_API_ROOT}/collections/{collection_id}/items"


def new_dea_client() -> HttpClient:
    return HttpClient(DEA_RETRY_POLICY, headers={"User-Agent": _USER_AGENT})


def validate_collection_json(payload: Mapping[str, Any], spec: SourceSpec) -> dict[str, Any]:
    """Validate a captured collection JSON against its pinned spec.

    Returns a summary dict for the snapshot's catalogue summary. Raises
    CatalogueValidationError naming the first failed check.
    """
    got_id = payload.get("id")
    if got_id != spec.collection_id:
        raise CatalogueValidationError(
            f"collection id mismatch: expected {spec.collection_id!r}, got {got_id!r}"
        )
    extent = payload.get("extent") or {}
    spatial = ((extent.get("spatial") or {}).get("bbox") or [None])[0]
    temporal = ((extent.get("temporal") or {}).get("interval") or [None])[0]
    if temporal is None or all(bound is None for bound in temporal):
        raise CatalogueValidationError(
            f"{spec.collection_id}: temporal extent absent or null -- the "
            f"empty-stub signature; a stub answers HTTP 200 and carries no data"
        )
    if spatial == _STUB_BBOX:
        raise CatalogueValidationError(
            f"{spec.collection_id}: global unbounded spatial extent -- the "
            f"empty-stub signature; a stub answers HTTP 200 and carries no data"
        )
    got_licence = payload.get("license")
    if got_licence != spec.licence_state:
        raise CatalogueValidationError(
            f"{spec.collection_id}: captured licence {got_licence!r} does not "
            f"match the pinned licence record {spec.licence_state!r} -- "
            f"re-adjudicate before any fetch proceeds"
        )
    return {
        "collection_id": spec.collection_id,
        "stac_url": collection_url(spec.collection_id),
        "license": got_licence,
        "temporal_extent": list(temporal),
        "spatial_extent": list(spatial) if spatial else None,
        # D13 C2 records the required assets alongside the collection: the
        # spec's declaration at capture time, so a later reader can tell
        # which asset set the fetch was validating against.
        "required_assets": list(spec.asset_roles),
    }


def validate_items(items: Sequence[Mapping[str, Any]], spec: SourceSpec) -> dict[str, Any]:
    """Validate a collection's full fetched item set.

    Zero items is a refusal (a stub or a wrong bbox, never a healthy
    catalogue); duplicate IDs fail reconciliation rather than inflating
    coverage; every item must carry the spec's required asset keys.
    """
    if not items:
        raise CatalogueValidationError(
            f"{spec.collection_id}: 0 items fetched -- an existing-but-empty "
            f"collection is the stub failure shape, not a healthy catalogue"
        )
    seen: set[str] = set()
    duplicates: list[str] = []
    years: set[int] = set()
    for item in items:
        item_id = str(item.get("id"))
        if item_id in seen:
            duplicates.append(item_id)
        seen.add(item_id)
        assets = item.get("assets") or {}
        missing = [role for role in spec.asset_roles if role not in assets]
        if missing:
            raise CatalogueValidationError(
                f"{spec.collection_id}: item {item_id} missing required asset(s) {missing}"
            )
        stamp = (item.get("properties") or {}).get("datetime")
        if not stamp:
            raise CatalogueValidationError(
                f"{spec.collection_id}: item {item_id} has no properties.datetime "
                f"-- cannot derive its epoch year"
            )
        try:
            years.add(int(str(stamp)[:4]))
        except ValueError:
            raise CatalogueValidationError(
                f"{spec.collection_id}: item {item_id} has an unparsable "
                f"properties.datetime {stamp!r}"
            ) from None
    if duplicates:
        raise CatalogueValidationError(
            f"{spec.collection_id}: {len(duplicates)} duplicate item id(s) "
            f"across pages (first: {duplicates[0]}) -- duplicates inflate "
            f"epoch coverage, so the fetch refuses rather than deduplicating"
        )
    return {
        "collection_id": spec.collection_id,
        "n_items": len(items),
        "years": sorted(years),
    }


def _next_link(page: Mapping[str, Any]) -> str | None:
    for link in page.get("links") or []:
        if link.get("rel") == "next" and link.get("href"):
            return str(link["href"])
    return None


def fetch_collection_catalogue(
    client: Any, spec: SourceSpec
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Fetch one collection's JSON and every item page; validate both.

    Returns ``(collection_json, pages, summary)``. The collection is
    validated BEFORE any item request -- a stub is refused at one request,
    not after paging nothing. Pagination follows ``next`` links to a hard
    ``MAX_PAGES`` ceiling; item fetching is serial by construction (each
    page names the next), so concurrency lives at the ACROSS-collections
    level only. This caller is completeness-sensitive: it never passes
    ``tolerate_errors=True`` anywhere, and any page failure propagates.
    """
    collection = dict(client.get_json(collection_url(spec.collection_id)))
    collection_summary = validate_collection_json(collection, spec)

    pages: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    reported_item_count: int | None = None
    url: str | None = items_url(spec.collection_id)
    params: dict[str, Any] | None = {"limit": PAGE_LIMIT, "bbox": ",".join(map(str, WA_BBOX))}
    while url is not None:
        if len(pages) >= MAX_PAGES:
            raise CatalogueValidationError(
                f"{spec.collection_id}: exceeded {MAX_PAGES} item pages -- "
                f"refusing a possible next-link loop"
            )
        page = dict(client.get_json(url, params=params))
        params = None  # next links carry their own query
        pages.append(page)
        if reported_item_count is None and page.get("numberMatched") is not None:
            reported_item_count = int(page["numberMatched"])
        items.extend(page.get("features") or [])
        url = _next_link(page)

    item_summary = validate_items(items, spec)
    summary = {
        **collection_summary,
        **item_summary,
        "n_pages": len(pages),
        # D13 C2's "reported item count" is the SOURCE's own figure. The DEA
        # Explorer does not always emit `numberMatched`; when it does not,
        # this is null WITH a disclosure rather than the fetched count wearing
        # the source's label -- a count we produced is not a count the source
        # reported, and only the second can corroborate the first.
        "reported_item_count": reported_item_count,
        "reported_item_count_disclosure": (
            "reported-by-source" if reported_item_count is not None else "absent-from-source"
        ),
    }
    return collection, pages, summary
