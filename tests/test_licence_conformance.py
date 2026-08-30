"""Static licence conformance, D13 §8 P1.

Iterates `licence.SOURCES` (never a hand-picked subset) so a newly
registered source cannot be silently skipped by any of these checks: every
entry must carry complete evidence fields, no conflicted/unverified entry
may claim `PUBLIC`, the fail-closed boolean mapping in `export_gate` must
track `licence_state` exactly, every literal `redistribute_public=` keyword
use outside `licence.py` itself must be accounted for (either it is a
project-internal artefact that carries no external source, or it is named
here against the real source whose state it reflects), and the human-read
matrix at `docs/licensing-matrix.md` must reconcile row-for-row against the
registry.
"""

from __future__ import annotations

import ast
from pathlib import Path

from wa_mine_monitor import export_gate
from wa_mine_monitor.licence import SOURCES, LicenceState

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "wa_mine_monitor"
MATRIX_PATH = REPO_ROOT / "docs" / "licensing-matrix.md"

#: `'<filename>:<lineno>'` (the `SourceAsset(...)` call's own line, per
#: `ast.Call.lineno`) of every literal `redistribute_public=` keyword use
#: outside `licence.py`, mapped to `(named registered source_ids, justification)`.
#: Populated from the real scan (`_literal_redistribute_uses`) run against
#: this worktree on 2026-08-29 -- see each justification for what the
#: literal actually reflects. An empty id-tuple marks a project-internal
#: artefact (a derived table, a config file, a validation fixture) that is
#: not itself a registered external source; the justification says so
#: explicitly rather than leaving the mapping unexplained.
EXEMPTIONS: dict[str, tuple[tuple[str, ...], str]] = {
    # -- wa_rdc_regions: fetched directly, matches the registry's PUBLIC state.
    "cli.py:5281": (
        ("wa_rdc_regions",),
        (
            "fetch-rdc-regions' own SourceAsset for the freshly captured download; "
            "CC-BY-4.0/True matches SOURCES['wa_rdc_regions'] exactly."
        ),
    ),
    # -- register.parquet input assets: register mixes MINEDEX (gated) with
    # tenements (public); every run-manifest entry for register_path is
    # pinned closed because dmirs_001_minedex is in its lineage.
    "cli.py:2919": (
        ("dmirs_001_minedex",),
        (
            "register_path input asset to build-crosswalk; register's lineage "
            "includes MINEDEX, so it is pinned closed regardless of the licence "
            "field left None here."
        ),
    ),
    "cli.py:3373": (
        ("dmirs_001_minedex",),
        (
            "register_path input asset to build-climate-context; licence is read "
            "directly from SOURCES['dmirs_001_minedex'].licence_id."
        ),
    ),
    "cli.py:3784": (
        ("dmirs_001_minedex",),
        (
            "register_path input asset to build-fire-context; licence is read "
            "directly from SOURCES['dmirs_001_minedex'].licence_id."
        ),
    ),
    "cli.py:4769": (
        ("dmirs_001_minedex",),
        (
            "register_path input asset to build-register's own manifest; licence "
            "is read directly from SOURCES['dmirs_001_minedex'].licence_id."
        ),
    ),
    "cli.py:5132": (
        ("dmirs_001_minedex",),
        (
            "register_path input asset to build-d3-inputs; register's lineage "
            "includes MINEDEX, so it is pinned closed regardless of the licence "
            "field left None here."
        ),
    ),
    "cli.py:6473": (
        ("dmirs_001_minedex",),
        (
            "register_path input asset to build-d3-threshold; register's lineage "
            "includes MINEDEX, so it is pinned closed regardless of the licence "
            "field left None here."
        ),
    ),
    "cli.py:7289": (
        ("dmirs_001_minedex",),
        (
            "register_path input asset re-recorded in build-register's own "
            "manifest; licence is read directly from "
            "SOURCES['dmirs_001_minedex'].licence_id."
        ),
    ),
    "cli.py:7863": (
        ("dmirs_001_minedex",),
        (
            "register_path input asset to the D3 extraction loop; licence is "
            "read directly from SOURCES['dmirs_001_minedex'].licence_id."
        ),
    ),
    # -- crosswalk.parquet input assets: crosswalk is register x maus, so it
    # inherits MINEDEX's gated state.
    "cli.py:3381": (
        ("dmirs_001_minedex",),
        (
            "crosswalk_path input asset to build-climate-context; crosswalk is "
            "derived from register (MINEDEX-bearing) x maus, so it stays closed."
        ),
    ),
    "cli.py:3792": (
        ("dmirs_001_minedex",),
        (
            "crosswalk_path input asset to build-fire-context; crosswalk is "
            "derived from register (MINEDEX-bearing) x maus, so it stays closed."
        ),
    ),
    "cli.py:5140": (
        ("dmirs_001_minedex",),
        (
            "crosswalk_path input asset to build-d3-inputs; crosswalk is derived "
            "from register (MINEDEX-bearing) x maus, so it stays closed."
        ),
    ),
    "cli.py:6481": (
        ("dmirs_001_minedex",),
        (
            "crosswalk_path input asset to build-d3-threshold; crosswalk is "
            "derived from register (MINEDEX-bearing) x maus, so it stays closed."
        ),
    ),
    "cli.py:7297": (
        ("dmirs_001_minedex",),
        (
            "crosswalk_path input asset re-recorded in build-register's own "
            "manifest; crosswalk is derived from register x maus, so it stays "
            "closed."
        ),
    ),
    "cli.py:7871": (
        ("dmirs_001_minedex",),
        (
            "crosswalk_path input asset to the D3 extraction loop; crosswalk is "
            "derived from register (MINEDEX-bearing) x maus, so it stays closed."
        ),
    ),
    # -- raw maus_path (the Maus GPKG geometry itself) and footprints derived
    # from it: pinned closed conservatively wherever it is recorded as an
    # input to a NON-Maus package, per licence.py's own docstring -- the
    # CC-BY-SA-4.0 share-alike package must publish separately and never
    # folds into another export, so these run manifests (climate-context,
    # fire-context, d3-inputs, d3 extraction) never claim it open even
    # though SOURCES['maus_v2'].redistribute_public is True.
    "cli.py:3389": (
        ("maus_v2",),
        (
            "maus_path (raw Maus GPKG) recorded as an input to build-climate-"
            "context, a non-Maus package; pinned closed conservatively per "
            "licence.py's ShareAlike-package note even though "
            "SOURCES['maus_v2'].redistribute_public is True."
        ),
    ),
    "cli.py:3800": (
        ("maus_v2",),
        (
            "maus_path (raw Maus GPKG) recorded as an input to build-fire-"
            "context, a non-Maus package; pinned closed conservatively per "
            "licence.py's ShareAlike-package note."
        ),
    ),
    "cli.py:7887": (
        ("maus_v2",),
        (
            "maus_path (raw Maus GPKG) recorded as an input to the D3 extraction "
            "loop, a non-Maus package; pinned closed conservatively per "
            "licence.py's ShareAlike-package note."
        ),
    ),
    "cli.py:5148": (
        ("maus_v2",),
        (
            "footprints_path (Maus-derived footprints) input asset to "
            "build-d3-inputs, a non-Maus package; pinned closed conservatively "
            "per licence.py's ShareAlike-package note."
        ),
    ),
    "cli.py:6489": (
        ("maus_v2",),
        (
            "footprints_path (Maus-derived footprints) input asset to "
            "build-d3-threshold, a non-Maus package; pinned closed "
            "conservatively per licence.py's ShareAlike-package note."
        ),
    ),
    "cli.py:7879": (
        ("maus_v2",),
        (
            "footprints_path (Maus-derived footprints) input asset to the D3 "
            "extraction loop, a non-Maus package; pinned closed conservatively "
            "per licence.py's ShareAlike-package note."
        ),
    ),
    # -- catalogue_sums_path: the whole DEA STAC catalogue capture
    # (`raw/dea_stac/<date>/SHA256SUMS.txt`), spanning every DEA geomedian
    # collection plus Fractional Cover Percentiles, all CC-BY-4.0/True.
    "cli.py:4777": (
        ("dea_gm_ls5t", "dea_gm_ls7e", "dea_gm_ls8cls9c", "dea_fc_pc"),
        (
            "catalogue_sums_path covers the whole raw/dea_stac catalogue "
            "capture (every DEA collection's STAC JSON); all four are "
            "CC-BY-4.0/True, matching the literal."
        ),
    ),
    "cli.py:5156": (
        ("dea_gm_ls5t", "dea_gm_ls7e", "dea_gm_ls8cls9c", "dea_fc_pc"),
        (
            "catalogue_sums_path covers the whole raw/dea_stac catalogue "
            "capture; all four DEA collections are CC-BY-4.0/True, matching "
            "the literal."
        ),
    ),
    "cli.py:6513": (
        ("dea_gm_ls5t", "dea_gm_ls7e", "dea_gm_ls8cls9c", "dea_fc_pc"),
        (
            "catalogue_dir/SHA256SUMS.txt covers the whole raw/dea_stac "
            "catalogue capture; all four DEA collections are CC-BY-4.0/True, "
            "matching the literal."
        ),
    ),
    "cli.py:7895": (
        ("dea_gm_ls5t", "dea_gm_ls7e", "dea_gm_ls8cls9c", "dea_fc_pc"),
        (
            "catalogue_dir/SHA256SUMS.txt covers the whole raw/dea_stac "
            "catalogue capture; all four DEA collections are CC-BY-4.0/True, "
            "matching the literal."
        ),
    ),
    # -- Project-internal artefacts: not registered external sources at all,
    # so they carry no source_id. Each is a file this project generated
    # itself (a config, a derived table, a validation fixture), always
    # pinned closed because there is no external licence grant to point to.
    "cli.py:5371": (
        (),
        (
            "protocol_config is a project-authored D3 protocol config file, not "
            "an external source; no SOURCES entry applies."
        ),
    ),
    "cli.py:6465": (
        (),
        (
            "protocol_artifact_path (build-d3-protocol's own output) is a "
            "project-internal derived artefact, not an external source."
        ),
    ),
    "cli.py:6902": (
        (),
        (
            "protocol_artifact_path (build-d3-protocol's own output) recorded "
            "as an input to build-d3-threshold; project-internal, not an "
            "external source."
        ),
    ),
    "cli.py:7903": (
        (),
        (
            "protocol_artifact_path (build-d3-protocol's own output) recorded "
            "as an input to the D3 extraction loop; project-internal, not an "
            "external source."
        ),
    ),
    "cli.py:7305": (
        (),
        (
            "threshold_path (build-d3-threshold's own output) is a "
            "project-internal derived artefact, not an external source."
        ),
    ),
    "cli.py:7313": (
        (),
        (
            "footprint_support_path (build-d3-inputs' own output table) is a "
            "project-internal derived artefact, not an external source."
        ),
    ),
    "cli.py:6911": (
        (),
        (
            "table_paths[name] iterates build-d3-inputs' own five output "
            "tables, recorded as inputs to build-d3-threshold; project-internal "
            "derived artefacts, not external sources."
        ),
    ),
    "cli.py:8370": (
        (),
        (
            "reference_cube is a Huntly validation fixture supplied for "
            "cross-checking, not an external registered source."
        ),
    ),
    "cli.py:8378": (
        (),
        (
            "site_meta is a Huntly validation fixture supplied for "
            "cross-checking, not an external registered source."
        ),
    ),
    # -- accept-trajectories (E4) and build-context-join (F6) input assets,
    # added 2026-08-30.
    "cli.py:8494": (
        (),
        (
            "summary_path (extraction_summary.json) input asset to "
            "accept-trajectories; project-internal derived artefact, not an "
            "external source."
        ),
    ),
    "cli.py:8502": (
        ("dmirs_001_minedex",),
        (
            "register_path input asset to accept-trajectories; register's "
            "lineage includes MINEDEX, so it is pinned closed regardless of "
            "the licence field left None here."
        ),
    ),
    "cli.py:8790": (
        (),
        (
            "summary_path (extraction_summary.json) input asset to "
            "build-context-join; project-internal derived artefact, not an "
            "external source."
        ),
    ),
    "cli.py:8798": (
        (),
        (
            "verdict_path (accept-trajectories' acceptance.json) input asset "
            "to build-context-join; project-internal derived artefact, not "
            "an external source."
        ),
    ),
    "cli.py:8806": (
        ("dbca_060_fire",),
        (
            "fire_path input asset to build-context-join; licence is read "
            "directly from SOURCES['dbca_060_fire'].licence_id. NOTE: "
            "redistribute_public is hard-coded False here rather than read "
            "from SOURCES['dbca_060_fire'].redistribute_public (True) as the "
            "rest of the codebase does (e.g. build-fire-context) -- flagged "
            "as a discrepancy for a follow-up fix, not corrected in this "
            "pass since it touches licence-gate code."
        ),
    ),
    "cli.py:8814": (
        ("silo",),
        (
            "climate_path input asset to build-context-join; licence is read "
            "directly from SOURCES['silo'].licence_id. NOTE: "
            "redistribute_public is hard-coded False here rather than read "
            "from SOURCES['silo'].redistribute_public (True) as the rest of "
            "the codebase does (e.g. build-climate-context) -- flagged as a "
            "discrepancy for a follow-up fix, not corrected in this pass "
            "since it touches licence-gate code."
        ),
    ),
}


def _literal_redistribute_uses() -> dict[str, int]:
    """Scan every `src/wa_mine_monitor/*.py` module except `licence.py` for
    a literal `redistribute_public=<constant>` keyword use.

    Returns `{'<filename>:<lineno>': value}` keyed on the enclosing call's
    own line (`ast.Call.lineno`) -- stable across reformatting of the
    keyword arguments themselves, and exactly what a human reading the scan
    output would point at.
    """
    hits: dict[str, int] = {}
    for path in sorted(SRC.glob("*.py")):
        if path.name == "licence.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "redistribute_public" and isinstance(kw.value, ast.Constant):
                    hits[f"{path.name}:{node.lineno}"] = kw.value.value
    return hits


def test_every_entry_has_state_and_evidence_fields():
    for source_id, entry in SOURCES.items():
        assert isinstance(entry.licence_state, LicenceState), source_id
        assert entry.source_url.strip(), source_id
        assert entry.licence_id.strip(), source_id
        assert entry.attribution_text.strip(), source_id
        assert entry.notes.strip(), source_id


def test_conflict_or_unverified_never_maps_public():
    for source_id, entry in SOURCES.items():
        upper = entry.licence_id.upper()
        if "CONFLICT" in upper or "UNVERIFIED" in upper or not upper.strip():
            assert entry.licence_state is not LicenceState.PUBLIC, source_id


def test_state_boolean_mapping_is_fail_closed():
    for source_id, entry in SOURCES.items():
        assert export_gate.licence_state_allows_public(entry.licence_state) is (
            entry.licence_state is LicenceState.PUBLIC
        ), source_id


def test_every_literal_redistribute_use_is_exempted_or_absent():
    hits = _literal_redistribute_uses()
    unexempted = sorted(set(hits) - set(EXEMPTIONS))
    assert not unexempted, (
        f"literal redistribute_public= use(s) outside licence.py with no "
        f"exemption recorded: {unexempted}"
    )
    for key, (source_ids, justification) in EXEMPTIONS.items():
        for source_id in source_ids:
            assert source_id in SOURCES, f"{key} names unregistered source_id {source_id!r}"
        assert justification.strip(), f"{key} has a blank justification"


def test_licensing_matrix_reconciles_with_registry():
    text = MATRIX_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    table_lines = [
        line
        for line in lines
        if line.startswith("|") and "---" not in line and "Source ID" not in line
    ]
    for source_id, entry in SOURCES.items():
        matching = [line for line in table_lines if f"`{source_id}`" in line]
        assert len(matching) == 1, (
            f"expected exactly one matrix row for {source_id!r}, found {len(matching)}"
        )
        row = matching[0]
        assert entry.licence_id in row, f"{source_id}: licence_id {entry.licence_id!r} not in row"
        assert entry.licence_state.value in row, (
            f"{source_id}: licence_state {entry.licence_state.value!r} not in row"
        )
        expected_flag = "yes" if entry.redistribute_public else "no"
        assert expected_flag in row, (
            f"{source_id}: redistribution flag {expected_flag!r} not in row: {row!r}"
        )
