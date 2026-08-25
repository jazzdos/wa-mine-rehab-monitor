"""Tests for `wa_mine_monitor.register` and the `build-register` CLI command.

Tier 0 statewide register: one row per MINEDEX `Sites.csv` record (design
doc §4), rebuilt for the D6-D8 rulings
(`docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md`).
MINEDEX now arrives as two DASC bundles (an SHP zip this module never reads,
and a CSV zip carrying `Sites.csv`/`ProjectsOwners.csv`), never a
GeoPackage, and `owners_at_snapshot` (plural, D8) replaces the old
unsupported `operator_at_snapshot` semantics.

Fixture-first, same discipline as `tests/sources/test_minedex.py` and
`tests/sources/test_tenements.py`: toy pandas/geopandas frames built in-test,
never a committed binary fixture, and shared zip-assembly helpers come from
`tests/sources/_fixtures.py` (built by the DASC-acquisition batch precisely
so this file could reuse them).
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon
from typer.testing import CliRunner

from tests.sources._fixtures import shapefile_members, write_zip
from wa_mine_monitor import cli as cli_module
from wa_mine_monitor import export_gate, snapshots
from wa_mine_monitor import register as register_module
from wa_mine_monitor.cli import app
from wa_mine_monitor.register import (
    MINEDEX_SITES_SOURCE_CRS,
    OWNER_JOIN_DISCLOSURE_KEYS,
    OWNER_ROW_COMPOSITION_KEYS,
    REGISTER_LONLAT_CRS,
    REGISTER_SCHEMA,
    STAGE_TO_INCLUSION,
    TENEMENT_COUNT_DISCLOSURE_KEYS,
    NoSnapshotFoundError,
    build_reconciliation_report,
    build_register,
    count_rows_without_location,
    latest_snapshot,
    owner_join_disclosures,
    owner_row_composition,
    owners_by_project,
    reconcile_counts,
    register_counts,
    site_id_duplication_counts,
    tenement_count_disclosure,
)
from wa_mine_monitor.sources.minedex import MINEDEX_CSV_ZIP_FILENAME
from wa_mine_monitor.sources.tenements import TENEMENTS_SHAPEFILE_BASENAME, TENEMENTS_ZIP_FILENAME
from wa_mine_monitor.tables import write_table

runner = CliRunner()

_REGISTER_COLUMNS = [field.name for field in REGISTER_SCHEMA]

# =============================================================================
# Fixture builders -- Sites.csv / ProjectsOwners.csv / CurrentTenements.shp
# =============================================================================


def _sites_row(
    *,
    site_code: str = "M0001",
    title: str = "Test Site",
    commodities: str = "Bauxite",
    stage: str = "Operating",
    project_code: object = "P0001",
    lon: object = 116.0,
    lat: object = -32.0,
) -> dict[str, object]:
    return {
        "SiteCode": site_code,
        "Title": title,
        "Commodities": commodities,
        "Stage": stage,
        "ProjectCode": project_code,
        "Longitude": lon,
        "Latitude": lat,
    }


def _sites_df(rows: list[dict[str, object]]) -> pd.DataFrame:
    """A toy `Sites.csv`-shaped frame -- the DASC MINEDEX CSV bundle's own
    column names, verbatim (`register._REQUIRED_SITES_COLUMNS`)."""
    return pd.DataFrame(rows)


def _owners_row(
    *,
    project_code: str,
    owner_code: str,
    owner_name: str,
    holding_pct: object = 100.0,
    end_date: object = "",
) -> dict[str, object]:
    return {
        "ProjectCode": project_code,
        "OwnerCode": owner_code,
        "OwnerName": owner_name,
        "HoldingPct": holding_pct,
        "EndDate": end_date,
    }


def _owners_df(rows: list[dict[str, object]]) -> pd.DataFrame:
    """A toy `ProjectsOwners.csv`-shaped frame
    (`register._REQUIRED_OWNERS_COLUMNS`).

    An empty `rows` list still carries the declared columns (zero rows, not
    zero columns) -- matching a real CSV read of a header-only
    `ProjectsOwners.csv`, unlike a bare `pd.DataFrame([])`, which drops the
    schema entirely and would fail `owners_by_project`'s required-column
    check for a reason that has nothing to do with the case under test.
    """
    if not rows:
        return pd.DataFrame(
            columns=["ProjectCode", "OwnerCode", "OwnerName", "HoldingPct", "EndDate"]
        )
    return pd.DataFrame(rows)


def _tenement_polygon(lon: float, lat: float, *, half_size: float = 0.05) -> Polygon:
    return Polygon(
        [
            (lon - half_size, lat - half_size),
            (lon + half_size, lat - half_size),
            (lon + half_size, lat + half_size),
            (lon - half_size, lat + half_size),
        ]
    )


def _tenements_gdf(polygons: list[Polygon], *, crs: str = "EPSG:7844") -> gpd.GeoDataFrame:
    """A toy DMIRS-003-shaped GeoDataFrame -- CurrentTenements.shp's own
    measured CRS (`sources.tenements.TENEMENTS_EXPECTED_CRS`) by default."""
    return gpd.GeoDataFrame(
        {"FMT_TENID": [f"E70/{1000 + i}" for i in range(len(polygons))]},
        geometry=polygons,
        crs=crs,
    )


# =============================================================================
# owners_by_project (D8)
# =============================================================================


def test_owners_by_project_requires_declared_columns() -> None:
    owners_df = pd.DataFrame({"ProjectCode": ["P1"]})
    with pytest.raises(ValueError, match="OwnerCode"):
        owners_by_project(owners_df)


def test_owners_by_project_returns_an_empty_frame_when_no_current_rows() -> None:
    owners_df = _owners_df(
        [_owners_row(project_code="P1", owner_code="O1", owner_name="Acme", end_date="2020-01-01")]
    )
    result = owners_by_project(owners_df)
    assert result.empty
    assert result.index.name == "ProjectCode"
    assert list(result.columns) == [
        "owners_at_snapshot",
        "n_current_owners",
        "n_missing_holding_pct",
        "n_missing_owner_name",
        "n_duplicate_relationships",
    ]


def test_owners_by_project_treats_null_and_blank_enddate_as_current() -> None:
    owners_df = _owners_df(
        [
            _owners_row(project_code="P1", owner_code="O1", owner_name="Acme", end_date=None),
            _owners_row(project_code="P1", owner_code="O2", owner_name="Beta", end_date="   "),
        ]
    )
    result = owners_by_project(owners_df)
    assert result.loc["P1", "n_current_owners"] == 2


def test_owners_by_project_excludes_a_non_blank_enddate() -> None:
    owners_df = _owners_df(
        [
            _owners_row(project_code="P1", owner_code="O1", owner_name="Acme", end_date=""),
            _owners_row(
                project_code="P1", owner_code="O2", owner_name="Beta", end_date="2019-06-01"
            ),
        ]
    )
    result = owners_by_project(owners_df)
    assert result.loc["P1", "n_current_owners"] == 1
    assert result.loc["P1", "owners_at_snapshot"] == "Acme (100%)"


def test_owners_by_project_canonical_rendering_example() -> None:
    """The exact example the task pre-registers: a missing-holding owner
    sorted before a stated-percentage owner by normalized name."""
    owners_df = _owners_df(
        [
            _owners_row(
                project_code="P1",
                owner_code="O2",
                owner_name="Westgold Resources Limited",
                holding_pct=100.00,
            ),
            _owners_row(
                project_code="P1",
                owner_code="O1",
                owner_name="Big Bell Gold Operations Pty Ltd",
                holding_pct=None,
            ),
        ]
    )
    result = owners_by_project(owners_df)
    assert result.loc["P1", "owners_at_snapshot"] == (
        "Big Bell Gold Operations Pty Ltd (holding not stated); Westgold Resources Limited (100%)"
    )


def test_owners_by_project_sorts_by_ownercode_when_normalized_names_tie() -> None:
    owners_df = _owners_df(
        [
            _owners_row(project_code="P1", owner_code="O2", owner_name="  Acme  Ltd"),
            _owners_row(project_code="P1", owner_code="O1", owner_name="ACME LTD"),
        ]
    )
    result = owners_by_project(owners_df)
    # Both normalize to "acme ltd" -- OwnerCode "O1" sorts before "O2".
    assert result.loc["P1", "owners_at_snapshot"] == "ACME LTD (100%); Acme  Ltd (100%)"


@pytest.mark.parametrize(
    ("holding_pct", "expected"),
    [
        (100.00, "100"),
        (12.50, "12.5"),
        (0.00, "0"),
        (33.33, "33.33"),
        # Regression: `_format_holding_pct` used to route through
        # `float(text)` and Python's `:.2f` format, which ROUNDS any value
        # carrying more than two decimal digits -- a stated share is
        # preserved at its full precision, never rounded.
        (12.345, "12.345"),
        (33.333, "33.333"),
        (66.6667, "66.6667"),
        ("33.333333", "33.333333"),
        # A non-zero stated share must never be published as zero.
        (0.004, "0.004"),
    ],
)
def test_owners_by_project_trims_holding_pct(holding_pct: float, expected: str) -> None:
    owners_df = _owners_df(
        [
            _owners_row(
                project_code="P1", owner_code="O1", owner_name="Acme", holding_pct=holding_pct
            )
        ]
    )
    result = owners_by_project(owners_df)
    assert result.loc["P1", "owners_at_snapshot"] == f"Acme ({expected}%)"


@pytest.mark.parametrize("holding_pct", [None, "", "   ", "Unknown", "unknown", "UNKNOWN"])
def test_owners_by_project_renders_missing_holding_as_not_stated(holding_pct: object) -> None:
    owners_df = _owners_df(
        [
            _owners_row(
                project_code="P1", owner_code="O1", owner_name="Acme", holding_pct=holding_pct
            )
        ]
    )
    result = owners_by_project(owners_df)
    assert result.loc["P1", "owners_at_snapshot"] == "Acme (holding not stated)"
    assert result.loc["P1", "n_missing_holding_pct"] == 1


@pytest.mark.parametrize("owner_name", [None, "", "   "])
def test_owners_by_project_renders_missing_owner_name_as_not_stated(
    owner_name: object,
) -> None:
    """Regression: a null `OwnerName` used to crash `owners_by_project` with
    an uncaught `AttributeError` on `name.strip()` -- `deduped["OwnerName"]
    .astype(str)` does not stringify `pd.NA` at pandas 3.0.5, it yields the
    "str" extension dtype with the null surfacing as `float("nan")` on
    iteration, which has no `.strip()`."""
    owners_df = _owners_df(
        [_owners_row(project_code="P1", owner_code="O1", owner_name=owner_name, holding_pct=50.0)]
    )
    result = owners_by_project(owners_df)
    assert result.loc["P1", "owners_at_snapshot"] == "(owner name not stated) (50%)"
    assert result.loc["P1", "n_missing_owner_name"] == 1


def test_owners_by_project_counts_current_owners_and_missing_holding_pct() -> None:
    owners_df = _owners_df(
        [
            _owners_row(project_code="P1", owner_code="O1", owner_name="Acme", holding_pct=60.0),
            _owners_row(project_code="P1", owner_code="O2", owner_name="Beta", holding_pct=None),
            _owners_row(project_code="P1", owner_code="O3", owner_name="Gamma", holding_pct=40.0),
        ]
    )
    result = owners_by_project(owners_df)
    assert result.loc["P1", "n_current_owners"] == 3
    assert result.loc["P1", "n_missing_holding_pct"] == 1


def test_owners_by_project_dedups_duplicate_relationships_deterministically() -> None:
    """The FIRST occurrence (by original row order, under a stable sort) of
    a repeated (ProjectCode, OwnerCode) pair is kept -- never an unspecified
    one, and never the last."""
    owners_df = _owners_df(
        [
            _owners_row(
                project_code="P1", owner_code="O1", owner_name="First Seen", holding_pct=50.0
            ),
            _owners_row(
                project_code="P1", owner_code="O1", owner_name="Second Seen", holding_pct=99.0
            ),
        ]
    )
    result = owners_by_project(owners_df)
    assert result.loc["P1", "n_current_owners"] == 1
    assert result.loc["P1", "n_duplicate_relationships"] == 1
    assert result.loc["P1", "owners_at_snapshot"] == "First Seen (50%)"


def test_owners_by_project_n_duplicate_relationships_is_zero_with_no_repeats() -> None:
    owners_df = _owners_df(
        [
            _owners_row(project_code="P1", owner_code="O1", owner_name="Acme"),
            _owners_row(project_code="P1", owner_code="O2", owner_name="Beta"),
        ]
    )
    result = owners_by_project(owners_df)
    assert result.loc["P1", "n_duplicate_relationships"] == 0


def test_owners_by_project_a_project_with_only_ended_rows_is_absent_from_the_index() -> None:
    owners_df = _owners_df(
        [
            _owners_row(project_code="P1", owner_code="O1", owner_name="Acme", end_date=""),
            _owners_row(
                project_code="P2", owner_code="O2", owner_name="Beta", end_date="2020-01-01"
            ),
        ]
    )
    result = owners_by_project(owners_df)
    assert "P1" in result.index
    assert "P2" not in result.index


# =============================================================================
# owner_join_disclosures (D8)
# =============================================================================


def test_owner_join_disclosures_requires_a_project_code_column() -> None:
    with pytest.raises(ValueError, match="ProjectCode"):
        owner_join_disclosures(pd.DataFrame({"SiteCode": ["M1"]}), _owners_df([]))


def test_owner_join_disclosures_exercises_every_declared_category() -> None:
    """One fixture, every one of the six declared categories fired at least
    once, counts asserted exactly. See this test's inline layout for which
    site/project drives which count.
    """
    sites_df = _sites_df(
        [
            # Missing ProjectCode entirely.
            _sites_row(site_code="S1", project_code=None),
            # Present ProjectCode, but ProjectsOwners.csv carries no CURRENT
            # relationship for it at all -- unmatched == no current owner.
            _sites_row(site_code="S2", project_code="PX"),
            # Matched to a project with TWO current owners.
            _sites_row(site_code="S3", project_code="PY"),
            # Matched to a project with ONE current owner, holding not
            # stated.
            _sites_row(site_code="S4", project_code="PZ"),
        ]
    )
    owners_df = _owners_df(
        [
            _owners_row(project_code="PY", owner_code="OA", owner_name="Owner A", holding_pct=60.0),
            _owners_row(project_code="PY", owner_code="OB", owner_name="Owner B", holding_pct=40.0),
            _owners_row(project_code="PZ", owner_code="OC", owner_name="Owner C", holding_pct=None),
            # PW is never cited by any site -- exercises the duplicate count
            # over its OWN population (every current owners_df row), not a
            # site-matched population.
            _owners_row(
                project_code="PW", owner_code="OD", owner_name="Owner D", holding_pct=100.0
            ),
            _owners_row(
                project_code="PW", owner_code="OD", owner_name="Owner D", holding_pct=100.0
            ),
        ]
    )

    disclosures = owner_join_disclosures(sites_df, owners_df)

    assert disclosures == {
        "sites_total": 4,
        "n_sites_missing_project_code": 1,
        "n_sites_unmatched_project_code": 1,
        "n_sites_no_current_owner": 1,
        "n_sites_multiple_current_owners": 1,
        "n_projects_missing_holding_pct": 1,
        "n_duplicate_current_owner_relationships": 1,
    }
    assert set(disclosures) == set(OWNER_JOIN_DISCLOSURE_KEYS)


def test_owner_join_disclosures_all_zero_on_a_clean_join() -> None:
    sites_df = _sites_df([_sites_row(site_code="S1", project_code="P1")])
    owners_df = _owners_df(
        [_owners_row(project_code="P1", owner_code="O1", owner_name="Acme", holding_pct=100.0)]
    )
    disclosures = owner_join_disclosures(sites_df, owners_df)
    assert disclosures["sites_total"] == 1
    assert disclosures["n_sites_missing_project_code"] == 0
    assert disclosures["n_sites_unmatched_project_code"] == 0
    assert disclosures["n_sites_no_current_owner"] == 0
    assert disclosures["n_sites_multiple_current_owners"] == 0
    assert disclosures["n_projects_missing_holding_pct"] == 0
    assert disclosures["n_duplicate_current_owner_relationships"] == 0


# =============================================================================
# owner_row_composition (D12.2)
# =============================================================================


def test_owner_row_composition_splits_current_and_ended_rows() -> None:
    """D12.2: the 2026-08-14 extract's `ProjectsOwners.csv` happens to be
    CURRENT-only, so D8's `owners_at_snapshot` 'current owner' filter has
    had no bite against it -- nothing pinned or disclosed that property.
    This mirrors `sources.minedex.validate_minedex_bundles`'s identical
    disclosure, but computed directly off the `ProjectsOwners.csv` frame
    `build-register` already has in hand, since the register manifest is
    where a reader adjudicates owner semantics."""
    owners_df = _owners_df(
        [
            _owners_row(project_code="P1", owner_code="O1", owner_name="Owner A", end_date=""),
            _owners_row(
                project_code="P1", owner_code="O2", owner_name="Owner B", end_date="31/12/2020"
            ),
            _owners_row(project_code="P2", owner_code="O3", owner_name="Owner C", end_date=""),
        ]
    )

    composition = owner_row_composition(owners_df)

    assert composition == {
        "owner_rows_total": 3,
        "n_owner_rows_current": 2,
        "n_owner_rows_ended": 1,
    }
    assert set(composition) == set(OWNER_ROW_COMPOSITION_KEYS)


def test_owner_row_composition_all_current_on_the_empty_extract_shape() -> None:
    owners_df = _owners_df(
        [_owners_row(project_code="P1", owner_code="O1", owner_name="Owner A", end_date="")]
    )
    composition = owner_row_composition(owners_df)
    assert composition["owner_rows_total"] == 1
    assert composition["n_owner_rows_current"] == 1
    assert composition["n_owner_rows_ended"] == 0


def test_owner_row_composition_requires_an_end_date_column() -> None:
    with pytest.raises(ValueError, match="EndDate"):
        owner_row_composition(pd.DataFrame({"ProjectCode": ["P1"]}))


# =============================================================================
# build_register: output shape, columns, row survival
# =============================================================================


def test_build_register_output_columns_are_exact() -> None:
    df = build_register(_sites_df([_sites_row()]), _owners_df([]), _tenements_gdf([]), "2026-08-15")
    assert list(df.columns) == _REGISTER_COLUMNS


def test_build_register_one_row_per_sites_record() -> None:
    rows = [_sites_row(site_code=f"M{i:04d}", lon=116.0 + i, lat=-32.0 - i) for i in range(5)]
    df = build_register(_sites_df(rows), _owners_df([]), _tenements_gdf([]), "2026-08-15")
    assert len(df) == 5
    assert sorted(df["site_id"]) == [f"M{i:04d}" for i in range(5)]


def test_build_register_requires_declared_sites_columns() -> None:
    sites_df = _sites_df([_sites_row()]).rename(columns={"SiteCode": "Code"})
    with pytest.raises(ValueError, match="SiteCode"):
        build_register(sites_df, _owners_df([]), _tenements_gdf([]), "2026-08-15")


def test_build_register_site_id_name_commodity_stage_are_verbatim() -> None:
    df = build_register(
        _sites_df(
            [
                _sites_row(
                    site_code="M9",
                    title="Ora Banda Pit",
                    commodities="Gold",
                    stage="Operating",
                )
            ]
        ),
        _owners_df([]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    assert df.loc[0, "site_id"] == "M9"
    assert df.loc[0, "site_name"] == "Ora Banda Pit"
    assert df.loc[0, "commodity"] == "Gold"
    assert df.loc[0, "stage"] == "Operating"


def test_build_register_snapshot_date_is_filled_with_the_argument() -> None:
    df = build_register(_sites_df([_sites_row()]), _owners_df([]), _tenements_gdf([]), "2019-07-01")
    assert (df["snapshot_date"] == "2019-07-01").all()


# --- owners_at_snapshot via the ProjectCode join --------------------------------


def test_build_register_owners_at_snapshot_populated_for_a_matched_project() -> None:
    df = build_register(
        _sites_df([_sites_row(site_code="M1", project_code="P1")]),
        _owners_df(
            [_owners_row(project_code="P1", owner_code="O1", owner_name="Acme", holding_pct=100.0)]
        ),
        _tenements_gdf([]),
        "2026-08-15",
    )
    assert df.loc[0, "owners_at_snapshot"] == "Acme (100%)"


def test_build_register_owners_at_snapshot_is_na_when_project_code_missing() -> None:
    df = build_register(
        _sites_df([_sites_row(site_code="M1", project_code=None)]),
        _owners_df([_owners_row(project_code="P1", owner_code="O1", owner_name="Acme")]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    assert pd.isna(df.loc[0, "owners_at_snapshot"])


def test_build_register_owners_at_snapshot_is_na_when_project_code_unmatched() -> None:
    """A present `ProjectCode` that names no CURRENT owner relationship at
    all (never in `ProjectsOwners.csv`, or every relationship it once had
    has ended) resolves to `pd.NA`, the same as a missing code -- D8's
    ruling reserves null for "no resolvable current owner"."""
    df = build_register(
        _sites_df([_sites_row(site_code="M1", project_code="PX")]),
        _owners_df([_owners_row(project_code="P1", owner_code="O1", owner_name="Acme")]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    assert pd.isna(df.loc[0, "owners_at_snapshot"])


def test_build_register_owners_at_snapshot_na_when_all_relationships_ended() -> None:
    df = build_register(
        _sites_df([_sites_row(site_code="M1", project_code="P1")]),
        _owners_df(
            [
                _owners_row(
                    project_code="P1", owner_code="O1", owner_name="Acme", end_date="2020-01-01"
                )
            ]
        ),
        _tenements_gdf([]),
        "2026-08-15",
    )
    assert pd.isna(df.loc[0, "owners_at_snapshot"])


def test_build_register_propagates_owners_by_project_column_error() -> None:
    bad_owners_df = pd.DataFrame({"ProjectCode": ["P1"]})
    with pytest.raises(ValueError, match="OwnerCode"):
        build_register(_sites_df([_sites_row()]), bad_owners_df, _tenements_gdf([]), "2026-08-15")


# --- lon/lat: constructed from Latitude/Longitude, declared CRS, reprojected ----


def test_build_register_lon_lat_come_from_latitude_longitude() -> None:
    df = build_register(
        _sites_df([_sites_row(lon=116.25, lat=-32.75)]),
        _owners_df([]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    assert df.loc[0, "lon"] == pytest.approx(116.25, abs=1e-6)
    assert df.loc[0, "lat"] == pytest.approx(-32.75, abs=1e-6)


def test_register_lonlat_crs_is_epsg_4326() -> None:
    assert REGISTER_LONLAT_CRS == "EPSG:4326"


def test_minedex_sites_source_crs_is_epsg_7844() -> None:
    assert MINEDEX_SITES_SOURCE_CRS == "EPSG:7844"


# --- rows with no usable location survive and are disclosed --------------------


def test_build_register_null_coordinate_row_survives_with_lon_lat_na() -> None:
    df = build_register(
        _sites_df(
            [
                _sites_row(site_code="M0001", lon=116.0, lat=-32.0),
                _sites_row(site_code="M0002", lon=None, lat=None),
            ]
        ),
        _owners_df([]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    assert len(df) == 2
    row = df.set_index("site_id").loc["M0002"]
    assert pd.isna(row["lon"])
    assert pd.isna(row["lat"])


def test_build_register_null_coordinate_row_has_not_computed_tenements_intersecting() -> None:
    """A coordinate-less row takes no part in the spatial join -- D12.2:
    `n_tenements_intersecting` is NOT COMPUTED (null) for it, never a
    fabricated zero. A diagnostic that could not be computed is not a
    diagnostic that fired."""
    df = build_register(
        _sites_df(
            [
                _sites_row(site_code="M0001", lon=116.0, lat=-32.0),
                _sites_row(site_code="M0002", lon=None, lat=None),
            ]
        ),
        _owners_df([]),
        _tenements_gdf(
            [_tenement_polygon(116.0, -32.0), _tenement_polygon(116.0, -32.0, half_size=0.5)]
        ),
        "2026-08-15",
    )
    row = df.set_index("site_id").loc["M0002"]
    assert pd.isna(row["n_tenements_intersecting"])


def test_build_register_a_single_null_coordinate_field_also_counts() -> None:
    """Latitude present but Longitude null (or vice versa) is no more usable
    than both being null."""
    df = build_register(
        _sites_df([_sites_row(site_code="M0001", lon=None, lat=-32.0)]),
        _owners_df([]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    assert pd.isna(df.loc[0, "lon"])
    assert pd.isna(df.loc[0, "lat"])


def test_count_rows_without_location_counts_null_lon_or_lat() -> None:
    df = build_register(
        _sites_df(
            [
                _sites_row(site_code="M0001", lon=116.0, lat=-32.0),
                _sites_row(site_code="M0002", lon=None, lat=None),
                _sites_row(site_code="M0003", lon=None, lat=-30.0),
            ]
        ),
        _owners_df([]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    assert count_rows_without_location(df) == 2


def test_count_rows_without_location_is_zero_on_a_fully_located_register() -> None:
    df = build_register(_sites_df([_sites_row()]), _owners_df([]), _tenements_gdf([]), "2026-08-15")
    assert count_rows_without_location(df) == 0


# --- tenements_gdf CRS enforcement ---------------------------------------------


def test_build_register_refuses_when_tenements_gdf_crs_is_unset_and_non_empty() -> None:
    tenements_gdf = _tenements_gdf([_tenement_polygon(116.0, -32.0)])
    tenements_gdf = tenements_gdf.set_crs(None, allow_override=True)
    with pytest.raises(ValueError, match="tenements_gdf.crs is not set"):
        build_register(
            _sites_df([_sites_row(lon=116.0, lat=-32.0)]),
            _owners_df([]),
            tenements_gdf,
            "2026-08-15",
        )


def test_build_register_allows_an_empty_crs_less_tenements_gdf() -> None:
    tenements_gdf = _tenements_gdf([]).set_crs(None, allow_override=True)
    df = build_register(
        _sites_df([_sites_row(lon=116.0, lat=-32.0)]), _owners_df([]), tenements_gdf, "2026-08-15"
    )
    assert df.loc[0, "n_tenements_intersecting"] == 0


def test_build_register_reprojects_a_differently_declared_tenements_crs() -> None:
    tenements_gdf = _tenements_gdf([_tenement_polygon(116.0, -32.0)]).to_crs("EPSG:3577")
    assert abs(tenements_gdf.geometry.iloc[0].centroid.x) > 1000.0
    df = build_register(
        _sites_df([_sites_row(lon=116.0, lat=-32.0)]), _owners_df([]), tenements_gdf, "2026-08-15"
    )
    assert df.loc[0, "n_tenements_intersecting"] == 1


# D12.3 triage, finding 5: "`_count_intersecting_tenements` `groupby(level=0)`
# conflates a non-unique index -- unreachable via the CLI (`build_register`
# always constructs `located_gdf` off a de-duplicated `RangeIndex` subset),
# but a latent trap for any other caller. `groupby(level=0)` groups by INDEX
# LABEL, not by row -- two rows sharing an index value are silently merged
# into one group, so a duplicate-index frame reports each duplicated row's
# count as the UNION of both rows' matches rather than each row's own.


def test_count_intersecting_tenements_raises_on_a_non_unique_index() -> None:
    tenements_gdf = _tenements_gdf([_tenement_polygon(116.0, -32.0)])
    located_gdf = gpd.GeoDataFrame(
        index=[0, 0, 1],
        geometry=[
            _tenement_polygon(116.0, -32.0).centroid,
            _tenement_polygon(116.0, -32.0).centroid,
            _tenement_polygon(120.0, -30.0).centroid,
        ],
        crs="EPSG:7844",
    )

    with pytest.raises(ValueError, match="non-unique index"):
        register_module._count_intersecting_tenements(located_gdf, tenements_gdf)


def test_count_intersecting_tenements_names_the_duplicated_index_values() -> None:
    tenements_gdf = _tenements_gdf([_tenement_polygon(116.0, -32.0)])
    located_gdf = gpd.GeoDataFrame(
        index=[7, 7, 3, 3],
        geometry=[
            _tenement_polygon(116.0, -32.0).centroid,
            _tenement_polygon(116.0, -32.0).centroid,
            _tenement_polygon(120.0, -30.0).centroid,
            _tenement_polygon(120.0, -30.0).centroid,
        ],
        crs="EPSG:7844",
    )

    with pytest.raises(ValueError, match=r"\[3, 7\]"):
        register_module._count_intersecting_tenements(located_gdf, tenements_gdf)


def test_count_intersecting_tenements_computes_each_row_independently_on_a_unique_index() -> None:
    # The counterfactual the guard protects: a unique-index frame with two
    # DISTINCT rows sharing the same point still gets each row's own count,
    # never a merged one.
    tenements_gdf = _tenements_gdf([_tenement_polygon(116.0, -32.0)])
    located_gdf = gpd.GeoDataFrame(
        index=[10, 11],
        geometry=[
            _tenement_polygon(116.0, -32.0).centroid,
            _tenement_polygon(116.0, -32.0).centroid,
        ],
        crs="EPSG:7844",
    )

    counts = register_module._count_intersecting_tenements(located_gdf, tenements_gdf)

    assert counts.loc[10] == 1
    assert counts.loc[11] == 1


# --- inclusion_status classification via STAGE_TO_INCLUSION --------------------


@pytest.mark.parametrize(("stage", "expected"), sorted(STAGE_TO_INCLUSION.items()))
def test_build_register_classifies_every_declared_stage_value(stage: str, expected: str) -> None:
    df = build_register(
        _sites_df([_sites_row(stage=stage)]), _owners_df([]), _tenements_gdf([]), "2026-08-15"
    )
    assert df.loc[0, "inclusion_status"] == expected


def test_build_register_maps_an_unmapped_stage_to_other_and_keeps_the_row() -> None:
    df = build_register(
        _sites_df([_sites_row(stage="Some Stage Nobody Declared")]),
        _owners_df([]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    assert len(df) == 1
    assert df.loc[0, "inclusion_status"] == "other"


def test_stage_to_inclusion_carries_the_measured_dasc_vocabulary() -> None:
    """Decisions doc §3: `Shut`/`Undeveloped` are no longer absent from the
    mapping, and `Under Development` deliberately lands in `other`."""
    assert STAGE_TO_INCLUSION == {
        "Operating": "operating",
        "Care and Maintenance": "care_and_maintenance",
        "Shut": "closed",
        "Undeveloped": "deposit",
        "Proposed": "prospect",
        "Under Development": "other",
    }


# --- n_tenements_intersecting via spatial join --------------------------------


def test_build_register_counts_zero_when_no_tenement_intersects() -> None:
    df = build_register(
        _sites_df([_sites_row(lon=120.0, lat=-30.0)]),
        _owners_df([]),
        _tenements_gdf([_tenement_polygon(116.0, -32.0)]),
        "2026-08-15",
    )
    assert df.loc[0, "n_tenements_intersecting"] == 0


def test_build_register_counts_one_when_one_tenement_intersects() -> None:
    df = build_register(
        _sites_df([_sites_row(lon=116.0, lat=-32.0)]),
        _owners_df([]),
        _tenements_gdf([_tenement_polygon(116.0, -32.0)]),
        "2026-08-15",
    )
    assert df.loc[0, "n_tenements_intersecting"] == 1


def test_build_register_counts_multiple_overlapping_tenements() -> None:
    df = build_register(
        _sites_df([_sites_row(lon=116.0, lat=-32.0)]),
        _owners_df([]),
        _tenements_gdf(
            [_tenement_polygon(116.0, -32.0), _tenement_polygon(116.01, -32.0, half_size=0.08)]
        ),
        "2026-08-15",
    )
    assert df.loc[0, "n_tenements_intersecting"] == 2


def test_build_register_counts_per_site_independently() -> None:
    df = build_register(
        _sites_df(
            [
                _sites_row(site_code="M0001", lon=116.0, lat=-32.0),
                _sites_row(site_code="M0002", lon=120.0, lat=-30.0),
            ]
        ),
        _owners_df([]),
        _tenements_gdf([_tenement_polygon(116.0, -32.0)]),
        "2026-08-15",
    )
    by_id = df.set_index("site_id")
    assert by_id.loc["M0001", "n_tenements_intersecting"] == 1
    assert by_id.loc["M0002", "n_tenements_intersecting"] == 0


def test_build_register_distinguishes_not_computed_from_a_genuine_zero() -> None:
    """D12.2: a coordinate-less site's `n_tenements_intersecting` is null
    (never computed); a located site with no intersecting tenement is a
    genuine 0. The two must never be conflated."""
    df = build_register(
        _sites_df(
            [
                _sites_row(site_code="M0001", lon=120.0, lat=-30.0),  # located, no intersection
                _sites_row(site_code="M0002", lon=None, lat=None),  # coordinate-less
            ]
        ),
        _owners_df([]),
        _tenements_gdf([_tenement_polygon(116.0, -32.0)]),
        "2026-08-15",
    )
    by_id = df.set_index("site_id")
    assert by_id.loc["M0001", "n_tenements_intersecting"] == 0
    assert not pd.isna(by_id.loc["M0001", "n_tenements_intersecting"])
    assert pd.isna(by_id.loc["M0002", "n_tenements_intersecting"])


def test_build_register_n_tenements_intersecting_dtype_is_nullable_integer() -> None:
    df = build_register(
        _sites_df(
            [
                _sites_row(site_code="M0001", lon=116.0, lat=-32.0),
                _sites_row(site_code="M0002", lon=None, lat=None),
            ]
        ),
        _owners_df([]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    assert str(df["n_tenements_intersecting"].dtype) == "Int64"


# =============================================================================
# tenement_count_disclosure (D12.2)
# =============================================================================


def test_tenement_count_disclosure_reconciles_and_separates_zero_from_not_computed() -> None:
    df = build_register(
        _sites_df(
            [
                _sites_row(site_code="M0001", lon=116.0, lat=-32.0),  # located, intersects
                _sites_row(site_code="M0002", lon=120.0, lat=-30.0),  # located, no intersection
                _sites_row(site_code="M0003", lon=None, lat=None),  # coordinate-less
            ]
        ),
        _owners_df([]),
        _tenements_gdf([_tenement_polygon(116.0, -32.0)]),
        "2026-08-15",
    )
    disclosure = tenement_count_disclosure(df)
    assert disclosure == {
        "sites_total": 3,
        "n_sites_tenement_count_computed": 2,
        "n_sites_tenement_count_zero": 1,
        "n_sites_tenement_count_not_computed": 1,
    }
    assert set(disclosure.keys()) == set(TENEMENT_COUNT_DISCLOSURE_KEYS)
    assert (
        disclosure["n_sites_tenement_count_computed"]
        + disclosure["n_sites_tenement_count_not_computed"]
        == disclosure["sites_total"]
    )


def test_tenement_count_disclosure_on_a_fully_located_register() -> None:
    df = build_register(_sites_df([_sites_row()]), _owners_df([]), _tenements_gdf([]), "2026-08-15")
    disclosure = tenement_count_disclosure(df)
    assert disclosure == {
        "sites_total": 1,
        "n_sites_tenement_count_computed": 1,
        "n_sites_tenement_count_zero": 1,
        "n_sites_tenement_count_not_computed": 0,
    }


# =============================================================================
# declared schema + export-gate geometry check
# =============================================================================


def test_register_writes_through_write_table_with_declared_schema(tmp_path: Path) -> None:
    df = build_register(_sites_df([_sites_row()]), _owners_df([]), _tenements_gdf([]), "2026-08-15")
    path = tmp_path / "register.parquet"

    write_table(df, path, REGISTER_SCHEMA)
    back = pd.read_parquet(path)

    assert list(back.columns) == _REGISTER_COLUMNS
    assert list(back["site_id"]) == ["M0001"]


def test_register_frame_is_flagged_geometry_bearing_by_its_lon_lat_columns() -> None:
    """`has_geometry` answers "does this frame carry geometry at all", not "is
    this frame safe to export" -- the register frame legitimately carries
    `lon`/`lat` (`REGISTER_SCHEMA`), and a point coordinate is geometry
    however it is spelled (`export_gate.COORDINATE_COLUMN_NAMES`), so
    `has_geometry` correctly returns `True` here. This is not a licence-gate
    breach: the register frame is never exported (`export_public` and
    `has_geometry` have no caller on a register frame anywhere in this
    tree), so no public artefact is affected by this result."""
    df = build_register(_sites_df([_sites_row()]), _owners_df([]), _tenements_gdf([]), "2026-08-15")
    assert export_gate.has_geometry(df) is True


def test_register_schema_has_nullable_owners_and_lonlat_types() -> None:
    fields = {field.name: field for field in REGISTER_SCHEMA}
    assert str(fields["owners_at_snapshot"].type) == "string"
    assert fields["owners_at_snapshot"].nullable
    assert str(fields["lon"].type) == "double"
    assert fields["lon"].nullable
    assert str(fields["lat"].type) == "double"
    assert fields["lat"].nullable


def test_register_schema_declares_n_tenements_intersecting_as_nullable_int64() -> None:
    """D12.2: `n_tenements_intersecting` must be able to carry NOT COMPUTED
    (null) separately from a genuine computed zero."""
    fields = {field.name: field for field in REGISTER_SCHEMA}
    assert str(fields["n_tenements_intersecting"].type) == "int64"
    assert fields["n_tenements_intersecting"].nullable


def test_register_write_table_preserves_not_computed_vs_zero_through_the_real_write_path(
    tmp_path: Path,
) -> None:
    """Through the REAL write/read-back path (`tables.write_table` /
    `pd.read_parquet`), never against the in-memory frame alone -- a
    nullable column is only proven nullable at the boundary that writes
    it."""
    df = build_register(
        _sites_df(
            [
                _sites_row(site_code="M0001", lon=116.0, lat=-32.0),  # located, no intersection
                _sites_row(site_code="M0002", lon=None, lat=None),  # coordinate-less
            ]
        ),
        _owners_df([]),
        _tenements_gdf([_tenement_polygon(200.0, 50.0)]),  # far away -- never intersects
        "2026-08-15",
    )
    path = tmp_path / "register.parquet"

    write_table(df, path, REGISTER_SCHEMA)
    back = pd.read_parquet(path)

    assert str(back["n_tenements_intersecting"].dtype) == "Int64"
    by_id = back.set_index("site_id")
    assert by_id.loc["M0001", "n_tenements_intersecting"] == 0
    assert pd.isna(by_id.loc["M0002", "n_tenements_intersecting"])


# =============================================================================
# register_counts / reconcile_counts / build_reconciliation_report
# =============================================================================


def test_register_counts_reconciles_against_its_own_total() -> None:
    df = build_register(
        _sites_df(
            [
                _sites_row(site_code="M0001", stage="Operating"),
                _sites_row(site_code="M0002", stage="Shut"),
                _sites_row(site_code="M0003", stage="Some Undeclared Stage"),
            ]
        ),
        _owners_df([]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    counts = register_counts(df)
    assert counts["total"] == 3
    assert counts["operating"] == 1
    assert counts["closed"] == 1
    assert counts["other"] == 1
    assert reconcile_counts(counts) == counts


def test_reconcile_counts_raises_naming_the_gap_on_a_corrupted_total() -> None:
    counts = {
        "operating": 2,
        "care_and_maintenance": 0,
        "closed": 1,
        "deposit": 0,
        "prospect": 0,
        "other": 0,
        "total": 4,
    }
    with pytest.raises(ValueError, match="reconcile"):
        reconcile_counts(counts)


def test_build_reconciliation_report_passes_on_a_clean_register() -> None:
    sites_df = _sites_df([_sites_row()])
    df = build_register(sites_df, _owners_df([]), _tenements_gdf([]), "2026-08-15")
    counts = register_counts(df)

    report = build_reconciliation_report(
        df, counts, minedex_feature_count=len(sites_df), tenements_feature_count=0
    )

    assert report.passed is True
    assert "PASS" in report.text


def test_build_reconciliation_report_fails_on_a_row_count_mismatch() -> None:
    sites_df = _sites_df([_sites_row()])
    df = build_register(sites_df, _owners_df([]), _tenements_gdf([]), "2026-08-15")
    counts = register_counts(df)

    report = build_reconciliation_report(
        df, counts, minedex_feature_count=len(sites_df) + 1, tenements_feature_count=0
    )

    assert report.passed is False
    assert "FAIL" in report.text


def test_build_reconciliation_report_renders_owner_join_disclosures() -> None:
    sites_df = _sites_df([_sites_row(project_code=None)])
    df = build_register(sites_df, _owners_df([]), _tenements_gdf([]), "2026-08-15")
    counts = register_counts(df)
    disclosures = owner_join_disclosures(sites_df, _owners_df([]))

    report = build_reconciliation_report(
        df,
        counts,
        minedex_feature_count=len(sites_df),
        tenements_feature_count=0,
        owner_join_disclosures=disclosures,
    )

    assert "## Owner-join disclosures" in report.text
    assert "n_sites_missing_project_code: 1" in report.text


def test_build_reconciliation_report_discloses_rows_without_a_location() -> None:
    sites_df = _sites_df(
        [
            _sites_row(site_code="M0001", lon=116.25, lat=-32.75),
            _sites_row(site_code="M0002", lon=None, lat=None),
        ]
    )
    df = build_register(sites_df, _owners_df([]), _tenements_gdf([]), "2026-08-15")
    counts = register_counts(df)

    report = build_reconciliation_report(
        df, counts, minedex_feature_count=len(sites_df), tenements_feature_count=0
    )

    assert "- Register rows with a usable lon/lat: 1" in report.text
    assert "- Register rows without a usable location (lon/lat null): 1" in report.text
    assert "n_sites_null_coordinates: 1" in report.text


def test_site_id_duplication_counts_on_a_clean_register_is_zero() -> None:
    df = build_register(
        _sites_df([_sites_row(site_code="M0001"), _sites_row(site_code="M0002")]),
        _owners_df([]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    counts = site_id_duplication_counts(df)
    assert counts == {"n_duplicate_site_id_values": 0, "n_site_id_rows_duplicated": 0}


def test_site_id_duplication_counts_names_distinct_values_and_total_rows() -> None:
    """`Sites.csv`'s own measured `SiteCode` duplication (D6/D8 decisions
    doc §1) reaches the register verbatim -- one duplicated site_id across
    3 rows, plus a second duplicated across 2, gives 2 distinct duplicated
    values across 5 total rows."""
    df = build_register(
        _sites_df(
            [
                _sites_row(site_code="M0001"),
                _sites_row(site_code="M0001"),
                _sites_row(site_code="M0001"),
                _sites_row(site_code="M0002"),
                _sites_row(site_code="M0002"),
                _sites_row(site_code="M0003"),
            ]
        ),
        _owners_df([]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    counts = site_id_duplication_counts(df)
    assert counts == {"n_duplicate_site_id_values": 2, "n_site_id_rows_duplicated": 5}


def test_build_reconciliation_report_discloses_site_id_duplication() -> None:
    df = build_register(
        _sites_df([_sites_row(site_code="M0001"), _sites_row(site_code="M0001")]),
        _owners_df([]),
        _tenements_gdf([]),
        "2026-08-15",
    )
    counts = register_counts(df)

    report = build_reconciliation_report(
        df, counts, minedex_feature_count=len(df), tenements_feature_count=0
    )

    assert "## site_id duplication" in report.text
    assert "n_duplicate_site_id_values: 1" in report.text
    assert "n_site_id_rows_duplicated: 2" in report.text
    # Disclosed, not refused -- this must not fail the reconciliation verdict.
    assert report.passed is True
    assert report.passed is True


# =============================================================================
# latest_snapshot
# =============================================================================


def test_latest_snapshot_returns_the_most_recent_dated_dir(tmp_path: Path) -> None:
    root = tmp_path / "data"
    for date_str in ["2026-07-01", "2026-08-15", "2026-01-01"]:
        (root / "raw" / "dmirs_001_minedex" / date_str).mkdir(parents=True)

    result = latest_snapshot(root, "dmirs_001_minedex")

    assert result == root / "raw" / "dmirs_001_minedex" / "2026-08-15"


def test_latest_snapshot_ignores_non_date_directories(tmp_path: Path) -> None:
    root = tmp_path / "data"
    (root / "raw" / "dmirs_001_minedex" / "2026-01-01").mkdir(parents=True)
    (root / "raw" / "dmirs_001_minedex" / "scratch").mkdir(parents=True)

    result = latest_snapshot(root, "dmirs_001_minedex")

    assert result.name == "2026-01-01"


def test_latest_snapshot_raises_naming_the_source_when_none_exists(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with pytest.raises(NoSnapshotFoundError, match="dmirs_001_minedex"):
        latest_snapshot(root, "dmirs_001_minedex")


def test_latest_snapshot_raises_when_the_raw_directory_itself_is_absent(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    with pytest.raises(NoSnapshotFoundError, match="dmirs_003_tenements"):
        latest_snapshot(root, "dmirs_003_tenements")


# =============================================================================
# build-register CLI command
# =============================================================================


def _write_config(tmp_path: Path, data_root: Path) -> Path:
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{data_root}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg_file


def _write_minedex_csv_zip(
    zip_path: Path, *, sites_df: pd.DataFrame, owners_df: pd.DataFrame
) -> Path:
    """The measured DASC BOM convention -- `utf-8-sig`, per D6/D8's
    decisions doc and `cli.build_register_cmd`'s own read."""
    members = {
        "Sites.csv": sites_df.to_csv(index=False).encode("utf-8-sig"),
        "ProjectsOwners.csv": owners_df.to_csv(index=False).encode("utf-8-sig"),
    }
    return write_zip(zip_path, members)


def _write_tenements_shp_zip(zip_path: Path, tmp_dir: Path, gdf: gpd.GeoDataFrame) -> Path:
    members = shapefile_members(gdf, tmp_dir, TENEMENTS_SHAPEFILE_BASENAME)
    return write_zip(zip_path, members)


def _seed_minedex_snapshot(
    data_root: Path,
    date_str: str,
    *,
    sites_df: pd.DataFrame,
    owners_df: pd.DataFrame,
    finalize: bool = True,
) -> Path:
    """`finalize=False` leaves the snapshot WITHOUT a `SHA256SUMS.txt` --
    exactly the state a validation-refused or interrupted fetch leaves
    behind, which `build-register`'s integrity gate must refuse to read."""
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "dmirs_001_minedex", date_str)
    _write_minedex_csv_zip(
        snapshot_dir / MINEDEX_CSV_ZIP_FILENAME, sites_df=sites_df, owners_df=owners_df
    )
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="MINEDEX (DMIRS-001)",
        endpoint="https://dasc.dmirs.wa.gov.au/Download/File/3981",
        licence_note="CC-BY-4.0 grant, contrary Data WA notice -- D7 keeps redistribution closed",
        purpose="test fixture",
    )
    if finalize:
        snapshots.finalize_snapshot(snapshot_dir)
    return snapshot_dir


def _seed_tenements_snapshot(
    data_root: Path, date_str: str, tmp_path: Path, *, tenements_gdf: gpd.GeoDataFrame
) -> Path:
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "dmirs_003_tenements", date_str)
    _write_tenements_shp_zip(
        snapshot_dir / TENEMENTS_ZIP_FILENAME,
        tmp_path / f"tenements_build_{date_str}",
        tenements_gdf,
    )
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="Mining Tenements (DMIRS-003)",
        endpoint="https://dasc.dmirs.wa.gov.au/Download/File/2056",
        licence_note="CC-BY-4.0",
        purpose="test fixture",
    )
    snapshots.finalize_snapshot(snapshot_dir)
    return snapshot_dir


def _patch_git_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )


def test_build_register_cli_writes_parquet_counts_reconciliation_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    sites_df = _sites_df(
        [
            _sites_row(
                site_code="M0001", stage="Operating", project_code="P1", lon=116.0, lat=-32.0
            ),
            _sites_row(site_code="M0002", stage="Shut", project_code=None, lon=120.0, lat=-30.0),
            # No usable location -- disclosed, never dropped.
            _sites_row(
                site_code="M0003", stage="Undeveloped", project_code="PX", lon=None, lat=None
            ),
        ]
    )
    owners_df = _owners_df(
        [
            _owners_row(
                project_code="P1", owner_code="O1", owner_name="Acme Bauxite", holding_pct=100.0
            )
        ]
    )
    _seed_minedex_snapshot(data_root, "2026-08-10", sites_df=sites_df, owners_df=owners_df)
    _seed_tenements_snapshot(
        data_root,
        "2026-08-12",
        tmp_path,
        tenements_gdf=_tenements_gdf([_tenement_polygon(116.0, -32.0)]),
    )
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert result.exit_code == 0, result.output

    output_dir = data_root / "curated" / "register" / "2026-08-15"
    register_path = output_dir / "register.parquet"
    assert register_path.is_file()

    written = pd.read_parquet(register_path)
    assert list(written.columns) == _REGISTER_COLUMNS
    assert len(written) == 3
    assert sorted(written["site_id"]) == ["M0001", "M0002", "M0003"]
    by_id = written.set_index("site_id")
    assert by_id.loc["M0001", "owners_at_snapshot"] == "Acme Bauxite (100%)"
    assert pd.isna(by_id.loc["M0002", "owners_at_snapshot"])
    assert by_id.loc["M0001", "n_tenements_intersecting"] == 1
    # M0002 is LOCATED (lon=120.0, lat=-30.0) but the tenements fixture only
    # carries a polygon at (116.0, -32.0) -- a genuine computed zero, D12.2.
    assert by_id.loc["M0002", "n_tenements_intersecting"] == 0
    assert not pd.isna(by_id.loc["M0002", "n_tenements_intersecting"])
    # M0003 has no coordinates -- n_tenements_intersecting is NOT COMPUTED
    # (null), never a fabricated zero, D12.2.
    assert pd.isna(by_id.loc["M0003", "n_tenements_intersecting"])
    assert pd.isna(by_id.loc["M0003", "lon"])
    # `snapshot_date` names the MINEDEX snapshot the rows were actually read
    # from, never the `--date` build argument.
    assert (written["snapshot_date"] == "2026-08-10").all()

    counts_path = output_dir / "register_counts.json"
    counts = json.loads(counts_path.read_text())
    assert reconcile_counts(counts) == counts
    assert counts["total"] == 3

    reconciliation_text = (output_dir / "reconciliation.md").read_text()
    assert "PASS" in reconciliation_text
    assert "## Owner-join disclosures" in reconciliation_text
    assert "n_sites_null_coordinates: 1" in reconciliation_text

    manifest_path = Path(str(register_path) + ".run_manifest.json")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["resolved_args"]["minedex_public_export_blocked"] is True
    assert manifest["resolved_args"]["register_lonlat_crs"] == REGISTER_LONLAT_CRS
    assert manifest["resolved_args"]["minedex_source_crs"] == MINEDEX_SITES_SOURCE_CRS
    assert manifest["resolved_args"]["minedex_snapshot_verification"] == {
        "n_ok": 2,
        "n_bad": 0,
        "n_missing": 0,
    }
    assert manifest["resolved_args"]["tenements_snapshot_verification"] == {
        "n_ok": 2,
        "n_bad": 0,
        "n_missing": 0,
    }
    assert manifest["resolved_args"]["owner_join_disclosures"] == owner_join_disclosures(
        sites_df, owners_df
    )
    assert manifest["resolved_args"]["n_sites_null_coordinates"] == 1
    # D12.2: M0001 and M0002 are located (computed: 1 intersecting, 0
    # intersecting respectively); M0003 has no coordinates (not computed).
    assert manifest["resolved_args"]["tenement_count_disclosure"] == {
        "sites_total": 3,
        "n_sites_tenement_count_computed": 2,
        "n_sites_tenement_count_zero": 1,
        "n_sites_tenement_count_not_computed": 1,
    }

    payload = json.loads(result.output)
    assert payload["minedex_public_export_blocked"] is True
    assert payload["owner_join_disclosures"]["sites_total"] == 3
    assert payload["n_sites_null_coordinates"] == 1
    assert payload["tenement_count_disclosure"]["n_sites_tenement_count_not_computed"] == 1
    assert (
        payload["tenement_count_disclosure"]["n_sites_tenement_count_computed"]
        + payload["tenement_count_disclosure"]["n_sites_tenement_count_not_computed"]
        == payload["tenement_count_disclosure"]["sites_total"]
    )


def test_build_register_cli_discloses_owner_row_composition_on_a_mixed_extract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D12.2: no earlier test exercises a `ProjectsOwners.csv` carrying BOTH
    current and ended relationships. Pins two things at once -- the new
    `owner_row_composition` disclosure in the register manifest/stdout, and
    the EXISTING `owners_at_snapshot` behaviour (D8's current-owner join
    still selects only the empty-EndDate row for a project that carries
    both) now that a mixed extract is actually under test."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    sites_df = _sites_df(
        [_sites_row(site_code="M0001", stage="Operating", project_code="P1", lon=116.0, lat=-32.0)]
    )
    owners_df = _owners_df(
        [
            # P1's current owner -- blank EndDate.
            _owners_row(
                project_code="P1", owner_code="O1", owner_name="Acme Bauxite", holding_pct=100.0
            ),
            # P1's ENDED former owner -- must NOT appear in owners_at_snapshot.
            _owners_row(
                project_code="P1",
                owner_code="O0",
                owner_name="Former Owner",
                holding_pct=100.0,
                end_date="31/12/2020",
            ),
        ]
    )
    _seed_minedex_snapshot(data_root, "2026-08-10", sites_df=sites_df, owners_df=owners_df)
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert result.exit_code == 0, result.output

    # The existing D8 current-owner join is unaffected by the ended row.
    register_path = data_root / "curated" / "register" / "2026-08-15" / "register.parquet"
    written = pd.read_parquet(register_path)
    assert written.loc[written["site_id"] == "M0001", "owners_at_snapshot"].iloc[0] == (
        "Acme Bauxite (100%)"
    )
    assert (
        "Former Owner"
        not in written.loc[written["site_id"] == "M0001", "owners_at_snapshot"].iloc[0]
    )

    expected_composition = owner_row_composition(owners_df)
    assert expected_composition == {
        "owner_rows_total": 2,
        "n_owner_rows_current": 1,
        "n_owner_rows_ended": 1,
    }

    manifest_path = Path(str(register_path) + ".run_manifest.json")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["resolved_args"]["owner_row_composition"] == expected_composition

    payload = json.loads(result.output)
    assert payload["owner_row_composition"] == expected_composition


def test_build_register_cli_survives_a_null_owner_name_as_structured_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression, demonstrated end to end: a null `OwnerName` in
    `ProjectsOwners.csv` used to crash `owners_by_project` with an uncaught
    `AttributeError`, and `build-register` exited 1 with empty stdout and a
    traceback rather than a structured refusal or, as here, a successful
    build with the missing name rendered explicitly."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    sites_df = _sites_df([_sites_row(site_code="M0001", stage="Operating", project_code="P1")])
    owners_df = _owners_df(
        [_owners_row(project_code="P1", owner_code="O1", owner_name=None, holding_pct=50.0)]
    )
    _seed_minedex_snapshot(data_root, "2026-08-10", sites_df=sites_df, owners_df=owners_df)
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert payload["reconciliation"] == "PASS"

    register_path = data_root / "curated" / "register" / "2026-08-15" / "register.parquet"
    written = pd.read_parquet(register_path)
    assert written.loc[written["site_id"] == "M0001", "owners_at_snapshot"].iloc[0] == (
        "(owner name not stated) (50%)"
    )


def test_build_register_cli_resolves_owners_for_a_numeric_project_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `Sites.csv` and `ProjectsOwners.csv` are read
    independently by `pandas.read_csv`, and a numeric `ProjectCode` used to
    infer a DIFFERENT dtype in each frame whenever one frame carried a null
    cell in that column and the other did not -- exactly the real
    2026-08-14 extract's shape, where `Sites.csv` alone carries 5,822 null
    `ProjectCode`s. An unpinned read would infer `Sites.csv`'s `ProjectCode`
    column `float64` ("1234.0") while `ProjectsOwners.csv`'s (no null cell)
    infers `int64` ("1234"); `register.owners_by_project`/`owner_join_
    disclosures` join on `astype(str)`, which stringifies whichever value
    each frame already inferred, so every owner for that project would
    silently fail to match and the loss would be reported as an
    unmatched-project count rather than a read-time schema drift.
    `MINEDEX_CODE_COLUMN_DTYPES` (`sources/minedex.py`) pins both reads to
    pandas' nullable "string" dtype, so both frames see the identical
    literal text.

    The CSV bytes are written BY HAND here, not via a pandas DataFrame's
    `.to_csv()` -- building `sites_df`/`owners_df` in Python first (as
    `_write_minedex_csv_zip` does elsewhere in this file) would coerce a
    mixed int/`None` Python list to `float64` in memory before it ever
    reached a CSV reader, silently writing `"1234.0"` into `Sites.csv`
    itself and reproducing a different defect (a source that writes
    inconsistent literal text) rather than the read-side dtype-inference
    divergence this test targets."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    sites_csv = (
        "SiteCode,Title,Commodities,Stage,ProjectCode,Latitude,Longitude\n"
        "M0001,Test Site,Bauxite,Operating,1234,-32.0,116.0\n"
        "M0002,Other Site,Gold,Operating,,-30.0,120.0\n"
    ).encode("utf-8-sig")
    owners_csv = (
        "ProjectCode,OwnerCode,OwnerName,HoldingPct,EndDate\n1234,O1,Acme Bauxite,100.0,\n"
    ).encode("utf-8-sig")

    snapshot_dir = snapshots.create_snapshot_dir(data_root, "dmirs_001_minedex", "2026-08-10")
    write_zip(
        snapshot_dir / MINEDEX_CSV_ZIP_FILENAME,
        {"Sites.csv": sites_csv, "ProjectsOwners.csv": owners_csv},
    )
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="MINEDEX (DMIRS-001)",
        endpoint="https://dasc.dmirs.wa.gov.au/Download/File/3981",
        licence_note="CC-BY-4.0 grant, contrary Data WA notice -- D7 keeps redistribution closed",
        purpose="test fixture",
    )
    snapshots.finalize_snapshot(snapshot_dir)
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert result.exit_code == 0, result.output

    register_path = data_root / "curated" / "register" / "2026-08-15" / "register.parquet"
    written = pd.read_parquet(register_path)
    by_id = written.set_index("site_id")
    assert by_id.loc["M0001", "owners_at_snapshot"] == "Acme Bauxite (100%)"

    payload = json.loads(result.output)
    assert payload["owner_join_disclosures"]["n_sites_unmatched_project_code"] == 0


def test_build_register_cli_refuses_when_no_minedex_snapshot_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert result.exit_code == 1
    assert "dmirs_001_minedex" in result.output


def test_build_register_cli_refuses_when_no_tenements_snapshot_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_minedex_snapshot(
        data_root, "2026-08-10", sites_df=_sites_df([_sites_row()]), owners_df=_owners_df([])
    )
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert result.exit_code == 1
    assert "dmirs_003_tenements" in result.output


def test_build_register_cli_refuses_an_unfinalized_latest_minedex_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    sites_df = _sites_df([_sites_row()])
    owners_df = _owners_df([])
    _seed_minedex_snapshot(data_root, "2026-08-01", sites_df=sites_df, owners_df=owners_df)
    _seed_minedex_snapshot(
        data_root, "2026-08-10", sites_df=sites_df, owners_df=owners_df, finalize=False
    )
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert "never finalized" in payload["refusal"]
    assert payload["source_id"] == "dmirs_001_minedex"
    assert not (data_root / "curated" / "register" / "2026-08-15").exists()


def test_build_register_cli_refuses_a_tampered_minedex_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    snapshot_dir = _seed_minedex_snapshot(
        data_root, "2026-08-10", sites_df=_sites_df([_sites_row()]), owners_df=_owners_df([])
    )
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    (snapshot_dir / "metadata.txt").write_text("tampered after finalize\n")
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "integrity verification" in payload["refusal"]
    assert payload["n_ok"] == 1
    assert payload["n_bad"] == 1
    assert payload["n_missing"] == 0
    assert not (data_root / "curated" / "register" / "2026-08-15").exists()


def test_build_register_cli_refuses_a_finalized_minedex_snapshot_missing_the_csv_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "dmirs_001_minedex", "2026-08-10")
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="MINEDEX (DMIRS-001)",
        endpoint="https://dasc.dmirs.wa.gov.au/Download/File/3981",
        licence_note="test",
        purpose="test fixture",
    )
    snapshots.finalize_snapshot(snapshot_dir)  # no CSV zip present at all
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert MINEDEX_CSV_ZIP_FILENAME in payload["refusal"]
    assert not (data_root / "curated" / "register" / "2026-08-15").exists()


def test_build_register_cli_refuses_a_minedex_csv_zip_missing_projectsowners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Sites.csv` present, `ProjectsOwners.csv` absent from the same zip --
    `zf.open` raises `KeyError`, caught and rendered as structured JSON."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    snapshot_dir = snapshots.create_snapshot_dir(data_root, "dmirs_001_minedex", "2026-08-10")
    write_zip(
        snapshot_dir / MINEDEX_CSV_ZIP_FILENAME,
        {"Sites.csv": _sites_df([_sites_row()]).to_csv(index=False).encode("utf-8-sig")},
    )
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="MINEDEX (DMIRS-001)",
        endpoint="https://dasc.dmirs.wa.gov.au/Download/File/3981",
        licence_note="test",
        purpose="test fixture",
    )
    snapshots.finalize_snapshot(snapshot_dir)
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert "ProjectsOwners.csv" in payload["refusal"]
    assert not (data_root / "curated" / "register" / "2026-08-15").exists()


def test_build_register_cli_refuses_a_renamed_sites_column_as_structured_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    sites_df = _sites_df([_sites_row()]).rename(columns={"SiteCode": "Code"})
    _seed_minedex_snapshot(data_root, "2026-08-10", sites_df=sites_df, owners_df=_owners_df([]))
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    payload = json.loads(result.output)
    assert "SiteCode" in payload["refusal"]
    assert not (data_root / "curated" / "register" / "2026-08-15").exists()


def test_build_register_cli_uses_the_latest_of_multiple_minedex_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_minedex_snapshot(
        data_root,
        "2020-01-01",
        sites_df=_sites_df([_sites_row(site_code="STALE")]),
        owners_df=_owners_df([]),
    )
    _seed_minedex_snapshot(
        data_root,
        "2026-08-10",
        sites_df=_sites_df([_sites_row(site_code="M0001")]),
        owners_df=_owners_df([]),
    )
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert result.exit_code == 0, result.output

    written = pd.read_parquet(
        data_root / "curated" / "register" / "2026-08-15" / "register.parquet"
    )
    assert "STALE" not in set(written["site_id"])
    assert sorted(written["site_id"]) == ["M0001"]


def test_build_register_cli_refuses_a_rerun_against_an_already_built_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _seed_minedex_snapshot(
        data_root, "2026-08-10", sites_df=_sites_df([_sites_row()]), owners_df=_owners_df([])
    )
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    _patch_git_state(monkeypatch)

    first = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert first.exit_code == 0, first.output

    register_path = data_root / "curated" / "register" / "2026-08-15" / "register.parquet"
    original_bytes = register_path.read_bytes()

    # A newer MINEDEX snapshot lands after the first build.
    _seed_minedex_snapshot(
        data_root,
        "2026-08-11",
        sites_df=_sites_df([_sites_row(site_code="M0002")]),
        owners_df=_owners_df([]),
    )

    second = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )

    assert second.exit_code == 1
    assert "Traceback" not in second.output
    payload = json.loads(second.output)
    assert "refusal" in payload
    assert str(register_path) in second.output
    assert register_path.read_bytes() == original_bytes


def test_build_register_cli_discloses_a_row_with_no_usable_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    sites_df = _sites_df(
        [
            _sites_row(site_code="M0001", lon=116.0, lat=-32.0),
            _sites_row(site_code="M0002", lon=None, lat=None),
        ]
    )
    _seed_minedex_snapshot(data_root, "2026-08-10", sites_df=sites_df, owners_df=_owners_df([]))
    _seed_tenements_snapshot(data_root, "2026-08-12", tmp_path, tenements_gdf=_tenements_gdf([]))
    _patch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["build-register", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert result.exit_code == 0, result.output

    output_dir = data_root / "curated" / "register" / "2026-08-15"
    written = pd.read_parquet(output_dir / "register.parquet")
    assert len(written) == 2
    assert written["lon"].isna().sum() == 1

    reconciliation = (output_dir / "reconciliation.md").read_text()
    assert "- Register rows without a usable location (lon/lat null): 1" in reconciliation
    assert "PASS" in reconciliation

    assert json.loads(result.output)["n_sites_null_coordinates"] == 1
    manifest = json.loads((output_dir / "register.parquet.run_manifest.json").read_text())
    assert manifest["resolved_args"]["n_sites_null_coordinates"] == 1


def test_snapshot_verification_covers_only_the_hashed_zip(tmp_path: Path) -> None:
    """Regression guard, adapted from the GeoPackage-era test: the (ok, bad,
    missing) triple this command records reflects `_verify_snapshot_or_
    refuse`'s OWN gate over the finalized snapshot, not a re-derivation."""
    data_root = tmp_path / "data"
    snapshot_dir = _seed_minedex_snapshot(
        data_root, "2026-08-10", sites_df=_sites_df([_sites_row()]), owners_df=_owners_df([])
    )
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)
    assert (n_ok, n_bad, n_missing) == (2, 0, 0)  # the CSV zip + metadata.txt


# =============================================================================
# ENRICHED_REGISTER_SCHEMA / enrich_register_with_dea_coverage (D13)
# =============================================================================


def _coverage_frame(site_ids, values=1):
    import pandas as pd

    from wa_mine_monitor.dea_coverage import DEA_EPOCH_COLUMN_BY_SOURCE

    frame = pd.DataFrame({"site_id": list(site_ids)})
    for column in DEA_EPOCH_COLUMN_BY_SOURCE.values():
        frame[column] = pd.array([values] * len(site_ids), dtype="Int64")
    return frame


def test_enriched_schema_is_register_schema_plus_four_nullable_int64():
    import pyarrow as pa

    from wa_mine_monitor.register import (
        DEA_COVERAGE_COLUMNS,
        ENRICHED_REGISTER_SCHEMA,
        REGISTER_SCHEMA,
    )

    assert ENRICHED_REGISTER_SCHEMA.names == REGISTER_SCHEMA.names + list(DEA_COVERAGE_COLUMNS)
    for column in DEA_COVERAGE_COLUMNS:
        field = ENRICHED_REGISTER_SCHEMA.field(column)
        assert field.type == pa.int64()
        assert field.nullable


def _conforming_register(n_rows: int) -> pd.DataFrame:
    """A conforming register frame from this file's OWN fixture builders --
    `_sites_df`/`_owners_df`/`_tenements_gdf` through `build_register`, the
    same construction every other test in this file uses."""
    rows = [_sites_row(site_code=f"M{i:04d}", lon=116.0 + i, lat=-32.0 - i) for i in range(n_rows)]
    return build_register(_sites_df(rows), _owners_df([]), _tenements_gdf([]), "2026-08-15")


def test_enrich_appends_columns_preserving_row_identity_and_order():
    register_df = _conforming_register(3)
    coverage = _coverage_frame(register_df["site_id"])
    enriched = register_module.enrich_register_with_dea_coverage(register_df, coverage)
    assert list(enriched["site_id"]) == list(register_df["site_id"])
    assert list(enriched.columns[: len(register_df.columns)]) == list(register_df.columns)
    assert str(enriched["n_dea_fc_pc_epochs"].dtype) == "Int64"
    # Existing nullable semantics untouched:
    assert str(enriched["n_tenements_intersecting"].dtype) == "Int64"


def test_enrich_refuses_row_loss_gain_and_mismatched_sites():
    register_df = _conforming_register(2)
    with pytest.raises(register_module.RegisterEnrichmentError, match="site"):
        register_module.enrich_register_with_dea_coverage(
            register_df, _coverage_frame(["NOT-A-SITE", "ALSO-NOT"])
        )
    with pytest.raises(register_module.RegisterEnrichmentError, match="row"):
        register_module.enrich_register_with_dea_coverage(
            register_df, _coverage_frame(register_df["site_id"][:1])
        )


# =============================================================================
# ELIGIBLE_REGISTER_SCHEMA / validate_eligible_register (D13 D5)
# =============================================================================


def test_eligible_schema_is_enriched_schema_plus_four_fields():
    import pyarrow as pa

    from wa_mine_monitor.register import (
        D3_ELIGIBILITY_COLUMNS,
        ELIGIBLE_REGISTER_SCHEMA,
        ENRICHED_REGISTER_SCHEMA,
    )

    assert ELIGIBLE_REGISTER_SCHEMA.names == ENRICHED_REGISTER_SCHEMA.names + list(
        D3_ELIGIBILITY_COLUMNS
    )
    for column in ("effective_pixel_support_px", "d3_threshold_px"):
        field = ELIGIBLE_REGISTER_SCHEMA.field(column)
        assert field.type == pa.int64()
        assert field.nullable
    d3_eligible_field = ELIGIBLE_REGISTER_SCHEMA.field("d3_eligible")
    assert d3_eligible_field.type == pa.bool_()
    assert d3_eligible_field.nullable
    trajectory_field = ELIGIBLE_REGISTER_SCHEMA.field("trajectory_status")
    assert trajectory_field.type == pa.string()
    assert not trajectory_field.nullable


def _eligibility_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_validate_eligible_register_rejects_unknown_status():
    frame = _eligibility_frame([{"trajectory_status": "not_a_real_status", "d3_eligible": pd.NA}])
    with pytest.raises(register_module.TrajectoryStatusError, match="unknown"):
        register_module.validate_eligible_register(frame)


def test_validate_eligible_register_rejects_eligible_true_on_non_eligible_status():
    frame = _eligibility_frame(
        [{"trajectory_status": "insufficient_pixel_support", "d3_eligible": True}]
    )
    with pytest.raises(register_module.TrajectoryStatusError, match="d3_eligible=True"):
        register_module.validate_eligible_register(frame)


def test_validate_eligible_register_rejects_null_eligible_on_judged_status():
    frame = _eligibility_frame([{"trajectory_status": "eligible", "d3_eligible": pd.NA}])
    with pytest.raises(register_module.TrajectoryStatusError, match="judged"):
        register_module.validate_eligible_register(frame)


def test_validate_eligible_register_accepts_a_conforming_frame():
    frame = _eligibility_frame(
        [
            {"trajectory_status": "eligible", "d3_eligible": True},
            {"trajectory_status": "insufficient_pixel_support", "d3_eligible": False},
            {"trajectory_status": "threshold_not_computed", "d3_eligible": False},
            {"trajectory_status": "no_usable_footprint", "d3_eligible": pd.NA},
            {"trajectory_status": "crosswalk_not_high_confidence", "d3_eligible": pd.NA},
        ]
    )
    register_module.validate_eligible_register(frame)  # must not raise


# =============================================================================
# assign_trajectory_eligibility -- forced-threshold path (Batch E Task 0,
# docs/decisions/2026-08-25-batch-e-forced-threshold-entry.md)
# =============================================================================


def _forced_threshold_register_df() -> pd.DataFrame:
    """Two matched, high-confidence sites: one with support >= 144 (would
    be `eligible` under the forced path), one with support < 144 (stays
    `insufficient_pixel_support` even when forced)."""
    return pd.DataFrame(
        {
            "site_id": ["site-a", "site-b"],
            "site_name": ["Site A", "Site B"],
            "commodity": ["Gold", "Gold"],
            "stage": ["Operating", "Operating"],
            "owners_at_snapshot": ["Owner A", "Owner B"],
            "snapshot_date": ["2026-08-10", "2026-08-10"],
            "lon": [116.0, 117.0],
            "lat": [-31.0, -32.0],
            "n_tenements_intersecting": [1, 1],
            "inclusion_status": ["operating", "operating"],
        }
    )


def _forced_threshold_crosswalk_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "site_id": ["site-a", "site-b"],
            "maus_id": ["maus-a", "maus-b"],
            "confidence": ["high", "high"],
        }
    )


def _forced_threshold_support_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "maus_id": ["maus-a", "maus-b"],
            "effective_pixel_support_px": [200, 100],
        }
    )


def test_forced_threshold_makes_supported_sites_eligible_without_passing_criteria():
    out = register_module.assign_trajectory_eligibility(
        _forced_threshold_register_df(),
        _forced_threshold_crosswalk_df(),
        _forced_threshold_support_df(),
        n_star=register_module.d3_protocol.MIN_FULL_SUPPORT_PX,
        criteria_passed=False,
        forced_threshold=True,
    )
    assert set(out["trajectory_status"]) <= set(register_module._TRAJECTORY_STATUSES)
    eligible = out.loc[out["trajectory_status"] == "eligible"]
    assert len(eligible) == 1
    assert eligible["d3_forced_threshold"].all()
    assert (eligible["d3_threshold_px"] == 144).all()


def test_forced_threshold_still_separates_insufficient_support():
    out = register_module.assign_trajectory_eligibility(
        _forced_threshold_register_df(),
        _forced_threshold_crosswalk_df(),
        _forced_threshold_support_df(),
        n_star=register_module.d3_protocol.MIN_FULL_SUPPORT_PX,
        criteria_passed=False,
        forced_threshold=True,
    )
    row_b = out.loc[out["site_id"] == "site-b"].iloc[0]
    assert row_b["trajectory_status"] == "insufficient_pixel_support"
    assert row_b["d3_eligible"] == False
    assert row_b["d3_forced_threshold"] == True


def test_forced_threshold_is_refused_when_criteria_actually_passed():
    with pytest.raises(ValueError, match="forced_threshold"):
        register_module.assign_trajectory_eligibility(
            _forced_threshold_register_df(),
            _forced_threshold_crosswalk_df(),
            _forced_threshold_support_df(),
            n_star=100,
            criteria_passed=True,
            forced_threshold=True,
        )


def test_forced_threshold_refuses_n_star_other_than_min_full_support():
    with pytest.raises(ValueError, match="forced_threshold"):
        register_module.assign_trajectory_eligibility(
            _forced_threshold_register_df(),
            _forced_threshold_crosswalk_df(),
            _forced_threshold_support_df(),
            n_star=100,
            criteria_passed=False,
            forced_threshold=True,
        )


def test_default_is_unchanged_criteria_failed_means_threshold_not_computed():
    out = register_module.assign_trajectory_eligibility(
        _forced_threshold_register_df(),
        _forced_threshold_crosswalk_df(),
        _forced_threshold_support_df(),
        n_star=register_module.d3_protocol.MIN_FULL_SUPPORT_PX,
        criteria_passed=False,
    )
    judged = out.loc[out["site_id"].isin(["site-a", "site-b"])]
    assert (judged["trajectory_status"] == "threshold_not_computed").all()
    assert (judged["d3_eligible"] == False).all()
    assert not judged["d3_forced_threshold"].fillna(False).any()


def test_d3_forced_threshold_is_false_not_null_on_every_judged_row():
    out = register_module.assign_trajectory_eligibility(
        _forced_threshold_register_df(),
        _forced_threshold_crosswalk_df(),
        _forced_threshold_support_df(),
        n_star=150,
        criteria_passed=True,
    )
    judged = out.loc[out["site_id"].isin(["site-a", "site-b"])]
    assert judged["trajectory_status"].isin(["eligible", "insufficient_pixel_support"]).all()
    assert (judged["d3_forced_threshold"] == False).all()
    assert judged["d3_forced_threshold"].notna().all()
