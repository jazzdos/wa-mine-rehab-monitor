# Ported from jarrah-rehab src/jarrah_rehab/reporting/export.py at commit cf1743e202d367fcb2016eca0a1563b4f9db240c (2026-08-15); MIT-relicensed by the same author.
"""The public-export boundary: the one place the licence gate is enforced.

This project's design doc and jarrah-rehab's licence gate both say the same thing --
nothing lands in a public artefact unless `redistribute_public = true`,
enforced *in code* at the export boundary, not by care. `export_public` is
that code, for a pandas frame.

**What actually enforces the gate today, stated exactly.** Enforcement
happens in the `export-release` CLI command (`cli.py`): it is the sole
caller of `export_public`, calling it on every frame it writes before that
frame reaches `<data_root>/releases/`. The share-alike-scalar caveat below
still holds -- this module enforces the geometry drop and the row gate, not
a `share_alike`-aware carve-out rule.

Two independent restrictions are enforced, because two different licences
bite in two different ways:

- **Row gate.** Any row whose `redistribute_public` is not exactly `True`
  fails the whole export with `PermissionError`. It REFUSES rather than
  filters: silently dropping the offending rows would publish a table that
  reconciles against nothing, and the caller would never learn that a
  restricted source had reached the export in the first place.
- **Geometry drop.** Two restrictions bite here, per the design doc §6/§8 D1.
  Maus et al. v2 mine polygons are CC-BY-SA-4.0 and are this project's SOLE
  Tier 1 measurement footprint; the design's own D1 ruling is that the
  Maus-derived package publishes SEPARATELY under CC-BY-SA-4.0 with
  attribution, "ShareAlike applied conservatively to the whole package, no
  scalar-field carve-outs asserted" -- so this function does not attempt to
  publish Maus-derived scalars as a CC-BY-SA carve-out; it drops the geometry
  outright. **Stated exactly, not aspirationally:** this function enforces
  that separation for GEOMETRY only. `SOURCES["maus_v2"].redistribute_public`
  is `True`, so a Maus-derived scalar column is not caught by the row gate
  either, and is dropped here only if its name or value independently reads
  as geometry -- nothing below distinguishes a CC-BY-SA scalar from a CC-BY
  one. Keeping Maus-derived scalars out of a non-Maus export is therefore a
  packaging discipline upstream of this function (never assembling such a
  frame), not a guarantee this function provides; closing that gap needs a
  `share_alike`-aware refusal this module does not yet have.

  Separately, DMIRS-001 MINEDEX
  carries a licence CONFLICT (Data WA `cc-nc` vs a DASC blanket CC-BY notice)
  and is fail-closed under the design's MINEDEX binding rule: MINEDEX
  attributes are internal-only unless the exact DASC download's captured
  metadata explicitly places that resource under CC-BY-4.0 with no contrary
  notice, so a MINEDEX-sourced geometry or attribute column must never cross
  this boundary on the strength of the DASC blanket alone. Dropping geometry
  at the boundary makes the Maus half structural: a dashboard served from a
  `pub_*`-equivalent frame cannot leak share-alike geometry, because the
  geometry is not in the source it reads. The MINEDEX row-level restriction
  is enforced by the row gate above, not by the geometry drop.

**What the geometry rule matches, exactly.** A column is dropped when its
lower-cased name contains any of `GEOMETRY_NAME_TOKENS` (`geom`, `wkt`,
`wkb`, `easting`, `northing`) -- so `geometry`, `geometry_crs`, `geom_wkt`,
`wkb_geometry`, `boundary_wkt`, `centre_easting_m` and `centre_northing_m`
all go. The literal substring `"geometry"` was considered and rejected: it
does not match `geom_wkt`, and a gate that publishes serialised geometry
under a spelling it never thought of is the failure shape CLAUDE.md names
three times -- a mechanism that looks present and enforces nothing.
`easting`/`northing` were added for the same reason: `effective_n`'s ring
centres are a point coordinate, not a measured scalar, and the value rule in
`_has_geometry_values` cannot catch a plain float column -- it short-circuits
`False` on numeric dtype, by design, to avoid scanning every numeric column
in the warehouse for coincidental geometry. The NAME rule is therefore the
only mechanism that can gate a coordinate pair, and it must carry the token.

A name rule alone is still a heuristic over whoever named the upstream
column, so a VALUE rule backs it: a column is also dropped when its dtype is
a geometry extension dtype (geopandas) or when any of its values exposes
`__geo_interface__` (shapely, or anything GeoJSON-shaped). That scan runs
only over object-dtype columns -- under pandas 3 a text column is `str`
dtype, not `object`, so strings are not scanned -- and it is O(rows) in the
worst case, which is the right trade at export scale (thousands of rows) for
a boundary that must not be bypassable by a rename.

**Fail-closed cases.** Three ways this gate can be asked to run and have
nothing to run on, all of which raise rather than pass:

- the frame carries NO `redistribute_public` column. An absent gate column is
  not permission; it is an unevaluable gate. This holds for an empty frame
  too: zero rows trivially contains no `False`, so a row-only check would
  bless a schema it had never seen.
- `redistribute_public` is null (`None` / `pd.NA`). Null means UNKNOWN, and
  unknown is not permitted -- the same sentinel-must-become-null rule CLAUDE.md
  states generally: a value meaning UNKNOWN must never be left able to compare
  equal to another unknown and pass as a match.
- `redistribute_public` is not bool-like. `bool("false")` is `True`, so a
  gate reading truthiness would publish a frame whose own column says not to.

The returned frame records the transformation it underwent in
`DataFrame.attrs["export_public"]` (`dropped_columns`, `row_count`), for the
same reason a run manifest records `git.diff_scrubbed`: a silently lossy
artefact is worse than an obviously lossy one, because the reader believes
they hold the whole frame. `redistribute_public` itself is dropped and listed
there -- it is a licence decision about a SOURCE, not a published
measurement, and no publishable frame described in
`docs/plans/2026-08-15-wa-mine-rehab-monitor-design.md` §6/§8 carries it.
"""

from __future__ import annotations

import re

import pandas as pd

#: The row-level licence gate column. A row is publishable only when this is
#: exactly `True`.
REDISTRIBUTE_COLUMN = "redistribute_public"

#: Lower-cased substrings that mark a column as geometry-bearing by NAME.
#: `wkt`/`wkb` are included because serialised geometry is still geometry:
#: a CC-BY-SA polygon published as a WKT string is as redistributed as one
#: published as a shapely object. `easting`/`northing` are included for the
#: same reason: `effective_n`'s `centre_easting_m` / `centre_northing_m` are
#: the centres of 550 (of 1,275) systematic rings screened by a Maus et al. v2
#: (CC-BY-SA-4.0) derived forest mask -- a POINT COORDINATE, not a measured
#: scalar, however innocuous the column name reads. CLAUDE.md's licence gate
#: permits "counts, areas and bounding boxes"; a ring centre is none of
#: those, and no numeric-dtype value rule can ever catch it (`_has_geometry_
#: values` short-circuits `False` on numeric dtype), so the NAME rule is the
#: only mechanism that can.
GEOMETRY_NAME_TOKENS: tuple[str, ...] = ("geom", "wkt", "wkb", "easting", "northing")

#: Column NAMES that are a coordinate by themselves. Exact match on the
#: lower-cased name -- NOT substring tokens like the set above, because
#: "lat" is a substring of "cumulative" and "dilation", and a licence gate
#: that silently drops an ordinary measured column is the quietly-lossy
#: failure this module's docstring names. `REGISTER_SCHEMA` declares
#: `lon`/`lat` (register.py); a point coordinate is geometry however it is
#: spelled, same reasoning as `easting`/`northing` above.
COORDINATE_COLUMN_NAMES: frozenset[str] = frozenset({"lon", "lat", "longitude", "latitude"})


def _has_geometry_name(column: str) -> bool:
    """True when the column's name marks it as geometry-bearing."""
    lowered = column.lower()
    if lowered in COORDINATE_COLUMN_NAMES:
        return True
    return any(token in lowered for token in GEOMETRY_NAME_TOKENS)


#: An OGC Well-Known Text geometry literal: a geometry keyword, an optional
#: `Z`/`M`/`ZM` dimension tag, then an opening parenthesis or `EMPTY`.
#:
#: The trailing `\(|EMPTY` is load-bearing and was earned by a counterfactual
#: test, not reasoned to. Matching the keyword as a bare PREFIX classified the
#: sentence "pointless variation in the understorey" as geometry and dropped an
#: ordinary prose column from a published table -- a licence gate that quietly
#: deletes findings is a different failure from one that leaks them, and just
#: as bad. A WKT literal always has a parenthesis or `EMPTY` after the keyword;
#: an English word that merely starts with those letters does not.
_WKT_LITERAL = re.compile(
    r"^(?:POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON"
    r"|GEOMETRYCOLLECTION|CIRCULARSTRING|COMPOUNDCURVE|CURVEPOLYGON|TRIANGLE"
    r"|TIN|POLYHEDRALSURFACE)\s*(?:ZM|Z|M)?\s*(?:\(|EMPTY\b)"
)

#: First byte of a Well-Known Binary geometry: 0x00 big-endian, 0x01 little.
_WKB_BYTE_ORDER_MARKS: frozenset[int] = frozenset({0x00, 0x01})


def _is_wkt_text(value: object) -> bool:
    """True when a string is an OGC WKT geometry literal."""
    if not isinstance(value, str):
        return False
    text = value.strip().upper()
    if text.startswith("SRID="):
        _, _, text = text.partition(";")
        text = text.lstrip()
    return _WKT_LITERAL.match(text) is not None


def _is_wkb_bytes(value: object) -> bool:
    """True when a bytes value looks like an OGC WKB geometry.

    Deliberately conservative: a valid WKB begins with a byte-order mark and
    then a 4-byte little- or big-endian geometry type in 1..17 (or with the
    0x20000000/0x40000000/0x80000000 SRID/Z/M flags set). Checking the type as
    well as the byte-order mark keeps arbitrary binary blobs -- a thumbnail, a
    hash -- from reading as geometry on a one-in-two byte coincidence.
    """
    if not isinstance(value, (bytes, bytearray)) or len(value) < 5:
        return False
    order = value[0]
    if order not in _WKB_BYTE_ORDER_MARKS:
        return False
    geometry_type = int.from_bytes(value[1:5], "little" if order == 0x01 else "big")
    return 1 <= (geometry_type & 0xFF) <= 17


def _has_geometry_values(series: pd.Series) -> bool:
    """True when the column HOLDS geometry, whatever it is called.

    The backstop for a geometry column with an innocuous name (`boundary`,
    `footprint`, `notes`). Three shapes are recognised, and the second and
    third exist because the first alone did not deliver what this module and
    `scripts/check_no_geometry_columns.py` both stated in writing:

    1. a geopandas geometry dtype, or any object exposing `__geo_interface__`;
    2. **WKT text** -- a `str` beginning with an OGC geometry keyword;
    3. **WKB bytes** -- a `bytes` value with a byte-order mark and a plausible
       geometry type.

    Serialised geometry is still geometry. A CC-BY-SA Maus polygon published as
    a WKT string in a column called `notes` is exactly as redistributed as one
    published as a shapely object, and it previously passed all three gates
    while `check_no_geometry_columns.py`'s docstring asserted this function
    caught it. That is the mechanism-that-enforces-nothing shape, arriving in a
    docstring rather than in code.

    Text columns must therefore be scanned, so the object-dtype-only shortcut
    is gone: under pandas 3 a WKT column is `str` dtype, not `object`, and
    skipping it would reinstate the hole on the exact dtype the case arrives in.
    Cost is bounded by short-circuiting on the first match and by the cheap
    prefix test.
    """
    if getattr(series.dtype, "name", "") == "geometry":
        return True
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_datetime64_any_dtype(series):
        return False
    return any(
        hasattr(value, "__geo_interface__") or _is_wkt_text(value) or _is_wkb_bytes(value)
        for value in series
    )


def is_geometry_column(name: str, series: pd.Series) -> bool:
    """True when a column is geometry-bearing by NAME or by VALUE.

    The single definition of "this column is geometry" for the whole package.
    Exported because `reporting.figures` is an export boundary too, and it
    previously carried its own, narrower rule -- exact equality with the
    literal `"geometry"` -- so `geom`, `footprint_wkt` and `the_geom` were
    dropped at the Parquet boundary and drawn at the figure boundary. A
    guarantee that depends on which gate a caller happens to reach is not a
    guarantee.
    """
    return _has_geometry_name(name) or _has_geometry_values(series)


def has_geometry(frame: pd.DataFrame) -> bool:
    """True when ANY column of `frame` is geometry-bearing, by name or value.

    Also true for a geopandas `GeoDataFrame` whose geometry column has been
    renamed to something innocuous, because the dtype check in
    `_has_geometry_values` catches it whatever the column is called.
    """
    return any(is_geometry_column(str(column), frame[column]) for column in frame.columns)


def _check_row_gate(frame: pd.DataFrame) -> None:
    """Raise `PermissionError` unless every row is explicitly redistributable.

    Fails closed on an absent column, on nulls and on non-bool-like values;
    see this module's docstring for why each of those is a refusal rather
    than a pass.
    """
    if REDISTRIBUTE_COLUMN not in frame.columns:
        raise PermissionError(
            f"licence gate: frame carries no {REDISTRIBUTE_COLUMN!r} column, so the gate "
            f"cannot be evaluated. An absent gate column is not permission -- add the "
            f"column upstream (columns present: {sorted(frame.columns)})."
        )

    series = frame[REDISTRIBUTE_COLUMN]
    try:
        flags = series.astype("boolean")
    except (TypeError, ValueError) as exc:
        raise PermissionError(
            f"licence gate: {REDISTRIBUTE_COLUMN!r} has dtype {series.dtype!r}, which is not "
            f"bool-like. Truthiness is not permission -- bool('false') is True -- so the "
            f"gate refuses rather than guess. Cast the column to boolean upstream."
        ) from exc

    unknown = int(flags.isna().sum())
    if unknown:
        raise PermissionError(
            f"licence gate: {unknown} row(s) carry a null {REDISTRIBUTE_COLUMN!r}. Null means "
            f"UNKNOWN, and unknown is not permitted -- a row whose licence status was never "
            f"established must not publish on the strength of never having been checked."
        )

    withheld = flags.eq(False)
    refused = int(withheld.sum())
    if refused:
        labels = frame.loc[withheld].index.tolist()
        raise PermissionError(
            f"licence gate: {refused} of {len(frame)} row(s) have "
            f"{REDISTRIBUTE_COLUMN} = False and must not be published "
            f"(row labels: {labels[:10]}{'...' if refused > 10 else ''}). The export refuses "
            f"the whole frame rather than filtering: a silently filtered table reconciles "
            f"against nothing and hides that a restricted source reached the boundary."
        )


def export_public(frame: pd.DataFrame) -> pd.DataFrame:
    """Return `frame` as a publishable table, or raise `PermissionError`.

    Enforces the row-level `redistribute_public` gate, then drops every
    geometry-bearing column (by name and by value) and the gate column
    itself. Row count is unchanged: this function never filters -- it
    publishes the whole frame or refuses it.

    The input frame is not mutated. The returned frame's
    `attrs["export_public"]` records `dropped_columns` and `row_count`, so a
    reader can see the export was lossy and exactly how.

    Raises:
        PermissionError: on any row that is not explicitly redistributable,
            and on the three unevaluable-gate cases (absent column, null
            values, non-bool-like dtype).
    """
    _check_row_gate(frame)

    dropped = sorted(
        column
        for column in frame.columns
        if column == REDISTRIBUTE_COLUMN or is_geometry_column(str(column), frame[column])
    )
    published = frame.drop(columns=dropped)
    published.attrs = {
        "export_public": {
            "dropped_columns": dropped,
            "row_count": len(published),
        }
    }
    return published
