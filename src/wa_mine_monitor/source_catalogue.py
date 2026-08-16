"""Declarative source specifications for remote collections.

Adapted from the dataplatform AdapterSpec/REGISTRY pattern under the D13 §3
reuse adjudication: ONLY the declarative frozen-spec contract is taken --
the dataplatform drivers are coupled to DuckLake/Polars/AWST and none of
that transfers. A spec here answers "which collection, what cadence, what
licence, which assets must every item carry" once, so fetch/validate code
reads the declaration instead of embedding the answers.

The four collection IDs were verified live 2026-08-15: the obvious
``*_nbart_gm_cyear_3`` names are EMPTY STUBS on the DEA Explorer STAC
(HTTP 200, global unbounded extent, zero items for any bbox) -- a pipeline
built on them passes existence checks and silently returns no data. Hence
``EMPTY_STUB_COLLECTION_PATTERN`` and its pin tests.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Regex identifying the retired stub family. Kept as a PATTERN (not a name
#: list) so `ga_ls9c_nbart_gm_cyear_3`-shaped future stubs are caught too.
EMPTY_STUB_COLLECTION_PATTERN = r"_nbart_gm_cyear"

#: Asset keys every geomedian item must carry (verified live from real item
#: `assets` dicts, 2026-08-15). The six reflectance bands make NBR, NDMI and
#: NDVI computable directly; `count` is the clear-observation support band.
GEOMEDIAN_REQUIRED_ASSETS: tuple[str, ...] = (
    "nbart_blue",
    "nbart_green",
    "nbart_red",
    "nbart_nir",
    "nbart_swir_1",
    "nbart_swir_2",
    "sdev",
    "edev",
    "bcdev",
    "count",
)

#: Asset keys every FC-percentile item must carry (verified live 2026-08-15).
FC_PC_REQUIRED_ASSETS: tuple[str, ...] = (
    "bs_pc_10",
    "bs_pc_50",
    "bs_pc_90",
    "pv_pc_10",
    "pv_pc_50",
    "pv_pc_90",
    "npv_pc_10",
    "npv_pc_50",
    "npv_pc_90",
    "qa",
)


@dataclass(frozen=True)
class SourceSpec:
    """One remote collection's frozen declaration."""

    source_id: str
    collection_id: str
    cadence: str
    region_scope: str
    licence_state: str
    asset_roles: tuple[str, ...]


DEA_COLLECTIONS: tuple[SourceSpec, ...] = (
    SourceSpec(
        source_id="dea_gm_ls5t",
        collection_id="ga_ls5t_gm_cyear_3",
        cadence="annual",
        region_scope="wa-statewide",
        licence_state="CC-BY-4.0",
        asset_roles=GEOMEDIAN_REQUIRED_ASSETS,
    ),
    SourceSpec(
        source_id="dea_gm_ls7e",
        collection_id="ga_ls7e_gm_cyear_3",
        cadence="annual",
        region_scope="wa-statewide",
        licence_state="CC-BY-4.0",
        asset_roles=GEOMEDIAN_REQUIRED_ASSETS,
    ),
    SourceSpec(
        source_id="dea_gm_ls8cls9c",
        collection_id="ga_ls8cls9c_gm_cyear_3",
        cadence="annual",
        region_scope="wa-statewide",
        licence_state="CC-BY-4.0",
        asset_roles=GEOMEDIAN_REQUIRED_ASSETS,
    ),
    SourceSpec(
        source_id="dea_fc_pc",
        collection_id="ga_ls_fc_pc_cyear_3",
        cadence="annual",
        region_scope="wa-statewide",
        licence_state="CC-BY-4.0",
        asset_roles=FC_PC_REQUIRED_ASSETS,
    ),
)

_BY_COLLECTION = {spec.collection_id: spec for spec in DEA_COLLECTIONS}
_BY_SOURCE = {spec.source_id: spec for spec in DEA_COLLECTIONS}


def spec_for_collection(collection_id: str) -> SourceSpec:
    """Return the pinned spec for ``collection_id``; KeyError on unknown."""
    return _BY_COLLECTION[collection_id]


def spec_for_source(source_id: str) -> SourceSpec:
    """Return the pinned spec for ``source_id``; KeyError on unknown.

    The coverage index keys captured items by ``source_id`` (the licence
    table's key) but D13 C3 requires the COLLECTION identity in the index
    frame; this is the one place the two identifiers are tied together.
    """
    return _BY_SOURCE[source_id]
