"""The MINEDEX-Maus crosswalk (design doc §3, "MINEDEX-Maus crosswalk
(deterministic specification, Tier 0 deliverable)").

Every MINEDEX site (design doc §4's Tier 0 register) is matched against the
Maus et al. v2 WA extract (`sources/maus.py`'s `clip_to_wa`) by geometry:
containment first, then nearest-within-2000m. One-to-many (a site sitting
near two or more candidate polygons) and many-to-one (two sites both inside
one polygon, e.g. adjacent operations sharing a lease) are both kept
EXPLICIT rather than silently resolved to a single winner -- CLAUDE.md's
recurring rule that a reduction over ambiguous candidates must never drop
the ambiguity itself.

This is the pure, testable core (`build_crosswalk`, `crosswalk_counts`,
`crosswalk_row_total`, `tier1_population`); the `build-crosswalk` CLI
command (`cli.py`) wires it to the latest curated register and the latest
Maus WA extract snapshot, the declared-schema parquet writer
(`tables.write_table`) and the run-manifest sidecar. Every input-shape
refusal this module raises is `CrosswalkInputError` (a `ValueError`
subclass) -- see that class's docstring for why the CLI catches it
specifically rather than bare `ValueError`.

Both inputs must already be projected to `TARGET_CRS` (`EPSG:3577`, GDA94 /
Australian Albers -- the CRS the design doc's §5 DEA probe measured the
project's own imagery in, and an equal-area projection over WA, which is
what makes a metre-valued `distance_m` meaningful). Reprojection is a CLI
concern (`cli.py`'s `build-crosswalk` reprojects the register's `lon`/`lat`
points and the Maus extract before calling this module) -- this module
never reprojects anything itself, so a caller cannot accidentally feed it
two frames in different CRSs and get a distance computed across them
silently. `build_crosswalk` asserts both CRSs explicitly and raises rather
than guessing.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa

from wa_mine_monitor import register

#: The CRS both `build_crosswalk` inputs must already carry. GDA94 /
#: Australian Albers -- an equal-area metric CRS over WA, so `distance_m`
#: is a real metre distance rather than a degree-based approximation.
TARGET_CRS = "EPSG:3577"

#: Maximum distance (metres) a MINEDEX site may sit from a Maus polygon and
#: still be considered a candidate match at all. A site farther than this
#: from every polygon is `unmatched`.
MAX_MATCH_DISTANCE_M = 2000.0

#: The four `confidence` categories, in the fixed order every counts table
#: (`crosswalk_counts`) reports them -- same discipline as `register.
#: INCLUSION_STATUSES`.
CONFIDENCE_LEVELS: tuple[str, ...] = ("high", "medium", "low", "none")

#: `manual_review_status` every row is written with. `build_crosswalk` never
#: adjudicates a match itself -- a human (or a later, separately-run review
#: pass) is the only thing that ever changes this value; see this module's
#: docstring and CLAUDE.md's licence-evidence-capture discipline for the
#: identical "capture, never adjudicate" shape.
_UNREVIEWED = "unreviewed"

#: `match_method` values, named so a caller can compare against them rather
#: than a scattered string literal.
MATCH_POINT_IN_POLYGON = "point_in_polygon"
MATCH_NEAREST_WITHIN_2000M = "nearest_within_2000m"
MATCH_UNMATCHED = "unmatched"

#: Declared Arrow schema for `curated/crosswalk/<date>/crosswalk.parquet`.
#: `maus_id` is declared NULLABLE (an `unmatched` row carries no candidate
#: polygon at all) -- CLAUDE.md's rule that a table written to disk
#: declares its column types, never infers them from the rows: an
#: all-matched fixture's `maus_id` column would otherwise infer as
#: non-nullable string and only fail the first time an `unmatched` row
#: appeared. `distance_m` is nullable for the same reason (no distance was
#: computed for an `unmatched` row). Carries NO Maus geometry column --
#: only the scalar `maus_id` key -- so the Maus et al. v2 CC-BY-SA-4.0
#: polygon geometry itself never crosses into this artefact (design doc
#: §6/§8 D1; `export_gate.py`'s geometry drop is the backstop, but this
#: schema never gives it anything to drop).
CROSSWALK_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string()),
        pa.field("maus_id", pa.string(), nullable=True),
        pa.field("match_method", pa.string()),
        pa.field("distance_m", pa.float64(), nullable=True),
        pa.field("confidence", pa.string()),
        pa.field("ambiguity_n", pa.int64()),
        pa.field("shared_by_n", pa.int64()),
        pa.field("manual_review_status", pa.string()),
    ]
)

#: Output column order -- `CROSSWALK_SCHEMA`'s declared order, so
#: `build_crosswalk`'s return value and the schema it is written under
#: never drift apart.
_COLUMNS: tuple[str, ...] = tuple(CROSSWALK_SCHEMA.names)

#: `geometry` is declared here alongside `site_id`/`maus_id` -- see
#: `CrosswalkInputError`'s docstring for why: `_assert_target_crs` reads
#: `gdf.crs`, a property that raises a bare `AttributeError` (not
#: `ValueError`) on a GeoDataFrame with no active geometry column, so the
#: required-column check (which reads `.columns`, never `.crs`/`.geometry`,
#: and cannot raise that way) must run, and catch a missing `geometry`
#: column, BEFORE the CRS check ever touches `.crs`. `build_crosswalk`'s
#: guard order enforces this.
_REQUIRED_MINEDEX_COLUMNS: frozenset[str] = frozenset({"site_id", "geometry"})
_REQUIRED_MAUS_COLUMNS: frozenset[str] = frozenset({"maus_id", "geometry"})

#: `filter_register_for_crosswalk`'s own required columns -- the raw
#: register frame, before point geometry is built from `lon`/`lat`. See
#: that function's docstring.
_REQUIRED_REGISTER_COLUMNS: frozenset[str] = frozenset({"site_id", "lon", "lat"})

#: How many offending `site_id`s a refusal message names before truncating.
#: A refusal naming every id in a statewide register would be unreadable;
#: naming none would leave the caller to re-derive which records failed.
_MAX_NAMED_SITES = 10


class CrosswalkInputError(ValueError):
    """Raised ONLY by this module's own declared input guards
    (`_assert_required_columns`, `_assert_target_crs`, `_assert_no_null_or_
    duplicate_site_ids`, `_assert_no_null_or_duplicate_maus_ids`,
    `_assert_every_site_has_a_usable_location`, and `filter_register_for_
    crosswalk`'s own column check) -- never by an unrelated failure inside
    pandas, geopandas or shapely that happens to raise a bare `ValueError`.

    A `ValueError` subclass, not an unrelated new exception type: every
    existing `pytest.raises(ValueError, ...)` / `except ValueError` caller
    keeps working unchanged. The reason for the dedicated subclass is the
    CLI boundary (`build_crosswalk_cmd` in `cli.py`): catching bare
    `ValueError` around `build_crosswalk` used to also catch an unrelated
    `ValueError` raised deep inside the matching passes (a pandas/shapely
    internal, not an input-shape problem) and report it as a clean
    structured "refusal" -- indistinguishable from a genuine bad input.
    Catching `CrosswalkInputError` specifically closes that hole: anything
    NOT raised by a declared guard now propagates uncaught, as it should.
    """


def _assert_target_crs(gdf: gpd.GeoDataFrame, *, name: str) -> None:
    """Raise `CrosswalkInputError`, naming `name` and the CRS actually
    found, unless `gdf` is already in `TARGET_CRS`.

    Reprojection happens in the CLI, never here -- see this module's
    docstring. Comparing via `gdf.crs == TARGET_CRS` (a `pyproj.CRS`
    equality test, not a string match) is deliberate: it recognises an
    equivalent CRS however it was constructed (EPSG code, WKT, proj4)
    rather than only the one spelling this module happens to write in its
    own error messages.

    Callers MUST run `_assert_required_columns` (naming `geometry`) over
    `gdf` before calling this: `gdf.crs` is a GeoPandas property that raises
    a bare `AttributeError` -- not `ValueError`, and so not a
    `CrosswalkInputError` -- when `gdf` has no active geometry column at
    all. `build_crosswalk` orders its guards accordingly.
    """
    if gdf.crs is None or gdf.crs != TARGET_CRS:
        raise CrosswalkInputError(f"{name} must be in {TARGET_CRS}, got {gdf.crs!r}")


def _assert_required_columns(gdf: pd.DataFrame, *, name: str, required: frozenset[str]) -> None:
    """Raise `CrosswalkInputError`, naming every missing column, unless
    every column in `required` is present in `gdf.columns`.

    Typed `pd.DataFrame`, not `gpd.GeoDataFrame`, deliberately: a
    `GeoDataFrame` is a `DataFrame` subclass, so this same check runs, with
    the identical guarantee, over `filter_register_for_crosswalk`'s raw
    register frame (no geometry yet) as well as `build_crosswalk`'s two
    GeoDataFrame inputs. `.columns` alone is read here -- never `.crs` or
    `.geometry` -- so this check itself never raises on a GeoDataFrame with
    no active geometry column; see `_assert_target_crs`'s docstring for why
    that ordering matters.
    """
    missing = sorted(required - set(gdf.columns))
    if missing:
        raise CrosswalkInputError(
            f"{name} is missing required column(s) {missing} "
            f"(columns present: {sorted(gdf.columns)})"
        )


def _assert_no_null_or_duplicate_site_ids(minedex_gdf: gpd.GeoDataFrame) -> None:
    """Refuse a `minedex_gdf` whose `site_id` is null or non-unique anywhere.

    Neither the register nor `validate_minedex_gpkg` enforces either
    property, and both corrupt `build_crosswalk` silently rather than
    loudly. A NULL `site_id` is handled inconsistently between the two
    matching passes: `_containment_rows` groups its `sjoin` output with
    pandas' default `groupby(dropna=True)`, which drops a null-`site_id`
    group entirely, while `_nearest_rows` groups with `dropna=False` -- so a
    null-`site_id` site that falls INSIDE a Maus polygon is silently
    re-classified as a `nearest_within_2000m`/`medium` match instead of
    `point_in_polygon`/`high`, and `crosswalk_counts` scores it in NO
    confidence bucket at all (`nunique` also skips `NaN`), so the count
    table still reconciles despite carrying a row with no home.

    A DUPLICATE `site_id` is worse: `_containment_rows` records the id in
    `already_matched` once it has ANY containment match, and `_nearest_rows`
    excludes every row carrying that id (`minedex_gdf["site_id"].isin(
    already_matched)`) from the nearest-match pass -- so a second, unrelated
    site sharing the same `site_id` (e.g. a real-world id collision) but
    sitting far from every polygon is dropped from the output entirely
    rather than correctly classified `unmatched`. `crosswalk_counts` then
    certifies a population that lost a member: its own total reconciles,
    because the dropped site was never counted anywhere.

    Raises `CrosswalkInputError`, naming every duplicated value, when any `site_id`
    appears more than once; raises `CrosswalkInputError` when any `site_id` is null.
    An empty `minedex_gdf` passes trivially (no ids to collide).
    """
    site_id = minedex_gdf["site_id"]
    if site_id.isna().any():
        n_null = int(site_id.isna().sum())
        raise CrosswalkInputError(
            f"minedex_gdf['site_id'] contains {n_null} null value(s) -- build_crosswalk "
            "requires every site to carry a non-null site_id: a null site_id is grouped "
            "inconsistently between the containment pass (which drops a null-site_id group "
            "outright) and the nearest-match pass (which keeps it), so a null-site_id site "
            "inside a Maus polygon is silently mis-classified as a lower-confidence nearest "
            "match instead of a containment match. Fix the register's site_id column before "
            "calling build_crosswalk."
        )
    duplicated_values = sorted(site_id[site_id.duplicated(keep=False)].unique())
    if duplicated_values:
        raise CrosswalkInputError(
            f"minedex_gdf['site_id'] contains duplicate value(s) {duplicated_values} -- "
            "build_crosswalk requires site_id to uniquely identify a MINEDEX site: a "
            "duplicate lets the containment pass's already_matched exclusion drop an "
            "UNRELATED site sharing the same id from the nearest-match pass entirely, "
            "rather than classify it independently. De-duplicate the register's site_id "
            "column before calling build_crosswalk."
        )


def _assert_no_null_or_duplicate_maus_ids(maus_gdf: gpd.GeoDataFrame) -> None:
    """Refuse a `maus_gdf` whose `maus_id` is null or non-unique anywhere --
    the symmetric guard to `_assert_no_null_or_duplicate_site_ids`.

    `maus_id` is `sha256(WKB)[:12]` (`sources/maus.py`'s `_geometry_id`), so
    any two features whose CLIPPED geometry is byte-identical share an id,
    and `validate_maus_extract` does not check uniqueness. A duplicate id
    turns `_nearest_rows`' `pd.merge(maus_lookup, on="maus_id")` into a
    cartesian join: with one polygon duplicated, a site with a single real
    candidate 500 m away fans out to FOUR rows (2 `sjoin` hits x 2 merge
    matches), `ambiguity_n` reads 4, and `confidence` is downgraded
    `medium` -> `low` on a site whose real candidate set never changed. A
    contained site fans out the same way (2 identical `point_in_polygon`
    rows, `ambiguity_n = 2`). None of it is visible in `crosswalk_counts`,
    which counts DISTINCT sites and therefore still reconciles, and
    `build_crosswalk`'s stated determinism fails too: sorting by
    `(site_id, maus_id)` cannot order rows that tie on both.

    A NULL `maus_id` is refused for the separate reason that `_nearest_rows`
    uses `maus_id.notna()` as its "this row matched a polygon" test -- a
    genuine candidate carrying a null id would be counted as no candidate at
    all and its site classified `unmatched`.

    Raises `CrosswalkInputError`, naming every duplicated value, when any `maus_id`
    appears more than once; raises `CrosswalkInputError`, naming the count, when any
    is null. An empty `maus_gdf` passes trivially.

    If duplicate geometries turn out to be a real property of the Maus v2 WA
    extract, the fix is an explicit de-duplication in `sources/maus.py`'s
    `clip_to_wa` with the dropped count recorded in `validate_maus_extract`'s
    summary -- never a silent fan-out here.
    """
    maus_id = maus_gdf["maus_id"]
    if maus_id.isna().any():
        n_null = int(maus_id.isna().sum())
        raise CrosswalkInputError(
            f"maus_gdf['maus_id'] contains {n_null} null value(s) -- build_crosswalk requires "
            "every candidate polygon to carry a non-null maus_id: the nearest-match pass uses "
            "a non-null maus_id as its 'this row matched a polygon' test, so a genuine "
            "candidate with a null id is counted as no candidate at all and its site is "
            "classified unmatched. Fix the Maus extract's maus_id column before calling "
            "build_crosswalk."
        )
    duplicated_values = sorted(maus_id[maus_id.duplicated(keep=False)].unique())
    if duplicated_values:
        raise CrosswalkInputError(
            f"maus_gdf['maus_id'] contains duplicate value(s) {duplicated_values} -- "
            "build_crosswalk requires maus_id to uniquely identify a candidate polygon: the "
            "nearest-match pass merges each sjoin hit against maus_id, so a duplicate makes "
            "that merge a cartesian join -- rows fan out, ambiguity_n over-counts, confidence "
            "is downgraded on a site whose real candidate set never changed, and the output "
            "is no longer deterministically ordered (rows tying on both sort keys). maus_id "
            "is sha256(WKB)[:12], so identical clipped geometries collide; de-duplicate the "
            "Maus WA extract explicitly, recording the dropped count, before calling "
            "build_crosswalk."
        )


def _assert_every_site_has_a_usable_location(minedex_gdf: gpd.GeoDataFrame) -> None:
    """Refuse a `minedex_gdf` carrying any site with no usable point --
    geometry null, geometry empty, or a coordinate that is not finite.

    A MINEDEX record with a null geometry reaches this module as `lon`/`lat`
    = `NaN` in the register (`register.build_register`, which counts and
    discloses them) and is rebuilt by the CLI as a `POINT (NaN NaN)`. Such a
    point survives the `predicate="covered_by"` pass (no match) and then
    kills the `predicate="dwithin"` pass with a bare `shapely.errors.
    GEOSException`, which through the CLI is an uncaught traceback with
    empty stdout -- the same failure shape `sources/maus.py`'s `clip_to_wa`
    docstring records having already been fixed once.

    Refused rather than absorbed: an unlocatable site scored `unmatched`
    would report a test that COULD NOT BE COMPUTED as one that fired (there
    is no distance to any polygon, not a distance exceeding
    `MAX_MATCH_DISTANCE_M`), which is exactly the conflation CLAUDE.md's
    three-count diagnostic rule forbids. The register discloses the count
    (`register.count_rows_without_location`, rendered in its
    `reconciliation.md`); this refusal names the sites, so the caller
    decides what to do about them rather than finding them silently
    reclassified.

    Raises `CrosswalkInputError`, naming the count and up to `_MAX_NAMED_SITES` of
    the offending `site_id`s.
    """
    if len(minedex_gdf) == 0:
        return

    geometry = minedex_gdf.geometry
    # The missing/empty mask is computed FIRST and `.x`/`.y` read only over
    # what survives it -- the same discipline `register._lon_lat_columns`
    # applies, and for the same reason: what `.x` returns for a null or
    # empty geometry is a library default this project would be inheriting.
    # A guard that raised while checking would be worse than the defect.
    # `isna()`, not `notna()`: at geopandas 1.1.4 the latter warns whenever
    # the series holds an empty geometry.
    unusable = geometry.isna() | geometry.is_empty
    present = ~unusable
    if bool(present.any()):
        located = geometry.loc[present]
        # FINITENESS, not just nullity: `~np.isfinite` catches NaN AND
        # +/-inf. An inf `lon`/`lat` in the register survives `to_crs` as
        # inf (unlike NaN, which stays NaN), passes an `isna()`-only check,
        # does not raise in the dwithin sjoin, and would be emitted as
        # `unmatched` -- reporting a distance test that was never computed
        # as one that fired, the exact conflation this guard's docstring
        # says it exists to prevent.
        unusable.loc[present] = ~np.isfinite(located.x) | ~np.isfinite(located.y)
    if not bool(unusable.any()):
        return

    offending = [str(value) for value in minedex_gdf.loc[unusable, "site_id"]]
    named = sorted(offending)[:_MAX_NAMED_SITES]
    suffix = "" if len(offending) <= _MAX_NAMED_SITES else f" (first {_MAX_NAMED_SITES} shown)"
    raise CrosswalkInputError(
        f"minedex_gdf carries {len(offending)} site(s) with no usable point geometry "
        f"(null, empty, or a non-finite coordinate): {named}{suffix} -- build_crosswalk "
        "cannot score such a site against any polygon, and the dwithin pass raises a bare "
        "GEOS exception on it rather than returning no match. The register discloses this "
        "count in its reconciliation.md ('rows without a usable location'); fix or exclude "
        "those records before calling build_crosswalk, rather than letting them be reported "
        "as unmatched, which would claim a distance test that was never computed."
    )


#: Keys, in a fixed order, `filter_register_for_crosswalk`'s exclusion-count
#: dict always carries -- same discipline as `OWNER_JOIN_DISCLOSURE_KEYS`
#: and `SITE_ID_DUPLICATION_KEYS`.
CROSSWALK_EXCLUSION_KEYS: tuple[str, ...] = (
    "n_excluded_null_site_id",
    "n_excluded_duplicate_site_id",
    "n_excluded_no_usable_location",
    "n_excluded_total",
    "n_crosswalk_input_rows",
)


def filter_register_for_crosswalk(
    register_df: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Reduce a curated register frame to the population `build_crosswalk`
    can actually score, EXCLUDING rather than refusing on three conditions
    that are measured properties of a healthy real MINEDEX extract, not
    defects in this build: a null `site_id`, a `site_id` duplicated across
    more than one register row (the register is now one row per
    `Sites.csv` record -- see `register.py`'s module docstring and
    `register.site_id_duplication_counts`, which discloses the SAME
    property at build-register time), and a row with no usable `lon`/`lat`
    (the row-survival guarantee `register.build_register` applies to a
    coordinate-less MINEDEX record).

    A row can match more than one condition (e.g. a duplicated `site_id`
    that also carries no coordinates) -- the three per-reason counts below
    are NOT mutually exclusive and must never be summed against
    `n_excluded_total`, which is the count of DISTINCT rows excluded for
    ANY reason. `n_crosswalk_input_rows` is `len(register_df) -
    n_excluded_total`, the population actually handed to
    `build_crosswalk`.

    This is the CLI's population-forming step, not the defect classifier --
    `build_crosswalk`'s own guards (`_assert_no_null_or_duplicate_site_ids`,
    `_assert_every_site_has_a_usable_location`) still run over the FILTERED
    frame as a backstop, and still refuse loudly on anything this function
    failed to catch (a null/duplicate `maus_id` on the OTHER input, for
    instance, is untouched by this function and stays a hard refusal; so
    does a hand-built `minedex_gdf` fed to `build_crosswalk` directly,
    bypassing this function entirely).

    Requires `register_df` to carry `site_id`, `lon` and `lat`
    (`_REQUIRED_REGISTER_COLUMNS`) -- raises `CrosswalkInputError`, naming
    every missing column, via `_assert_required_columns`, the SAME declared
    guard `build_crosswalk` runs over its own two inputs, so a schema-
    drifted or hand-built register frame is caught here, explicitly and by
    name, rather than by an incidental `KeyError` from the bracket access
    below.
    """
    _assert_required_columns(register_df, name="register_df", required=_REQUIRED_REGISTER_COLUMNS)

    site_id = register_df["site_id"]
    lon = register_df["lon"]
    lat = register_df["lat"]

    null_site_id = site_id.isna()
    # Duplicate among the NON-null site_ids only -- a null site_id is
    # already counted above and must not double-count into this reason too.
    duplicate_site_id = pd.Series(False, index=register_df.index)
    non_null = ~null_site_id
    if bool(non_null.any()):
        duplicate_site_id.loc[non_null] = site_id.loc[non_null].astype(str).duplicated(keep=False)
    no_usable_location = lon.isna() | lat.isna()

    excluded = null_site_id | duplicate_site_id | no_usable_location
    counts = {
        "n_excluded_null_site_id": int(null_site_id.sum()),
        "n_excluded_duplicate_site_id": int(duplicate_site_id.sum()),
        "n_excluded_no_usable_location": int(no_usable_location.sum()),
        "n_excluded_total": int(excluded.sum()),
        "n_crosswalk_input_rows": int((~excluded).sum()),
    }
    return register_df.loc[~excluded].copy(), counts


def _containment_rows(
    minedex_gdf: gpd.GeoDataFrame, maus_gdf: gpd.GeoDataFrame
) -> tuple[list[dict[str, object]], set[object]]:
    """Point-in-polygon matches: `match_method="point_in_polygon"`,
    `distance_m=0.0`, `confidence="high"`.

    Returns the rows built plus the set of `site_id`s matched this way --
    those sites are excluded from the nearest-within-`MAX_MATCH_DISTANCE_M`
    pass below, so a site already contained by a polygon is never ALSO
    scored against it (or another polygon) by distance.

    A site contained by more than one polygon (overlapping Maus features)
    is not silently reduced to one winner: every containing polygon gets
    its own row, and `ambiguity_n` on each is the count of polygons that
    site fell inside -- the same "keep it explicit" discipline this
    module's docstring states for the nearest-match pass.

    Uses `predicate="covered_by"` (the point-side name for the polygon-side
    `covers`), NOT `"within"`. D12.3 triage, finding 4: `within` is a STRICT
    interior predicate that excludes a point sitting exactly ON a polygon's
    boundary (an edge or a vertex) -- such a site fell through to
    `_nearest_rows` instead, which reports `distance_m=0.0` (a boundary
    point really is zero distance from the polygon) under
    `match_method="nearest_within_2000m"`/`confidence="medium"`, so
    `distance_m == 0.0` no longer implied containment. `covered_by` is
    boundary-inclusive (verified directly against geopandas 1.1.4/shapely
    2.1.2: a point on a square's edge joins under `covered_by` and not under
    `within`), so `distance_m == 0.0` now always means `point_in_polygon`/
    `high`.
    """
    if len(minedex_gdf) == 0 or len(maus_gdf) == 0:
        return [], set()

    joined = gpd.sjoin(
        minedex_gdf[["site_id", "geometry"]],
        maus_gdf[["maus_id", "geometry"]],
        how="inner",
        predicate="covered_by",
    )

    rows: list[dict[str, object]] = []
    matched_site_ids: set[object] = set()
    for site_id, group in joined.groupby("site_id", sort=False, dropna=False):
        n_candidates = len(group)
        for maus_id in group["maus_id"]:
            rows.append(
                {
                    "site_id": site_id,
                    "maus_id": maus_id,
                    "match_method": MATCH_POINT_IN_POLYGON,
                    "distance_m": 0.0,
                    "confidence": "high",
                    "ambiguity_n": n_candidates,
                    "manual_review_status": _UNREVIEWED,
                }
            )
        matched_site_ids.add(site_id)
    return rows, matched_site_ids


def _nearest_rows(
    minedex_gdf: gpd.GeoDataFrame,
    maus_gdf: gpd.GeoDataFrame,
    *,
    already_matched: set[object],
) -> list[dict[str, object]]:
    """Within-`MAX_MATCH_DISTANCE_M` matches for every site NOT already
    matched by containment.

    `gpd.sjoin(..., predicate="dwithin", distance=MAX_MATCH_DISTANCE_M)`
    returns EVERY candidate polygon within the bound, not just the nearest
    one -- unlike `sjoin_nearest`, which returns only the single closest
    polygon (plus any candidate exactly tied with it, a measure-zero
    configuration on real geometry). Design doc §3/T10 Step 1: "a site
    within 2,000 m of TWO polygons -> one row per candidate polygon,
    confidence = 'low', ambiguity_n = 2 (one-to-many kept explicit, never
    resolved silently)" -- that population is every polygon within the
    bound, not merely a nearest-distance tie, so `dwithin` is the predicate
    that actually implements it. `how="left"` keeps a site with NO
    candidate within the distance bound in the output (as a null join),
    rather than dropping it, so it can be classified `unmatched` below
    instead of silently vanishing. `distance_m` is then computed explicitly
    per matched pair (`GeoSeries.distance`), since `sjoin` itself does not
    report one.

    - Exactly one candidate within the bound -> `confidence="medium"`.
    - Two or more candidates within the bound -> `confidence="low"`,
      `ambiguity_n` equal to the candidate count on every one of those
      rows.
    - Zero candidates (nothing within `MAX_MATCH_DISTANCE_M`) -> one
      `unmatched` row: `maus_id=None`, `distance_m=None`,
      `confidence="none"`, `ambiguity_n=0`.
    """
    remaining = minedex_gdf.loc[
        ~minedex_gdf["site_id"].isin(already_matched), ["site_id", "geometry"]
    ]
    if len(remaining) == 0:
        return []

    if len(maus_gdf) == 0:
        # No candidate polygon exists at all -- every remaining site is
        # unmatched. `sjoin` against an empty right frame is not exercised;
        # this is the same outcome by construction.
        nearest = remaining.copy()
        nearest["maus_id"] = None
        nearest["distance_m"] = float("nan")
    else:
        nearest = gpd.sjoin(
            remaining,
            maus_gdf[["maus_id", "geometry"]],
            how="left",
            predicate="dwithin",
            distance=MAX_MATCH_DISTANCE_M,
        )
        # `sjoin` reports which polygons matched, not how far away they are
        # -- compute `distance_m` explicitly per matched pair. Merge in the
        # matched polygon's own geometry by `maus_id` (a plain `pd.merge`,
        # not another spatial join) rather than relying on index alignment,
        # so a polygon matched by SEVERAL sites still pairs each of those
        # sjoin rows with its own polygon correctly. That holds only because
        # `maus_id` is unique WITHIN `maus_gdf` -- `build_crosswalk`'s
        # `_assert_no_null_or_duplicate_maus_ids` guarantees it. Were an id
        # duplicated in `maus_gdf`, this merge would be a cartesian join and
        # every matched row would fan out once per duplicate; see that
        # guard's docstring for the measured effect.
        maus_lookup = pd.DataFrame(
            {"maus_id": maus_gdf["maus_id"], "_maus_geometry": maus_gdf.geometry.array}
        )
        # `sjoin`'s output can carry a duplicate index (one row per matched
        # site/polygon pair, all sharing the matched site's original
        # index) -- merge on a freshly reset index so `matched` (computed
        # below, against this same merged frame) aligns unambiguously with
        # every subsequent `.loc[matched, ...]` selection.
        nearest = nearest.reset_index(drop=True).merge(maus_lookup, on="maus_id", how="left")
        matched = nearest["maus_id"].notna()
        nearest["distance_m"] = float("nan")
        site_points = gpd.GeoSeries(nearest.loc[matched, "geometry"], crs=maus_gdf.crs)
        candidate_polygons = gpd.GeoSeries(nearest.loc[matched, "_maus_geometry"], crs=maus_gdf.crs)
        nearest.loc[matched, "distance_m"] = site_points.distance(candidate_polygons, align=False)

    rows: list[dict[str, object]] = []
    for site_id, group in nearest.groupby("site_id", sort=False, dropna=False):
        candidates = group.loc[group["maus_id"].notna()]
        n_candidates = len(candidates)
        if n_candidates == 0:
            rows.append(
                {
                    "site_id": site_id,
                    "maus_id": None,
                    "match_method": MATCH_UNMATCHED,
                    "distance_m": None,
                    "confidence": "none",
                    "ambiguity_n": 0,
                    "manual_review_status": _UNREVIEWED,
                }
            )
            continue

        confidence = "medium" if n_candidates == 1 else "low"
        for _, candidate in candidates.iterrows():
            rows.append(
                {
                    "site_id": site_id,
                    "maus_id": candidate["maus_id"],
                    "match_method": MATCH_NEAREST_WITHIN_2000M,
                    "distance_m": float(candidate["distance_m"]),
                    "confidence": confidence,
                    "ambiguity_n": n_candidates,
                    "manual_review_status": _UNREVIEWED,
                }
            )
    return rows


def _shared_by_n(df: pd.DataFrame) -> pd.Series:
    """Per row, the count of DISTINCT `site_id`s that share that row's
    `maus_id` -- the many-to-one signal (design doc §3: "shared
    infrastructure", "adjacent operations").

    A row with a null `maus_id` (`unmatched`) has nothing to be shared, so
    it is given the neutral value 1 rather than left null -- `shared_by_n`
    is declared non-nullable (`CROSSWALK_SCHEMA`), and 1 reads honestly as
    "not part of any many-to-one group" without requiring a reader to treat
    a null specially only on this one column.
    """
    has_maus_id = df["maus_id"].notna()
    counts = df.loc[has_maus_id].groupby("maus_id")["site_id"].nunique()
    shared = df["maus_id"].map(counts)
    return shared.fillna(1).astype("int64")


def build_crosswalk(minedex_gdf: gpd.GeoDataFrame, maus_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Build the MINEDEX-Maus crosswalk: one or more rows per MINEDEX site.

    `minedex_gdf` needs `site_id`/`geometry` (point geometry); `maus_gdf`
    needs `maus_id`/`geometry` (polygon geometry). Required columns are
    checked FIRST, before anything else -- raises `CrosswalkInputError`,
    naming every missing column, when either is absent. This ordering is
    deliberate, not incidental: the CRS check below reads `gdf.crs`, a
    GeoPandas property that raises a bare `AttributeError` (not
    `ValueError`, and so not catchable as a `CrosswalkInputError`) on a
    GeoDataFrame with no active geometry column, so `geometry` must already
    be confirmed present before that check ever runs.

    Both inputs must already be projected to `TARGET_CRS` -- raises
    `CrosswalkInputError`, naming which frame and the CRS actually found,
    otherwise.

    `CrosswalkInputError` (a `ValueError` subclass) is raised ONLY by this
    function's own declared guards, listed below -- never by an unrelated
    failure inside `gpd.sjoin`, `pd.merge` or similar that happens to raise
    a bare `ValueError`. That distinction is what lets `build-crosswalk`'s
    CLI command catch `CrosswalkInputError` specifically for its structured
    refusal, rather than catching every `ValueError` and risking reporting
    a real defect as a clean input-shape refusal. See `CrosswalkInputError`'s
    own docstring.

    Three further refusals, each closing a way an unenforced input property
    corrupts the matching passes below silently rather than loudly. The
    `build-crosswalk` CLI command avoids hitting the first and third of
    these in the normal path by filtering the register through
    `filter_register_for_crosswalk` BEFORE calling this function, and
    recording the excluded count -- see that function's docstring for why
    exclusion, not refusal, is the right treatment for a measured property
    of a healthy real MINEDEX extract. These three refusals remain the hard
    backstop regardless: they fire on anything the filter failed to catch,
    and on a hand-built `minedex_gdf` passed to this function directly,
    bypassing the CLI's filtering step entirely.

    - `minedex_gdf["site_id"]` null or duplicated
      (`_assert_no_null_or_duplicate_site_ids`) -- neither the register nor
      `validate_minedex_gpkg` enforces uniqueness or non-nullity.
    - `maus_gdf["maus_id"]` null or duplicated
      (`_assert_no_null_or_duplicate_maus_ids`) -- `validate_maus_extract`
      does not check uniqueness, and `maus_id` is a geometry digest, so
      identical clipped geometries collide and fan the nearest-match merge
      out cartesian-wise.
    - any `minedex_gdf` site with no usable point
      (`_assert_every_site_has_a_usable_location`) -- a null-geometry
      MINEDEX record arrives here as `POINT (NaN NaN)` and raises a bare
      GEOS exception inside the `dwithin` pass.

    Matching, in order:

    1. **Containment** (`gpd.sjoin`, `predicate="covered_by"`): a site point
       falling inside OR exactly on the boundary of a Maus polygon ->
       `match_method="point_in_polygon"`, `distance_m=0.0`, `confidence=
       "high"`. `covered_by` (not the stricter, boundary-excluding `within`)
       is what keeps `distance_m == 0.0` and `match_method="point_in_polygon"`
       in agreement for a boundary point -- see `_containment_rows`.
    2. **Within-`MAX_MATCH_DISTANCE_M`** (`gpd.sjoin`, `predicate="dwithin"`),
       for every site NOT matched by containment ->
       `match_method="nearest_within_2000m"`, `confidence="medium"` (exactly
       one candidate polygon within the bound) or `"low"` (two or more
       candidate polygons within the bound, every one kept as its own row --
       see `_nearest_rows`).
    3. **Unmatched**: no polygon within `MAX_MATCH_DISTANCE_M` ->
       `maus_id=None`, `distance_m=None`, `match_method="unmatched"`,
       `confidence="none"`.

    `ambiguity_n` is the number of candidate polygons that produced THIS
    site's rows at whichever stage matched it (1 when unambiguous, 0 for
    `unmatched`). `shared_by_n` is the number of distinct sites sharing
    THIS row's `maus_id` (see `_shared_by_n`) -- the many-to-one signal,
    independent of `ambiguity_n`'s one-to-many signal. Every row's
    `manual_review_status` is `"unreviewed"`: this function never
    adjudicates a match, only proposes it (see this module's docstring).

    Deterministic: the returned frame is sorted by `(site_id, maus_id)`
    (nulls last) with a reset integer index, so calling this twice over the
    same input in any row order yields byte-identical frames.
    """
    # Required columns FIRST, `geometry` included -- `_assert_target_crs`
    # reads `gdf.crs`, which raises a bare `AttributeError` on a frame with
    # no active geometry column at all; see both functions' docstrings.
    _assert_required_columns(minedex_gdf, name="minedex_gdf", required=_REQUIRED_MINEDEX_COLUMNS)
    _assert_required_columns(maus_gdf, name="maus_gdf", required=_REQUIRED_MAUS_COLUMNS)
    _assert_target_crs(minedex_gdf, name="minedex_gdf")
    _assert_target_crs(maus_gdf, name="maus_gdf")
    _assert_no_null_or_duplicate_site_ids(minedex_gdf)
    _assert_no_null_or_duplicate_maus_ids(maus_gdf)
    _assert_every_site_has_a_usable_location(minedex_gdf)

    containment_rows, matched_site_ids = _containment_rows(minedex_gdf, maus_gdf)
    nearest_rows = _nearest_rows(minedex_gdf, maus_gdf, already_matched=matched_site_ids)

    df = pd.DataFrame(containment_rows + nearest_rows)
    if len(df) == 0:
        # No MINEDEX sites at all -- an empty, correctly-typed frame, not a
        # frame missing columns `_shared_by_n`/the sort below would choke on.
        return pd.DataFrame({column: pd.Series(dtype="object") for column in _COLUMNS})

    df["shared_by_n"] = _shared_by_n(df)
    df = df.sort_values(by=["site_id", "maus_id"], na_position="last", kind="stable")
    df = df.loc[:, list(_COLUMNS)].reset_index(drop=True)
    return df


def crosswalk_counts(df: pd.DataFrame) -> dict[str, int]:
    """Count `df`'s DISTINCT `site_id`s per `confidence` level, plus a
    `register.TOTAL_KEY` total -- also distinct sites, never rows.

    A site is matched at exactly one confidence level overall (a site's
    rows are all `high`, all `medium`, all `low`, or the single `none`
    row), so the per-level distinct-site counts partition the site
    population and reconcile against their own total via `register.
    reconcile_counts` -- called here, before anything is returned, per
    CLAUDE.md's rule that a count table reconciles against its own total
    before it is trusted.

    The returned dict carries EXACTLY `CONFIDENCE_LEVELS` plus `register.
    TOTAL_KEY` -- the same fixed key set `register_counts` returns -- and
    the same invariant `test_register_counts_reconciles_against_its_own_
    total` asserts of that function holds here too:
    `register.reconcile_counts(crosswalk_counts(df)) == crosswalk_counts(df)`.
    An earlier version of this function added a `row_total` key (total ROW
    count) to this SAME dict after reconciling, which broke that round-trip
    for any caller handed the return value: `row_total` is not a category,
    a `low`-confidence site legitimately contributes MULTIPLE rows (one per
    ambiguous candidate polygon), and summing it into `reconcile_counts`'s
    category total would make a correct crosswalk fail its own
    reconciliation the SECOND time it was checked. `row_total` is now
    `crosswalk_row_total`'s own return value -- a structural separation,
    not a wider checker, per CLAUDE.md's "state both, since one-to-many
    inflates rows" rule: both counts are still available, just never mixed
    into the one dict that must itself reconcile cleanly.
    """
    counts = {
        level: int(df.loc[df["confidence"] == level, "site_id"].nunique())
        for level in CONFIDENCE_LEVELS
    }
    counts[register.TOTAL_KEY] = int(df["site_id"].nunique())
    register.reconcile_counts(counts)
    return counts


def crosswalk_row_total(df: pd.DataFrame) -> int:
    """`df`'s total ROW count -- deliberately kept OUT of `crosswalk_counts`'s
    reconciling dict; see that function's docstring for why. A `low`-
    confidence site contributes one row per ambiguous candidate polygon, so
    this can legitimately exceed `crosswalk_counts(df)[register.TOTAL_KEY]`
    (a distinct-SITE count) -- that is the expected, one-to-many shape, not
    a reconciliation failure.
    """
    return len(df)


def tier1_population(crosswalk_df: pd.DataFrame) -> pd.DataFrame:
    """The Tier 1 measurement population: `confidence == "high"` rows only.

    Design doc §4/§8 D1: "High-confidence crosswalk matches only; no
    tenement/buffer substitution." `medium`/`low`/`none` rows stay Tier 0
    only -- callers that need to know how many sites that excludes should
    read `crosswalk_counts(crosswalk_df)`, which reports every level's
    distinct-site count (and the row-inflated total) rather than silently
    discarding that information here.
    """
    return crosswalk_df.loc[crosswalk_df["confidence"] == "high"].reset_index(drop=True)
