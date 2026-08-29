"""Tier 0 public-safe fallback packages (D13 §8 P3): tenements + Maus.

Two small, source-derived reference layers that ship WITH geometry, deliberately
outside `export_gate.export_public`. That function's geometry drop exists to
guard the MINEDEX-frame register products -- it keeps CC-BY-SA-4.0 Maus
geometry and licence-conflicted MINEDEX attributes out of a CC-BY export that
never asked for them (see its docstring). The two packages built here are not
that kind of export: they are direct, source-scoped extracts -- DMIRS-003
tenements geometry, and the project's own Maus et al. v2 WA clip -- and D13
§8 P3 explicitly permits geometry in both. Running them through
`export_public`'s geometry drop would silently strip the one thing that makes
either package useful (a tenements boundary, a mine footprint) while adding
no protection `export_public` isn't already providing elsewhere.

Their gates are therefore not `export_public`'s row/geometry gate but three
narrower ones, all enforced here:

1. **Lineage refusal.** Every input column (the assemblers receive the FULL
   source frame -- callers must never pre-select, or this gate is blind) is
   checked against `MINEDEX_LINEAGE_TOKENS` as case-insensitive SUBSTRINGS.
   Any match refuses the whole build, naming the offending column. `holder`
   and bare `owner` are deliberately NOT tokens: `HOLDER1..n` are legitimate
   DMIRS-003 source columns (a tenement's registered holder), not MINEDEX
   lineage -- `ownername`/`owners_at_snapshot` (the actual MINEDEX/crosswalk
   markers) are tokens instead.
2. **Exact output allowlist.** Tenements: unconsumed non-lineage columns are
   DROPPED WITH DISCLOSURE -- the sorted dropped names are returned, never
   silently discarded and never a refusal trigger, because the real DMIRS-003
   shapefile always carries extra columns (`HOLDER1`, `EXTRACT_DA`, ...) and
   refusing on their presence would refuse every live build. Maus: the
   project's own `wa_extract.gpkg` is `clip_to_wa`'s output, which carries
   the global Maus v2 source columns through the clip unmodified (it clips
   and adds `maus_id`; it never selects columns) -- so exactly the KNOWN
   source columns (`MAUS_BENIGN_SOURCE_COLUMNS`) are dropped with
   disclosure, and any OTHER extra column is contamination, not a benign
   source artefact -- refused, naming it.
3. **PUBLIC licence-state assertion.** Each package's source registry entry
   (`licence.SOURCES`) must read `licence_state == PUBLIC` via
   `export_gate.licence_state_allows_public`, checked at build time so a
   registry edit that regresses a source's state (accidentally or otherwise)
   fails the next build rather than silently re-publishing a now-closed
   source.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import yaml

from wa_mine_monitor import export_gate, licence, provenance

#: Exact output columns for the tenements Tier 0 package, in order.
TIER0_TENEMENTS_FIELDS: tuple[str, ...] = (
    "fmt_tenid",
    "tenstatus",
    "snapshot_date",
    "source_id",
    "licence_id",
    "attribution",
    "geometry",
)

#: Exact output columns for the Maus Tier 0 package, in order.
TIER0_MAUS_FIELDS: tuple[str, ...] = (
    "maus_id",
    "snapshot_date",
    "source_url",
    "attribution",
    "modification_statement",
    "geometry",
)

#: Case-insensitive SUBSTRING tokens marking a column as true MINEDEX/
#: crosswalk lineage -- never a benign DMIRS-003 source column. `holder` and
#: bare `owner` are deliberately absent (see module docstring point 1).
MINEDEX_LINEAGE_TOKENS: tuple[str, ...] = (
    "sitecode",
    "site_code",
    "site_id",
    "projectcode",
    "project_code",
    "ownername",
    "owners_at_snapshot",
    "dmirs_001",
    "minedex",
)

#: `maus_id` is legitimate on the Maus side but is itself MINEDEX-crosswalk
#: lineage if it leaks onto the tenements side -- forbidden there only.
_MAUS_ID_TOKEN = "maus_id"

#: Required input columns for `assemble_tier0_tenements`, beyond geometry.
_TENEMENTS_REQUIRED_INPUT: tuple[str, ...] = ("FMT_TENID", "TENSTATUS")

#: CC-BY-SA-4.0 modification statement stamped on every Maus package row.
#: Exact text, not paraphrased per-run -- ShareAlike requires a consistent,
#: checkable statement of what was changed from the upstream Maus release.
MAUS_MODIFICATION_STATEMENT = (
    "Modified from the Maus et al. v2 polygons: clipped to Western Australia "
    "(WA_BBOX), deterministic maus_id added; distributed under CC-BY-SA-4.0."
)

#: Global Maus v2 source columns that `sources.maus.clip_to_wa` carries
#: through into `wa_extract.gpkg` unmodified (it clips and adds `maus_id`;
#: it never selects columns). These are the ONLY extra columns
#: `assemble_tier0_maus` drops with disclosure -- a closed allowlist, unlike
#: the tenements side's open drop, because any column NOT on it cannot have
#: come from the pinned Maus source and is therefore contamination.
MAUS_BENIGN_SOURCE_COLUMNS: tuple[str, ...] = ("AREA", "COUNTRY_NAME", "ISO3_CODE")


#: D11's exact claim-boundary sentence (docs/decisions/2026-08-16-d9-d12-
#: commit-remote-naming-sequencing.md): required at the first product
#: reference on every public landing page and release. Reproduced verbatim
#: here, never paraphrased, so `RELEASE_NOTES.md` carries it byte-for-byte.
D11_CLAIM_BOUNDARY_SENTENCE = (
    "Descriptive spectral change chronologies; not a compliance or performance assessment."
)


class PublicRcError(ValueError):
    """Any refusal from this module: lineage leak, schema drift, gated source."""


def _refuse_lineage(columns: list[str], *, extra_tokens: tuple[str, ...] = ()) -> None:
    """Refuse if any column name contains a lineage token as a substring.

    Matching is case-insensitive and substring-based (not exact-name), per
    the design's intent: a column named e.g. `MinedexSiteCode` must be
    caught even though it matches no single token exactly. `extra_tokens`
    lets a caller add side-specific forbidden names (`maus_id` on the
    tenements side) without polluting the shared `MINEDEX_LINEAGE_TOKENS`
    constant that both assemblers check.
    """
    tokens = MINEDEX_LINEAGE_TOKENS + extra_tokens
    for column in columns:
        lowered = column.lower()
        for token in tokens:
            if token in lowered:
                raise PublicRcError(
                    f"column {column!r} carries MINEDEX/crosswalk lineage "
                    f"(matched token {token!r}) -- refusing to build a "
                    "public-RC package from it"
                )


def _require_public_source(source_id: str) -> licence.SourceLicence:
    """Fetch a `licence.SOURCES` entry and refuse unless it is `PUBLIC`.

    Reads `licence.SOURCES` fresh (not a module-level snapshot) so a test's
    `monkeypatch.setattr(licence, "SOURCES", ...)` -- or a genuine registry
    edit -- takes effect on the very next build, matching `export_gate`'s
    own fail-closed posture toward licence state.
    """
    entry = licence.SOURCES[source_id]
    if not export_gate.licence_state_allows_public(entry.licence_state):
        raise PublicRcError(
            f"source {source_id!r} is licence_state={entry.licence_state!r}, "
            "not PUBLIC -- refusing to build a public-RC package from it"
        )
    return entry


def assemble_tier0_tenements(
    gdf: gpd.GeoDataFrame, *, snapshot_date: str
) -> tuple[gpd.GeoDataFrame, list[str]]:
    """Build the DMIRS-003 tenements Tier 0 public-RC package.

    `gdf` must be the FULL frame as read from the source shapefile -- never
    pre-selected by the caller, or the lineage gate below is blind to
    whatever the caller already dropped. Returns `(frame, dropped)`: `dropped`
    is the sorted list of non-lineage input columns this function discarded
    (module docstring point 2) -- always returned, never a refusal by itself.
    """
    columns = list(gdf.columns)
    _refuse_lineage(columns, extra_tokens=(_MAUS_ID_TOKEN,))

    missing = [c for c in _TENEMENTS_REQUIRED_INPUT if c not in gdf.columns]
    if missing:
        raise PublicRcError(f"tenements input is missing required column(s): {', '.join(missing)}")
    if gdf.geometry.name not in gdf.columns:
        raise PublicRcError("tenements input has no geometry column")

    source = _require_public_source("dmirs_003_tenements")

    consumed = {*_TENEMENTS_REQUIRED_INPUT, gdf.geometry.name}
    dropped = sorted(c for c in columns if c not in consumed)

    out = gpd.GeoDataFrame(
        {
            "fmt_tenid": gdf["FMT_TENID"],
            "tenstatus": gdf["TENSTATUS"],
            "snapshot_date": snapshot_date,
            "source_id": source.source_id,
            "licence_id": source.licence_id,
            "attribution": source.attribution_text,
        },
        geometry=gdf.geometry.reset_index(drop=True),
        crs=gdf.crs,
    )
    out = out[list(TIER0_TENEMENTS_FIELDS)]
    return out, dropped


def assemble_tier0_maus(
    gdf: gpd.GeoDataFrame, *, snapshot_date: str
) -> tuple[gpd.GeoDataFrame, list[str]]:
    """Build the Maus et al. v2 WA-extract Tier 0 public-RC package.

    `wa_extract.gpkg` is `clip_to_wa`'s output: the global Maus v2 source
    columns pass through the clip unmodified, plus `maus_id`. So the KNOWN
    source columns (`MAUS_BENIGN_SOURCE_COLUMNS`) are dropped with
    disclosure -- returned sorted, mirroring the tenements side -- while any
    OTHER extra column cannot have come from the pinned source and is
    contamination: refused, naming it (module docstring point 2).
    """
    columns = list(gdf.columns)
    _refuse_lineage(columns)

    if "maus_id" not in gdf.columns:
        raise PublicRcError("maus input is missing required column: maus_id")
    if gdf.geometry.name not in gdf.columns:
        raise PublicRcError("maus input has no geometry column")

    expected = {"maus_id", gdf.geometry.name, *MAUS_BENIGN_SOURCE_COLUMNS}
    extra = sorted(c for c in columns if c not in expected)
    if extra:
        raise PublicRcError(
            "maus input carries unexpected extra column(s) beyond maus_id, "
            "geometry, and the known Maus v2 source columns "
            f"({', '.join(MAUS_BENIGN_SOURCE_COLUMNS)}): {', '.join(extra)}"
        )
    dropped = sorted(c for c in columns if c in MAUS_BENIGN_SOURCE_COLUMNS)

    source = _require_public_source("maus_v2")

    out = gpd.GeoDataFrame(
        {
            "maus_id": gdf["maus_id"],
            "snapshot_date": snapshot_date,
            "source_url": source.source_url,
            "attribution": source.attribution_text,
            "modification_statement": MAUS_MODIFICATION_STATEMENT,
        },
        geometry=gdf.geometry.reset_index(drop=True),
        crs=gdf.crs,
    )
    return out[list(TIER0_MAUS_FIELDS)], dropped


def reconcile_packages(
    tenements: gpd.GeoDataFrame,
    maus: gpd.GeoDataFrame,
    *,
    n_tenements_source: int,
    n_maus_source: int,
) -> dict[str, int]:
    """Cross-check both assembled packages against their source row counts.

    Refuses (rather than silently accepting) a row-count mismatch against
    the source frame -- the same "reconcile against nothing" failure
    `export_gate`'s row gate refuses on -- and refuses any null or empty
    geometry, since a geometry-bearing package with a hole in its geometry
    column is worse than one that failed to build at all.
    """
    if len(tenements) != n_tenements_source:
        raise PublicRcError(
            f"tenements package has {len(tenements)} row(s), source had "
            f"{n_tenements_source} -- refusing to reconcile"
        )
    if len(maus) != n_maus_source:
        raise PublicRcError(
            f"maus package has {len(maus)} row(s), source had "
            f"{n_maus_source} -- refusing to reconcile"
        )

    for label, frame in (("tenements", tenements), ("maus", maus)):
        if len(frame) == 0:
            raise PublicRcError(f"{label} package has zero rows")
        geometry = frame.geometry
        if geometry.isna().any():
            raise PublicRcError(f"{label} package has null geometry")
        if geometry.is_empty.any():
            raise PublicRcError(f"{label} package has empty geometry")

    return {"tenements": len(tenements), "maus": len(maus)}


def _package_licence_block(source_id: str) -> str:
    """Render one package's licence/attribution block, `release.
    attribution_block`'s style: attribution text, source URL, licence id +
    URL. Registry-driven from `licence.SOURCES` -- never hardcoded here, so
    a registry edit (e.g. a corrected `source_url`) is reflected the next
    time a release is built, not frozen into this module's own text.
    """
    source = licence.SOURCES[source_id]
    return "\n".join(
        [
            source.attribution_text,
            f"Source: {source.source_url}",
            f"Licence: {source.licence_id} ({source.licence_url})",
        ]
    )


def render_release_notes(version: str, tenements_date: str, maus_date: str) -> str:
    """Render `RELEASE_NOTES.md` for one Tier 0 public-RC version (D13 §8 P3).

    Registry-driven throughout: every licence fact (attribution text,
    source URL, licence id/URL) is read from `licence.SOURCES` at render
    time, never hardcoded here -- the same discipline `release.
    attribution_block` applies to `export-release`'s `ATTRIBUTION.txt`.

    Both packages are named "licence-clean reference-layer fallbacks -- not
    a public MINEDEX site register" (D13 §8 P3's own naming: this project's
    internal MINEDEX-frame register is never what these packages ship).
    Carries the exact D11 claim-boundary sentence at the top, so a reader
    who opens only this file still meets it. States plainly that no
    MINEDEX-derived aggregate is included -- these two packages are
    source-derived reference layers (tenements geometry, a mine-footprint
    mask), not a register export.
    """
    tenements_source = licence.SOURCES["dmirs_003_tenements"]
    maus_source = licence.SOURCES["maus_v2"]

    return "\n\n".join(
        [
            f"# Tier 0 public-RC release {version}",
            D11_CLAIM_BOUNDARY_SENTENCE,
            (
                "This release ships two licence-clean reference-layer fallbacks -- "
                "not a public MINEDEX site register. Each is a direct, source-scoped "
                "extract (tenement boundaries; a mine-footprint mask), never a MINEDEX "
                "register export -- no MINEDEX-derived aggregate is included in this "
                "release."
            ),
            "\n".join(
                [
                    f"## {tenements_source.title} (tier0-tenements.parquet)",
                    f"Snapshot date: {tenements_date}",
                    _package_licence_block("dmirs_003_tenements"),
                ]
            ),
            "\n".join(
                [
                    f"## {maus_source.title} (tier0-maus-wa.parquet)",
                    f"Snapshot date: {maus_date}",
                    _package_licence_block("maus_v2"),
                    MAUS_MODIFICATION_STATEMENT,
                ]
            ),
        ]
    )


# ---------------------------------------------------------------------------
# D13 §8 P6 public-flip checkpoint
# ---------------------------------------------------------------------------

#: The closed D13 §8 P6 checkpoint schema: exactly these 15 booleans, plus
#: the separately-typed `public_aggregate_clearances` list field (schema
#: total 16). `public_flip_authorized` is deliberately last, matching D13's
#: own ordering -- it is the field the repository-visibility owner action
#: reads, never a machine-set value on its own.
CHECKPOINT_BOOL_FIELDS: tuple[str, ...] = (
    "d7_exclusion_passed",
    "fallback_release_passed",
    "licensing_matrix_reconciled",
    "attribution_tests_passed",
    "permitted_fixture_passed",
    "prohibited_fixture_passed",
    "staged_tree_audit_passed",
    "release_payload_audit_passed",
    "full_history_secret_scan_passed",
    "private_ci_green",
    "actions_logs_reviewed",
    "readme_claim_boundary_passed",
    "private_snapshot_verification_passed",
    "reconciliation_report_committed",
    "public_flip_authorized",
)

#: The complete D13 §8 P6 `fields` key set: the 15 booleans above plus the
#: list-typed aggregate-clearances field. Closed -- `load_checkpoint` refuses
#: any doc whose `fields` mapping has a key missing from, or extra beyond,
#: this exact set.
_CHECKPOINT_FIELD_KEYS: frozenset[str] = frozenset(CHECKPOINT_BOOL_FIELDS) | {
    "public_aggregate_clearances"
}

#: Matches the FIRST ```yaml fenced block in a checkpoint markdown doc.
_YAML_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL)

# Matches an actual grant of permission ("permission granted", "grants
# permission", "granted permission", ...) -- not a bare "permission"
# substring, which also appears in honest exclusion language such as
# "exclusion evidence, not permission."
_PERMISSION_GRANT_RE = re.compile(
    r"\b(?:grants?|granted|granting)\s+permission\b"
    r"|\bpermission\s+(?:is\s+|was\s+)?(?:hereby\s+)?granted\b",
    re.IGNORECASE,
)

# A grant phrase preceded closely (within a short word window) by a
# negation reads as an honest denial ("no permission was granted"), not an
# actual grant -- so it must not block authorization.
_PERMISSION_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|without|isn'?t|wasn'?t|aren'?t|weren'?t)\b", re.IGNORECASE
)


def _reads_as_permission_grant(lowered: str) -> bool:
    """Whether `lowered` contains an unnegated grant-of-permission phrase."""
    for match in _PERMISSION_GRANT_RE.finditer(lowered):
        preceding = lowered[max(0, match.start() - 20) : match.start()]
        if _PERMISSION_NEGATION_RE.search(preceding):
            continue
        return True
    return False


#: `verify_checkpoint_digests` identifier prefix marking a data-root-relative
#: artefact (as opposed to a repo-relative one). Live release artefacts
#: (parquet outputs, run manifests) live under the data root, never the git
#: repo -- so their digests can only be checked when a `data_root` is given.
_DATA_ROOT_PREFIX = "data_root:"


def load_checkpoint(path: Path) -> dict[str, Any]:
    """Parse the ONE ```yaml fenced block from a checkpoint markdown doc.

    Refuses (`PublicRcError`) rather than silently accepting: no fenced
    block, a non-mapping top level, a top level that isn't exactly
    `{fields, evidence}`, an `evidence` that isn't a mapping, or a `fields`
    mapping whose keys aren't EXACTLY the closed 16-key D13 schema (missing
    or extra -- both refuse, naming the mismatch). This closes the schema:
    evidence fields (digests, notes) can never leak into `fields` and be
    mistaken for a machine-checkable boolean.
    """
    text = Path(path).read_text()
    match = _YAML_FENCE_RE.search(text)
    if match is None:
        raise PublicRcError(f"{path}: no fenced ```yaml block found")

    raw = yaml.safe_load(match.group(1))
    if not isinstance(raw, dict):
        raise PublicRcError(f"{path}: checkpoint yaml top level must be a mapping")

    if set(raw) != {"fields", "evidence"}:
        raise PublicRcError(
            f"{path}: checkpoint yaml top level must be exactly "
            f"{{'fields', 'evidence'}}, got {sorted(raw)}"
        )

    fields = raw["fields"]
    if not isinstance(fields, dict):
        raise PublicRcError(f"{path}: checkpoint 'fields' must be a mapping")
    field_keys = set(fields)
    missing = _CHECKPOINT_FIELD_KEYS - field_keys
    extra = field_keys - _CHECKPOINT_FIELD_KEYS
    if missing or extra:
        raise PublicRcError(
            f"{path}: checkpoint 'fields' must have exactly the 16 D13 §8 P6 "
            f"keys -- missing {sorted(missing)}, extra {sorted(extra)}"
        )

    evidence = raw["evidence"]
    if not isinstance(evidence, dict):
        raise PublicRcError(f"{path}: checkpoint 'evidence' must be a mapping")

    return raw


def checkpoint_authorizes_flip(doc: dict[str, Any]) -> bool:
    """Whether `doc` (as parsed by `load_checkpoint`) authorizes the public flip.

    Deliberately conservative and never raises -- any structural problem
    (missing field, wrong type) is itself a reason to withhold
    authorization, not an exception to propagate. Booleans passing alone is
    NOT sufficient: the D7 exclusion note is inspected for language that
    reads as a grant of permission (D7 is an EXCLUSION -- redistribution
    stays closed -- never evidence of a licence clearing), and
    `public_aggregate_clearances` must actually be a list, not merely
    truthy.

    This function is HALF of the authorization story, never the whole of
    it: a True here says only that the checkpoint document's fields and
    wording pass. The evidence behind those fields must independently
    verify via `verify_checkpoint_digests(doc, repo_root,
    data_root=...)` with `failed == 0` and no `skipped_offline` gaps left
    unexplained -- an all-true checkpoint whose cited artefacts are
    missing, tampered with, or unverifiable authorizes nothing. Both
    checks are required by the checkpoint doc and D13 §8 P6; no caller
    may treat this boolean alone as authorization.
    """
    try:
        fields = doc["fields"]
        evidence = doc["evidence"]

        for name in CHECKPOINT_BOOL_FIELDS:
            if fields[name] is not True:
                return False

        clearances = fields["public_aggregate_clearances"]
        if not isinstance(clearances, list):
            return False

        d7_note = evidence["d7_exclusion"]
        if not isinstance(d7_note, str):
            return False
        lowered = d7_note.lower()
        if "closed" not in lowered or _reads_as_permission_grant(lowered):
            return False
    except (KeyError, TypeError):
        return False

    return True


def verify_checkpoint_digests(
    doc: dict[str, Any], repo_root: Path, *, data_root: Path | None = None
) -> dict[str, int]:
    """Recompute `evidence.artefact_digests` against real files, disclosing gaps.

    Never trusts a boolean field alone -- a checkpoint claiming e.g.
    `release_payload_audit_passed: true` is only as good as the artefact it
    claims to have audited actually matching. Each identifier is either
    repo-relative (recomputed via `provenance.sha256_file` against
    `repo_root`; a missing file or digest mismatch counts as `failed`) or
    `data_root:`-prefixed (data-root-relative -- live release artefacts
    never live in the git repo). A data-root artefact is verified when
    `data_root` is given, otherwise counted `skipped_offline`: disclosed
    explicitly rather than silently treated as passed.
    """
    counts = {"verified": 0, "failed": 0, "skipped_offline": 0}
    digests = doc.get("evidence", {}).get("artefact_digests", {})
    if not isinstance(digests, dict):
        raise PublicRcError("checkpoint 'evidence.artefact_digests' must be a mapping")

    for identifier, expected_digest in digests.items():
        if identifier.startswith(_DATA_ROOT_PREFIX):
            if data_root is None:
                counts["skipped_offline"] += 1
                continue
            candidate = Path(data_root) / identifier[len(_DATA_ROOT_PREFIX) :]
        else:
            candidate = Path(repo_root) / identifier

        if not candidate.is_file():
            counts["failed"] += 1
            continue

        actual_digest = provenance.sha256_file(candidate)
        if actual_digest == expected_digest:
            counts["verified"] += 1
        else:
            counts["failed"] += 1

    return counts
