"""Tier 0 statewide register: one row per `Sites.csv` record (design doc §4).

Rebuilt for the D6-D8 rulings
(`docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md`):
MINEDEX is acquired as two DASC bundles (an SHP zip and a CSV database zip),
never a single GeoPackage, and this module's `build_register` now takes the
CSV zip's `Sites.csv`/`ProjectsOwners.csv` frames directly rather than a
`geopandas.GeoDataFrame` read off a GeoPackage. Point geometry is
constructed HERE, from `Sites.csv`'s own `Latitude`/`Longitude` columns,
under the DECLARED source CRS `MINEDEX_SITES_SOURCE_CRS` -- never read off
a geometry column the CSV bundle does not carry.

D8 replaces `operator_at_snapshot` with `owners_at_snapshot` (plural),
derived from `ProjectsOwners.csv`'s CURRENT (non-ended) owner records,
joined through `Sites.csv.ProjectCode` -- see `owners_by_project` and
`owner_join_disclosures` below, and D8's ruling text for the exact
canonical rendering rule.

`STAGE_TO_INCLUSION` carries the measured MINEDEX stage vocabulary read off
the real 2026-08-14 DASC extract (decisions doc §1's stage-count
reconciliation: Shut 20,578 + Undeveloped 17,717 + Proposed 4,727 +
Operating 4,717 + Care and Maintenance 2,189 + Under Development 236 =
50,164). Every stage value NOT in this mapping still falls to `other`
(`_OTHER`) rather than being dropped -- the row-survival guarantee below is
unchanged by this rework.

The register's grain change (one row per MINEDEX SITE, formerly, to one row
per `Sites.csv` RECORD, now that `Sites.csv` is read directly rather than a
GeoPackage carrying one feature per site) means `site_id`
(`Sites.csv["SiteCode"]`, copied verbatim) is NO LONGER GUARANTEED UNIQUE:
`Sites.csv`'s own `SiteCode` carries measured duplication on the real
product (`sources.minedex.validate_minedex_bundles`'s docstring: 1,327
duplicated values across 1,411 excess rows in the 2026-08-14 extract), and
`build_register` does not deduplicate it. `site_id_duplication_counts`
discloses this in `build_reconciliation_report` -- disclosed, not refused,
matching the treatment `validate_minedex_bundles` already gives the
identical property of `Sites.csv` itself -- and `crosswalk.
filter_register_for_crosswalk` excludes the affected rows from the
crosswalk's input population, recording the excluded count, rather than
`build-crosswalk` refusing the whole run on a property of a healthy source.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow as pa
from shapely.geometry import Point

from wa_mine_monitor.snapshots import latest_dated_subdir

#: The single declared CRS `build_register`'s `lon`/`lat` output columns are
#: always in -- WGS84 geographic coordinates. `build_register` reprojects
#: the points it constructs into this CRS before reading `.x`/`.y`, so a
#: caller reading `lon`/`lat` back (e.g. `build-crosswalk` in `cli.py`) reads
#: this constant rather than a literal `"EPSG:4326"`.
REGISTER_LONLAT_CRS = "EPSG:4326"

#: The CRS `minedex_sites_df`'s `Latitude`/`Longitude` columns are DECLARED
#: to carry -- GDA2020 geographic, matching the DASC MINEDEX shapefile
#: bundle's own measured CRS (`sources.minedex.MINEDEX_EXPECTED_CRS`, the
#: SAME DASC extract `Sites.csv` is drawn from). `Sites.csv` itself carries
#: no CRS metadata -- it is a plain CSV -- so this is a DECLARED assumption,
#: not something read off the file; `build_register` constructs point
#: geometry under this CRS explicitly rather than writing the raw float
#: pair into `lon`/`lat` under an unstated label (CLAUDE.md's rule that an
#: inferred or assumed CRS must be recorded, not left invisible).
MINEDEX_SITES_SOURCE_CRS = "EPSG:7844"

#: Declared Arrow schema for `curated/register/<date>/register.parquet`.
#: Written explicitly to `tables.write_table` -- a table written to disk
#: declares its column types, never infers them from the rows. Geometry is
#: carried as `lon`/`lat` float columns ONLY, so the ported export gate's
#: geometry-column check (`export_gate.has_geometry`, `GEOMETRY_NAME_TOKENS`)
#: never fires on this table. `owners_at_snapshot` is a NULLABLE string --
#: `pd.NA` on a site with no resolvable current owner (D8).
#:
#: `n_tenements_intersecting` is a NULLABLE int64 (D12.2,
#: `docs/decisions/2026-08-16-d9-d12-commit-remote-naming-sequencing.md`):
#: `pa.field`'s default `nullable=True` already permits this without any
#: extra declaration, but the semantics it now carries are stated here
#: because they changed -- `pd.NA` for a site with no usable location (the
#: intersection was NEVER COMPUTED for it), and a genuine, computed
#: `0`/`1`/... only for a site the spatial join actually ran against. A
#: diagnostic that could not be computed is not a diagnostic that fired
#: (CLAUDE.md's rule); before this ruling the column wrote a fabricated `0`
#: for every coordinate-less site, conflating "no tenement intersects this
#: located site" with "this site could not be located so the count was
#: never computed". `build_register` constructs the column under pandas'
#: nullable `"Int64"` dtype so this distinction survives in memory before
#: it ever reaches `tables.write_table`; the write path needs no extra
#: nullable-column registry (unlike a nullable BOOLEAN, `astype("Int64")`
#: does not coerce `pd.NA` into a fabricated value the way `astype("bool")`
#: does) -- verified end to end through `write_table`/`pd.read_parquet` in
#: `tests/test_register.py`.
REGISTER_SCHEMA = pa.schema(
    [
        pa.field("site_id", pa.string()),
        pa.field("site_name", pa.string()),
        pa.field("commodity", pa.string()),
        pa.field("stage", pa.string()),
        pa.field("owners_at_snapshot", pa.string()),
        pa.field("snapshot_date", pa.string()),
        pa.field("lon", pa.float64()),
        pa.field("lat", pa.float64()),
        pa.field("n_tenements_intersecting", pa.int64()),
        pa.field("inclusion_status", pa.string()),
    ]
)

#: The six `inclusion_status` categories, in the fixed order every counts
#: table (`register_counts`, the reconciliation report) reports them.
INCLUSION_STATUSES: tuple[str, ...] = (
    "operating",
    "care_and_maintenance",
    "closed",
    "deposit",
    "prospect",
    "other",
)

#: Fallback category for a `stage` value not in `STAGE_TO_INCLUSION` (an
#: unmapped string, a null, or a non-string value) -- the row is NEVER
#: dropped for carrying an unrecognised stage; see this module's docstring.
_OTHER = "other"

#: Maps a MINEDEX `Stage` string, verbatim, to one of `INCLUSION_STATUSES`.
#: The measured 2026-08-14 DASC extract vocabulary (decisions doc §1/§3),
#: not a guess: `Undeveloped` -> `deposit` and `Proposed` -> `prospect` are
#: the two name-obvious mappings; `Under Development` has no equally obvious
#: slot and lands in `other` deliberately, per the ruling.
STAGE_TO_INCLUSION: dict[str, str] = {
    "Operating": "operating",
    "Care and Maintenance": "care_and_maintenance",
    "Shut": "closed",
    "Undeveloped": "deposit",
    "Proposed": "prospect",
    "Under Development": "other",
}

#: Key `register_counts` uses for the row total, and the key `reconcile_
#: counts` checks every other key sums to.
TOTAL_KEY = "total"

#: `Sites.csv` columns `build_register` requires -- the DASC MINEDEX CSV
#: bundle's own column names, verbatim; the CLI renames nothing before
#: calling this function.
_REQUIRED_SITES_COLUMNS: frozenset[str] = frozenset(
    {"SiteCode", "Title", "Stage", "Commodities", "ProjectCode", "Latitude", "Longitude"}
)

#: `ProjectsOwners.csv` columns `owners_by_project` requires.
_REQUIRED_OWNERS_COLUMNS: frozenset[str] = frozenset(
    {"ProjectCode", "OwnerCode", "OwnerName", "HoldingPct", "EndDate"}
)


class NoSnapshotFoundError(Exception):
    """No dated snapshot directory exists for a source under the data root.

    Always names the offending `source_id` in its message, so a caller does
    not have to reconstruct which source is missing from a bare traceback.
    """


def latest_snapshot(root: Path, source_id: str) -> Path:
    """Return the most recent date-named snapshot directory for `source_id`
    under `<root>/raw/<source_id>/`.

    Snapshot directories are named `YYYY-MM-DD` (`snapshots.create_snapshot_
    dir`), so the most recent is found by comparing PARSED dates, not by
    lexicographic string sort. Only directories whose name parses as
    `date.fromisoformat` are considered -- both handled by `snapshots.
    latest_dated_subdir`, which this function calls; it never re-implements
    that scan (`cli._latest_curated_dated_dir` calls the identical shared
    function for the curated-artefact case, and neither loop is a copy of
    the other's).

    Raises `NoSnapshotFoundError`, naming `source_id`, when `<root>/raw/
    <source_id>/` does not exist or contains no date-named subdirectory.
    """
    source_dir = Path(root) / "raw" / source_id
    result = latest_dated_subdir(source_dir)
    if result is None:
        raise NoSnapshotFoundError(
            f"no dated snapshot directory found for source {source_id!r} under "
            f"{source_dir} -- fetch a snapshot for this source first"
        )
    return result


def _classify_inclusion_status(stage: object) -> str:
    """`stage` -> one of `INCLUSION_STATUSES`, via `STAGE_TO_INCLUSION`.

    A non-string value (null, `NaN`, anything else a CSV column can carry)
    and any string not in `STAGE_TO_INCLUSION` both fall to `_OTHER` -- the
    row survives either way, per this module's row-survival guarantee.
    """
    if isinstance(stage, str):
        return STAGE_TO_INCLUSION.get(stage, _OTHER)
    return _OTHER


def _normalize_owner_name(name: object) -> str:
    """Casefold, whitespace-collapsed form of `OwnerName`, for sort ordering
    ONLY -- never rendered. A non-string value sorts as the empty string."""
    if not isinstance(name, str):
        return ""
    return " ".join(name.split()).casefold()


#: The rendered text for a null, non-string, or blank-after-strip
#: `OwnerName` -- see `_format_owner_name`. D8's ruling text gives no rule
#: for a missing owner NAME (only for a missing holding PERCENTAGE, via
#: `"(holding not stated)"`); this is this project's own decision, in the
#: same parenthetical-placeholder style, made explicit here rather than
#: left for a reader to infer from a fabricated `"nan"` string.
_OWNER_NAME_NOT_STATED = "(owner name not stated)"


def _format_owner_name(name: object) -> str:
    """Render one `OwnerName` value for the canonical string.

    A non-string value (`pd.NA`, `float("nan")`, `None`) or a value that is
    blank after stripping leading/trailing whitespace renders as the
    literal `_OWNER_NAME_NOT_STATED` -- never a fabricated empty name and
    never the literal text `"nan"` a bare `str(name)` would produce on a
    float `NaN`. A genuine name has only its leading/trailing whitespace
    stripped; internal spacing is preserved exactly, unchanged from before
    this function existed.
    """
    if not isinstance(name, str):
        return _OWNER_NAME_NOT_STATED
    stripped = name.strip()
    if stripped == "":
        return _OWNER_NAME_NOT_STATED
    return stripped


def _format_holding_pct(value: object) -> str | None:
    """Render one `HoldingPct` value per D8, or `None` for "not stated".

    `None` (renders downstream as `"(holding not stated)"`) for a missing,
    blank, or literal `"Unknown"` value (case-insensitive) -- `pd.isna`
    covers `NaN`/`None`/`pd.NA` alike.

    Otherwise the value AS STATED BY THE SOURCE, with only a trailing
    `.00`/trailing zero trimmed (`100.00` -> `"100"`, `12.50` -> `"12.5"`)
    -- never inferred, never normalised beyond that trim, and in
    particular NEVER ROUNDED to a fixed number of decimal places: a value
    the source states to three or more decimal digits (`33.333333`,
    `12.345`, `0.004`) is rendered at its full stated precision, trailing
    zeros trimmed the same way. This is a `decimal.Decimal` parse rather
    than a `float` one specifically to make that true -- `float(text)`
    followed by Python's `:.2f` format ROUNDS any value carrying more than
    two decimal digits (`33.335` -> `"33.34"`, `0.004` -> `"0"`, a non-zero
    stated share silently published as zero), which is a transformation
    this function's docstring did not admit to and D8's ruling text
    ("Preserve stated percentages without inferring missing shares") does
    not license. A value that does not parse as a number at all is
    rendered verbatim rather than silently downgraded to "not stated",
    which would misstate what the source actually carries.
    """
    if pd.isna(value):
        return None
    text = str(value).strip()
    if text == "" or text.casefold() == "unknown":
        return None
    try:
        numeric = Decimal(text)
    except InvalidOperation:
        return text
    if numeric == 0:
        return "0"
    # `format(..., "f")` renders in plain fixed-point notation -- without
    # it, `Decimal.normalize()` on a value like `Decimal("100.00")` returns
    # `Decimal("1E+2")`, whose default `str()` is scientific notation
    # (`"1E+2"`), not the trimmed decimal digits this function promises.
    formatted = format(numeric.normalize(), "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def _render_owner(name: str, holding_text: object) -> str:
    """One owner's canonical rendering, D8: `"Name (P%)"` or
    `"Name (holding not stated)"`.

    `name` is already the FORMATTED name -- the caller passes it through
    `_format_owner_name` first, so this function never sees a null or
    non-string value and never needs to decide what one renders as.

    `holding_text` is checked with `pd.isna`, not `is None` -- a value
    round-tripped through a pandas Series can surface as `float("nan")`
    rather than the `None` `_format_holding_pct` returned, and `pd.isna`
    catches both.
    """
    if pd.isna(holding_text):
        return f"{name} (holding not stated)"
    return f"{name} ({holding_text}%)"


def owners_by_project(owners_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate `ProjectsOwners.csv`'s CURRENT owner records to one row per
    `ProjectCode`, indexed by `ProjectCode`.

    A CURRENT relationship is one whose `EndDate` is null or blank (the
    MINEDEX data dictionary's own definition, D8's ruling text) -- a
    non-current (ended) relationship never contributes to any column below.

    Columns:

    - `owners_at_snapshot` (`str`): every current owner for the project,
      rendered `"Owner A (60%); Owner B (40%)"`, joined with `"; "`. Sorted
      by normalized `OwnerName` (casefold, whitespace-collapsed;
      `_normalize_owner_name`) with `OwnerCode` as the tie-breaker --
      normalization decides SORT ORDER only, the rendered name is `OwnerName`
      with only its leading/trailing whitespace stripped -- internal spacing
      is left exactly as the source carries it, never collapsed. A null,
      non-string, or blank-after-strip `OwnerName` renders as
      `"(owner name not stated)"` (`_format_owner_name`) -- D8's ruling text
      gives no rule for a missing NAME, only a missing PERCENTAGE, and this
      project's own decision is recorded there rather than left to a
      fabricated `"nan"` string. Each owner's holding renders via
      `_format_holding_pct`/`_render_owner` -- see those functions for the
      exact percentage-trimming and "not stated" rules.
    - `n_current_owners` (`int`): count of DISTINCT current
      `(ProjectCode, OwnerCode)` relationships for the project, after
      deduplication (below).
    - `n_missing_holding_pct` (`int`): count of those distinct current
      owners whose `HoldingPct` renders as "not stated"
      (`_format_holding_pct` returns `None`).
    - `n_missing_owner_name` (`int`): count of those distinct current
      owners whose `OwnerName` renders as `"(owner name not stated)"`
      (`_format_owner_name`) -- null, non-string, or blank after strip.
    - `n_duplicate_relationships` (`int`): count of EXCESS rows removed by
      deduplication -- see below.

    Deduplication: a project can carry the SAME `(ProjectCode, OwnerCode)`
    pair more than once among its current rows (a genuine defect in the
    source, not this project's construction). Every such pair is
    deduplicated DETERMINISTICALLY -- the current rows are first sorted by
    `(ProjectCode, OwnerCode)` under a STABLE sort (`kind="stable"`, which
    preserves each pair's original relative row order rather than an
    unspecified one), then `keep="first"` drops every row after the first
    for a repeated pair. This is deterministic regardless of the INPUT
    row order (unlike a bare `drop_duplicates(keep="first")` on
    unsorted data, whose "first" would depend on whichever order the
    caller happened to hand rows in). The count of rows dropped this way,
    per project, is `n_duplicate_relationships`.

    Raises `ValueError`, naming every missing column, when `owners_df` does
    not carry all of `_REQUIRED_OWNERS_COLUMNS`.

    A project with NO current rows at all is simply ABSENT from the
    returned frame's index -- `build_register`/`owner_join_disclosures`
    read that absence as "no resolvable current owner" for any site whose
    `ProjectCode` names it.
    """
    missing = sorted(_REQUIRED_OWNERS_COLUMNS - set(owners_df.columns))
    if missing:
        raise ValueError(
            f"owners_df is missing required column(s) {missing} "
            f"(columns present: {sorted(owners_df.columns)})"
        )

    empty_result = pd.DataFrame(
        {
            "owners_at_snapshot": pd.Series(dtype="object"),
            "n_current_owners": pd.Series(dtype="int64"),
            "n_missing_holding_pct": pd.Series(dtype="int64"),
            "n_missing_owner_name": pd.Series(dtype="int64"),
            "n_duplicate_relationships": pd.Series(dtype="int64"),
        }
    )
    empty_result.index.name = "ProjectCode"

    end_date_blank = owners_df["EndDate"].isna() | (
        owners_df["EndDate"].astype(str).str.strip() == ""
    )
    current = owners_df.loc[end_date_blank].copy()
    if current.empty:
        return empty_result

    # Deterministic dedup: stable sort by the pair, then keep the first
    # occurrence of each -- see the docstring above.
    current = current.sort_values(["ProjectCode", "OwnerCode"], kind="stable")
    is_duplicate = current.duplicated(subset=["ProjectCode", "OwnerCode"], keep="first")
    n_duplicate_relationships = is_duplicate.groupby(current["ProjectCode"]).sum().astype("int64")
    deduped = current.loc[~is_duplicate].copy()

    deduped["_normalized_name"] = deduped["OwnerName"].map(_normalize_owner_name)
    deduped = deduped.sort_values(["ProjectCode", "_normalized_name", "OwnerCode"], kind="stable")
    # NOT `.map(_format_holding_pct)`/`.astype(str)`: on pandas 3.0's default
    # nullable "string" dtype, `.map()` over a column that can return `None`
    # infers the result to the "str" extension dtype, which silently
    # rewrites `None` to `float("nan")` on iteration -- indistinguishable
    # from a real "nan" string by identity (`is None` fails) and rendered
    # literally as `"(nan%)"` downstream. `OwnerName.astype(str)` has the
    # SAME hazard from the other direction: at pandas 3.0.5, `astype(str)`
    # over a column carrying `pd.NA` does NOT stringify it -- it yields the
    # "str" extension dtype with the null surfacing as `float("nan")` on
    # iteration, which is not a `str`, so `name.strip()` two lines below
    # raised an uncaught `AttributeError` on any null `OwnerName` (a real
    # MINEDEX defect, demonstrated end to end through `build-register`).
    # Both columns go through the identical fix: an explicit `object`-dtype
    # `pd.Series` built from a list comprehension over the RAW column,
    # preserving exactly what each formatting function returned.
    deduped["_holding_text"] = pd.Series(
        [_format_holding_pct(v) for v in deduped["HoldingPct"]],
        index=deduped.index,
        dtype="object",
    )
    deduped["_owner_name_text"] = pd.Series(
        [_format_owner_name(v) for v in deduped["OwnerName"]],
        index=deduped.index,
        dtype="object",
    )
    deduped["_missing_holding"] = deduped["_holding_text"].isna()
    # Computed directly from the RAW column, not by comparing the rendered
    # `_owner_name_text` against `_OWNER_NAME_NOT_STATED` -- a genuine
    # `OwnerName` that happened to equal that exact placeholder string
    # would otherwise be misclassified as missing.
    deduped["_missing_owner_name"] = [
        not isinstance(v, str) or v.strip() == "" for v in deduped["OwnerName"]
    ]
    deduped["_rendered"] = [
        _render_owner(name, holding)
        for name, holding in zip(deduped["_owner_name_text"], deduped["_holding_text"], strict=True)
    ]

    grouped = deduped.groupby("ProjectCode", sort=False)
    result = pd.DataFrame(
        {
            "owners_at_snapshot": grouped["_rendered"].apply(lambda s: "; ".join(s)),
            "n_current_owners": grouped.size().astype("int64"),
            "n_missing_holding_pct": grouped["_missing_holding"].sum().astype("int64"),
            "n_missing_owner_name": grouped["_missing_owner_name"].sum().astype("int64"),
        }
    )
    result["n_duplicate_relationships"] = n_duplicate_relationships.reindex(
        result.index, fill_value=0
    ).astype("int64")
    result.index.name = "ProjectCode"
    return result


def owner_join_disclosures(sites_df: pd.DataFrame, owners_df: pd.DataFrame) -> dict[str, int]:
    """Count the `Sites.csv` <-> `ProjectsOwners.csv` owner join, per D8's
    manifest requirement -- SEVEN keys, each a count over a population named
    below, never a rate.

    - `sites_total`: every row of `sites_df` (the join's whole population).
    - `n_sites_missing_project_code`: `sites_df` rows whose `ProjectCode` is
      null or blank -- population: `sites_total`.
    - `n_sites_unmatched_project_code`: `sites_df` rows whose `ProjectCode`
      IS present (non-null, non-blank) but does not appear in
      `owners_by_project(owners_df)`'s index -- i.e. `ProjectsOwners.csv`
      carries no CURRENT relationship for that project, whether because the
      code never appears there at all or every relationship it once had has
      ended. Population: rows with a present `ProjectCode`.
    - `n_sites_no_current_owner`: population and computed value are
      IDENTICAL to `n_sites_unmatched_project_code` above -- "a project code
      that resolves to no current-owner record" and "a project code absent
      from `ProjectsOwners.csv`'s current records" are the same condition,
      so this key carries the same number under the vocabulary D8's ruling
      text uses. Kept as its own declared key (per the ruling) rather than
      dropped, with this identity stated here rather than left for a reader
      to re-derive.
    - `n_sites_multiple_current_owners`: `sites_df` rows whose resolved
      project carries MORE than one current owner
      (`owners_by_project`'s `n_current_owners > 1`). Population: rows with
      a MATCHED `ProjectCode` (i.e. `sites_total` minus the missing and
      unmatched counts above).
    - `n_projects_missing_holding_pct`: DISTINCT projects (rows of
      `owners_by_project`'s result) carrying at least one current owner
      whose `HoldingPct` is not stated. Population: every project with at
      least one current owner (`owners_by_project`'s own row count) -- a
      PROJECT-level count, not a site-level one, since one project's
      missing holding percentage is a single fact regardless of how many
      sites cite that project.
    - `n_duplicate_current_owner_relationships`: total EXCESS duplicate
      current-owner rows across every project (the sum of
      `owners_by_project`'s `n_duplicate_relationships` column). Population:
      every current row of `owners_df`.

    Raises `ValueError` when `sites_df` carries no `ProjectCode` column, or
    propagates `owners_by_project`'s own `ValueError` when `owners_df` is
    missing a required column.
    """
    if "ProjectCode" not in sites_df.columns:
        raise ValueError(
            f"sites_df is missing required column 'ProjectCode' "
            f"(columns present: {sorted(sites_df.columns)})"
        )

    owners_summary = owners_by_project(owners_df)
    owners_summary = owners_summary.set_axis(owners_summary.index.astype(str))

    project_codes = sites_df["ProjectCode"]
    project_code_present = project_codes.notna() & (project_codes.astype(str).str.strip() != "")
    n_sites_missing_project_code = int((~project_code_present).sum())

    present_codes = project_codes.loc[project_code_present].astype(str)
    matched = present_codes.isin(owners_summary.index)
    n_sites_unmatched_project_code = int((~matched).sum())
    # Same population, same value -- see the docstring's
    # `n_sites_no_current_owner` paragraph for why these two collapse.
    n_sites_no_current_owner = n_sites_unmatched_project_code

    matched_codes = present_codes.loc[matched]
    matched_current_owner_counts = owners_summary["n_current_owners"].reindex(
        matched_codes.to_numpy()
    )
    n_sites_multiple_current_owners = int((matched_current_owner_counts.to_numpy() > 1).sum())

    n_projects_missing_holding_pct = int((owners_summary["n_missing_holding_pct"] > 0).sum())
    n_duplicate_current_owner_relationships = int(owners_summary["n_duplicate_relationships"].sum())

    return {
        "sites_total": len(sites_df),
        "n_sites_missing_project_code": n_sites_missing_project_code,
        "n_sites_unmatched_project_code": n_sites_unmatched_project_code,
        "n_sites_no_current_owner": n_sites_no_current_owner,
        "n_sites_multiple_current_owners": n_sites_multiple_current_owners,
        "n_projects_missing_holding_pct": n_projects_missing_holding_pct,
        "n_duplicate_current_owner_relationships": n_duplicate_current_owner_relationships,
    }


#: Keys, in a fixed order, `owner_join_disclosures` always returns -- used by
#: `build_reconciliation_report` to render them in a stable order and by
#: tests asserting the dict's exact key set.
OWNER_JOIN_DISCLOSURE_KEYS: tuple[str, ...] = (
    "sites_total",
    "n_sites_missing_project_code",
    "n_sites_unmatched_project_code",
    "n_sites_no_current_owner",
    "n_sites_multiple_current_owners",
    "n_projects_missing_holding_pct",
    "n_duplicate_current_owner_relationships",
)


#: Keys, in a fixed order, `owner_row_composition` always returns -- same
#: discipline as `OWNER_JOIN_DISCLOSURE_KEYS`/`TENEMENT_COUNT_DISCLOSURE_KEYS`.
OWNER_ROW_COMPOSITION_KEYS: tuple[str, ...] = (
    "owner_rows_total",
    "n_owner_rows_current",
    "n_owner_rows_ended",
)


def owner_row_composition(owners_df: pd.DataFrame) -> dict[str, int]:
    """Split `owners_df` (`ProjectsOwners.csv`) rows into CURRENT (blank
    `EndDate`) and ENDED (non-blank `EndDate`) -- D12.2
    (`docs/decisions/2026-08-16-d9-d12-commit-remote-naming-sequencing.md`).

    `owners_by_project`'s CURRENT filter (D8: `EndDate` null or blank) is
    what `owners_at_snapshot` is built from, and the real 2026-08-14 extract
    happens to be current-only -- every `ProjectsOwners.csv` row carries a
    blank `EndDate` -- so that filter has had no bite and nothing pinned or
    disclosed the property. This is the identical disclosure `sources.
    minedex.validate_minedex_bundles` makes at fetch time
    (`n_owner_rows_current`/`n_owner_rows_ended`), computed here directly off
    the `ProjectsOwners.csv` frame `build-register` already has in hand --
    the register manifest is where a reader adjudicates owner semantics, so
    the composition is disclosed there too rather than left to be re-derived
    from the MINEDEX snapshot's own validation summary.

    Disclosure, not refusal: an extract carrying ended rows is a valid
    extract, and `owner_join_disclosures`/`owners_by_project`'s CURRENT-only
    selection is unaffected either way.

    - `owner_rows_total`: every row of `owners_df` -- the population the
      other two keys are counted against.
    - `n_owner_rows_current`: rows whose `EndDate` is null or blank (empty
      after `str().strip()`) -- the same `end_date_blank` condition
      `owners_by_project` filters on.
    - `n_owner_rows_ended`: rows whose `EndDate` is non-blank.

    Reconciles by construction: `n_owner_rows_current + n_owner_rows_ended
    == owner_rows_total`, since the two are exact complements of the same
    boolean partition.

    Raises `ValueError`, naming the column, when `owners_df` carries no
    `EndDate` column.
    """
    if "EndDate" not in owners_df.columns:
        raise ValueError(
            f"owners_df is missing required column 'EndDate' "
            f"(columns present: {sorted(owners_df.columns)})"
        )

    end_date_blank = owners_df["EndDate"].isna() | (
        owners_df["EndDate"].astype(str).str.strip() == ""
    )
    return {
        "owner_rows_total": len(owners_df),
        "n_owner_rows_current": int(end_date_blank.sum()),
        "n_owner_rows_ended": int((~end_date_blank).sum()),
    }


def _count_intersecting_tenements(
    located_gdf: gpd.GeoDataFrame, tenements_gdf: gpd.GeoDataFrame
) -> pd.Series:
    """Per row of `located_gdf` (MINEDEX sites already filtered to those
    carrying both `Latitude` and `Longitude` -- see `build_register`), the
    count of tenement polygons whose geometry intersects that row's point --
    0 when none, via a spatial join. Returned `Series` is indexed
    identically to `located_gdf`.

    `located_gdf` is reprojected to `tenements_gdf`'s OWN CRS before the
    join, when the two differ -- the direction is deliberately TENEMENTS ->
    unchanged, MINEDEX POINTS -> reprojected: the points are constructed by
    `build_register` under a DECLARED CRS (`MINEDEX_SITES_SOURCE_CRS`), not
    read off a live geometry column, while `tenements_gdf`'s CRS comes
    straight off the measured DASC shapefile being joined against.

    A non-empty `tenements_gdf` carrying no CRS is refused -- reprojecting
    or comparing against an unset CRS cannot be done safely, and letting it
    fall through to `sjoin` on two frames GeoPandas treats as compatible
    only by silently assuming they share a CRS would silently produce
    `n_tenements_intersecting = 0` for every site.

    `located_gdf.index` must be UNIQUE -- refused, naming every duplicated
    value, otherwise. D12.3 triage, finding 5: the reduction below groups
    `sjoin`'s output by `level=0` (index LABEL, not row), so two rows
    sharing an index value are silently merged into one group and each
    reports the UNION of both rows' matches rather than its own count.
    `build_register` (this function's only CLI-reachable caller) always
    builds `located_gdf` off a de-duplicated subset of its own frame's
    `RangeIndex`, so this never fires through the CLI today -- it is a
    latent trap for any other caller, closed here rather than left for the
    next one to rediscover.
    """
    if len(tenements_gdf) == 0:
        return pd.Series(0, index=located_gdf.index, dtype="int64")

    if not located_gdf.index.is_unique:
        duplicated = sorted(
            located_gdf.index[located_gdf.index.duplicated(keep=False)].unique().tolist()
        )
        raise ValueError(
            f"located_gdf has a non-unique index -- duplicated value(s) {duplicated} -- "
            "_count_intersecting_tenements groups its sjoin output by index level 0, so a "
            "duplicated index label silently merges the rows sharing it into one group and "
            "reports the union of their matches as each row's own count; pass a located_gdf "
            "with a unique index (e.g. reset_index() to a fresh RangeIndex first)"
        )

    if tenements_gdf.crs is None:
        raise ValueError(
            "tenements_gdf.crs is not set -- n_tenements_intersecting cannot be safely "
            "computed without knowing the target CRS to reproject the located MINEDEX "
            "points into; set tenements_gdf.crs to the shapefile's actual CRS before "
            "calling build_register"
        )

    join_points = located_gdf
    if join_points.crs != tenements_gdf.crs:
        join_points = join_points.to_crs(tenements_gdf.crs)

    joined = gpd.sjoin(
        join_points[["geometry"]], tenements_gdf[["geometry"]], how="left", predicate="intersects"
    )
    counts = joined.groupby(level=0)["index_right"].apply(
        lambda matches: int(matches.notna().sum())
    )
    return counts.reindex(located_gdf.index, fill_value=0).astype("int64")


def build_register(
    minedex_sites_df: pd.DataFrame,
    owners_df: pd.DataFrame,
    tenements_gdf: gpd.GeoDataFrame,
    snapshot_date: str,
) -> pd.DataFrame:
    """Build the Tier 0 register: one row per record in `minedex_sites_df`
    (`Sites.csv`, D6's CSV bundle).

    Output columns, in order (see `REGISTER_SCHEMA`): `site_id`,
    `site_name`, `commodity`, `stage`, `owners_at_snapshot`, `snapshot_date`,
    `lon`, `lat`, `n_tenements_intersecting`, `inclusion_status`.

    - `site_id`/`site_name`/`commodity`/`stage` are `minedex_sites_df`'s own
      `SiteCode`/`Title`/`Commodities`/`Stage` columns, verbatim.
    - `owners_at_snapshot` (D8) is `owners_by_project(owners_df)` joined
      through `ProjectCode` -- `pd.NA` when the site has no resolvable
      current owner, which covers BOTH a missing/blank `ProjectCode` and a
      present `ProjectCode` absent from `ProjectsOwners.csv`'s current
      records (`owner_join_disclosures` counts each separately). Owners are
      never labelled operators.
    - `snapshot_date` is filled with the `snapshot_date` argument, verbatim,
      on every row.
    - `lon`/`lat`: point geometry is constructed HERE from `Latitude`/
      `Longitude`, under the DECLARED `MINEDEX_SITES_SOURCE_CRS`, then
      reprojected to `REGISTER_LONLAT_CRS` before `.x`/`.y` are read. A row
      whose `Latitude` OR `Longitude` is null carries NO geometry at all --
      it is never given a fabricated `Point(NaN, NaN)` -- and `lon`/`lat`
      are `NaN` on that row (the row-survival guarantee: the row is kept,
      never dropped, and is COUNTED by `count_rows_without_location`).
    - `n_tenements_intersecting`: a spatial join (point-in-tenement-polygon)
      against `tenements_gdf`, computed ONLY over rows that carry both
      coordinates. A coordinate-less row takes NO part in the join and is
      `pd.NA` -- NOT COMPUTED, D12.2 -- never a fabricated `0`: "no tenement
      intersects this located site" (a fired zero) and "this site could not
      be located so the count was never computed" are different facts, and
      conflating them was the defect this ruling closes. A located row's
      count is a genuine computed integer, including `0` when the join
      found no intersecting tenement.
    - `inclusion_status` is `Stage` classified via `STAGE_TO_INCLUSION`
      (`_classify_inclusion_status`) -- an unmapped stage lands in `other`
      and the row is never dropped.

    Raises `ValueError`, naming every missing column, when
    `minedex_sites_df` does not carry all of `_REQUIRED_SITES_COLUMNS`, or
    when `owners_by_project`/`_count_intersecting_tenements` raise their own
    declared `ValueError`s (a missing `owners_df` column; an unset,
    non-empty `tenements_gdf.crs`).
    """
    missing = sorted(_REQUIRED_SITES_COLUMNS - set(minedex_sites_df.columns))
    if missing:
        raise ValueError(
            f"minedex_sites_df is missing required column(s) {missing} "
            f"(columns present: {sorted(minedex_sites_df.columns)})"
        )

    has_coordinates = minedex_sites_df["Latitude"].notna() & minedex_sites_df["Longitude"].notna()

    located_index = minedex_sites_df.index[has_coordinates]
    located_points = [
        Point(lon, lat)
        for lon, lat in zip(
            minedex_sites_df.loc[located_index, "Longitude"],
            minedex_sites_df.loc[located_index, "Latitude"],
            strict=True,
        )
    ]
    located_gdf = gpd.GeoDataFrame(
        index=located_index, geometry=located_points, crs=MINEDEX_SITES_SOURCE_CRS
    )

    # `pd.NA` for every row, under pandas' nullable "Int64" dtype -- D12.2:
    # NOT COMPUTED is the default, and only a row that actually goes through
    # `_count_intersecting_tenements` below (i.e. carries usable coordinates)
    # is overwritten with a genuine computed integer, including `0`. See
    # `REGISTER_SCHEMA`'s docstring for why this dtype survives the write
    # path without a separate nullable-column registry.
    n_tenements = pd.Series(pd.NA, index=minedex_sites_df.index, dtype="Int64")
    lon = pd.Series(float("nan"), index=minedex_sites_df.index, dtype="float64")
    lat = pd.Series(float("nan"), index=minedex_sites_df.index, dtype="float64")
    if len(located_gdf) > 0:
        n_tenements.loc[located_index] = _count_intersecting_tenements(located_gdf, tenements_gdf)
        located_lonlat = located_gdf.to_crs(REGISTER_LONLAT_CRS)
        lon.loc[located_index] = located_lonlat.geometry.x.to_numpy()
        lat.loc[located_index] = located_lonlat.geometry.y.to_numpy()
    elif len(tenements_gdf) > 0 and tenements_gdf.crs is None:
        # No located rows to join, but a non-empty, CRS-less tenements_gdf
        # is still an input defect `_count_intersecting_tenements` would
        # have refused on had there been any rows to join -- refuse the
        # same way rather than silently succeeding because the join never
        # ran.
        raise ValueError(
            "tenements_gdf.crs is not set -- n_tenements_intersecting cannot be safely "
            "computed without knowing the target CRS to reproject the located MINEDEX "
            "points into; set tenements_gdf.crs to the shapefile's actual CRS before "
            "calling build_register"
        )

    owners_summary = owners_by_project(owners_df)
    owners_summary = owners_summary.set_axis(owners_summary.index.astype(str))

    project_codes = minedex_sites_df["ProjectCode"]
    project_code_present = project_codes.notna() & (project_codes.astype(str).str.strip() != "")
    owners_at_snapshot = pd.Series(pd.NA, index=minedex_sites_df.index, dtype="object")
    present_index = minedex_sites_df.index[project_code_present]
    present_codes = project_codes.loc[present_index].astype(str)
    matched = owners_summary["owners_at_snapshot"].reindex(present_codes.to_numpy())
    matched = matched.where(matched.notna(), pd.NA)
    owners_at_snapshot.loc[present_index] = matched.to_numpy()

    return pd.DataFrame(
        {
            "site_id": minedex_sites_df["SiteCode"],
            "site_name": minedex_sites_df["Title"],
            "commodity": minedex_sites_df["Commodities"],
            "stage": minedex_sites_df["Stage"],
            "owners_at_snapshot": owners_at_snapshot,
            "snapshot_date": snapshot_date,
            "lon": lon,
            "lat": lat,
            "n_tenements_intersecting": n_tenements,
            "inclusion_status": minedex_sites_df["Stage"].map(_classify_inclusion_status),
        }
    ).reset_index(drop=True)


def count_rows_without_location(df: pd.DataFrame) -> int:
    """The number of `df` rows carrying no usable location -- `lon` or `lat`
    null (`NaN`). Also the population `resolved_args["n_sites_null_
    coordinates"]` (`cli.py`'s `build-register`) reports, read back off the
    BUILT register rather than off the source frame, so every route into
    the register is counted by the same definition.
    """
    return int((df["lon"].isna() | df["lat"].isna()).sum())


#: Keys `tenement_count_disclosure` always returns, in a fixed order -- same
#: discipline as `OWNER_JOIN_DISCLOSURE_KEYS`/`SITE_ID_DUPLICATION_KEYS`.
TENEMENT_COUNT_DISCLOSURE_KEYS: tuple[str, ...] = (
    "sites_total",
    "n_sites_tenement_count_computed",
    "n_sites_tenement_count_zero",
    "n_sites_tenement_count_not_computed",
)


def tenement_count_disclosure(df: pd.DataFrame) -> dict[str, int]:
    """Count `df["n_tenements_intersecting"]` by NOT COMPUTED vs. computed
    (D12.2): the same "a diagnostic that could not be computed is not a
    diagnostic that fired" discipline `owner_join_disclosures` and
    `site_id_duplication_counts` already apply to their own columns.

    - `sites_total`: every row of `df` -- the population the other three
      keys are counted against, so a caller can check the reconciliation
      identity below without a separate `len(df)` call.
    - `n_sites_tenement_count_computed`: rows whose `n_tenements_
      intersecting` is NOT null -- the spatial join actually ran for these
      (the row carried usable coordinates).
    - `n_sites_tenement_count_zero`: the SUBSET of computed rows whose
      count is exactly `0` -- a genuine "no tenement intersects this
      located site" result, never confused with a not-computed row.
    - `n_sites_tenement_count_not_computed`: rows whose `n_tenements_
      intersecting` IS null -- coordinate-less rows the join never ran
      against (`build_register` never fabricates a `0` for these).

    Reconciles by construction: `n_sites_tenement_count_computed +
    n_sites_tenement_count_not_computed == sites_total`, since "computed"
    and "not computed" are defined as exact complements of the same
    null/non-null partition of `df["n_tenements_intersecting"]`.
    """
    computed = df["n_tenements_intersecting"].notna()
    not_computed = ~computed
    return {
        "sites_total": len(df),
        "n_sites_tenement_count_computed": int(computed.sum()),
        "n_sites_tenement_count_zero": int(
            (df.loc[computed, "n_tenements_intersecting"] == 0).sum()
        ),
        "n_sites_tenement_count_not_computed": int(not_computed.sum()),
    }


#: Keys `site_id_duplication_counts` always returns, in a fixed order --
#: same discipline as `OWNER_JOIN_DISCLOSURE_KEYS`.
SITE_ID_DUPLICATION_KEYS: tuple[str, ...] = (
    "n_duplicate_site_id_values",
    "n_site_id_rows_duplicated",
)


def site_id_duplication_counts(df: pd.DataFrame) -> dict[str, int]:
    """Count `df["site_id"]` duplication -- the register is now one row per
    `Sites.csv` record (this module's docstring), so `site_id`
    (`Sites.csv["SiteCode"]`, copied verbatim by `build_register`) is no
    longer guaranteed unique: `Sites.csv` itself carries measured
    duplication on the real product (`sources.minedex.
    validate_minedex_bundles`'s docstring records 1,327 duplicated
    `SiteCode` values across 1,411 excess rows in the 2026-08-14 extract),
    and `build_register` does not deduplicate it.

    - `n_duplicate_site_id_values`: count of DISTINCT `site_id` values that
      appear more than once in `df`.
    - `n_site_id_rows_duplicated`: total ROW count across those duplicated
      values (every row sharing a duplicated value, not just the excess
      beyond the first) -- the identical vocabulary
      `validate_minedex_bundles` uses for the same quantity measured
      directly off `Sites.csv`.

    A null `site_id` is EXCLUDED from both counts: this function measures
    collisions BETWEEN populated values, and two nulls are never counted as
    "the same" `site_id` here (a null `site_id` is disclosed by the row's
    own carried fields, not by this function; the register schema declares
    `site_id` non-nullable, so a null one is itself a schema violation, not
    the duplication property this function measures).

    Disclosed, not refused -- the same treatment `validate_minedex_bundles`
    already gives the identical property of `Sites.csv` itself: this is a
    measured property of a healthy real MINEDEX extract, not a defect in
    this build. `build_reconciliation_report` renders these counts without
    letting them decide the reconciliation verdict, and `crosswalk.
    filter_register_for_crosswalk` is what actually acts on them, by
    excluding the affected rows from the crosswalk's input population.
    """
    site_id = df["site_id"].dropna().astype(str)
    value_counts = site_id.value_counts()
    duplicated = value_counts[value_counts > 1]
    return {
        "n_duplicate_site_id_values": len(duplicated),
        "n_site_id_rows_duplicated": int(duplicated.sum()),
    }


def register_counts(df: pd.DataFrame) -> dict[str, int]:
    """Count `df`'s rows per `inclusion_status`, plus a `TOTAL_KEY` total.

    Always returns one key per `INCLUSION_STATUSES` entry (0 when a
    category has no rows) plus `TOTAL_KEY` -- a fixed key set, so the
    reconciliation report and `reconcile_counts` never have to guess which
    categories a given register happened to produce.
    """
    counts = {
        status: int((df["inclusion_status"] == status).sum()) for status in INCLUSION_STATUSES
    }
    counts[TOTAL_KEY] = len(df)
    return counts


def reconcile_counts(counts: dict[str, int]) -> dict[str, int]:
    """Return `counts` unchanged when its category values sum to `TOTAL_KEY`;
    raise `ValueError`, naming the gap, when they do not.

    Sums every key except `TOTAL_KEY` -- a `counts` dict missing `TOTAL_KEY`
    altogether is itself a reconciliation failure, not a silent 0.
    """
    if TOTAL_KEY not in counts:
        raise ValueError(f"counts dict carries no {TOTAL_KEY!r} key to reconcile against: {counts}")

    category_sum = sum(value for key, value in counts.items() if key != TOTAL_KEY)
    total = counts[TOTAL_KEY]
    if category_sum != total:
        raise ValueError(
            f"register counts do not reconcile: category counts sum to {category_sum}, "
            f"but {TOTAL_KEY!r} is {total} (gap of {total - category_sum})"
        )
    return counts


@dataclass(frozen=True)
class ReconciliationReport:
    """The Tier 0 register's reconciliation report: rendered Markdown plus
    the pass/fail verdict it renders, kept together so a caller can never
    read one without the other and drift out of sync."""

    text: str
    passed: bool


def build_reconciliation_report(
    df: pd.DataFrame,
    counts: dict[str, int],
    *,
    minedex_feature_count: int,
    tenements_feature_count: int,
    owner_join_disclosures: dict[str, int] | None = None,
) -> ReconciliationReport:
    """Render the Tier 0 register's `reconciliation.md`, never raising.

    Two independent checks decide the verdict:

    1. `len(df)` (the register's own row count) equals `minedex_feature_
       count` -- one row per `Sites.csv` record. `minedex_feature_count`
       must come from an INDEPENDENT read of the snapshot on disk, never
       from `len()` of the same in-memory frame `build_register` was
       handed.
    2. `counts` reconciles against its own total (`reconcile_counts`).

    Both checks are recorded rather than raised, so a caller always gets a
    report describing exactly what passed or failed.

    Three further quantities are rendered but deliberately do NOT decide the
    verdict, since none is a defect in this build, only a property of
    the source snapshot: the count of register rows carrying no usable
    location (`count_rows_without_location` -- `lon`/`lat` null, rendered
    under the key name `n_sites_null_coordinates` too, matching D8's
    manifest vocabulary), `site_id` duplication
    (`site_id_duplication_counts` -- `SITE_ID_DUPLICATION_KEYS`, always
    rendered), and, when `owner_join_disclosures` is supplied, its seven
    counts (`OWNER_JOIN_DISCLOSURE_KEYS`) rendered in their own section.
    """
    register_row_count = len(df)
    row_count_ok = register_row_count == minedex_feature_count
    rows_without_location = count_rows_without_location(df)
    rows_with_location = register_row_count - rows_without_location
    site_id_duplication = site_id_duplication_counts(df)

    try:
        reconcile_counts(counts)
    except ValueError as exc:
        counts_ok = False
        counts_error: str | None = str(exc)
    else:
        counts_ok = True
        counts_error = None

    passed = row_count_ok and counts_ok

    lines = [
        "# Tier 0 register reconciliation",
        "",
        "## Source feature totals",
        "",
        f"- MINEDEX snapshot feature count: {minedex_feature_count}",
        f"- DMIRS-003 Tenements snapshot feature count: {tenements_feature_count}",
        f"- Register row count: {register_row_count}",
        "",
        "## Counts per inclusion_status",
        "",
        "| inclusion_status | count |",
        "|---|---|",
        *(f"| {status} | {counts.get(status, 0)} |" for status in (*INCLUSION_STATUSES, TOTAL_KEY)),
        "",
        "## Location coverage",
        "",
        f"- Register rows with a usable lon/lat: {rows_with_location}",
        f"- Register rows without a usable location (lon/lat null): {rows_without_location}",
        f"- Register row count: {register_row_count}",
        f"- n_sites_null_coordinates: {rows_without_location}",
        "",
        "## site_id duplication",
        "",
        *(f"- {key}: {site_id_duplication[key]}" for key in SITE_ID_DUPLICATION_KEYS),
        "",
    ]
    if owner_join_disclosures is not None:
        lines.append("## Owner-join disclosures")
        lines.append("")
        lines.extend(
            f"- {key}: {owner_join_disclosures.get(key, 0)}" for key in OWNER_JOIN_DISCLOSURE_KEYS
        )
        lines.append("")
    if rows_without_location:
        lines.append(
            f"{rows_without_location} register row(s) carry no usable location: the MINEDEX "
            "record's Latitude/Longitude were null, so lon/lat are null. Those rows are kept "
            "in the register (one row per source record) and are disclosed here rather than "
            "dropped; they cannot be matched against a Maus polygon, so build-crosswalk "
            "excludes them from its input population (`crosswalk.filter_register_for_crosswalk`) "
            "and discloses the excluded count rather than scoring them as unmatched."
        )
        lines.append("")
    if site_id_duplication["n_duplicate_site_id_values"]:
        lines.append(
            f"{site_id_duplication['n_duplicate_site_id_values']} distinct site_id value(s) "
            f"are duplicated, across {site_id_duplication['n_site_id_rows_duplicated']} register "
            "row(s) total: a measured property of Sites.csv's own SiteCode column, not a defect "
            "in this build. build-crosswalk excludes the affected rows from its input population "
            "(`crosswalk.filter_register_for_crosswalk`) rather than refusing the whole run."
        )
        lines.append("")
    if not row_count_ok:
        lines.append(
            f"Register row count ({register_row_count}) does not reconcile against the "
            f"MINEDEX snapshot's own feature count ({minedex_feature_count})."
        )
    if not counts_ok:
        lines.append(f"Category counts do not reconcile against their own total: {counts_error}")
    lines.append("")
    lines.append(f"**{'PASS' if passed else 'FAIL'}**")

    return ReconciliationReport(text="\n".join(lines) + "\n", passed=passed)


from wa_mine_monitor.dea_coverage import DEA_EPOCH_COLUMN_BY_SOURCE

#: The four appended coverage columns, in declared order (D13 C3/C4).
DEA_COVERAGE_COLUMNS: tuple[str, ...] = tuple(DEA_EPOCH_COLUMN_BY_SOURCE.values())

#: REGISTER_SCHEMA plus the four nullable epoch counts -- built FROM the
#: base schema so the two can never drift.
ENRICHED_REGISTER_SCHEMA = pa.schema(
    list(REGISTER_SCHEMA)
    + [pa.field(column, pa.int64(), nullable=True) for column in DEA_COVERAGE_COLUMNS]
)


class RegisterEnrichmentError(ValueError):
    """Enrichment would change row identity, count or order -- refused."""


def enrich_register_with_dea_coverage(
    register_df: pd.DataFrame, coverage_df: pd.DataFrame
) -> pd.DataFrame:
    """Append the four epoch-coverage columns; NEVER touch existing rows.

    Refuses on any site-set or order difference: enrichment is an append of
    columns, and a merge that drops, adds or reorders rows is a different
    register wearing the old one's name (D13 C4: before/after row totals
    equal, order byte-stable apart from the appended fields).
    """
    if len(coverage_df) != len(register_df):
        raise RegisterEnrichmentError(
            f"coverage has {len(coverage_df)} row(s) against the register's "
            f"{len(register_df)} -- row loss or gain is refused"
        )
    register_sites = register_df["site_id"].tolist()
    coverage_sites = coverage_df["site_id"].tolist()
    if register_sites != coverage_sites:
        if sorted(register_sites) == sorted(coverage_sites):
            raise RegisterEnrichmentError(
                "coverage site_id ORDER differs from the register -- reordering is refused"
            )
        raise RegisterEnrichmentError(
            "coverage site_id set differs from the register -- mismatched sites refused"
        )
    enriched = register_df.copy()
    for column in DEA_COVERAGE_COLUMNS:
        enriched[column] = coverage_df[column].array
    return enriched
