"""D3 simulation protocol: frozen constants, loading, and digest (D13 D1).

The module pins the D13-immutable values as constants; `load_protocol`
refuses any config that drifts from them. The YAML is therefore not a
tuning surface -- it is the human-readable declaration whose canonical-JSON
sha256 (`protocol_digest`) is written to the frozen protocol artefact
before metric extraction, and checked by every downstream command.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import geopandas as gpd
import pandas as pd
import yaml

REQUIRED_SUPPORTS: tuple[int, ...] = (9, 16, 25, 36, 49, 64, 100, 144)
REQUIRED_REGIONS: tuple[str, ...] = ("pilbara", "goldfields_esperance", "other_wa")
REQUIRED_COMMODITY_GROUPS: tuple[str, ...] = (
    "iron_ore",
    "gold",
    "bauxite_alumina",
    "nickel",
    "mineral_sands",
    "other",
)
REQUIRED_CRITERIA: dict[str, float] = {
    "nbr_p90_abs_error_max": 0.03,
    "ndmi_p90_abs_error_max": 0.03,
    "fc_p90_abs_error_pp_max": 5.0,
    "spearman_median_min": 0.95,
    "computable_site_year_fraction_min": 0.90,
}
REQUIRED_REPLICATES = 100
REQUIRED_SHAPE_CLASSES = {"elongated_below": 0.20, "compact_at_least": 0.50}
REQUIRED_ADEQUACY = {"min_footprints": 10, "min_full_support_years": 10}
REQUIRED_SELECTION = {"use_all_below": 30, "select_n": 30}
MIN_FULL_SUPPORT_PX = 144
REQUIRED_PROCEDURE_KEYS = (
    "boundary_tie",
    "commodity_mode",
    "compactness",
    "stable_hash",
    "seed_template",
    "sampling_rank",
    "metric_formulas",
    "decode_rules",
    "full_support_year",
    "item_selection",
    "quantile_method",
)


class D3ProtocolError(ValueError):
    """The protocol config drifts from the D13-frozen values -- refused."""


@dataclass(frozen=True)
class Criteria:
    nbr_p90_abs_error_max: float
    ndmi_p90_abs_error_max: float
    fc_p90_abs_error_pp_max: float
    spearman_median_min: float
    computable_site_year_fraction_min: float


@dataclass(frozen=True)
class Adequacy:
    min_footprints: int
    min_full_support_years: int


@dataclass(frozen=True)
class Selection:
    use_all_below: int
    select_n: int


@dataclass(frozen=True)
class ShapeClasses:
    elongated_below: float
    compact_at_least: float


@dataclass(frozen=True)
class CommodityRule:
    group: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class D3Protocol:
    supports: tuple[int, ...]
    regions: tuple[str, ...]
    region_source_names: tuple[tuple[str, str], ...]
    commodity_groups: tuple[str, ...]
    commodity_token_rules: tuple[CommodityRule, ...]
    shape_classes: ShapeClasses
    adequacy: Adequacy
    selection: Selection
    replicates: int
    criteria: Criteria
    procedures: dict[str, str]


def load_protocol(path: Path) -> D3Protocol:
    """Load and validate the protocol YAML against the frozen constants."""
    raw = yaml.safe_load(Path(path).read_text())
    try:
        d3 = raw["d3"]
        protocol = D3Protocol(
            supports=tuple(d3["supports"]),
            regions=tuple(d3["regions"]),
            region_source_names=tuple(sorted(d3["region_source_names"].items())),
            commodity_groups=tuple(d3["commodity_groups"]),
            commodity_token_rules=tuple(
                CommodityRule(group=r["group"], tokens=tuple(r["tokens"]))
                for r in d3["commodity_token_rules"]
            ),
            shape_classes=ShapeClasses(**d3["shape_classes"]),
            adequacy=Adequacy(**d3["adequacy"]),
            selection=Selection(**d3["selection"]),
            replicates=int(d3["replicates"]),
            criteria=Criteria(**d3["criteria"]),
            procedures=dict(d3["procedures"]),
        )
    except (KeyError, TypeError) as exc:
        raise D3ProtocolError(f"malformed d3 protocol config: {exc}") from exc

    # Validate frozen fields
    if protocol.supports != REQUIRED_SUPPORTS:
        raise D3ProtocolError(f"supports {protocol.supports} != frozen {REQUIRED_SUPPORTS}")
    if protocol.regions != REQUIRED_REGIONS:
        raise D3ProtocolError(f"regions {protocol.regions} != frozen {REQUIRED_REGIONS}")
    if protocol.commodity_groups != REQUIRED_COMMODITY_GROUPS:
        raise D3ProtocolError(
            f"commodity groups {protocol.commodity_groups} != frozen {REQUIRED_COMMODITY_GROUPS}"
        )
    for name, value in REQUIRED_CRITERIA.items():
        if getattr(protocol.criteria, name) != value:
            raise D3ProtocolError(
                f"criteria.{name}={getattr(protocol.criteria, name)} != frozen {value}"
            )
    if protocol.replicates != REQUIRED_REPLICATES:
        raise D3ProtocolError(f"replicates {protocol.replicates} != frozen {REQUIRED_REPLICATES}")
    # Validate shape classes
    if protocol.shape_classes.elongated_below != REQUIRED_SHAPE_CLASSES["elongated_below"]:
        raise D3ProtocolError(
            f"shape_classes.elongated_below {protocol.shape_classes.elongated_below} "
            f"!= frozen {REQUIRED_SHAPE_CLASSES['elongated_below']}"
        )
    if protocol.shape_classes.compact_at_least != REQUIRED_SHAPE_CLASSES["compact_at_least"]:
        raise D3ProtocolError(
            f"shape_classes.compact_at_least {protocol.shape_classes.compact_at_least} "
            f"!= frozen {REQUIRED_SHAPE_CLASSES['compact_at_least']}"
        )
    # Validate adequacy
    if protocol.adequacy.min_footprints != REQUIRED_ADEQUACY["min_footprints"]:
        raise D3ProtocolError(
            f"adequacy.min_footprints {protocol.adequacy.min_footprints} "
            f"!= frozen {REQUIRED_ADEQUACY['min_footprints']}"
        )
    if protocol.adequacy.min_full_support_years != REQUIRED_ADEQUACY["min_full_support_years"]:
        raise D3ProtocolError(
            f"adequacy.min_full_support_years {protocol.adequacy.min_full_support_years} "
            f"!= frozen {REQUIRED_ADEQUACY['min_full_support_years']}"
        )
    # Validate selection
    if protocol.selection.use_all_below != REQUIRED_SELECTION["use_all_below"]:
        raise D3ProtocolError(
            f"selection.use_all_below {protocol.selection.use_all_below} "
            f"!= frozen {REQUIRED_SELECTION['use_all_below']}"
        )
    if protocol.selection.select_n != REQUIRED_SELECTION["select_n"]:
        raise D3ProtocolError(
            f"selection.select_n {protocol.selection.select_n} "
            f"!= frozen {REQUIRED_SELECTION['select_n']}"
        )
    # Validate procedures block
    missing_procedures = set(REQUIRED_PROCEDURE_KEYS) - set(protocol.procedures.keys())
    if missing_procedures:
        raise D3ProtocolError(f"procedures missing required keys: {sorted(missing_procedures)}")

    rule_groups = {rule.group for rule in protocol.commodity_token_rules}
    unknown = rule_groups - set(protocol.commodity_groups)
    if unknown:
        raise D3ProtocolError(f"token rules name unknown groups: {sorted(unknown)}")
    return protocol


def _canonical(value: object) -> object:
    """Convert a Python object to a canonical JSON-serializable form."""
    if isinstance(value, tuple):
        return [_canonical(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: _canonical(getattr(value, name)) for name in sorted(value.__dataclass_fields__)
        }
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    return value


def canonical_protocol(protocol: D3Protocol) -> dict:
    """Return the protocol's canonical dictionary form for digest/JSON."""
    return cast(dict, _canonical(protocol))


def protocol_digest(protocol: D3Protocol) -> str:
    """sha256 of the protocol's canonical JSON -- binds content, not bytes."""
    payload = json.dumps(canonical_protocol(protocol), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def classify_commodity(raw: str | None, protocol: D3Protocol) -> str:
    """Map raw MINEDEX commodity text to a frozen group. Refuses blank."""
    if raw is None or not str(raw).strip():
        raise D3ProtocolError("commodity is unclassified: null or blank raw value refused")
    lowered = str(raw).lower()
    for rule in protocol.commodity_token_rules:
        if any(token in lowered for token in rule.tokens):
            return rule.group
    return "other"


def shape_class(compactness: float, protocol: D3Protocol) -> str:
    """Polsby-Popper class. Compactness must be finite and in (0, 1+1e-9]."""
    if not math.isfinite(compactness) or compactness <= 0.0 or compactness > (1.0 + 1e-9):
        raise D3ProtocolError(f"compactness {compactness} is unclassifiable")
    if compactness < protocol.shape_classes.elongated_below:
        return "elongated"
    if compactness < protocol.shape_classes.compact_at_least:
        return "intermediate"
    return "compact"


def assign_regions(
    points: gpd.GeoDataFrame,
    regions: gpd.GeoDataFrame,
    protocol: D3Protocol,
) -> tuple[pd.Series, dict[str, int]]:
    """Assign each point a region stratum by covered_by membership.

    Supports both register site points and footprint representative_point()
    geometries. With non-overlapping region interiors (design decision 2),
    a multi-match indicates a shared boundary, which resolves to the
    lexicographically smallest source region name (deterministic,
    pre-registered, never value-dependent). A point covered by no region
    refuses, naming its site_ids. The disclosure tracks ambiguous boundary
    points for the record.
    """
    if str(points.crs) != str(regions.crs):
        raise D3ProtocolError(f"points CRS {points.crs} != regions CRS {regions.crs}")
    named = dict(protocol.region_source_names)  # stratum -> source name
    source_to_stratum = {v: k for k, v in named.items()}
    joined = gpd.sjoin(
        points[["site_id", "geometry"]],
        regions[["region_name", "geometry"]],
        how="left",
        predicate="covered_by",
    )
    matches = joined.groupby("site_id", sort=False)["region_name"].agg(list)

    # Collect all uncovered sites and refuse at once
    uncovered = []
    for site_id, names in matches.items():
        # Filter out NaN values
        names_clean = [n for n in names if isinstance(n, str)]
        if not names_clean:
            uncovered.append(site_id)

    if uncovered:
        raise D3ProtocolError(
            f"region is unclassified for site(s): {sorted(uncovered)} -- point(s) "
            "covered by no RDC polygon"
        )

    n_ambiguous = 0
    assigned: list[str] = []
    for site_id in points["site_id"]:
        names = [n for n in matches[site_id] if isinstance(n, str)]
        if len(names) > 1:
            n_ambiguous += 1
        chosen = min(names)
        assigned.append(source_to_stratum.get(chosen, "other_wa"))
    return (
        pd.Series(assigned, index=points.index, name="region"),
        {"n_ambiguous_boundary_points": n_ambiguous},
    )


Stratum = tuple[str, str, str]  # (region, commodity_group, shape_class)


def stratum_adequacy(
    footprints: pd.DataFrame, protocol: D3Protocol
) -> dict[Stratum, dict[str, object]]:
    """Per-stratum adequacy: >=10 footprints with >=10 full-support years.

    Returns the FULL frozen 54-stratum space (3 regions × 6 commodity groups
    × 3 shape classes), including zero-count strata with adequate=False.
    The adequacy unit is maus_id (footprint).
    """
    eligible = footprints[
        footprints["n_full_support_years"] >= protocol.adequacy.min_full_support_years
    ]
    counts = eligible.groupby(["region", "commodity_group", "shape_class"], sort=True)[
        "maus_id"
    ].nunique()

    # Generate all 54 strata
    out: dict[Stratum, dict[str, object]] = {}
    shape_classes_set = {"elongated", "intermediate", "compact"}
    for region in protocol.regions:
        for commodity_group in protocol.commodity_groups:
            for shape_cls in shape_classes_set:
                stratum = (region, commodity_group, shape_cls)
                n = int(counts.get(stratum, 0))
                out[stratum] = {
                    "n_footprints_meeting_years": n,
                    "adequate": n >= protocol.adequacy.min_footprints,
                }
    return out


def _stable_hash(maus_id: str) -> str:
    return hashlib.sha256(maus_id.encode("utf-8")).hexdigest()


def select_stratum_footprints(
    footprints: pd.DataFrame, protocol: D3Protocol
) -> dict[Stratum, tuple[str, ...]]:
    """Select simulation footprints per adequate stratum (D13 D1).

    10-29 qualifying footprints: use all. 30+: the 30 smallest by
    sha256(maus_id) hex. Returned tuples are sorted by maus_id so equality
    is order-insensitive; selection itself depends only on the hash order,
    never on input row order.
    """
    adequacy = stratum_adequacy(footprints, protocol)
    eligible = footprints[
        footprints["n_full_support_years"] >= protocol.adequacy.min_full_support_years
    ]
    selected: dict[Stratum, tuple[str, ...]] = {}
    for stratum, info in adequacy.items():
        if not info["adequate"]:
            continue
        members = sorted(
            eligible[
                (eligible["region"] == stratum[0])
                & (eligible["commodity_group"] == stratum[1])
                & (eligible["shape_class"] == stratum[2])
            ]["maus_id"].unique()
        )
        if len(members) >= protocol.selection.use_all_below:
            members = sorted(sorted(members, key=_stable_hash)[: protocol.selection.select_n])
        selected[stratum] = tuple(members)
    return selected
