# D9–D12: initial commit, private remote, naming, post-acceptance sequencing

Rulings issued by the delegated codex director, 2026-08-16, consulted after
the live Tier 0 acceptance pass (`docs/checkpoints/tier0-result.md`). The
consult ran detached (`codex exec -s read-only`) against the repo tree at
zero commits; the director's response is recorded verbatim below, unedited.
Prior rulings: D1–D5 in `docs/plans/2026-08-15-wa-mine-rehab-monitor-design.md`;
D6–D8 in `docs/decisions/2026-08-16-d6-d8-dasc-acquisition-and-minedex-licence.md`.

---

Repository inspection confirms zero commits and no remote. The acceptance records support the 407-test and live DASC-pass claims, subject to two stale passages identified below.

## D9 — Initial commit

**Ruling:** Authorize one atomic initial commit, `feat: establish Tier 0 WA mine rehabilitation monitor`, containing the complete accepted Tier 0 code, tests, configuration, CI, governance documents, licensing matrix, checkpoint, corrected handoff, and D9–D12 record. Do not reconstruct an artificial multi-commit development history.

Before staging:

1. Correct the handoff’s stale statement that the live rerun had not occurred and update implementation-plan Task 11 from `adjudicated: false` to the D7-adjudicated closed state.
2. Extend `.gitignore` to cover at least `.env*`, `.envrc`, `config/local.*`, `*.local.yaml`, credential JSON, private keys, `.DS_Store`, IDE files, logs, coverage output, notebooks/checkpoints, Shapefile sidecars, CSV/XLSX/Arrow/Feather/NetCDF/Zarr outputs, with exceptions only for explicitly audited synthetic fixtures.
3. Audit the staged file list and rerun ruff, format-check, mypy, and pytest immediately before committing.
4. Do not commit raw DASC ZIPs, extracted MINEDEX files, licence-evidence bundles, snapshots, SHA256SUMS from the data root, register/crosswalk Parquet or manifests, generated site assets, credentials, local paths, machine files, or any MINEDEX-derived row-level data. Hand-built licence-clean synthetic test fixtures are permitted.

The current `.gitignore` covers principal bulk formats and data directories but is not adequate for secrets, local configuration, machine files, or Shapefile components. The recorded green battery is 407 tests, and the live pass is documented separately.

**Rationale:** Tier 0 was built and accepted as one integrated baseline, so one auditable foundation commit is more accurate and safer than manufacturing partially functional historical layers.

## D10 — Private GitHub remote and public flip

**Ruling:** Authorize creation of `jazzdos/wa-mine-rehab-monitor` with visibility `PRIVATE`, default branch `main`, and no GitHub-generated README, licence, or `.gitignore`; add it as `origin` and push only after D9 is committed. Do not create it under the currently configured `jasmineownns` account, whose local GitHub CLI authentication is invalid.

The repository remains private until all of these Tier 0 RC conditions pass:

1. D7 is treated as a resolved fail-closed decision: MINEDEX redistribution remains disabled, and the public repository, release payload, and generated site contain no MINEDEX-derived row-level records, coordinates, ownership fields, or raw evidence.
2. A public-safe Tier 0 release candidate exists. If the tenements-plus-Maus fallback described by the design has not been built and audited, the repository remains private.
3. `docs/licensing-matrix.md` is committed and reconciled with the authoritative source registry.
4. Attribution rendering and both permitted and prohibited licence-gate fixtures pass.
5. Raw/bulk exclusions pass both staged-tree and release-payload audits.
6. A full-history secret scan is clean.
7. GitHub Actions is green and its logs have been manually reviewed.
8. README states the descriptive claim boundary, uses D8 owner terminology, and accurately distinguishes the internal MINEDEX monitoring frame from publicly distributable outputs.
9. The immutable private Tier 0 snapshots verify cleanly, the reconciliation report is committed, and every public aggregate or report is explicitly cleared by the D7 payload audit.

The current live acceptance does not itself authorize the public flip because private CI has not run and no audited public-safe Tier 0 release payload is recorded. These criteria preserve D2 while clarifying that D7 resolves the licence question by exclusion, not by permission.

**Rationale:** The private remote is needed for CI and durable history now, while D7 requires a separately audited, MINEDEX-closed release candidate before public visibility.

## D11 — Repository and product naming

**Ruling:** Confirm the following names without amendment:

- Repository: `wa-mine-rehab-monitor`
- Product title: **WA Mine Rehabilitation Spectral Monitor**
- Short label: **WA Mine Rehab Monitor**
- Python package: `wa_mine_monitor`
- Distribution and CLI: `wa-mine-monitor`

On every public landing page and release, the first product reference must be accompanied by: **“Descriptive spectral change chronologies; not a compliance or performance assessment.”** The short label may appear alone only where the full title and claim-boundary statement are already visible. Public wording must not imply that MINEDEX-derived rows are available while D7 remains closed, and owners must never be described as operators.

These names reproduce D2 and the current README/package metadata.

**Rationale:** The established title remains accurate when “Spectral” and the mandatory claim boundary qualify “Rehabilitation,” while changing it now would create unnecessary identity drift.

## D12 — Post-acceptance batch sequence

**Ruling:** Tier 0 acceptance closes Batch B and authorizes Batch C after a short Batch B closeout:

1. Record D9–D12, correct stale documents, harden `.gitignore`, commit, create the private remote, and obtain green private CI.
2. Before adding Batch C coverage fields, represent `n_tenements_intersecting` “not computed” separately from a genuine zero for coordinate-less sites, rebuild the affected register/checkpoint, and add extract-validation disclosure for current versus ended `ProjectsOwners` relationships.
3. Triage the seven deferred minor findings; close them or record an explicit non-blocking disposition.
4. Detail Batches C–G and record a port/adopt/build decision for each.
5. Preserve the internal build order C → D → E → F → G. Batch H remains conditional under D4.
6. Move the D2 public-repository checklist out of Batch G into a distinct Tier 0 public-RC lane. It may proceed alongside later private work but independently gates the public flip. Batch G retains export/site work and the D5 Pages gate.

The accepted run clears the principal Batch B dependency but records the zero-versus-not-computed issue and unpinned owner-history composition.

**Rationale:** The live pass permits Tier 1 preparation, but the disclosed semantic gap must be closed before adding more per-site counts, and D2’s Tier 0 public gate cannot remain deferred to Batch G.

---

Editorial note (orchestrator, not director): the director's inline file/line
citations were rendered as local-path markdown links in the raw output; they
are omitted above as they carried absolute local paths (D9 item 4 forbids
committing local paths). The cited files are: `.gitignore`, the Tier 0
handoff, the checkpoint, the design doc (D2 criteria, fallback rule), the
D6–D8 decision record, `README.md`, and the implementation plan's later
batches section. The raw output is preserved outside the repo in the session
scratchpad.
