"""Tests for the public-RC tier-0 fallback package assembly (D13 §8 P3).

Toy geopandas frames built in-test, the discipline `test_maus_footprints.py`
already uses; no committed geometry fixture. Tenements input carries
EPSG:7844 (GDA2020, the real DMIRS-003 shapefile CRS); Maus input carries
`crosswalk.TARGET_CRS` (an equal-area CRS), matching how `assemble_tier0_maus`
callers hand it the already-clipped WA extract.
"""

from __future__ import annotations

import dataclasses

import geopandas as gpd
import pytest
from shapely.geometry import box

from wa_mine_monitor import export_gate, licence, public_rc

TENEMENTS_CRS = "EPSG:7844"


def _tenements_gdf(**extra_columns):
    n = 3
    base = {
        "FMT_TENID": [f"T{i}" for i in range(n)],
        "TENSTATUS": ["Live", "Live", "Pending"],
    }
    base.update(extra_columns)
    geometry = [box(i, i, i + 1, i + 1) for i in range(n)]
    return gpd.GeoDataFrame(base, geometry=geometry, crs=TENEMENTS_CRS)


def _maus_gdf(**extra_columns):
    n = 2
    base = {"maus_id": [f"M{i}" for i in range(n)]}
    base.update(extra_columns)
    geometry = [box(i, i, i + 1, i + 1) for i in range(n)]
    return gpd.GeoDataFrame(base, geometry=geometry, crs=TENEMENTS_CRS)


# ---------------------------------------------------------------------------
# Tenements
# ---------------------------------------------------------------------------


def test_tenements_package_has_exactly_the_allowlisted_fields():
    frame, dropped = public_rc.assemble_tier0_tenements(
        _tenements_gdf(), snapshot_date="2026-08-16"
    )
    assert list(frame.columns) == [
        "fmt_tenid",
        "tenstatus",
        "snapshot_date",
        "source_id",
        "licence_id",
        "attribution",
        "geometry",
    ]
    assert dropped == []


def test_tenements_keeps_geometry():
    frame, _dropped = public_rc.assemble_tier0_tenements(
        _tenements_gdf(), snapshot_date="2026-08-16"
    )
    assert isinstance(frame, gpd.GeoDataFrame)
    assert frame.geometry.notna().all()
    assert (~frame.geometry.is_empty).all()


@pytest.mark.parametrize(
    "column_name",
    ["SiteCode", "maus_id", "site_id", "owners_at_snapshot"],
)
def test_tenements_refuses_minedex_lineage_columns(column_name):
    gdf = _tenements_gdf(**{column_name: ["x", "y", "z"]})
    with pytest.raises(public_rc.PublicRcError, match=column_name):
        public_rc.assemble_tier0_tenements(gdf, snapshot_date="2026-08-16")


def test_tenements_drops_benign_source_columns_with_disclosure():
    gdf = _tenements_gdf(HOLDER1=["a", "b", "c"], EXTRACT_DA=["1", "2", "3"])
    frame, dropped = public_rc.assemble_tier0_tenements(gdf, snapshot_date="2026-08-16")
    assert dropped == ["EXTRACT_DA", "HOLDER1"]
    assert "holder1" not in [c.lower() for c in frame.columns]


def test_tenements_refuses_missing_required_input_columns():
    gdf = gpd.GeoDataFrame(
        {"TENSTATUS": ["Live"]},
        geometry=[box(0, 0, 1, 1)],
        crs=TENEMENTS_CRS,
    )
    with pytest.raises(public_rc.PublicRcError, match="FMT_TENID"):
        public_rc.assemble_tier0_tenements(gdf, snapshot_date="2026-08-16")


# ---------------------------------------------------------------------------
# Maus
# ---------------------------------------------------------------------------


def test_maus_package_has_exactly_the_allowlisted_fields():
    frame, dropped = public_rc.assemble_tier0_maus(_maus_gdf(), snapshot_date="2026-08-16")
    assert dropped == []
    assert list(frame.columns) == [
        "maus_id",
        "snapshot_date",
        "source_url",
        "attribution",
        "modification_statement",
        "geometry",
    ]


def test_maus_refuses_register_lineage():
    gdf = _maus_gdf(site_id=["S0", "S1"])
    with pytest.raises(public_rc.PublicRcError, match="site_id"):
        public_rc.assemble_tier0_maus(gdf, snapshot_date="2026-08-16")


def test_maus_refuses_any_unexpected_extra_column():
    gdf = _maus_gdf(area_km2=[1.0, 2.0])
    with pytest.raises(public_rc.PublicRcError, match="area_km2"):
        public_rc.assemble_tier0_maus(gdf, snapshot_date="2026-08-16")


def test_maus_drops_known_source_columns_with_disclosure():
    # The live wa_extract.gpkg carries the global Maus v2 source columns
    # through clip_to_wa unmodified; exactly those (and nothing else) are
    # dropped with disclosure rather than refused.
    gdf = _maus_gdf(
        ISO3_CODE=["AUS", "AUS"],
        COUNTRY_NAME=["Australia", "Australia"],
        AREA=[1.0, 2.0],
    )
    frame, dropped = public_rc.assemble_tier0_maus(gdf, snapshot_date="2026-08-16")
    assert dropped == ["AREA", "COUNTRY_NAME", "ISO3_CODE"]
    assert list(frame.columns) == list(public_rc.TIER0_MAUS_FIELDS)


def test_maus_refuses_extra_column_even_alongside_benign_ones():
    gdf = _maus_gdf(ISO3_CODE=["AUS", "AUS"], area_km2=[1.0, 2.0])
    with pytest.raises(public_rc.PublicRcError, match="area_km2"):
        public_rc.assemble_tier0_maus(gdf, snapshot_date="2026-08-16")


# ---------------------------------------------------------------------------
# Cross-package leakage
# ---------------------------------------------------------------------------


def test_no_maus_column_leaks_into_tenements_package_and_vice_versa():
    tenements_frame, _dropped = public_rc.assemble_tier0_tenements(
        _tenements_gdf(), snapshot_date="2026-08-16"
    )
    maus_frame, _maus_dropped = public_rc.assemble_tier0_maus(
        _maus_gdf(), snapshot_date="2026-08-16"
    )

    assert "maus_id" not in tenements_frame.columns
    assert all("CC-BY-4.0" in value for value in tenements_frame["attribution"])
    assert all("CC-BY-SA" not in value for value in tenements_frame["attribution"])

    assert all("CC-BY-SA-4.0" in value for value in maus_frame["attribution"])
    assert all(
        value == public_rc.MAUS_MODIFICATION_STATEMENT
        for value in maus_frame["modification_statement"]
    )


# ---------------------------------------------------------------------------
# Source licence-state gate
# ---------------------------------------------------------------------------


def test_assembly_refuses_when_source_state_is_not_public(monkeypatch):
    gated = dataclasses.replace(
        licence.SOURCES["dmirs_003_tenements"],
        licence_state=licence.LicenceState.GATED_INTERNAL,
    )
    patched_sources = dict(licence.SOURCES)
    patched_sources["dmirs_003_tenements"] = gated
    monkeypatch.setattr(licence, "SOURCES", patched_sources)

    assert export_gate.licence_state_allows_public(gated.licence_state) is False

    with pytest.raises(public_rc.PublicRcError):
        public_rc.assemble_tier0_tenements(_tenements_gdf(), snapshot_date="2026-08-16")


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_packages_counts():
    tenements_frame, _dropped = public_rc.assemble_tier0_tenements(
        _tenements_gdf(), snapshot_date="2026-08-16"
    )
    maus_frame, _maus_dropped = public_rc.assemble_tier0_maus(
        _maus_gdf(), snapshot_date="2026-08-16"
    )

    counts = public_rc.reconcile_packages(
        tenements_frame,
        maus_frame,
        n_tenements_source=3,
        n_maus_source=2,
    )
    assert counts == {"tenements": 3, "maus": 2}


def test_reconcile_packages_refuses_row_count_mismatch():
    tenements_frame, _dropped = public_rc.assemble_tier0_tenements(
        _tenements_gdf(), snapshot_date="2026-08-16"
    )
    maus_frame, _maus_dropped = public_rc.assemble_tier0_maus(
        _maus_gdf(), snapshot_date="2026-08-16"
    )

    with pytest.raises(public_rc.PublicRcError):
        public_rc.reconcile_packages(
            tenements_frame,
            maus_frame,
            n_tenements_source=99,
            n_maus_source=2,
        )


def test_reconcile_packages_refuses_null_or_empty_geometry():
    tenements_frame, _dropped = public_rc.assemble_tier0_tenements(
        _tenements_gdf(), snapshot_date="2026-08-16"
    )
    maus_frame, _maus_dropped = public_rc.assemble_tier0_maus(
        _maus_gdf(), snapshot_date="2026-08-16"
    )
    broken = tenements_frame.copy()
    broken.loc[broken.index[0], "geometry"] = None

    with pytest.raises(public_rc.PublicRcError):
        public_rc.reconcile_packages(
            broken,
            maus_frame,
            n_tenements_source=3,
            n_maus_source=2,
        )


# ---------------------------------------------------------------------------
# CLI: build-tier0-public-rc (D13 §8 P3)
# ---------------------------------------------------------------------------

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.sources._fixtures import shapefile_members, write_zip
from wa_mine_monitor import cli as cli_module
from wa_mine_monitor import public_audit, snapshots
from wa_mine_monitor.cli import app

cli_runner = CliRunner()


def _write_cli_config(tmp_path: Path, data_root: Path) -> Path:
    cfg_file = tmp_path / "cli-config.yaml"
    cfg_file.write_text(
        f'run:\n  data_root: "{data_root}"\n  redistribute_public: false\n'
        "sources:\n  minedex_public_export_blocked: true\n"
    )
    return cfg_file


def _stub_cli_git_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": False, "diff": ""},
    )


def _seed_tenements_snapshot(
    data_root: Path,
    date_str: str,
    tmp_dir: Path,
    *,
    extra_columns: dict | None = None,
    finalize: bool = True,
) -> Path:
    """Build `raw/dmirs_003_tenements/<date_str>/tenements_current_gda2020_shp.zip`
    from a synthetic DASC-shaped shapefile (FMT_TENID/TENSTATUS, EPSG:7844,
    member name `CurrentTenements.*`), finalized via `finalize_snapshot`."""
    n = 3
    base: dict[str, object] = {
        "FMT_TENID": [f"T{i}" for i in range(n)],
        "TENSTATUS": ["Live", "Live", "Pending"],
    }
    if extra_columns:
        base.update({k: [v] * n for k, v in extra_columns.items()})
    geometry = [box(i, i, i + 1, i + 1) for i in range(n)]
    gdf = gpd.GeoDataFrame(base, geometry=geometry, crs=TENEMENTS_CRS)

    snapshot_dir = snapshots.create_snapshot_dir(data_root, "dmirs_003_tenements", date_str)
    members = shapefile_members(gdf, tmp_dir, "CurrentTenements")
    write_zip(snapshot_dir / "tenements_current_gda2020_shp.zip", members)
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="Mining Tenements (DMIRS-003)",
        endpoint="https://dasc.dmirs.wa.gov.au/Download/File/2056",
        licence_note="CC-BY-4.0",
        purpose="test fixture",
    )
    if finalize:
        snapshots.finalize_snapshot(snapshot_dir)
    return snapshot_dir


def _seed_maus_snapshot(data_root: Path, date_str: str, *, finalize: bool = True) -> Path:
    n = 2
    base = {"maus_id": [f"M{i}" for i in range(n)]}
    geometry = [box(i, i, i + 1, i + 1) for i in range(n)]
    gdf = gpd.GeoDataFrame(base, geometry=geometry, crs=TENEMENTS_CRS)

    snapshot_dir = snapshots.create_snapshot_dir(data_root, "maus_v2", date_str)
    gdf.to_file(snapshot_dir / "wa_extract.gpkg", driver="GPKG", layer="wa_extract")
    snapshots.write_snapshot_metadata(
        snapshot_dir,
        source="Maus et al. v2 WA extract",
        endpoint="https://example.test/maus",
        licence_note="CC-BY-SA-4.0",
        purpose="test fixture",
    )
    if finalize:
        snapshots.finalize_snapshot(snapshot_dir)
    return snapshot_dir


def _seed_public_rc_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tenements_extra_columns: dict | None = None,
    finalize_tenements: bool = True,
    finalize_maus: bool = True,
    seed_tenements: bool = True,
    seed_maus: bool = True,
    tenements_date: str = "2026-08-16",
    maus_date: str = "2026-08-14",
) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    cfg_file = _write_cli_config(tmp_path, data_root)
    _stub_cli_git_state(monkeypatch)
    if seed_tenements:
        _seed_tenements_snapshot(
            data_root,
            tenements_date,
            tmp_path / "build-tenements",
            extra_columns=tenements_extra_columns,
            finalize=finalize_tenements,
        )
    if seed_maus:
        _seed_maus_snapshot(data_root, maus_date, finalize=finalize_maus)
    return cfg_file, data_root


def _invoke_build_public_rc(cfg_file: Path, *, version: str):
    return cli_runner.invoke(
        app,
        [
            "build-tier0-public-rc",
            "--config",
            str(cfg_file),
            "--version",
            version,
        ],
    )


def test_public_manifests_omit_dirty_tree_diff_with_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The two run manifests ship INSIDE the public payload; the internal
    # convention of embedding the full working-tree diff (tracked AND
    # untracked, provenance.collect_git_state) would ship arbitrary
    # uncommitted bytes -- plan docs, local paths -- inside a public
    # artefact. The public manifests must keep sha/dirty, empty the diff,
    # and disclose the omission plus the omitted bytes' sha256.
    cfg_file, data_root = _seed_public_rc_world(tmp_path, monkeypatch)
    dirty_diff = "diff --git a/docs/plans/x.md b/docs/plans/x.md\n+/Users/someone/secret"
    monkeypatch.setattr(
        cli_module,
        "collect_git_state",
        lambda repo_root: {"sha": "testsha", "dirty": True, "diff": dirty_diff},
    )
    result = _invoke_build_public_rc(cfg_file, version="2026.08.29")
    assert result.exit_code == 0, result.output

    import hashlib as _hashlib

    for name in ("tier0-tenements.parquet", "tier0-maus-wa.parquet"):
        manifest_path = (
            data_root / "releases" / "tier0-public-rc" / "2026.08.29" / f"{name}.run_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text())
        git = manifest["git"]
        assert git["diff"] == ""
        assert git["diff_omitted_for_public_payload"] is True
        assert git["diff_sha256"] == _hashlib.sha256(dirty_diff.encode("utf-8")).hexdigest()
        assert git["dirty"] is True
        assert "secret" not in manifest_path.read_text()

    audit_findings = public_audit.audit_release_dir(
        data_root / "releases" / "tier0-public-rc" / "2026.08.29"
    )
    assert audit_findings == []


def test_build_tier0_public_rc_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file, data_root = _seed_public_rc_world(tmp_path, monkeypatch)

    result = _invoke_build_public_rc(cfg_file, version="2026.08.29")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    version_dir = data_root / "releases" / "tier0-public-rc" / "2026.08.29"
    assert payload["version"] == "2026.08.29"

    tenements_path = version_dir / "tier0-tenements.parquet"
    maus_path = version_dir / "tier0-maus-wa.parquet"
    assert Path(payload["tenements_path"]) == tenements_path
    assert Path(payload["maus_path"]) == maus_path
    assert tenements_path.is_file()
    assert maus_path.is_file()
    assert (version_dir / "RELEASE_NOTES.md").is_file()
    assert len(list(version_dir.glob("*.run_manifest.json"))) == 2

    assert payload["counts"] == {"tenements": 3, "maus": 2}
    assert payload["dropped_source_columns"] == []

    tenements_gdf = gpd.read_parquet(tenements_path)
    assert list(tenements_gdf.columns) == list(public_rc.TIER0_TENEMENTS_FIELDS)
    assert tenements_gdf.geometry.notna().all()

    maus_gdf = gpd.read_parquet(maus_path)
    assert list(maus_gdf.columns) == list(public_rc.TIER0_MAUS_FIELDS)
    assert maus_gdf.geometry.notna().all()


def test_refuses_existing_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file, _data_root = _seed_public_rc_world(tmp_path, monkeypatch)

    first = _invoke_build_public_rc(cfg_file, version="2026.08.29")
    assert first.exit_code == 0, first.output

    second = _invoke_build_public_rc(cfg_file, version="2026.08.29")
    assert second.exit_code == 1
    payload = json.loads(second.output)
    assert payload["stage"] == "version_exists"


def test_refuses_bad_version_string(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file, _data_root = _seed_public_rc_world(tmp_path, monkeypatch)

    result = _invoke_build_public_rc(cfg_file, version="vNext")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "refusal" in payload


def test_refuses_unverified_tenements_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, _data_root = _seed_public_rc_world(tmp_path, monkeypatch, finalize_tenements=False)

    result = _invoke_build_public_rc(cfg_file, version="2026.08.29")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["source_id"] == "dmirs_003_tenements"


def test_refuses_unverified_maus_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file, _data_root = _seed_public_rc_world(tmp_path, monkeypatch, finalize_maus=False)

    result = _invoke_build_public_rc(cfg_file, version="2026.08.29")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["source_id"] == "maus_v2"


def test_refuses_missing_snapshot_as_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    cfg_file = _write_cli_config(tmp_path, data_root)
    _stub_cli_git_state(monkeypatch)

    result = _invoke_build_public_rc(cfg_file, version="2026.08.29")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["stage"] == "snapshot_missing"


def test_contaminated_tenements_input_refuses_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, _data_root = _seed_public_rc_world(
        tmp_path, monkeypatch, tenements_extra_columns={"SiteCode": "S1"}
    )

    result = _invoke_build_public_rc(cfg_file, version="2026.08.29")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "SiteCode" in payload["refusal"]


def test_dropped_source_columns_are_disclosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, data_root = _seed_public_rc_world(
        tmp_path, monkeypatch, tenements_extra_columns={"HOLDER1": "Holder 0"}
    )

    result = _invoke_build_public_rc(cfg_file, version="2026.08.29")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dropped_source_columns"] == ["HOLDER1"]

    version_dir = data_root / "releases" / "tier0-public-rc" / "2026.08.29"
    tenements_manifest = json.loads(
        (version_dir / "tier0-tenements.parquet.run_manifest.json").read_text()
    )
    assert tenements_manifest["resolved_args"]["dropped_source_columns"] == ["HOLDER1"]


def test_release_notes_wording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file, data_root = _seed_public_rc_world(tmp_path, monkeypatch)

    result = _invoke_build_public_rc(cfg_file, version="2026.08.29")
    assert result.exit_code == 0, result.output

    version_dir = data_root / "releases" / "tier0-public-rc" / "2026.08.29"
    notes = (version_dir / "RELEASE_NOTES.md").read_text()

    assert "licence-clean reference-layer fallback" in notes
    assert (
        "Descriptive spectral change chronologies; not a compliance or performance assessment."
    ) in notes
    assert "CC-BY-4.0" in notes
    assert "CC-BY-SA-4.0" in notes
    assert public_rc.MAUS_MODIFICATION_STATEMENT in notes
    assert "not a public MINEDEX site register" in notes


def test_release_payload_audit_passes_on_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file, data_root = _seed_public_rc_world(tmp_path, monkeypatch)

    result = _invoke_build_public_rc(cfg_file, version="2026.08.29")
    assert result.exit_code == 0, result.output

    version_dir = data_root / "releases" / "tier0-public-rc" / "2026.08.29"
    findings = public_audit.audit_release_dir(version_dir)
    assert findings == []
