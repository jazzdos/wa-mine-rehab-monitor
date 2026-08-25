# Public web page withdrawn from scope (2026-08-25)

**Trigger.** A standing contradiction in the record, made explicit by the
2026-08-25 critical review of decisions against the project's remaining
use cases. The design's fixed owner decision reads "output is a static
site + map + GeoParquet; home is a new public repo" (design §1), while
the Tier 1 product framing decided the same day
(`2026-08-25-tier1-product-framing.md`) concludes "the portfolio
artefact is the repository, decision record, and private product, not a
public site." With D7 fail-closed (MINEDEX redistribution prohibited,
L12), the D5 Pages gate cannot pass and D13 §2 pre-registers it as
recordable-failed (O5). Every remaining use of the product — private
screening/triage over footprints, a citable descriptive dataset, an
event-timing layer for cross-project analyses — is data-level and
private. The static site (MapLibre/PMTiles, per-site cards, D5's
rendering preconditions) is build effort for a surface with no
remaining audience, and every public rendering surface is claim-boundary
wording that must be policed.

**Options considered.** (1) Status quo: Batch G builds the full
rendering privately and the D5 Pages gate is recorded failed as
pre-registered. (2) Withdraw the web deliverable entirely: no site
cards, no tables, no MapLibre/PMTiles map; the D5 gate is never
evaluated because the deliverable behind it no longer exists; private
consumption moves to a committed QGIS project over the curated
GeoParquet artefacts. (3) Defer to Batch G planning.

**Decision.** (2), decided by the owner 2026-08-25 ("plan to remove
public web page from scope", approved same day). This is a scope
withdrawal, not a gate waiver: D13 §2's rule that a failed gate "is
never relaxed, silently bypassed, or converted into a compliance or
performance conclusion" is untouched, because D5 is not being failed,
relaxed, or passed — the deliverable it gates is withdrawn and the gate
is recorded as never evaluated. The claim boundary is unchanged and
continues to bind every artefact, including QGIS project symbology,
labels, and any layout exported from it.

**Consequences.**

- **Amendment A8.** Design §1's fixed output decision is amended: the
  output is versioned GeoParquet data releases plus a private QGIS
  project (`qgis/` directory: project file and styles reading the
  curated parquet artefacts), not a static site. Recorded in
  `docs/amendments-and-limitations.md`. No protocol digest is affected;
  no acquisition, threshold, or extraction semantics change.
- **Batch G re-scope.** Withdrawn: site cards, tables, the
  MapLibre/PMTiles map, and D5's rendering preconditions (artifact
  < 800 MiB, PMTiles verification, mobile/keyboard checks). Retained:
  versioned releases, export gating, and — promoted from deferred
  (L11) to a Batch G task — wiring `export_gate.export_public` into an
  `export-release` CLI command, because with a private product every
  artefact that leaves the repository (including any cross-repo
  analysis share) is an export and today that boundary is unenforced
  (L10).
- **Disclosure surface.** Tier 1 product framing required every
  rendered surface to state footprint sharing when
  `shared_footprint_site_count` exceeds 1. With those surfaces
  withdrawn, the obligation attaches to the schema field itself, the
  data dictionary, and the QGIS project's default styling and labels;
  the L17 disclosure travels with the data, not with pages.
- **New deliverable.** The `qgis/` project is built after Tier 1
  exists (post-E4), replacing Batch G's rendering tasks at a fraction
  of their cost.
- **Unchanged.** The C → E5 → E4 build sequence and all its gates; D7
  closure and L12; the claim boundary (README, design §1); the Tier 0
  public-RC lane, which concerns flipping the repository public with a
  MINEDEX-free payload and is independent of Pages by D13 §2's own lane
  separation — withdrawing the web page does not withdraw the repo
  flip.

Closes open item **O5**.
