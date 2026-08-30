# Batch G closes QGIS-only; release decision deferred (2026-08-30)

**Status: authorised by the owner 2026-08-30 (chat).** This is
amendment **A11** in `docs/amendments-and-limitations.md`.

## Decision

Batch G closes QGIS-only. No trajectory, register, or context release
package is added to `release.PACKAGES`; the deliverables are the
`build-trajectory-summary` curated GeoPackage, its QML styles, the
`qgis/README.md`, and the interactively-saved `qgis/wa-mine-monitor.qgz`
(owner, in QGIS). The release decision for a public, `site_id`-keyed
trajectory package is explicitly NOT taken in this batch — it is
deferred, not declined, and not superseded by anything here.

## Why

ROADMAP row 5 previously read Batch G as versioned releases plus an
`export-release` package for the trajectory product. `export-release`
and `export_gate.export_public` already exist and are exercised today
by exactly one package, `footprint-areas` (A8); amendment L10 is
explicit that a register/trajectory package is "added only when a
release of it is actually decided" — that decision has not been made,
and this batch does not make it.

Taking that decision now would also force a D7 question this batch is
not positioned to resolve: a public, `site_id`-keyed trajectory release
would expose MINEDEX-derived row-level records and crosswalk membership
at the export boundary, and `export_gate`'s existing row gate is
`source_id`-keyed — it does not inspect column names, so it would not
catch a `site_id` column carrying the same exposure by a different
route. Deferring the release decision defers that unresolved question
with it, recorded here so it is not silently forgotten if a release is
decided later. `export_gate` is not invoked anywhere in this batch; the
trajectory summary is a private curated artifact and crosses no export
boundary.

## What changes

- `wa-mine-monitor build-trajectory-summary` writes
  `curated/trajectory-summary/<date>/trajectory_summary.gpkg`
  (`register_sites` + `site_summary` layers) plus its run-manifest
  sidecar. No new entry in `release.PACKAGES`.
- `qgis/styles/*.qml`, `qgis/README.md` ship in this batch; the `.qgz`
  itself is saved and opened interactively by the owner, confirmed in
  `docs/checkpoints/batch-g-qgis.md`.
- ROADMAP row 5 is rewritten to this narrower scope; the release-package
  half of the old row 5 text is removed, not replaced — it returns only
  if and when a release is separately decided.
- Amendments register gains A11, citing this record. L10 and L11's
  existing wording is unchanged: both were already closed/re-scoped
  against the `footprint-areas` package before this batch, and this
  decision narrows ROADMAP row 5 only — it does not reopen or amend L10
  or L11 themselves.

## Cites

ROADMAP row 5 (pre-rescope text); A8
(`decisions/2026-08-25-public-web-page-descope.md`); L10's "only when a
release of it is actually decided" language
(`docs/amendments-and-limitations.md`); the approved design doc
(`docs/plans/2026-08-30-batch-g-qgis-design.md`, §1 "Owner decisions,
fixed").
