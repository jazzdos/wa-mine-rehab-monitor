"""Batch C acceptance (D13 task C6): fixture-driven end-to-end chain.

Chains fetch-dea-catalogue -> build-dea-coverage -> build-maus-footprint-
areas -> derive-dea-volume over committed synthetic fixtures in one tmp
tree, then asserts the manifest chain, the disclosure reconciliation, and
the D7 export block. Reuses the arrange helpers test_cli.py built for Tasks
7/11/13/15 -- import them, do not duplicate them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.test_cli import (
    _init_git_repo,
    _seed_curated_register,
    _seed_dea_catalogue_snapshot,
    _seed_derive_dea_volume_chain,
    _write_monitor_config,
)
from wa_mine_monitor import export_gate, manifests, snapshots, tables
from wa_mine_monitor.cli import app
from wa_mine_monitor.dea_coverage import DEA_EPOCH_COLUMN_BY_SOURCE
from wa_mine_monitor.provenance import sha256_file

runner = CliRunner()


def _digest_verifies(manifest_path: Path, artefact_path: Path) -> None:
    """The manifest beside `artefact_path` names ITS CURRENT bytes' sha256.

    A manifest that once matched at build time and is never re-checked at
    read time is exactly how a later hand-edit or partial regeneration goes
    silently unnoticed -- the same property `cli.py`'s `_digest_verified_
    manifest` enforces per command, checked here across the whole chain.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["output"]["sha256"] == sha256_file(artefact_path)


def test_end_to_end_chain_manifests_and_reconciliation(tmp_path, monkeypatch):
    cfg_file, data_root = _seed_derive_dea_volume_chain(tmp_path, monkeypatch)

    result = runner.invoke(
        app, ["derive-dea-volume", "--config", str(cfg_file), "--date", "2026-08-19"]
    )
    assert result.exit_code == 0, result.output

    catalogue_dir = data_root / "raw" / "dea_stac" / "2026-08-15"
    catalogue_sums_path = catalogue_dir / snapshots.SHA256SUMS_FILENAME
    catalogue_manifest_path = Path(str(catalogue_sums_path) + manifests.MANIFEST_SUFFIX)

    footprints_path = (
        data_root / "curated" / "maus_footprint_areas" / "2026-08-17" / "footprint_areas.parquet"
    )
    footprints_manifest_path = Path(str(footprints_path) + manifests.MANIFEST_SUFFIX)

    register_path = data_root / "curated" / "register" / "2026-08-16" / "register.parquet"
    register_manifest_path = Path(str(register_path) + manifests.MANIFEST_SUFFIX)

    estimate_path = data_root / "reports" / "dea-volume" / "2026-08-19" / "estimate.json"
    estimate_manifest_path = Path(str(estimate_path) + manifests.MANIFEST_SUFFIX)

    # 1. Every manifest in the chain digest-verifies against the CURRENT
    #    bytes of its own artefact.
    _digest_verifies(catalogue_manifest_path, catalogue_sums_path)
    _digest_verifies(footprints_manifest_path, footprints_path)
    _digest_verifies(register_manifest_path, register_path)
    _digest_verifies(estimate_manifest_path, estimate_path)

    # 2. The enriched register's manifest names the catalogue manifest; the
    #    estimate names all FOUR source digests.
    register_manifest = json.loads(register_manifest_path.read_text(encoding="utf-8"))
    register_args = register_manifest["resolved_args"]
    assert register_args["source_catalogue_manifest_root"] == "data_root"
    assert (data_root / register_args["source_catalogue_manifest"]) == catalogue_manifest_path

    estimate = json.loads(estimate_path.read_text(encoding="utf-8"))
    assert set(estimate["source_manifest_digests"]) == {
        "register",
        "crosswalk",
        "footprints",
        "catalogue",
    }
    assert estimate["source_manifest_digests"]["footprints"] == sha256_file(
        footprints_manifest_path
    )
    assert estimate["source_manifest_digests"]["register"] == sha256_file(register_manifest_path)
    assert estimate["source_manifest_digests"]["catalogue"] == sha256_file(catalogue_manifest_path)

    # 3. Coverage disclosures reconcile: computed + not_computed == rows,
    #    for all four collections.
    disclosures = register_args["dea_coverage_disclosure"]
    n_rows = register_args["register_rows_after"]
    assert set(disclosures) == set(DEA_EPOCH_COLUMN_BY_SOURCE)
    for source_id, counts in disclosures.items():
        total = counts["n_sites_coverage_computed"] + counts["n_sites_coverage_not_computed"]
        assert total == n_rows, source_id

    # 4. The estimate's windowed-read and upper-bound bytes differ from the
    #    provisional constants, and every window is sized from a footprint
    #    or the declared floor -- never from the MINEDEX point.
    provisional = estimate["provisional_figures_comparison_only"]
    assert (
        estimate["bytes"]["windowed_read_bytes_estimate"]
        != provisional["provisional_bytes_estimate"]
    )
    assert estimate["bytes"]["upper_bound_bytes"] != provisional["provisional_bytes_upper_bound"]
    by_site = estimate["windows"]["by_site"]
    assert by_site  # at least one eligible, footprint-linked site
    for site_id, window in by_site.items():
        assert window["window_sizing"] in {"floor", "footprint"}, site_id


def test_enriched_register_public_export_is_blocked(tmp_path, monkeypatch):
    """D7: MINEDEX-derived rows carry redistribute_public=False semantics,
    so export_public refuses the enriched register whole.

    The enriched register itself carries no `redistribute_public` column --
    see `docs/checkpoints/batch-b-closeout.md`, which records that
    `export_gate.export_public` has no caller anywhere in this tree yet.
    This test documents the actual export seam: were a caller to assemble a
    public-bound frame from this register and stamp MINEDEX-derived rows
    `redistribute_public=False` per D7's binding rule, `export_public`
    refuses the whole frame rather than filtering or publishing it.
    """
    _init_git_repo(tmp_path)
    monkeypatch.setattr("wa_mine_monitor.cli._REPO_ROOT", tmp_path)
    cfg_file = _write_monitor_config(tmp_path)
    data_root = tmp_path / "data"
    _seed_curated_register(data_root, "2026-08-15")
    _seed_dea_catalogue_snapshot(cfg_file, "2026-08-16", monkeypatch)

    result = runner.invoke(
        app,
        [
            "build-dea-coverage",
            "--config",
            str(cfg_file),
            "--date",
            "2026-08-17",
            "--catalogue-date",
            "2026-08-16",
        ],
    )
    assert result.exit_code == 0, result.output

    out_path = data_root / "curated" / "register" / "2026-08-17" / "register.parquet"
    enriched = tables.read_table(out_path)
    assert "redistribute_public" not in enriched.columns
    frame = enriched.assign(redistribute_public=False)
    with pytest.raises(PermissionError, match="licence gate"):
        export_gate.export_public(frame)
