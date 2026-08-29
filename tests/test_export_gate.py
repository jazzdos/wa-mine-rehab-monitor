# Ported from jarrah-rehab tests/test_pub_export.py at commit
# cf1743e202d367fcb2016eca0a1563b4f9db240c (2026-08-15); MIT-relicensed by
# the same author. Import rewritten only (jarrah_rehab.reporting.export ->
# wa_mine_monitor.export_gate); every test compiles and behaves unchanged.
"""The published-export licence gate (`wa_mine_monitor.export_gate`).

This is the boundary the whole publication story rests on: nothing reaches a
`pub_*` table, a figure, a README or a dashboard except through
`export_public`. Two independent restrictions are enforced here --
`redistribute_public` at row level, and a geometry drop for the share-alike
(Maus et al. v2, CC-BY-SA-4.0) and non-commercial (DBCA-047/DBCA-082,
CC-BY-NC-4.0) sources -- so the gate is tested from both directions plus the
three ways it can silently fail to run at all.

Nothing in this file is guarded with `pytest.importorskip`. CLAUDE.md's
drift-guard rule is explicit: a check that only runs when an optional extra is
installed is not a check, and this project has three prior instances of a
mechanism that looked present and enforced nothing (the pre-registration
template, the schema-drift test behind the `geo` extra, the `MACHINES.md`
self-declaration). The licence gate must fail under a bare `uv run pytest -q`.
"""

from __future__ import annotations

import pandas as pd
import pytest

from wa_mine_monitor import export_gate, licence
from wa_mine_monitor.export_gate import (
    GEOMETRY_NAME_TOKENS,
    REDISTRIBUTE_COLUMN,
    export_public,
    is_geometry_column,
)


@pytest.fixture
def result_frame() -> pd.DataFrame:
    """A small, clean, publishable frame shaped like a `pub_*` source.

    Carries both geometry-named columns the event registry actually holds
    (`geometry`, `geometry_crs`) plus the `geom_wkt` spelling, because a rule
    keyed on the literal substring `"geometry"` would pass the first two and
    silently publish the third.
    """
    return pd.DataFrame(
        {
            "site_id": ["H001", "H002", "W001"],
            "mine": ["huntly", "huntly", "willowdale"],
            "metric": ["nbr", "ndvi", "bare"],
            "geometry": ["POINT (116.0 -32.6)", "POINT (116.1 -32.7)", "POINT (116.2 -33.0)"],
            "geometry_crs": ["EPSG:4326", "EPSG:4326", "EPSG:4326"],
            "geom_wkt": ["POINT (116.0 -32.6)", "POINT (116.1 -32.7)", "POINT (116.2 -33.0)"],
            "redistribute_public": [True, True, True],
        }
    )


def test_public_export_rejects_nonredistributable_rows(result_frame: pd.DataFrame) -> None:
    """A single non-redistributable row fails the whole export."""
    result_frame.loc[0, "redistribute_public"] = False
    with pytest.raises(PermissionError, match="licence gate"):
        export_public(result_frame)


def test_public_export_drops_geometry_columns(result_frame: pd.DataFrame) -> None:
    """No geometry-bearing column survives the export boundary."""
    exported = export_public(result_frame)
    assert not [c for c in exported.columns if "geometry" in c]


def test_public_export_passes_a_clean_frame(result_frame: pd.DataFrame) -> None:
    """A clean frame keeps every row."""
    exported = export_public(result_frame)
    assert len(exported) == len(result_frame)


def test_public_export_rejects_a_frame_with_no_redistribute_column() -> None:
    """A frame carrying no `redistribute_public` column FAILS CLOSED.

    The failure shape CLAUDE.md names three times: a gate that cannot find
    the thing it gates and passes anyway is a mechanism that looks present and
    enforces nothing. An absent column is not permission; it is an
    unevaluable gate.
    """
    frame = pd.DataFrame({"site_id": ["H001"], "mine": ["huntly"]})
    with pytest.raises(PermissionError, match="licence gate"):
        export_public(frame)


def test_public_export_rejects_null_redistribute_values(result_frame: pd.DataFrame) -> None:
    """`redistribute_public = null` is UNKNOWN, and unknown is not permitted.

    Same rule as the DBCA-047 `-1` sentinel: a value meaning "unknown"
    compares equal to another unknown and must never be read as the permitted
    branch. Exercised for both `None` and `pd.NA`, since a nullable-boolean
    column and an object column reach the gate by different routes.
    """
    with_none = result_frame.copy()
    with_none["redistribute_public"] = [True, None, True]
    with pytest.raises(PermissionError, match="licence gate"):
        export_public(with_none)

    with_na = result_frame.copy()
    with_na["redistribute_public"] = pd.array([True, pd.NA, True], dtype="boolean")
    with pytest.raises(PermissionError, match="licence gate"):
        export_public(with_na)


def test_public_export_rejects_a_non_boolean_redistribute_column(
    result_frame: pd.DataFrame,
) -> None:
    """A truthy string is not permission.

    `bool("false")` is `True`, so a gate that evaluated truthiness would
    publish a frame whose own column says not to. The gate requires
    bool-like values and fails closed on anything else.
    """
    result_frame["redistribute_public"] = ["true", "false", "true"]
    with pytest.raises(PermissionError, match="licence gate"):
        export_public(result_frame)


def test_public_export_names_the_offending_rows(result_frame: pd.DataFrame) -> None:
    """The refusal says how many rows failed and which sites they were.

    A gate that refuses without naming what it refused sends the caller back
    to guess.
    """
    result_frame.loc[2, "redistribute_public"] = False
    with pytest.raises(PermissionError) as excinfo:
        export_public(result_frame)
    message = str(excinfo.value)
    assert "1" in message
    assert REDISTRIBUTE_COLUMN in message


def test_public_export_drops_every_geometry_name_token(result_frame: pd.DataFrame) -> None:
    """Every documented spelling is dropped, not only the literal `geometry`.

    `geom_wkt` does not contain the substring `"geometry"`; a name rule keyed
    on that literal would publish serialised CC-BY-SA geometry under a column
    the gate believed it had never seen.
    """
    exported = export_public(result_frame)
    surviving = [
        column
        for column in exported.columns
        if any(token in column.lower() for token in GEOMETRY_NAME_TOKENS)
    ]
    assert surviving == []
    assert "geom_wkt" not in exported.columns
    assert list(exported.columns) == ["site_id", "mine", "metric"]


def test_public_export_drops_a_geometry_column_with_an_innocuous_name() -> None:
    """A geometry-valued column escapes the NAME rule; the VALUE rule catches it.

    The name rule is a heuristic over column spellings, so on its own it is a
    gate whose coverage depends on whoever named the upstream column. Any
    column holding objects that expose `__geo_interface__` (shapely,
    geopandas, anything GeoJSON-shaped) is dropped regardless of its name.
    """

    class _Shape:
        @property
        def __geo_interface__(self) -> dict[str, object]:
            return {"type": "Point", "coordinates": (116.0, -32.6)}

    frame = pd.DataFrame(
        {
            "site_id": ["H001", "H002"],
            "boundary": [_Shape(), _Shape()],
            "redistribute_public": [True, True],
        }
    )
    exported = export_public(frame)
    assert "boundary" not in exported.columns
    assert list(exported.columns) == ["site_id"]


def test_public_export_records_what_it_dropped(result_frame: pd.DataFrame) -> None:
    """The export discloses the transformation it applied.

    CLAUDE.md's `git.diff_scrubbed` rule: a transformation applied to a
    published artefact must record that it was applied, so a reader is never
    left believing they hold the whole frame.
    """
    exported = export_public(result_frame)
    dropped = exported.attrs["export_public"]["dropped_columns"]
    assert dropped == ["geom_wkt", "geometry", "geometry_crs", REDISTRIBUTE_COLUMN]
    assert exported.attrs["export_public"]["row_count"] == 3


def test_public_export_does_not_mutate_its_input(result_frame: pd.DataFrame) -> None:
    """The caller's frame is untouched -- the gate returns a new frame."""
    before = list(result_frame.columns)
    export_public(result_frame)
    assert list(result_frame.columns) == before


def test_public_export_rejects_an_empty_frame_with_no_gate_column() -> None:
    """Zero rows does not excuse a missing gate column.

    An empty frame trivially has no `False` row, so a gate that only scanned
    rows would pass a schema it has never checked -- and the next run with
    data would inherit that schema.
    """
    frame = pd.DataFrame({"site_id": pd.Series([], dtype="string")})
    with pytest.raises(PermissionError, match="licence gate"):
        export_public(frame)


def test_public_export_passes_an_empty_frame_that_declares_the_gate_column() -> None:
    """An empty frame WITH the gate column is publishable and stays empty."""
    frame = pd.DataFrame(
        {
            "site_id": pd.Series([], dtype="string"),
            "redistribute_public": pd.Series([], dtype="boolean"),
        }
    )
    exported = export_public(frame)
    assert len(exported) == 0
    assert list(exported.columns) == ["site_id"]


def test_wkt_geometry_under_an_innocuous_name_is_dropped() -> None:
    """Serialised geometry is still geometry, whatever the column is called.

    A CC-BY-SA Maus polygon written as WKT into a column named `notes` passed
    every gate while `scripts/check_no_geometry_columns.py`'s docstring
    asserted this function caught it -- a guarantee stated in prose and absent
    from the code.
    """
    frame = pd.DataFrame(
        {
            "site_id": ["H001"],
            "notes": ["POLYGON ((116.0 -32.6, 116.1 -32.6, 116.1 -32.7, 116.0 -32.6))"],
            "redistribute_public": [True],
        }
    )
    exported = export_public(frame)
    assert "notes" not in exported.columns
    assert list(exported.columns) == ["site_id"]


def test_wkt_with_an_srid_prefix_is_dropped() -> None:
    """`SRID=28350;POLYGON(...)` is the PostGIS spelling of the same hazard."""
    frame = pd.DataFrame(
        {
            "site_id": ["H001"],
            "footprint": ["SRID=28350;POINT (400000 6400000)"],
            "redistribute_public": [True],
        }
    )
    assert "footprint" not in export_public(frame).columns


def test_wkb_bytes_under_an_innocuous_name_is_dropped() -> None:
    """WKB: byte-order mark plus a geometry type, in a column called `blob`."""
    wkb_point = b"\x01\x01\x00\x00\x00" + b"\x00" * 16
    frame = pd.DataFrame({"site_id": ["H001"], "blob": [wkb_point], "redistribute_public": [True]})
    assert "blob" not in export_public(frame).columns


def test_ordinary_prose_and_ordinary_bytes_are_not_mistaken_for_geometry() -> None:
    """The counterfactual: the value rule must not eat non-geometry columns.

    Without this, "drops every column" would pass the three tests above for the
    wrong reason. `pointless` begins with the letters of POINT and is not WKT;
    the digest is bytes whose first byte is a legal WKB byte-order mark.
    """
    frame = pd.DataFrame(
        {
            "site_id": ["H001"],
            "comment": ["pointless variation in the understorey"],
            "digest": [b"\x01\xff\xff\xff\xff\xff\xff\xff"],
            "area_ha": [12.5],
            "redistribute_public": [True],
        }
    )
    exported = export_public(frame)
    assert set(exported.columns) == {"site_id", "comment", "digest", "area_ha"}


def test_coordinate_names_are_geometry_bearing() -> None:
    series = pd.Series([115.8, 116.1])
    for name in ("lon", "lat", "longitude", "latitude", "LON", "Latitude"):
        assert is_geometry_column(name, series), name


def test_coordinate_matching_is_exact_not_substring() -> None:
    # "lat" must not fire inside an ordinary word: a substring rule would
    # drop `cumulative_area_m2` (contains "lat") from every export.
    series = pd.Series([1.0, 2.0])
    assert not is_geometry_column("cumulative_area_m2", series)
    assert not is_geometry_column("dilation_px", series)


def test_export_public_drops_lon_lat() -> None:
    frame = pd.DataFrame(
        {"site_id": ["a"], "lon": [115.8], "lat": [-31.9], "redistribute_public": [True]}
    )
    published = export_public(frame)
    assert sorted(published.columns) == ["site_id"]
    assert "lon" in published.attrs["export_public"]["dropped_columns"]


class TestLicenceStateMapping:
    """Fail-closed mapping from `licence.LicenceState` to the export gate's
    boolean world (D13 §8 P1): only the `PUBLIC` member itself passes.
    """

    def test_public_member_allows(self) -> None:
        assert export_gate.licence_state_allows_public(licence.LicenceState.PUBLIC)

    def test_everything_else_denies(self) -> None:
        for value in (
            licence.LicenceState.GATED_INTERNAL,
            licence.LicenceState.RESEARCH_ONLY,
            "public",
            "PUBLIC",
            None,
            True,
            1,
            object(),
        ):
            assert not export_gate.licence_state_allows_public(value)
