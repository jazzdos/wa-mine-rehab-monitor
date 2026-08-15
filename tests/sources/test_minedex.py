"""Tests for `wa_mine_monitor.sources.minedex`, the `fetch-minedex` CLI
command and the `adjudicate-minedex-licence` CLI command.

D6 acquisition route (2026-08-16): DMIRS-001 MINEDEX is acquired as TWO
unauthenticated DASC bundle zips (SHP file id 3978, CSV file id 3981), not
the auth-gated SLIP direct-download GeoPackage this file used to test
against -- see `sources/minedex.py`'s module docstring for the ruling. D7
keeps MINEDEX public redistribution closed: `fetch-minedex` only CAPTURES
licence evidence (`explicit_grant`/`contrary_notice` both `null`,
`adjudicated: false`); the separate `adjudicate-minedex-licence` command
applies the ruling to an already-finalized snapshot and the gate stays
`False` either way.

Fixture-first rule: no test in this file touches the network. The pure
validation tests build toy DASC-shaped zips in-test with geopandas/pandas
(via `tests.sources._fixtures`), never a committed binary fixture. The
download/metadata-fetch functions' request-shaping is checked against a fake
`requests.get`, never a real endpoint. The CLI tests monkeypatch
`wa_mine_monitor.cli.download_minedex_zip` and
`wa_mine_monitor.cli.fetch_datawa_package_show` outright -- a live download or
metadata fetch only ever happens when an operator runs the CLI for real.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Self

import geopandas as gpd
import pandas as pd
import pytest
import requests
from shapely.geometry import Point
from typer.testing import CliRunner

from tests.sources._fixtures import (
    PLACEHOLDER_LICENCE_PDF_BYTES,
    shapefile_members,
    write_zip,
)
from wa_mine_monitor import cli as cli_module
from wa_mine_monitor import licence, manifests, snapshots
from wa_mine_monitor.cli import app
from wa_mine_monitor.provenance import sha256_file
from wa_mine_monitor.sources import minedex

runner = CliRunner()

# --- fixture builders -------------------------------------------------------


def _csv_bytes(df: pd.DataFrame) -> bytes:
    """`utf-8-sig` -- the measured DASC CSV convention every real
    `Sites.csv`/`ProjectsOwners.csv` carries a BOM under."""
    return df.to_csv(index=False).encode("utf-8-sig")


def _build_minedex_shp_gdf(
    *,
    site_codes: list[str],
    crs: str = "EPSG:7844",
    extract_date: str | list[str] = "14/08/2026",
    geometry: list[object] | None = None,
) -> gpd.GeoDataFrame:
    n = len(site_codes)
    if geometry is None:
        geometry = [Point(115.0 + i * 0.01, -32.0 - i * 0.01) for i in range(n)]
    extract_dates = extract_date if isinstance(extract_date, list) else [extract_date] * n
    return gpd.GeoDataFrame(
        {"SITE_CODE": site_codes, "EXTRACT_DA": extract_dates, "geometry": geometry},
        crs=crs,
    )


def _write_minedex_shp_zip(
    zip_path: Path,
    tmp_dir: Path,
    *,
    site_codes: list[str],
    crs: str = "EPSG:7844",
    extract_date: str | list[str] = "14/08/2026",
    omit_members: tuple[str, ...] = (),
    drop_columns: tuple[str, ...] = (),
    geometry: list[object] | None = None,
) -> Path:
    gdf = _build_minedex_shp_gdf(
        site_codes=site_codes, crs=crs, extract_date=extract_date, geometry=geometry
    )
    if drop_columns:
        gdf = gdf.drop(columns=list(drop_columns))
    members = shapefile_members(gdf, tmp_dir, minedex.MINEDEX_SHAPEFILE_BASENAME)
    members["MINEDEX_Database_DataDictionary_GDA2020.pdf"] = b"placeholder data dictionary"
    members["Minedex_Database_Metadata_GDA2020.pdf"] = b"placeholder metadata"
    members["Licence_CCBY4.pdf"] = PLACEHOLDER_LICENCE_PDF_BYTES
    for name in omit_members:
        members.pop(name, None)
    return write_zip(zip_path, members)


def _build_sites_df(
    *,
    site_codes: list[str],
    stages: list[str],
    project_codes: list[str | None],
    extract_date: str | list[str] = "14/08/2026",
) -> pd.DataFrame:
    n = len(site_codes)
    extract_dates = extract_date if isinstance(extract_date, list) else [extract_date] * n
    return pd.DataFrame(
        {
            "SiteCode": site_codes,
            "ShortTitle": [f"Site {c}" for c in site_codes],
            "Stage": stages,
            "ProjectCode": project_codes,
            "Latitude": [-32.0 - i * 0.01 for i in range(n)],
            "Longitude": [115.0 + i * 0.01 for i in range(n)],
            "EXTRACT_DATE": extract_dates,
        }
    )


def _build_owners_df(
    *,
    project_codes: list[str],
    end_dates: list[str] | None = None,
    extract_date: str | list[str] = "14/08/2026",
) -> pd.DataFrame:
    n = len(project_codes)
    extract_dates = extract_date if isinstance(extract_date, list) else [extract_date] * n
    return pd.DataFrame(
        {
            "ProjectCode": project_codes,
            "OwnerCode": [f"O{i}" for i in range(n)],
            "OwnerName": [f"Owner {i}" for i in range(n)],
            "HoldingPct": [100.0] * n,
            "StartDate": ["01/01/2020"] * n,
            "EndDate": end_dates if end_dates is not None else [""] * n,
            "EXTRACT_DATE": extract_dates,
        }
    )


def _write_minedex_csv_zip(
    zip_path: Path,
    *,
    sites_df: pd.DataFrame,
    owners_df: pd.DataFrame,
    omit_members: tuple[str, ...] = (),
) -> Path:
    members = {
        "Licence_CCBY4.pdf": PLACEHOLDER_LICENCE_PDF_BYTES,
        "MINEDEX_CSV_DataDictionary_GDA2020.pdf": b"placeholder data dictionary",
        "Minedex_Database_Metadata_GDA2020.pdf": b"placeholder metadata",
        "Sites.csv": _csv_bytes(sites_df),
        "ProjectsOwners.csv": _csv_bytes(owners_df),
    }
    for name in omit_members:
        members.pop(name, None)
    return write_zip(zip_path, members)


#: A coherent default fixture population -- shared across every happy-path
#: test so a single test isn't the only place the "matched pair" shape is
#: defined.
_DEFAULT_SITE_CODES = ["M1000", "M1001", "M1002", "M1003"]
_DEFAULT_STAGES = ["Operating", "Care and Maintenance", "Shut", "Operating"]
_DEFAULT_PROJECT_CODES = ["P1000", "P1000", "P1001", "P1002"]


def _write_matched_pair(
    tmp_path: Path,
    *,
    site_codes: list[str] | None = None,
    stages: list[str] | None = None,
    project_codes: list[str | None] | None = None,
    extract_date: str | list[str] = "14/08/2026",
    shp_extract_date: str | list[str] | None = None,
    sites_extract_date: str | list[str] | None = None,
    owners_extract_date: str | list[str] | None = None,
    owner_project_codes: list[str] | None = None,
    csv_zip_omit_members: tuple[str, ...] = (),
    shp_zip_omit_members: tuple[str, ...] = (),
    shp_drop_columns: tuple[str, ...] = (),
    sites_drop_columns: tuple[str, ...] = (),
    crs: str = "EPSG:7844",
    csv_site_codes: list[str] | None = None,
) -> tuple[Path, Path]:
    """Build a coherent, cross-checking-clean SHP+CSV DASC MINEDEX bundle
    pair: the same SITE_CODE/SiteCode set, the same extract date across all
    three sources, and every ProjectsOwners ProjectCode present in
    Sites.csv. Every keyword lets a caller break exactly one of those
    invariants without touching anything else."""
    # `is None` (never bare truthiness) -- an explicitly passed empty list
    # (e.g. `site_codes=[]`, exercised by the empty-shapefile refusal test)
    # must build an empty fixture, not silently fall back to the shared
    # default population.
    site_codes = list(_DEFAULT_SITE_CODES) if site_codes is None else site_codes
    stages = list(_DEFAULT_STAGES) if stages is None else stages
    project_codes = list(_DEFAULT_PROJECT_CODES) if project_codes is None else project_codes

    shp_zip = tmp_path / "minedex_shp.zip"
    csv_zip = tmp_path / "minedex_csv.zip"

    _write_minedex_shp_zip(
        shp_zip,
        tmp_path / "shp_build",
        site_codes=site_codes,
        crs=crs,
        extract_date=shp_extract_date if shp_extract_date is not None else extract_date,
        omit_members=shp_zip_omit_members,
        drop_columns=shp_drop_columns,
    )

    sites_df = _build_sites_df(
        site_codes=csv_site_codes if csv_site_codes is not None else site_codes,
        stages=stages,
        project_codes=project_codes,
        extract_date=sites_extract_date if sites_extract_date is not None else extract_date,
    )
    if sites_drop_columns:
        sites_df = sites_df.drop(columns=list(sites_drop_columns))
    default_owner_project_codes = sorted({c for c in project_codes if c is not None})
    owners_df = _build_owners_df(
        project_codes=(
            owner_project_codes if owner_project_codes is not None else default_owner_project_codes
        ),
        extract_date=owners_extract_date if owners_extract_date is not None else extract_date,
    )
    _write_minedex_csv_zip(
        csv_zip, sites_df=sites_df, owners_df=owners_df, omit_members=csv_zip_omit_members
    )
    return shp_zip, csv_zip


# --- pinned constants ---------------------------------------------------------


def test_dasc_minedex_shp_url_is_pinned() -> None:
    assert minedex.DASC_MINEDEX_SHP_URL == "https://dasc.dmirs.wa.gov.au/Download/File/3978"


def test_dasc_minedex_csv_url_is_pinned() -> None:
    assert minedex.DASC_MINEDEX_CSV_URL == "https://dasc.dmirs.wa.gov.au/Download/File/3981"


def test_datawa_package_show_url_is_pinned() -> None:
    assert minedex.DATAWA_PACKAGE_SHOW_URL == (
        "https://catalogue.data.wa.gov.au/api/3/action/package_show?id=minedex-dmirs-001"
    )


def test_minedex_zip_filenames_are_pinned() -> None:
    assert minedex.MINEDEX_SHP_ZIP_FILENAME == "minedex_gda2020_shp.zip"
    assert minedex.MINEDEX_CSV_ZIP_FILENAME == "minedex_gda2020_csv.zip"


def test_minedex_shp_required_members_are_pinned() -> None:
    assert minedex.MINEDEX_SHP_REQUIRED_MEMBERS == (
        "Minedex.cpg",
        "Minedex.dbf",
        "Minedex.prj",
        "Minedex.shp",
        "Minedex.shx",
        "MINEDEX_Database_DataDictionary_GDA2020.pdf",
        "Minedex_Database_Metadata_GDA2020.pdf",
        "Licence_CCBY4.pdf",
    )


def test_minedex_csv_required_members_are_pinned() -> None:
    assert minedex.MINEDEX_CSV_REQUIRED_MEMBERS == (
        "Licence_CCBY4.pdf",
        "Sites.csv",
        "ProjectsOwners.csv",
    )


def test_minedex_expected_crs_is_pinned() -> None:
    assert minedex.MINEDEX_EXPECTED_CRS == "EPSG:7844"


# --- validate_minedex_bundles --------------------------------------------------


def test_validate_minedex_bundles_returns_summary_for_a_matched_pair(tmp_path: Path) -> None:
    shp_zip, csv_zip = _write_matched_pair(tmp_path)

    summary = minedex.validate_minedex_bundles(shp_zip, csv_zip)

    assert summary["feature_count"] == 4
    assert summary["crs"] == "EPSG:7844"
    assert summary["sites_row_count"] == 4
    assert summary["extract_date"] == "2026-08-14"
    stage_counts = summary["stage_counts"]
    assert isinstance(stage_counts, dict)
    assert sum(stage_counts.values()) == 4
    assert stage_counts["Operating"] == 2
    assert stage_counts["Care and Maintenance"] == 1
    assert stage_counts["Shut"] == 1
    assert summary["n_projects"] == 3
    assert summary["n_projects_with_current_owner"] == 3
    assert summary["n_sites_null_project_code"] == 0
    assert summary["coordinate_columns_present"] is True
    assert summary["n_sites_null_coordinates"] == 0
    assert summary["n_sites_absent_from_shapefile"] == 0
    assert summary["n_duplicate_site_code_values"] == 0
    assert summary["n_site_code_rows_duplicated"] == 0
    assert summary["n_orphan_owner_project_codes"] == 0
    for member in minedex.MINEDEX_SHP_REQUIRED_MEMBERS:
        assert member in summary["shp_members"]
    for member in minedex.MINEDEX_CSV_REQUIRED_MEMBERS:
        assert member in summary["csv_members"]


def test_validate_minedex_bundles_counts_sites_with_null_coordinates(tmp_path: Path) -> None:
    shp_zip, csv_zip = _write_matched_pair(tmp_path)

    # Null out coordinates for two of the four fixture sites, directly in
    # the CSV zip -- `_write_matched_pair` has no null-coordinate keyword,
    # so this rewrites Sites.csv in place after the coherent pair is built.
    sites_df = _build_sites_df(
        site_codes=_DEFAULT_SITE_CODES, stages=_DEFAULT_STAGES, project_codes=_DEFAULT_PROJECT_CODES
    )
    sites_df.loc[[0, 2], "Latitude"] = None
    sites_df.loc[[0, 2], "Longitude"] = None
    owners_df = _build_owners_df(project_codes=sorted(set(_DEFAULT_PROJECT_CODES)))
    _write_minedex_csv_zip(csv_zip, sites_df=sites_df, owners_df=owners_df)

    summary = minedex.validate_minedex_bundles(shp_zip, csv_zip)

    assert summary["coordinate_columns_present"] is True
    assert summary["n_sites_null_coordinates"] == 2


def test_validate_minedex_bundles_reports_not_computed_when_coordinate_columns_are_absent(
    tmp_path: Path,
) -> None:
    """Regression: a schema change dropping/renaming `Latitude`/`Longitude`
    silently reported `n_sites_null_coordinates == sites_total` -- "every
    site lacks coordinates" -- rather than disclosing that the count could
    not be computed at all. This is not a validation refusal (a real site
    genuinely lacking coordinates is expected), so the summary must instead
    carry `coordinate_columns_present: False` and `n_sites_null_coordinates:
    None`, never a number standing in for "unknown"."""
    shp_zip, csv_zip = _write_matched_pair(tmp_path, sites_drop_columns=("Latitude", "Longitude"))

    summary = minedex.validate_minedex_bundles(shp_zip, csv_zip)

    assert summary["coordinate_columns_present"] is False
    assert summary["n_sites_null_coordinates"] is None


def test_validate_minedex_bundles_raises_on_missing_member(tmp_path: Path) -> None:
    shp_zip, csv_zip = _write_matched_pair(
        tmp_path,
        shp_zip_omit_members=("Licence_CCBY4.pdf",),
        csv_zip_omit_members=("ProjectsOwners.csv",),
    )

    with pytest.raises(minedex.SnapshotValidationError) as excinfo:
        minedex.validate_minedex_bundles(shp_zip, csv_zip)
    message = str(excinfo.value)
    assert "Licence_CCBY4.pdf" in message
    assert "ProjectsOwners.csv" in message


def test_validate_minedex_bundles_raises_on_wrong_crs(tmp_path: Path) -> None:
    shp_zip, csv_zip = _write_matched_pair(tmp_path, crs="EPSG:4326")

    with pytest.raises(minedex.SnapshotValidationError) as excinfo:
        minedex.validate_minedex_bundles(shp_zip, csv_zip)
    message = str(excinfo.value)
    assert "4326" in message
    assert "7844" in message


def test_validate_minedex_bundles_raises_on_empty_shapefile(tmp_path: Path) -> None:
    shp_zip, csv_zip = _write_matched_pair(tmp_path, site_codes=[], stages=[], project_codes=[])

    with pytest.raises(minedex.SnapshotValidationError) as excinfo:
        minedex.validate_minedex_bundles(shp_zip, csv_zip)
    assert "empty" in str(excinfo.value)


def test_validate_minedex_bundles_raises_on_non_point_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ESRI Shapefile is a geometry-type-homogeneous format at the file
    level -- `gdf.to_file(..., driver="ESRI Shapefile")` refuses outright to
    write a Polygon feature into a Point-typed .shp (measured: pyogrio
    raises `FeatureError: Attempt to write non-point (POLYGON) geometry to
    point shapefile`), so a real, on-disk mixed-geometry DASC shapefile
    cannot be constructed to exercise this defensive check. Instead, build a
    structurally valid (all members present) zip and monkeypatch
    `minedex.gpd.read_file` to hand back a mixed-geometry GeoDataFrame in
    its place -- the read seam `validate_minedex_bundles` actually calls,
    exercised with data no legitimate shapefile could ever produce.
    """
    from shapely.geometry import Polygon

    polygon = Polygon([(115.0, -32.0), (115.1, -32.0), (115.1, -31.9), (115.0, -31.9)])
    shp_zip, csv_zip = _write_matched_pair(
        tmp_path,
        site_codes=["M1000", "M1001"],
        stages=["Operating", "Shut"],
        project_codes=["P1000", "P1001"],
    )
    mixed_gdf = _build_minedex_shp_gdf(
        site_codes=["M1000", "M1001"],
        geometry=[Point(115.0, -32.0), polygon],
    )
    monkeypatch.setattr(minedex.gpd, "read_file", lambda *_args, **_kwargs: mixed_gdf)

    with pytest.raises(minedex.SnapshotValidationError) as excinfo:
        minedex.validate_minedex_bundles(shp_zip, csv_zip)
    message = str(excinfo.value)
    assert "non-Point" in message or "Point" in message


def test_validate_minedex_bundles_raises_on_missing_required_shp_column(tmp_path: Path) -> None:
    shp_zip, csv_zip = _write_matched_pair(tmp_path, shp_drop_columns=("EXTRACT_DA",))

    with pytest.raises(minedex.SnapshotValidationError) as excinfo:
        minedex.validate_minedex_bundles(shp_zip, csv_zip)
    assert "EXTRACT_DA" in str(excinfo.value)


def test_validate_minedex_bundles_raises_on_stage_count_reconciliation_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`stage_counts` is built as `{str(key): int(count) for key, count in
    series.value_counts(dropna=False).items()}` -- if two DISTINCT index
    values render to the same `str()`, the dict comprehension silently
    drops one and the summed counts undercount the true row total.
    Reconstructing that exact collision through an honest CSV round-trip is
    not possible: pandas' own NA-token detection collapses even a quoted
    `"nan"` string back to a real `NaN` before `value_counts` ever sees it
    (verified empirically), so the two colliding values can never coexist in
    a parsed `Stage` column. This monkeypatches `pandas.Series.value_counts`
    -- scoped to calls on a Series named `Stage`, delegating to the real
    implementation for everything else -- to hand back two index values that
    are DISTINCT objects but share a `__str__`, which is the one seam that
    can exercise the reconciliation check's own defensive arithmetic."""
    shp_zip, csv_zip = _write_matched_pair(tmp_path)

    class _CollidingKey:
        def __str__(self) -> str:
            return "Operating"

    real_value_counts = pd.Series.value_counts

    def fake_value_counts(self: pd.Series, *args: Any, **kwargs: Any) -> pd.Series:
        if self.name == "Stage":
            return pd.Series([1, 1], index=[_CollidingKey(), _CollidingKey()])
        return real_value_counts(self, *args, **kwargs)

    monkeypatch.setattr(pd.Series, "value_counts", fake_value_counts)

    with pytest.raises(minedex.SnapshotValidationError) as excinfo:
        minedex.validate_minedex_bundles(shp_zip, csv_zip)
    message = str(excinfo.value)
    assert "stage counts" in message
    assert "reconcile" in message


def test_validate_minedex_bundles_raises_on_extract_date_mismatch(tmp_path: Path) -> None:
    shp_zip, csv_zip = _write_matched_pair(
        tmp_path, sites_extract_date="13/08/2026", extract_date="14/08/2026"
    )

    with pytest.raises(minedex.SnapshotValidationError) as excinfo:
        minedex.validate_minedex_bundles(shp_zip, csv_zip)
    message = str(excinfo.value)
    assert "2026-08-14" in message
    assert "2026-08-13" in message


def test_validate_minedex_bundles_raises_when_shapefile_carries_a_code_absent_from_sites_csv(
    tmp_path: Path,
) -> None:
    """A SITE_CODE the shapefile carries and Sites.csv does not is
    unexplainable either way -- Sites.csv is the full site register -- so
    this stays a hard refusal (the OTHER direction, a Sites.csv-only code,
    is expected and handled by the reconciliation tests below)."""
    shp_zip, csv_zip = _write_matched_pair(
        tmp_path,
        site_codes=["M1000", "M1001", "M1002", "M1003"],
        csv_site_codes=["M1000", "M1001", "M1002", "M9999"],
    )

    with pytest.raises(minedex.SnapshotValidationError) as excinfo:
        minedex.validate_minedex_bundles(shp_zip, csv_zip)
    message = str(excinfo.value)
    assert "SITE_CODE" in message
    assert "M1003" in message


def test_validate_minedex_bundles_discloses_sites_absent_from_shapefile_when_reconciled(
    tmp_path: Path,
) -> None:
    """Regression: a former exact SITE_CODE set-equality check refused this
    shape outright -- which is exactly the shape of the real 2026-08-14 DASC
    extract (351 SiteCode values present in Sites.csv but absent from the
    shapefile, all 351 reconciling exactly against Sites.csv's own
    null-coordinate rows). A point shapefile structurally cannot carry a
    coordinate-less site, so this is disclosed, not refused."""
    shp_site_codes = ["M1000", "M1001", "M1002"]
    csv_site_codes = ["M1000", "M1001", "M1002", "M9999"]

    shp_zip = tmp_path / "minedex_shp.zip"
    _write_minedex_shp_zip(shp_zip, tmp_path / "shp_build", site_codes=shp_site_codes)

    sites_df = _build_sites_df(
        site_codes=csv_site_codes, stages=["Operating"] * 4, project_codes=["P1000"] * 4
    )
    # M9999 -- the SiteCode absent from the shapefile -- carries no
    # coordinates, which is the reconciling condition (e) checks for.
    sites_df.loc[sites_df["SiteCode"] == "M9999", ["Latitude", "Longitude"]] = None
    owners_df = _build_owners_df(project_codes=["P1000"])
    csv_zip = _write_minedex_csv_zip(
        tmp_path / "minedex_csv.zip", sites_df=sites_df, owners_df=owners_df
    )

    summary = minedex.validate_minedex_bundles(shp_zip, csv_zip)

    assert summary["n_sites_absent_from_shapefile"] == 1
    assert summary["n_sites_null_coordinates"] == 1


def test_validate_minedex_bundles_raises_when_a_site_absent_from_shapefile_has_coordinates(
    tmp_path: Path,
) -> None:
    """The counterpart failure: a Sites.csv-only SiteCode that Sites.csv
    itself says HAS coordinates is not explainable by the point-shapefile
    structural limit -- it means the shapefile is genuinely missing a site
    -- and is refused by name."""
    shp_site_codes = ["M1000", "M1001", "M1002"]
    csv_site_codes = ["M1000", "M1001", "M1002", "M9999"]

    shp_zip = tmp_path / "minedex_shp.zip"
    _write_minedex_shp_zip(shp_zip, tmp_path / "shp_build", site_codes=shp_site_codes)

    sites_df = _build_sites_df(
        site_codes=csv_site_codes, stages=["Operating"] * 4, project_codes=["P1000"] * 4
    )  # M9999 carries coordinates like every other row -- unreconciled.
    owners_df = _build_owners_df(project_codes=["P1000"])
    csv_zip = _write_minedex_csv_zip(
        tmp_path / "minedex_csv.zip", sites_df=sites_df, owners_df=owners_df
    )

    with pytest.raises(minedex.SnapshotValidationError) as excinfo:
        minedex.validate_minedex_bundles(shp_zip, csv_zip)
    message = str(excinfo.value)
    assert "M9999" in message
    assert "reconcile" in message


def test_validate_minedex_bundles_discloses_duplicate_site_codes_in_sites_csv_without_refusing(
    tmp_path: Path,
) -> None:
    """Regression: a former shapefile-feature-count-vs-Sites.csv-row-count
    equality check refused this shape outright -- a Sites.csv row
    duplicated for an EXISTING code, which the real 2026-08-14 DASC extract
    exhibits at scale (1,327 duplicated SiteCode values, 1,411 excess rows:
    50,164 rows, 48,753 distinct) and is not itself a corrupted download.
    The duplication is disclosed, never refused on."""
    shp_zip, _ = _write_matched_pair(tmp_path)  # 4 features: M1000..M1003

    sites_df = _build_sites_df(
        site_codes=["M1000", "M1000", "M1001", "M1002", "M1003"],  # M1000 duplicated
        stages=["Operating"] * 5,
        project_codes=["P1000"] * 5,
    )
    owners_df = _build_owners_df(project_codes=["P1000"])
    csv_zip = write_zip(
        tmp_path / "minedex_csv_dup_row.zip",
        {
            "Licence_CCBY4.pdf": PLACEHOLDER_LICENCE_PDF_BYTES,
            "MINEDEX_CSV_DataDictionary_GDA2020.pdf": b"placeholder data dictionary",
            "Minedex_Database_Metadata_GDA2020.pdf": b"placeholder metadata",
            "Sites.csv": _csv_bytes(sites_df),
            "ProjectsOwners.csv": _csv_bytes(owners_df),
        },
    )

    summary = minedex.validate_minedex_bundles(shp_zip, csv_zip)

    assert summary["n_sites_absent_from_shapefile"] == 0
    assert summary["n_duplicate_site_code_values"] == 1
    assert summary["n_site_code_rows_duplicated"] == 2


def test_validate_minedex_bundles_raises_on_a_duplicate_site_code(tmp_path: Path) -> None:
    """Regression: the same duplicate, present IDENTICALLY on both sides,
    keeps BOTH check (e)'s set comparison AND (e2)'s row-count
    reconciliation passing -- the duplicate itself, not a count mismatch,
    is what must be caught, or a Sites.csv with one duplicated SiteCode row
    passes every check in this validator."""
    duplicated_codes = ["M1000", "M1000", "M1002", "M1003"]
    shp_zip = tmp_path / "minedex_shp_dup.zip"
    _write_minedex_shp_zip(shp_zip, tmp_path / "shp_dup_build", site_codes=duplicated_codes)

    sites_df = _build_sites_df(
        site_codes=duplicated_codes, stages=["Operating"] * 4, project_codes=["P1000"] * 4
    )
    owners_df = _build_owners_df(project_codes=["P1000"])
    csv_zip = write_zip(
        tmp_path / "minedex_csv_dup.zip",
        {
            "Licence_CCBY4.pdf": PLACEHOLDER_LICENCE_PDF_BYTES,
            "MINEDEX_CSV_DataDictionary_GDA2020.pdf": b"placeholder data dictionary",
            "Minedex_Database_Metadata_GDA2020.pdf": b"placeholder metadata",
            "Sites.csv": _csv_bytes(sites_df),
            "ProjectsOwners.csv": _csv_bytes(owners_df),
        },
    )

    with pytest.raises(minedex.SnapshotValidationError) as excinfo:
        minedex.validate_minedex_bundles(shp_zip, csv_zip)
    message = str(excinfo.value)
    assert "M1000" in message
    assert "SITE_CODE" in message


def test_validate_minedex_bundles_discloses_orphan_owner_project_codes_without_refusing(
    tmp_path: Path,
) -> None:
    """Regression: a former hard refusal on any orphan ProjectsOwners.csv
    ProjectCode would have blocked fetch-minedex against the real
    2026-08-14 extract, which carries 179. Disclosed, not refused."""
    shp_zip, csv_zip = _write_matched_pair(
        tmp_path, owner_project_codes=["P1000", "P1001", "P9999"]
    )

    summary = minedex.validate_minedex_bundles(shp_zip, csv_zip)

    assert summary["n_orphan_owner_project_codes"] == 1


def test_validate_minedex_bundles_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(minedex.SnapshotValidationError):
        minedex.validate_minedex_bundles(
            tmp_path / "does-not-exist-shp.zip", tmp_path / "does-not-exist-csv.zip"
        )


def test_validate_minedex_bundles_raises_on_unreadable_zip(tmp_path: Path) -> None:
    shp_zip = tmp_path / "garbage_shp.zip"
    shp_zip.write_bytes(b"not a zip")
    _, csv_zip = _write_matched_pair(tmp_path)

    with pytest.raises(minedex.SnapshotValidationError):
        minedex.validate_minedex_bundles(shp_zip, csv_zip)


def test_validate_minedex_bundles_raises_a_structured_error_on_a_corrupt_csv_member(
    tmp_path: Path,
) -> None:
    """Regression: `_read_csv_member` wrapped neither `zipfile` nor
    `pandas.read_csv` errors, so a malformed CSV member inside an
    otherwise well-formed bundle escaped `validate_minedex_bundles` as an
    uncaught `pandas.errors.ParserError` rather than the structured
    `SnapshotValidationError` every other check in this module raises --
    which the CLI's `except MinedexSnapshotValidationError` clause does not
    catch, dying with a bare traceback and no stdout."""
    shp_zip, _ = _write_matched_pair(tmp_path)
    owners_df = _build_owners_df(project_codes=sorted(set(_DEFAULT_PROJECT_CODES)))
    csv_zip = write_zip(
        tmp_path / "minedex_csv_corrupt.zip",
        {
            "Licence_CCBY4.pdf": PLACEHOLDER_LICENCE_PDF_BYTES,
            "MINEDEX_CSV_DataDictionary_GDA2020.pdf": b"placeholder data dictionary",
            "Minedex_Database_Metadata_GDA2020.pdf": b"placeholder metadata",
            # A ragged row -- more fields than the header declares -- inside
            # an otherwise well-formed member: `pandas.read_csv` raises
            # `ParserError` on this rather than silently coercing it, the
            # exact failure measured on the real CLI path.
            "Sites.csv": b"SiteCode,Stage\nM1000,Operating\nM1001,Operating,extra,columns,here\n",
            "ProjectsOwners.csv": _csv_bytes(owners_df),
        },
    )

    with pytest.raises(minedex.SnapshotValidationError) as excinfo:
        minedex.validate_minedex_bundles(shp_zip, csv_zip)
    assert "Sites.csv" in str(excinfo.value)


# --- download_minedex_zip / fetch_datawa_package_show --------------------------


class _FakeStreamedResponse:
    """A minimal stand-in for `requests.Response` used as a context manager."""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True

    def iter_content(self, chunk_size: int):  # type: ignore[no-untyped-def]
        yield self._content

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_download_minedex_zip_streams_with_explicit_timeout_and_user_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> _FakeStreamedResponse:
        captured["url"] = url
        captured.update(kwargs)
        return _FakeStreamedResponse(b"fake-zip-bytes")

    monkeypatch.setattr(minedex.requests, "get", fake_get)

    dest_path = tmp_path / "nested" / "minedex.zip"
    result = minedex.download_minedex_zip("https://example.test/download", dest_path)

    assert result == dest_path
    assert dest_path.read_bytes() == b"fake-zip-bytes"
    assert captured["url"] == "https://example.test/download"
    assert captured["stream"] is True
    assert isinstance(captured["timeout"], (int, float))
    assert captured["timeout"] > 0
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "wa-mine-rehab-monitor" in headers["User-Agent"]


def test_download_minedex_zip_raises_for_a_bad_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_get(url: str, **kwargs: object) -> _FakeStreamedResponse:
        class _Failing(_FakeStreamedResponse):
            def raise_for_status(self) -> None:
                raise requests.HTTPError("500 Server Error")

        return _Failing(b"")

    monkeypatch.setattr(minedex.requests, "get", fake_get)

    with pytest.raises(requests.HTTPError):
        minedex.download_minedex_zip("https://example.test/download", tmp_path / "out.zip")


class _FakeTextResponse:
    """A minimal stand-in for `requests.Response` when NOT streamed."""

    def __init__(self, text: str, *, status_error: Exception | None = None) -> None:
        self.text = text
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error


#: A genuine-shaped CKAN `package_show` body -- the fixture every
#: `fetch_datawa_package_show` happy-path test returns, so the new content
#: validation (name + license_id) passes it exactly like a real response.
_GENUINE_DATAWA_PACKAGE_SHOW_TEXT = (
    '{"result": {"name": "minedex-dmirs-001", "license_id": "cc-nc"}}'
)


def test_fetch_datawa_package_show_returns_the_response_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> _FakeTextResponse:
        captured["url"] = url
        captured.update(kwargs)
        return _FakeTextResponse(_GENUINE_DATAWA_PACKAGE_SHOW_TEXT)

    monkeypatch.setattr(minedex.requests, "get", fake_get)

    text = minedex.fetch_datawa_package_show("https://example.test/package_show")

    assert text == _GENUINE_DATAWA_PACKAGE_SHOW_TEXT
    assert captured["url"] == "https://example.test/package_show"
    assert isinstance(captured["timeout"], (int, float))
    assert captured["timeout"] > 0
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "wa-mine-rehab-monitor" in headers["User-Agent"]


def test_fetch_datawa_package_show_raises_for_a_bad_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs: object) -> _FakeTextResponse:
        return _FakeTextResponse("", status_error=requests.HTTPError("500 Server Error"))

    monkeypatch.setattr(minedex.requests, "get", fake_get)

    with pytest.raises(requests.HTTPError):
        minedex.fetch_datawa_package_show("https://example.test/package_show")


def test_fetch_datawa_package_show_raises_on_an_html_login_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: `fetch_datawa_package_show` performed no content check at
    all, so an HTTP 200 carrying an SSO login page -- D6's own measured
    finding, on the sibling SLIP endpoint -- would have been captured and
    listed as a genuine 'captured Data WA metadata record'."""

    def fake_get(url: str, **kwargs: object) -> _FakeTextResponse:
        return _FakeTextResponse("<html><body>Please log in</body></html>")

    monkeypatch.setattr(minedex.requests, "get", fake_get)

    with pytest.raises(minedex.DataWaMetadataValidationError):
        minedex.fetch_datawa_package_show("https://example.test/package_show")


def test_fetch_datawa_package_show_raises_on_a_ckan_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A well-formed JSON body that is nonetheless not a `package_show`
    record (e.g. CKAN's own `{"success": false, ...}` error envelope, or an
    unrelated dataset's record) must be treated the same as a login page,
    not captured as evidence."""

    def fake_get(url: str, **kwargs: object) -> _FakeTextResponse:
        return _FakeTextResponse('{"success": false, "error": {"message": "Not found"}}')

    monkeypatch.setattr(minedex.requests, "get", fake_get)

    with pytest.raises(minedex.DataWaMetadataValidationError):
        minedex.fetch_datawa_package_show("https://example.test/package_show")


def test_fetch_datawa_package_show_raises_on_the_wrong_package_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs: object) -> _FakeTextResponse:
        return _FakeTextResponse(
            '{"result": {"name": "some-other-dataset", "license_id": "cc-by"}}'
        )

    monkeypatch.setattr(minedex.requests, "get", fake_get)

    with pytest.raises(minedex.DataWaMetadataValidationError):
        minedex.fetch_datawa_package_show("https://example.test/package_show")


def test_fetch_datawa_package_show_raises_on_a_missing_license_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs: object) -> _FakeTextResponse:
        return _FakeTextResponse('{"result": {"name": "minedex-dmirs-001"}}')

    monkeypatch.setattr(minedex.requests, "get", fake_get)

    with pytest.raises(minedex.DataWaMetadataValidationError):
        minedex.fetch_datawa_package_show("https://example.test/package_show")


# --- capture_licence_evidence -------------------------------------------------


def test_capture_licence_evidence_extracts_pdf_byte_identically_and_writes_unadjudicated_json(
    tmp_path: Path,
) -> None:
    _, csv_zip = _write_matched_pair(tmp_path)

    evidence_path = minedex.capture_licence_evidence(
        tmp_path, csv_zip, '{"result": {"license_id": "cc-nc"}}', captured="2026-08-15"
    )

    assert evidence_path == tmp_path / licence.EVIDENCE_FILENAME
    pdf_path = tmp_path / minedex.LICENCE_PDF_FILENAME
    assert pdf_path.is_file()
    assert pdf_path.read_bytes() == PLACEHOLDER_LICENCE_PDF_BYTES

    datawa_path = tmp_path / minedex.DATAWA_METADATA_FILENAME
    assert datawa_path.is_file()
    assert datawa_path.read_text() == '{"result": {"license_id": "cc-nc"}}'

    payload = json.loads(evidence_path.read_text())
    assert payload["explicit_grant"] is None
    assert payload["contrary_notice"] is None
    assert payload["adjudicated"] is False
    assert payload["captured"] == "2026-08-15"
    assert set(payload["evidence_files"]) == {
        minedex.LICENCE_PDF_FILENAME,
        minedex.DATAWA_METADATA_FILENAME,
    }
    assert "resource" in payload
    assert "datawa_fetch_failed" not in payload

    # Capture never self-adjudicates, and never unblocks anything -- true
    # under BOTH require_hashed settings, because explicit_grant is null
    # regardless of whether the snapshot has been finalized yet.
    assert licence.minedex_redistribution_allowed(evidence_dir=tmp_path) is False
    assert (
        licence.minedex_redistribution_allowed(evidence_dir=tmp_path, require_hashed=False) is False
    )


def test_capture_licence_evidence_records_a_failed_datawa_fetch(tmp_path: Path) -> None:
    _, csv_zip = _write_matched_pair(tmp_path)

    evidence_path = minedex.capture_licence_evidence(tmp_path, csv_zip, None, captured="2026-08-15")

    assert (tmp_path / minedex.LICENCE_PDF_FILENAME).is_file()
    assert not (tmp_path / minedex.DATAWA_METADATA_FILENAME).exists()
    payload = json.loads(evidence_path.read_text())
    assert payload["explicit_grant"] is None
    assert payload["contrary_notice"] is None
    assert payload["adjudicated"] is False
    assert payload["evidence_files"] == [minedex.LICENCE_PDF_FILENAME]
    assert payload["datawa_fetch_failed"] is True

    assert licence.minedex_redistribution_allowed(evidence_dir=tmp_path) is False


def test_capture_licence_evidence_raises_when_pdf_member_is_absent(tmp_path: Path) -> None:
    _, csv_zip = _write_matched_pair(tmp_path, csv_zip_omit_members=("Licence_CCBY4.pdf",))

    with pytest.raises(minedex.LicenceEvidenceCaptureError) as excinfo:
        minedex.capture_licence_evidence(tmp_path, csv_zip, None, captured="2026-08-15")
    message = str(excinfo.value)
    assert str(csv_zip) in message
    assert "Licence_CCBY4.pdf" in message


def test_capture_licence_evidence_never_overwrites_an_adjudicated_evidence_file(
    tmp_path: Path,
) -> None:
    """Regression: re-running `fetch-minedex` on a snapshot a human had
    adjudicated silently reverted `licence_evidence.json`'s `explicit_grant`/
    `adjudicated` back to `null`/`false`, destroying the adjudication record
    the whole MINEDEX licence-conflict design exists to produce. This
    guards the function itself, in addition to the CLI-level refusal for an
    already-finalized snapshot."""
    _, csv_zip = _write_matched_pair(tmp_path)

    (tmp_path / minedex.LICENCE_PDF_FILENAME).write_bytes(b"original captured PDF bytes")
    adjudicated_payload = {
        "resource": {"licence_pdf": minedex.DASC_MINEDEX_CSV_URL},
        "explicit_grant": "CC-BY-4.0",
        "contrary_notice": True,
        "adjudicated": True,
        "captured": "2026-08-10",
        "evidence_files": [minedex.LICENCE_PDF_FILENAME],
    }
    evidence_path = tmp_path / licence.EVIDENCE_FILENAME
    evidence_path.write_text(
        json.dumps(adjudicated_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    returned_path = minedex.capture_licence_evidence(
        tmp_path, csv_zip, '{"a": "NEW, different metadata"}', captured="2026-08-15"
    )

    assert returned_path == evidence_path
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload == adjudicated_payload
    assert (tmp_path / minedex.LICENCE_PDF_FILENAME).read_bytes() == b"original captured PDF bytes"
    assert not (tmp_path / minedex.DATAWA_METADATA_FILENAME).exists()


# --- fetch-minedex CLI command ------------------------------------------------


def _write_config(tmp_path: Path, data_root: Path) -> Path:
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{data_root}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg_file


def _monkeypatch_downloads_from_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    url_to_zip: dict[str, Path],
    *,
    call_counts: dict[str, int] | None = None,
    fail_urls: frozenset[str] = frozenset(),
) -> None:
    def fake_download(url: str, dest_path: Path, **kwargs: object) -> Path:
        if call_counts is not None:
            call_counts[url] = call_counts.get(url, 0) + 1
        if url in fail_urls:
            raise requests.ConnectionError(f"simulated failure for {url}")
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(url_to_zip[url], dest_path)
        return dest_path

    monkeypatch.setattr(cli_module, "download_minedex_zip", fake_download)


def _monkeypatch_git_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )


def test_fetch_minedex_cli_writes_a_verified_snapshot_with_unadjudicated_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    shp_zip, csv_zip = _write_matched_pair(tmp_path)
    _monkeypatch_downloads_from_fixtures(
        monkeypatch,
        {minedex.DASC_MINEDEX_SHP_URL: shp_zip, minedex.DASC_MINEDEX_CSV_URL: csv_zip},
    )
    monkeypatch.setattr(
        cli_module, "fetch_datawa_package_show", lambda url, **kwargs: '{"license_id": "cc-nc"}'
    )
    _monkeypatch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["fetch-minedex", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert result.exit_code == 0, result.output

    snapshot_dir = data_root / "raw" / "dmirs_001_minedex" / "2026-08-15"
    assert (snapshot_dir / minedex.MINEDEX_SHP_ZIP_FILENAME).is_file()
    assert (snapshot_dir / minedex.MINEDEX_CSV_ZIP_FILENAME).is_file()
    assert (snapshot_dir / minedex.LICENCE_PDF_FILENAME).is_file()
    assert (snapshot_dir / minedex.DATAWA_METADATA_FILENAME).is_file()

    metadata_text = (snapshot_dir / "metadata.txt").read_text()
    assert "CONFLICT" in metadata_text
    conflict_source = licence.SOURCES["dmirs_001_minedex"]
    assert conflict_source.notes in metadata_text

    evidence_payload = json.loads((snapshot_dir / licence.EVIDENCE_FILENAME).read_text())
    assert evidence_payload["adjudicated"] is False
    assert evidence_payload["explicit_grant"] is None
    assert evidence_payload["contrary_notice"] is None

    assert (snapshot_dir / "SHA256SUMS.txt").is_file()
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)
    assert n_bad == 0
    assert n_missing == 0
    assert n_ok >= 1

    # Capture never unblocks anything -- the finalized, unadjudicated
    # evidence still fails closed.
    assert licence.minedex_redistribution_allowed(evidence_dir=snapshot_dir) is False

    manifest_path = snapshot_dir / "SHA256SUMS.txt.run_manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest["inputs"]) == 2
    for entry in manifest["inputs"]:
        assert entry["redistribute_public"] is False
        assert "CONFLICT" in entry["licence"]
    assert manifest["resolved_args"]["validation_summary"]["extract_date"] == "2026-08-14"


def test_fetch_minedex_cli_continues_fail_closed_on_a_datawa_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Data WA metadata fetch is best-effort: any exception is recorded
    as `datawa_fetch_failed` and the fetch continues -- it must never abort
    the whole command, and the result stays fail-closed either way."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    shp_zip, csv_zip = _write_matched_pair(tmp_path)
    _monkeypatch_downloads_from_fixtures(
        monkeypatch,
        {minedex.DASC_MINEDEX_SHP_URL: shp_zip, minedex.DASC_MINEDEX_CSV_URL: csv_zip},
    )

    def fake_fetch_datawa_raises(url: str, **kwargs: object) -> str:
        raise requests.ConnectionError("Data WA unreachable")

    monkeypatch.setattr(cli_module, "fetch_datawa_package_show", fake_fetch_datawa_raises)
    _monkeypatch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["fetch-minedex", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert result.exit_code == 0, result.output

    snapshot_dir = data_root / "raw" / "dmirs_001_minedex" / "2026-08-15"
    assert (snapshot_dir / minedex.LICENCE_PDF_FILENAME).is_file()
    assert not (snapshot_dir / minedex.DATAWA_METADATA_FILENAME).exists()
    evidence_payload = json.loads((snapshot_dir / licence.EVIDENCE_FILENAME).read_text())
    assert evidence_payload["datawa_fetch_failed"] is True
    assert evidence_payload["evidence_files"] == [minedex.LICENCE_PDF_FILENAME]

    _n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)
    assert n_bad == 0
    assert n_missing == 0
    assert licence.minedex_redistribution_allowed(evidence_dir=snapshot_dir) is False


def test_fetch_minedex_cli_rejects_a_malformed_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    def fake_download(url: str, dest_path: Path, **kwargs: object) -> Path:
        raise AssertionError("download must not be reached for a rejected --date")

    monkeypatch.setattr(cli_module, "download_minedex_zip", fake_download)

    result = runner.invoke(
        app, ["fetch-minedex", "--config", str(cfg_file), "--date", "15-08-2026"]
    )
    assert result.exit_code != 0
    assert not (data_root / "raw").exists()


def test_fetch_minedex_cli_atomicity_on_csv_download_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D6's atomicity requirement: a failure downloading EITHER bundle
    leaves the whole snapshot unfinalized -- no `SHA256SUMS.txt`, and the
    SHP zip that DID download successfully must not look like a complete,
    verifiable snapshot on its own."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    shp_zip, csv_zip = _write_matched_pair(tmp_path)
    _monkeypatch_downloads_from_fixtures(
        monkeypatch,
        {minedex.DASC_MINEDEX_SHP_URL: shp_zip, minedex.DASC_MINEDEX_CSV_URL: csv_zip},
        fail_urls=frozenset({minedex.DASC_MINEDEX_CSV_URL}),
    )
    _monkeypatch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["fetch-minedex", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert result.exit_code != 0

    snapshot_dir = data_root / "raw" / "dmirs_001_minedex" / "2026-08-15"
    assert not (snapshot_dir / "SHA256SUMS.txt").exists()


def test_fetch_minedex_cli_atomicity_on_validation_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cross-bundle validation refusal (here: a wrong CRS) must also
    leave the snapshot unfinalized -- both downloads succeeded, but a bad
    bundle pair must never be checksummed and manifested as though it were
    good."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    shp_zip, csv_zip = _write_matched_pair(tmp_path, crs="EPSG:4326")
    _monkeypatch_downloads_from_fixtures(
        monkeypatch,
        {minedex.DASC_MINEDEX_SHP_URL: shp_zip, minedex.DASC_MINEDEX_CSV_URL: csv_zip},
    )
    monkeypatch.setattr(cli_module, "fetch_datawa_package_show", lambda url, **kwargs: '{"a": "b"}')
    _monkeypatch_git_state(monkeypatch)

    result = runner.invoke(
        app, ["fetch-minedex", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert result.exit_code != 0

    snapshot_dir = data_root / "raw" / "dmirs_001_minedex" / "2026-08-15"
    assert not (snapshot_dir / "SHA256SUMS.txt").exists()


def test_fetch_minedex_cli_refuses_a_second_invocation_for_an_already_finalized_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: re-running `fetch-minedex` for a date that already
    succeeded used to re-download, overwrite both zips, and re-run
    `capture_licence_evidence` -- silently reverting an already-adjudicated
    `licence_evidence.json` back to `null`/`false` -- before finally hitting
    an uncaught `FileExistsError` from `finalize_snapshot`. This asserts the
    re-run is refused BEFORE any download is even reached, and that the zip
    bytes, the evidence JSON and `verify_snapshot`'s verdict are all
    untouched by the second invocation."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    shp_zip, csv_zip = _write_matched_pair(tmp_path)
    call_counts: dict[str, int] = {}
    _monkeypatch_downloads_from_fixtures(
        monkeypatch,
        {minedex.DASC_MINEDEX_SHP_URL: shp_zip, minedex.DASC_MINEDEX_CSV_URL: csv_zip},
        call_counts=call_counts,
    )
    monkeypatch.setattr(cli_module, "fetch_datawa_package_show", lambda url, **kwargs: '{"a": "b"}')
    _monkeypatch_git_state(monkeypatch)

    first = runner.invoke(app, ["fetch-minedex", "--config", str(cfg_file), "--date", "2026-08-15"])
    assert first.exit_code == 0, first.output
    assert call_counts == {
        minedex.DASC_MINEDEX_SHP_URL: 1,
        minedex.DASC_MINEDEX_CSV_URL: 1,
    }

    snapshot_dir = data_root / "raw" / "dmirs_001_minedex" / "2026-08-15"

    # Simulate a human adjudication having landed on this finalized snapshot
    # -- the exact scenario the original bug destroyed.
    evidence_path = snapshot_dir / licence.EVIDENCE_FILENAME
    adjudicated_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    adjudicated_payload["explicit_grant"] = "CC-BY-4.0"
    adjudicated_payload["contrary_notice"] = True
    adjudicated_payload["adjudicated"] = True
    evidence_path.write_text(
        json.dumps(adjudicated_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    shp_bytes_before = (snapshot_dir / minedex.MINEDEX_SHP_ZIP_FILENAME).read_bytes()
    evidence_before = evidence_path.read_text(encoding="utf-8")
    verify_before = snapshots.verify_snapshot(snapshot_dir)

    second = runner.invoke(
        app, ["fetch-minedex", "--config", str(cfg_file), "--date", "2026-08-15"]
    )
    assert second.exit_code != 0
    assert "Traceback" not in second.output
    assert call_counts == {
        minedex.DASC_MINEDEX_SHP_URL: 1,
        minedex.DASC_MINEDEX_CSV_URL: 1,
    }  # neither download reached on the re-run

    assert (snapshot_dir / minedex.MINEDEX_SHP_ZIP_FILENAME).read_bytes() == shp_bytes_before
    assert evidence_path.read_text(encoding="utf-8") == evidence_before
    assert snapshots.verify_snapshot(snapshot_dir) == verify_before


# --- adjudicate-minedex-licence CLI command -----------------------------------


def _run_fetch_minedex_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg_file: Path, *, date: str = "2026-08-15"
) -> Path:
    """Run `fetch-minedex` end to end against fixture downloads, returning
    the finalized snapshot directory -- the shared setup every
    `adjudicate-minedex-licence` test starts from."""
    shp_zip, csv_zip = _write_matched_pair(tmp_path)
    _monkeypatch_downloads_from_fixtures(
        monkeypatch,
        {minedex.DASC_MINEDEX_SHP_URL: shp_zip, minedex.DASC_MINEDEX_CSV_URL: csv_zip},
    )
    monkeypatch.setattr(cli_module, "fetch_datawa_package_show", lambda url, **kwargs: '{"a": "b"}')
    _monkeypatch_git_state(monkeypatch)

    result = runner.invoke(app, ["fetch-minedex", "--config", str(cfg_file), "--date", date])
    assert result.exit_code == 0, result.output
    data_root_line = json.loads(result.output)
    return Path(data_root_line["snapshot_dir"])


def test_adjudicate_minedex_licence_happy_path_keeps_the_gate_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    snapshot_dir = _run_fetch_minedex_happy_path(tmp_path, monkeypatch, cfg_file)

    result = runner.invoke(
        app,
        [
            "adjudicate-minedex-licence",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-15",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["minedex_redistribution_allowed"] is False

    evidence_payload = json.loads((snapshot_dir / licence.EVIDENCE_FILENAME).read_text())
    assert evidence_payload["explicit_grant"] == "CC-BY-4.0"
    assert evidence_payload["contrary_notice"] is True
    assert evidence_payload["adjudicated"] is True
    assert evidence_payload["decision"] == "licence conflict; redistribution closed"
    assert (
        evidence_payload["ruling_reference"]
        == "docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md"
    )
    assert evidence_payload["evidence_files"] == [
        minedex.LICENCE_PDF_FILENAME,
        minedex.DATAWA_METADATA_FILENAME,
    ]

    # `contrary_notice: true` is exactly what keeps this gate False -- the
    # ruling closed the conflict, it did not resolve it.
    assert licence.minedex_redistribution_allowed(evidence_dir=snapshot_dir) is False


def test_adjudicate_minedex_licence_leaves_the_snapshot_passing_integrity_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `adjudicate-minedex-licence` rewrites `licence_evidence.
    json` in place inside an already-finalized snapshot.
    `snapshots.verify_snapshot` re-hashes every file `SHA256SUMS.txt` names,
    `licence_evidence.json` included, so a rewrite that does not also
    re-sign that one line permanently breaks verification for this
    snapshot -- `cli._verify_snapshot_or_refuse`, which every build command
    that reads a raw snapshot must call first, then refuses forever after,
    and its only offered remedy (re-fetch) is itself refused by
    `_refuse_if_snapshot_already_finalized` because `SHA256SUMS.txt` already
    exists. Both the direct `verify_snapshot` counts and the real
    `_verify_snapshot_or_refuse` gate a build command actually calls are
    asserted here, not just one or the other.
    """
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    snapshot_dir = _run_fetch_minedex_happy_path(tmp_path, monkeypatch, cfg_file)

    result = runner.invoke(
        app,
        ["adjudicate-minedex-licence", "--config", str(cfg_file), "--date", "2026-08-15"],
    )
    assert result.exit_code == 0, result.output

    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)
    assert (n_bad, n_missing) == (0, 0), (n_ok, n_bad, n_missing)
    assert n_ok > 0

    # The gate every build command reading a raw snapshot must pass first --
    # must not raise `typer.Exit` after a successful adjudication.
    verification = cli_module._verify_snapshot_or_refuse(
        snapshot_dir, source_id="dmirs_001_minedex"
    )
    assert verification == {"n_ok": n_ok, "n_bad": 0, "n_missing": 0}

    # The adjudication record names the digest it superseded, so the
    # rewrite is disclosed rather than silently invisible.
    evidence_payload = json.loads((snapshot_dir / licence.EVIDENCE_FILENAME).read_text())
    assert isinstance(evidence_payload["evidence_json_sha256_before"], str)
    assert len(evidence_payload["evidence_json_sha256_before"]) == 64


def test_adjudicate_minedex_licence_writes_a_manifest_naming_the_digest_it_supersedes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: adjudication rewrites one line of `SHA256SUMS.txt`, which
    is exactly the artefact `fetch-minedex`'s run manifest pinned under
    `output.sha256`. Before this, a successful adjudication left that
    manifest asserting a digest the live file no longer had, with nothing
    anywhere disclosing the change -- a correctly adjudicated snapshot read
    as tampering to anyone verifying it against its own manifest. The pair
    is asserted here: the fetch manifest's `output.sha256` matches the live
    sums file BEFORE adjudication and differs AFTER, and an adjudication
    manifest exists naming BOTH values plus a pointer back to the manifest
    it supersedes.
    """
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    snapshot_dir = _run_fetch_minedex_happy_path(tmp_path, monkeypatch, cfg_file)

    sums_path = snapshot_dir / snapshots.SHA256SUMS_FILENAME
    fetch_manifest_path = Path(str(sums_path) + manifests.MANIFEST_SUFFIX)
    fetch_manifest = json.loads(fetch_manifest_path.read_text(encoding="utf-8"))
    sums_sha256_before = sha256_file(sums_path)
    assert fetch_manifest["output"]["sha256"] == sums_sha256_before

    evidence_path = snapshot_dir / licence.EVIDENCE_FILENAME
    evidence_sha256_before = sha256_file(evidence_path)

    result = runner.invoke(
        app,
        ["adjudicate-minedex-licence", "--config", str(cfg_file), "--date", "2026-08-15"],
    )
    assert result.exit_code == 0, result.output

    # The fetch manifest is immutable and is now superseded: its recorded
    # digest no longer describes the live file.
    assert json.loads(fetch_manifest_path.read_text(encoding="utf-8")) == fetch_manifest
    sums_sha256_after = sha256_file(sums_path)
    assert sums_sha256_after != sums_sha256_before
    assert fetch_manifest["output"]["sha256"] != sums_sha256_after

    adjudication_manifest_path = Path(str(evidence_path) + manifests.MANIFEST_SUFFIX)
    assert adjudication_manifest_path.is_file()
    adjudication_manifest = json.loads(adjudication_manifest_path.read_text(encoding="utf-8"))
    args = adjudication_manifest["resolved_args"]

    # Both values, named by the one record that explains the difference.
    assert args["sha256sums_sha256_before"] == sums_sha256_before
    assert args["sha256sums_sha256_after"] == sums_sha256_after
    assert args["supersedes_manifest_output_sha256"] == fetch_manifest["output"]["sha256"]
    assert args["supersedes_manifest"].endswith(
        snapshots.SHA256SUMS_FILENAME + manifests.MANIFEST_SUFFIX
    )
    assert args["supersedes_manifest_read_error"] is None

    # The evidence JSON's own two sides, plus the digest finalize captured.
    assert args["evidence_json_sha256_before"] == evidence_sha256_before
    assert args["evidence_json_sha256_after"] == sha256_file(evidence_path)
    assert args["evidence_json_sha256_after"] != evidence_sha256_before
    assert args["evidence_json_sha256_recorded_at_finalize"] == evidence_sha256_before
    assert adjudication_manifest["output"]["sha256"] == sha256_file(evidence_path)

    # The adjudication is dated by its manifest -- the evidence JSON carries
    # no clock of its own.
    assert adjudication_manifest["timestamp"].endswith("Z")

    # Adding a manifest to a finalized snapshot must not disturb the
    # integrity gate: `verify_snapshot` counts only what SHA256SUMS.txt
    # names, and no manifest is ever named there.
    n_ok, n_bad, n_missing = snapshots.verify_snapshot(snapshot_dir)
    assert (n_bad, n_missing) == (0, 0), (n_ok, n_bad, n_missing)


def test_adjudicate_minedex_licence_refuses_an_unfinalized_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    # Create the snapshot directory without ever calling fetch-minedex, so
    # SHA256SUMS.txt never exists.
    unfinalized_dir = data_root / "raw" / "dmirs_001_minedex" / "2026-08-15"
    unfinalized_dir.mkdir(parents=True)
    (unfinalized_dir / licence.EVIDENCE_FILENAME).write_text(
        json.dumps({"adjudicated": False, "evidence_files": []})
    )

    result = runner.invoke(
        app,
        ["adjudicate-minedex-licence", "--config", str(cfg_file), "--date", "2026-08-15"],
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert "never finalized" in payload["refusal"]


def test_adjudicate_minedex_licence_refuses_a_tampered_evidence_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    snapshot_dir = _run_fetch_minedex_happy_path(tmp_path, monkeypatch, cfg_file)

    # Tamper with the extracted licence PDF AFTER finalize -- its recorded
    # sha256 in SHA256SUMS.txt no longer matches.
    (snapshot_dir / minedex.LICENCE_PDF_FILENAME).write_bytes(b"tampered bytes, not the original")

    result = runner.invoke(
        app,
        ["adjudicate-minedex-licence", "--config", str(cfg_file), "--date", "2026-08-15"],
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert "not all hashed" in payload["refusal"]

    evidence_payload = json.loads((snapshot_dir / licence.EVIDENCE_FILENAME).read_text())
    assert evidence_payload["adjudicated"] is False  # never mutated on refusal


def test_adjudicate_minedex_licence_refuses_a_snapshot_missing_the_datawa_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D7 requires BOTH the licence PDF and a captured Data WA metadata
    record before adjudication. A snapshot captured through a failed Data WA
    fetch is a real, reachable state (`fetch-minedex` continues fail-closed,
    recording `datawa_fetch_failed` with `evidence_files` holding only the
    PDF) -- adjudicating it must refuse, naming the missing
    `datawa_package_show.json`, and must leave the evidence unadjudicated."""
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)

    shp_zip, csv_zip = _write_matched_pair(tmp_path)
    _monkeypatch_downloads_from_fixtures(
        monkeypatch,
        {minedex.DASC_MINEDEX_SHP_URL: shp_zip, minedex.DASC_MINEDEX_CSV_URL: csv_zip},
    )

    def fake_fetch_datawa_raises(url: str, **kwargs: object) -> str:
        raise requests.ConnectionError("Data WA unreachable")

    monkeypatch.setattr(cli_module, "fetch_datawa_package_show", fake_fetch_datawa_raises)
    _monkeypatch_git_state(monkeypatch)

    fetch = runner.invoke(app, ["fetch-minedex", "--config", str(cfg_file), "--date", "2026-08-15"])
    assert fetch.exit_code == 0, fetch.output

    snapshot_dir = data_root / "raw" / "dmirs_001_minedex" / "2026-08-15"
    evidence_before = (snapshot_dir / licence.EVIDENCE_FILENAME).read_text(encoding="utf-8")
    assert json.loads(evidence_before)["evidence_files"] == [minedex.LICENCE_PDF_FILENAME]

    result = runner.invoke(
        app,
        ["adjudicate-minedex-licence", "--config", str(cfg_file), "--date", "2026-08-15"],
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert minedex.DATAWA_METADATA_FILENAME in payload["refusal"]
    assert payload["evidence_files"] == [minedex.LICENCE_PDF_FILENAME]

    # The refusal mutates nothing: the evidence stays byte-identical,
    # unadjudicated, and the gate stays closed.
    assert (snapshot_dir / licence.EVIDENCE_FILENAME).read_text(encoding="utf-8") == evidence_before
    assert licence.minedex_redistribution_allowed(evidence_dir=snapshot_dir) is False


def test_adjudicate_minedex_licence_refuses_idempotently_on_a_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_config(tmp_path, data_root)
    _run_fetch_minedex_happy_path(tmp_path, monkeypatch, cfg_file)

    first = runner.invoke(
        app,
        ["adjudicate-minedex-licence", "--config", str(cfg_file), "--date", "2026-08-15"],
    )
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        app,
        ["adjudicate-minedex-licence", "--config", str(cfg_file), "--date", "2026-08-15"],
    )
    assert second.exit_code != 0
    payload = json.loads(second.output)
    assert payload["refusal"] == "already adjudicated"
    assert payload["existing_adjudication"]["adjudicated"] is True
