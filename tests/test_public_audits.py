"""Tests for the public-flip payload audits (D13 §8 P4).

`audit_tree` / `audit_file` classify individual files by extension, name,
and content sniff into `Finding`s with a safe rule name and note -- never
the matched bytes -- so `render_report` can be echoed to a terminal or CI
log without itself becoming a leak. `collect_repo_files` is the git-aware
file lister the two audit scripts run against: tracked plus
untracked-but-not-ignored, so a file created during a build and not yet
`git add`-ed is still caught before a commit makes it public.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wa_mine_monitor import public_audit, public_rc
from wa_mine_monitor.public_audit import (
    EmptyReleaseDirError,
    Finding,
    audit_file,
    audit_release_dir,
    audit_tree,
    collect_repo_files,
    render_report,
)


def _write(root: Path, rel_path: str, content: bytes | str = "") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)


def _rules(findings: list[Finding]) -> set[str]:
    return {f.rule for f in findings}


# ---------------------------------------------------------------------------
# 1. Clean tree
# ---------------------------------------------------------------------------


def test_clean_tree_yields_zero_findings(tmp_path: Path) -> None:
    _write(tmp_path, "src/module.py", "def f() -> None:\n    return None\n")
    _write(tmp_path, "README.md", "# Project\n\nSome docs.\n")
    _write(tmp_path, "config.yaml", "key: value\n")

    findings = audit_tree(tmp_path, ["src/module.py", "README.md", "config.yaml"])

    assert findings == []


# ---------------------------------------------------------------------------
# 2. Extension / bulk-format and evidence-bundle detection
# ---------------------------------------------------------------------------


def test_bulk_extension_parquet_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "snap.parquet", b"\x00\x01")
    findings = audit_tree(tmp_path, ["snap.parquet"])
    assert "bulk_format" in _rules(findings)


def test_bulk_extension_zip_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "archive.zip", b"PK\x03\x04")
    findings = audit_tree(tmp_path, ["archive.zip"])
    assert "bulk_format" in _rules(findings)


def test_bulk_extension_gpkg_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "fire.gpkg", b"\x00")
    findings = audit_tree(tmp_path, ["fire.gpkg"])
    assert "bulk_format" in _rules(findings)


def test_bulk_extension_shapefile_and_sidecars_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "tenements.shp", b"\x00")
    _write(tmp_path, "tenements.dbf", b"\x00")
    findings = audit_tree(tmp_path, ["tenements.shp", "tenements.dbf"])
    paths = {f.path for f in findings}
    assert "tenements.shp" in paths
    assert "tenements.dbf" in paths
    assert "bulk_format" in _rules(findings)


def test_evidence_bundle_sha256sums_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "SHA256SUMS.txt", "abc  file\n")
    findings = audit_tree(tmp_path, ["SHA256SUMS.txt"])
    assert "evidence_bundle" in _rules(findings)


def test_evidence_bundle_run_manifest_suffix_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "x.run_manifest.json", "{}")
    findings = audit_tree(tmp_path, ["x.run_manifest.json"])
    assert "evidence_bundle" in _rules(findings)


def test_evidence_bundle_licence_evidence_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "licence_evidence.json", "{}")
    findings = audit_tree(tmp_path, ["licence_evidence.json"])
    assert "evidence_bundle" in _rules(findings)


# ---------------------------------------------------------------------------
# 3. Synthetic fixture allowlist
# ---------------------------------------------------------------------------


def test_synthetic_fixture_allowlist_permits(tmp_path: Path) -> None:
    rel = "tests/fixtures/dea/collection.json"
    _write(tmp_path, rel, '{"type": "FeatureCollection", "features": []}')

    allowed = audit_tree(tmp_path, [rel], allowlist=frozenset({rel}))
    assert allowed == []

    disallowed = audit_tree(tmp_path, [rel])
    assert disallowed != []


# ---------------------------------------------------------------------------
# 4. Credentials
# ---------------------------------------------------------------------------


def test_aws_secret_key_flagged_as_credential(tmp_path: Path) -> None:
    _write(tmp_path, "cfg.py", 'aws_secret_access_key = "AKIAABCDEFGHIJKLMNOP"\n')
    findings = audit_tree(tmp_path, ["cfg.py"])
    assert "credential" in _rules(findings)


def test_private_key_block_flagged_as_credential(tmp_path: Path) -> None:
    _write(tmp_path, "key.pem", "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
    findings = audit_tree(tmp_path, ["key.pem"])
    assert "credential" in _rules(findings)


# ---------------------------------------------------------------------------
# 5. Local paths
# ---------------------------------------------------------------------------


def test_users_path_flagged_as_local_path(tmp_path: Path) -> None:
    _write(tmp_path, "notes.txt", "data lives at /Users/someone/data\n")
    findings = audit_tree(tmp_path, ["notes.txt"])
    assert "local_path" in _rules(findings)


def test_home_path_flagged_as_local_path(tmp_path: Path) -> None:
    _write(tmp_path, "notes.txt", "data lives at /home/x\n")
    findings = audit_tree(tmp_path, ["notes.txt"])
    assert "local_path" in _rules(findings)


def test_windows_path_flagged_as_local_path(tmp_path: Path) -> None:
    _write(tmp_path, "notes.txt", "data lives at C:\\x\n")
    findings = audit_tree(tmp_path, ["notes.txt"])
    assert "local_path" in _rules(findings)


# ---------------------------------------------------------------------------
# 6. Geometry content
# ---------------------------------------------------------------------------


def test_wkt_polygon_flagged_as_geometry(tmp_path: Path) -> None:
    _write(tmp_path, "geom.txt", "POLYGON ((0 0, 1 0, 1 1, 0 0))\n")
    findings = audit_tree(tmp_path, ["geom.txt"])
    assert "geometry_content" in _rules(findings)


def test_wkb_magic_flagged_as_geometry(tmp_path: Path) -> None:
    _write(tmp_path, "geom.bin", b"\x01\x03\x00\x00\x00rest")
    findings = audit_tree(tmp_path, ["geom.bin"])
    assert "geometry_content" in _rules(findings)


def test_geojson_feature_collection_flagged_as_geometry(tmp_path: Path) -> None:
    _write(tmp_path, "geom.json", '{"type": "FeatureCollection", "features": []}')
    findings = audit_tree(tmp_path, ["geom.json"])
    assert "geometry_content" in _rules(findings)


# ---------------------------------------------------------------------------
# 7. MINEDEX markers, with code/docs carve-out
# ---------------------------------------------------------------------------


def test_minedex_csv_header_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "data.csv", "SiteCode,ProjectCode,OwnerName\n1,2,3\n")
    findings = audit_tree(tmp_path, ["data.csv"])
    assert "minedex_lineage" in _rules(findings)


def test_minedex_json_token_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "data.json", '{"source": "dmirs_001_minedex"}')
    findings = audit_tree(tmp_path, ["data.json"])
    assert "minedex_lineage" in _rules(findings)


def test_minedex_tokens_in_code_and_docs_are_exempt(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "module.py",
        "# SiteCode, ProjectCode, OwnerName, dmirs_001_minedex are lineage tokens\n",
    )
    _write(
        tmp_path,
        "NOTES.md",
        "SiteCode, ProjectCode, OwnerName, dmirs_001_minedex are lineage tokens\n",
    )

    findings = audit_tree(tmp_path, ["module.py", "NOTES.md"])

    assert "minedex_lineage" not in _rules(findings)


# ---------------------------------------------------------------------------
# 8/9. git-aware collection: untracked-but-not-ignored vs gitignored
# ---------------------------------------------------------------------------


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=root, check=True, capture_output=True
    )


def test_untracked_files_are_audited(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "clean.py", "x = 1\n")
    subprocess.run(["git", "add", "clean.py"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    _write(tmp_path, "leak.parquet", b"\x00\x01")

    files = collect_repo_files(tmp_path)

    assert "clean.py" in files
    assert "leak.parquet" in files

    findings = audit_tree(tmp_path, files)
    assert any(f.path == "leak.parquet" and f.rule == "bulk_format" for f in findings)


def test_gitignored_files_are_not_audited(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _write(tmp_path, "clean.py", "x = 1\n")
    _write(tmp_path, ".gitignore", "data/\n")
    subprocess.run(
        ["git", "add", "clean.py", ".gitignore"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    _write(tmp_path, "data/x.parquet", b"\x00\x01")

    files = collect_repo_files(tmp_path)

    assert "data/x.parquet" not in files


# ---------------------------------------------------------------------------
# 10. Redaction in the rendered report
# ---------------------------------------------------------------------------


def test_audit_output_redacts_matches(tmp_path: Path) -> None:
    _write(tmp_path, "secret.py", 'api_key = "SECRETTOKEN123"\n')
    _write(tmp_path, "path.txt", "root is /Users/jarrod/x\n")

    findings = audit_tree(tmp_path, ["secret.py", "path.txt"])
    report = render_report(findings)

    assert "secret.py" in report
    assert "path.txt" in report
    assert "credential" in report
    assert "local_path" in report
    assert "SECRETTOKEN123" not in report
    assert "/Users/jarrod/x" not in report


# ---------------------------------------------------------------------------
# 11. Release payload audit
# ---------------------------------------------------------------------------


def _write_parquet(version_dir: Path, name: str, columns: tuple[str, ...]) -> None:
    """Write a real (minimal) parquet file whose column names are exactly
    `columns`, in order -- the schema gate reads names only, so one string
    row per column suffices."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    version_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({column: ["x"] for column in columns}), version_dir / name)


def test_release_payload_audit_permits_the_rc_artefacts(tmp_path: Path) -> None:
    version_dir = tmp_path / "v0.1.0"
    _write_parquet(version_dir, "tier0-tenements.parquet", public_rc.TIER0_TENEMENTS_FIELDS)
    _write_parquet(version_dir, "tier0-maus-wa.parquet", public_rc.TIER0_MAUS_FIELDS)
    _write(version_dir, "RELEASE_NOTES.md", "# Release notes\n")
    _write(version_dir, "tier0-tenements.parquet.run_manifest.json", "{}")

    findings = audit_release_dir(version_dir)
    assert findings == []

    _write(version_dir, "Sites.csv", "SiteCode,Foo\n1,2\n")
    findings_with_leak = audit_release_dir(version_dir)
    assert "minedex_lineage" in _rules(findings_with_leak)


def test_release_payload_audit_flags_unauthorised_tier0_parquet(tmp_path: Path) -> None:
    version_dir = tmp_path / "v0.1.0"
    _write_parquet(version_dir, "tier0-private.parquet", ("anything",))

    findings = audit_release_dir(version_dir)
    assert "bulk_format" in _rules(findings)


def test_release_payload_audit_flags_package_schema_mismatch(tmp_path: Path) -> None:
    version_dir = tmp_path / "v0.1.0"
    smuggled = (*public_rc.TIER0_TENEMENTS_FIELDS, "SiteCode")
    _write_parquet(version_dir, "tier0-tenements.parquet", smuggled)

    findings = audit_release_dir(version_dir)
    assert "package_schema" in _rules(findings)
    # The safe fixed note must not copy the substituted package's actual
    # column names into the audit's own output.
    assert all("SiteCode" not in finding.note for finding in findings)


def test_release_payload_audit_flags_unreadable_package_parquet(tmp_path: Path) -> None:
    version_dir = tmp_path / "v0.1.0"
    _write(version_dir, "tier0-maus-wa.parquet", b"\x00\x01")

    findings = audit_release_dir(version_dir)
    assert "package_schema" in _rules(findings)


def test_release_payload_audit_refuses_missing_version_dir(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-version"

    with pytest.raises(EmptyReleaseDirError, match="does not exist"):
        audit_release_dir(missing)


def test_release_payload_audit_refuses_empty_version_dir(tmp_path: Path) -> None:
    version_dir = tmp_path / "v0.1.0"
    version_dir.mkdir()

    with pytest.raises(EmptyReleaseDirError, match="zero files"):
        audit_release_dir(version_dir)


def test_render_report_scanned_files_distinguishes_clean_from_vacuous() -> None:
    clean_with_count = render_report([], scanned_files=4)
    clean_without_count = render_report([])

    assert "4 file(s) scanned" in clean_with_count
    assert "scanned" not in clean_without_count


def test_audit_file_matches_audit_tree_for_single_file(tmp_path: Path) -> None:
    _write(tmp_path, "snap.parquet", b"\x00\x01")
    findings = audit_file(tmp_path, "snap.parquet")
    assert "bulk_format" in _rules(findings)


def test_public_audit_module_exposes_synthetic_fixture_allowlist() -> None:
    assert isinstance(public_audit.SYNTHETIC_FIXTURE_ALLOWLIST, frozenset)
    assert public_audit.SYNTHETIC_FIXTURE_ALLOWLIST
