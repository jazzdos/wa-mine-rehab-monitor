# Tier 0 public-RC flip checkpoint — 2026-08-29

Status: BUILT TO CHECKPOINT — owner actions pending

The D2/D10 public-flip checklist for the public-RC lane, per D13 §8 P6
(`docs/decisions/2026-08-16-d13-batches-c-g-detailing.md`). This is the
gate the repository-visibility change and the Tier 0 fallback release both
sit behind: D10 fixes the flip conditions, D13 §8 P6 fixes the exact
16-field schema below and the acceptance rule ("repository visibility
changes only after every field passes and the checkpoint is committed").
Nothing in this document authorizes anything by itself — every
machine-checkable field below is `true` on cited live evidence
(2026-08-29 run, `docs/reviews/2026-08-29-public-rc-audit.md`), but the
three OWNER-ONLY fields remain `false` and
`checkpoint_authorizes_flip` therefore still returns `False`. Only the
repository owner may flip those, after reviewing the evidence.

## Checkpoint

```yaml
fields:
  d7_exclusion_passed: true
  fallback_release_passed: true
  licensing_matrix_reconciled: true
  attribution_tests_passed: true
  permitted_fixture_passed: true
  prohibited_fixture_passed: true
  staged_tree_audit_passed: true
  release_payload_audit_passed: true
  full_history_secret_scan_passed: true
  private_ci_green: false
  actions_logs_reviewed: false
  readme_claim_boundary_passed: true
  private_snapshot_verification_passed: true
  reconciliation_report_committed: true
  public_flip_authorized: false
  public_aggregate_clearances: []
evidence:
  d7_exclusion: >-
    D7 adjudication closed: licence conflict (contrary_notice: true),
    recorded in docs/checkpoints/tier0-result.md — exclusion evidence, not
    permission.
  licensing_matrix_reconciled: >-
    2026-08-29 live run: uv run pytest
    tests/test_public_wording.py::test_licensing_matrix_names_the_two_packages
    tests/test_licence_conformance.py::test_licensing_matrix_reconciles_with_registry
    -- 2 passed. Full docs/reviews/2026-08-29-public-rc-audit.md.
  attribution_tests_passed: >-
    2026-08-29 live run: uv run pytest tests/test_attribution_rendering.py
    -q -- 4 passed. Full docs/reviews/2026-08-29-public-rc-audit.md.
  permitted_fixture_passed: >-
    2026-08-29 live run: uv run pytest
    tests/test_public_audits.py::test_synthetic_fixture_allowlist_permits
    tests/test_public_audits.py::test_release_payload_audit_permits_the_rc_artefacts
    -q -- 2 passed. Full docs/reviews/2026-08-29-public-rc-audit.md.
  prohibited_fixture_passed: >-
    2026-08-29 live run: uv run pytest tests/test_public_audits.py -k
    "flagged" -q -- 17 passed, 9 deselected. Full
    docs/reviews/2026-08-29-public-rc-audit.md.
  staged_tree_audit_passed: >-
    2026-08-29 live run: uv run python scripts/audit_public_tree.py -- 0
    finding(s) across 0 file(s), exit 0, tracked+untracked tree including
    worktree-untracked docs/plans/*.md. Full
    docs/reviews/2026-08-29-public-rc-audit.md.
  release_payload_audit_passed: >-
    2026-08-29 live run (run 2): uv run python
    scripts/audit_release_payload.py
    ~/data/wa-mine-monitor/releases/tier0-public-rc/2026.08.29 -- exit 0,
    "0 finding(s) across 0 file(s); 5 file(s) scanned" -- all five
    release files walked (two parquet packages, two run manifests,
    RELEASE_NOTES.md), non-vacuous. Full
    docs/reviews/2026-08-29-public-rc-audit.md.
  full_history_secret_scan_passed: >-
    2026-08-29 live run (run 2): gitleaks 8.30.1, gitleaks git
    --no-banner --log-opts=--all (all refs), 75 commits scanned.
    Baseline default-rules scan found 2 findings, both adjudicated as
    synthetic planted test fixtures (the fake AKIA-prefixed key in
    tests/test_public_audits.py; a fake api_token in tests/test_cli.py's
    scrub test) and allowlisted narrowly in .gitleaks.toml; final scan
    with that config: no leaks found, exit 0. Full adjudication in
    docs/reviews/2026-08-29-public-rc-audit.md.
  readme_claim_boundary_passed: >-
    2026-08-29 live run: uv run pytest
    tests/test_public_wording.py::test_readme_carries_the_exact_d11_sentence_at_first_reference
    -q -- 1 passed. Full docs/reviews/2026-08-29-public-rc-audit.md.
  fallback_release_passed: >-
    2026-08-29 live run (run 2): uv run wa-mine-monitor
    build-tier0-public-rc --config config/base.yaml --version 2026.08.29
    -- exit 0. tier0-tenements.parquet (30,456 rows) and
    tier0-maus-wa.parquet (1,753 rows) built, reconciled
    (reconcile_packages), and written with RELEASE_NOTES.md and per-package
    run manifests; maus source columns AREA, COUNTRY_NAME, ISO3_CODE
    dropped with disclosure per the closed MAUS_BENIGN_SOURCE_COLUMNS
    allowlist. Run 1's refusal on those columns, its root cause, and the
    fix are recorded in docs/reviews/2026-08-29-public-rc-audit.md.
  private_snapshot_verification_passed: >-
    2026-08-29 live run (run 2): gate 4 (_verify_snapshot_or_refuse)
    verify_snapshot triples, recorded in the release run manifests'
    resolved_args -- dmirs_003_tenements {n_ok: 2, n_bad: 0,
    n_missing: 0}; maus_v2 {n_ok: 2, n_bad: 0, n_missing: 0}. Full
    docs/reviews/2026-08-29-public-rc-audit.md.
  reconciliation_report_committed: >-
    2026-08-29 live run (run 2): reconcile_packages ran inside the
    successful build; reconciled row counts (tenements 30456, maus 1753)
    and artefact digests are recorded in the committed
    docs/reviews/2026-08-29-public-rc-audit.md and pinned below in
    artefact_digests (including both data_root: run manifests).
  artefact_digests:
    docs/reviews/2026-08-29-public-rc-audit.md: 0c6fe213d2b5ad7dd469d9c0b15371167fd636f53b40700c189c4f19021e18f6
    evidence/provenance.yaml: 233892066ac10ab5a43c09e326040e6674a0d2dfe80d53ac78e2e4900b4f9a9a
    data_root:releases/tier0-public-rc/2026.08.29/tier0-tenements.parquet.run_manifest.json: ea0a7c7078735c90cc0b4ee1958d6880bb8b4a42bf816484dc7e7e7308d9d8a8
    data_root:releases/tier0-public-rc/2026.08.29/tier0-maus-wa.parquet.run_manifest.json: 1568b9745952674f1b863223a783ec713fb522fd579b66369004f5d4580c04ab
```

## Field reference

Each field is machine-checkable (`public_rc.checkpoint_authorizes_flip`)
except the three named OWNER-ONLY below, which no agent may set.

- **`d7_exclusion_passed`** — the D7 MINEDEX-licence adjudication is
  closed and its exclusion (not a grant of permission) is on record. See
  `evidence.d7_exclusion` — `checkpoint_authorizes_flip` independently
  refuses if that note's language reads as permission rather than
  exclusion, so this field alone can never smuggle a MINEDEX release
  through.
- **`fallback_release_passed`** — the Tier 0 tenements + Maus fallback
  packages (`assemble_tier0_tenements`, `assemble_tier0_maus`) built,
  reconciled (`reconcile_packages`), and released cleanly.
- **`licensing_matrix_reconciled`** — `docs/licensing-matrix.md` agrees
  with `licence.SOURCES` and D7.
- **`attribution_tests_passed`** — `tests/test_attribution_rendering.py`
  (D13 §8 P5) green: every fallback attribution and modification notice
  renders.
- **`permitted_fixture_passed`** — the permitted-content fixture (what
  IS allowed to ship) passes its gate.
- **`prohibited_fixture_passed`** — the prohibited-content fixture (what
  must NEVER ship — MINEDEX row-level data, credentials, local paths)
  is correctly refused.
- **`staged_tree_audit_passed`** — the staged-tree secret/lineage audit
  (git-tracked + untracked, per the cross-task ledger's `git ls-files
  --cached --others --exclude-standard` convention) returns zero
  findings.
- **`release_payload_audit_passed`** — the same audit run against the
  actual release payload (not just the working tree) returns zero
  findings.
- **`full_history_secret_scan_passed`** — a full-git-history secret scan
  (not just the current tree) is clean. D13 §8 P4 is explicit that a
  clean working-tree scan is insufficient on its own.
- **`private_ci_green`** (OWNER-ONLY) — the private repository's CI is
  green on the commit being flipped. Set only by the repository owner,
  never by an agent — no agent may run `gh repo edit` or `git push`.
- **`actions_logs_reviewed`** (OWNER-ONLY) — a human has actually read
  the CI Actions logs, not merely observed a green checkmark.
  `checkpoint_authorizes_flip` refuses `private_ci_green: true` combined
  with `actions_logs_reviewed: false` — green CI without reviewed logs is
  insufficient (D13 §8 P6's own acceptance test).
- **`readme_claim_boundary_passed`** — the exact D11 claim-boundary
  sentence (`D11_CLAIM_BOUNDARY_SENTENCE`) appears at the first product
  reference in `README.md`.
- **`private_snapshot_verification_passed`** — the private-repository
  Tier 0 snapshot verification (fetch/verify triples) passed against live
  data, per the `docs/checkpoints/tier0-result.md` precedent.
- **`reconciliation_report_committed`** — a reconciliation report
  (row counts, digest verification) for this flip is committed to the
  repository, not held only in an agent's working memory.
- **`public_aggregate_clearances`** — a list enumerating every public
  aggregate actually shipped in this release. Must be a real list (not a
  string, not merely present) — an empty list is honest when nothing has
  shipped yet, but a non-list value blocks authorization outright.
- **`public_flip_authorized`** (OWNER-ONLY) — the final field: true only
  once the owner has reviewed every other field and every piece of
  evidence, and personally decides to authorize the flip. Setting every
  other field true does not set this one — it is a distinct owner
  action, never inferred.

`evidence.artefact_digests` maps artefact identifiers to sha256 hex
digests, checked by `public_rc.verify_checkpoint_digests`. A plain
repo-relative path (e.g. `docs/reviews/audit.md`) is checked against the
git repository; a `data_root:`-prefixed identifier is checked against the
live data root and is disclosed as `skipped_offline` (never silently
passed) when no data root is available to the checking process.

## Commands and results

Live-execution runs: 2026-08-29, worktree `.worktrees/public-rc-lane`,
data root `~/data/wa-mine-monitor`. Run 1 (commit `bd097f3`) refused at
the fallback build; run 2 (commit `9d7366f` plus the fixes it
motivated) re-executed the full chain after the root-cause fix. Full
detail (run 1 refusal analysis, fixes, run 2 evidence) in
`docs/reviews/2026-08-29-public-rc-audit.md`; this section is the
per-command summary of run 2.

1. `uv run python bin/verify_evidence.py --ledger evidence/provenance.yaml --data-root ~/data/wa-mine-monitor`
   — exit 0. `{"counts": {"closed": 1, "digest_only": 9, "failed": 0,
   "skipped_offline": 0, "verified": 3}, "failures": []}`. PASS.
2. `uv run python scripts/audit_public_tree.py` — exit 0. "0 finding(s)
   across 0 file(s)". PASS (after `.gitleaks.toml` was reviewed onto
   `CREDENTIAL_FALSE_POSITIVE_ALLOWLIST` — see the audit doc).
3. `uv run wa-mine-monitor build-tier0-public-rc --config config/base.yaml --version 2026.08.29`
   — exit 0. Packages built and reconciled: tenements 30,456 rows, maus
   1,753 rows; maus dropped `AREA`, `COUNTRY_NAME`, `ISO3_CODE` with
   disclosure; snapshot verification triples `{n_ok: 2, n_bad: 0,
   n_missing: 0}` for both sources, recorded in the run manifests.
   (Run 1 at commit `bd097f3` refused here; the refusal, root cause,
   and fix are preserved in the audit doc.)
4. `uv run python scripts/audit_release_payload.py ~/data/wa-mine-monitor/releases/tier0-public-rc/2026.08.29`
   — exit 0. "0 finding(s) across 0 file(s); 5 file(s) scanned" —
   non-vacuous, all five release files walked. PASS.
5. Full-history secret scan — gitleaks 8.30.1, `--log-opts=--all`, 75
   commits. Baseline: 2 findings, both adjudicated synthetic test
   fixtures and allowlisted in `.gitleaks.toml`; final scan with that
   config: no leaks found, exit 0. PASS.
6. `docs/reviews/2026-08-29-public-rc-audit.md` updated with the full
   redacted transcript and analysis for both runs plus the independent
   evidence below.
7. Independent evidence for fields not gated by step 3 (all from this
   same commit):
   - `uv run pytest tests/test_public_wording.py::test_licensing_matrix_names_the_two_packages tests/test_licence_conformance.py::test_licensing_matrix_reconciles_with_registry -q` — 2 passed.
   - `uv run pytest tests/test_attribution_rendering.py -q` — 4 passed.
   - `uv run pytest tests/test_public_audits.py::test_synthetic_fixture_allowlist_permits tests/test_public_audits.py::test_release_payload_audit_permits_the_rc_artefacts -q` — 2 passed.
   - `uv run pytest tests/test_public_audits.py -k "flagged" -q` — 17 passed, 9 deselected.
   - `uv run pytest tests/test_public_wording.py::test_readme_carries_the_exact_d11_sentence_at_first_reference -q` — 1 passed.
8. `uv run pytest tests/test_public_rc_checkpoint.py -q` — all passed.
   The committed checkpoint parses, live digest verification against
   this repo and the live data root returns `{"verified": 4,
   "failed": 0, "skipped_offline": 0}` (the two `data_root:` manifests
   verify when a data root is supplied and are disclosed as
   `skipped_offline` when it is not), and `checkpoint_authorizes_flip`
   returns `False`.

Net result: this checkpoint does **not** authorize the public flip.
Every machine-checkable field is genuinely `true` on its own cited
evidence, but the three OWNER-ONLY fields (`private_ci_green`,
`actions_logs_reviewed`, `public_flip_authorized`) remain `false`, so
`checkpoint_authorizes_flip` refuses. The flip waits on the owner:
push, run CI, review the Actions logs, review this evidence, and
personally set the owner-only fields.

## Honesty flags

- The secret-scan tooling situation (which scanner, full-history coverage,
  any known gaps) is recorded at execution time in this section, not
  asserted here in advance.
- This authorization, once every field is genuinely true and the owner
  sets `public_flip_authorized`, covers the repository visibility change
  and the Tier 0 fallback release ONLY. It does not authorize Batch G
  Pages deployment, and it does not authorize any MINEDEX-derived
  release — D7 keeps that closed regardless of this checkpoint's state.
- No agent may run `gh repo edit` or `git push` to act on this checkpoint;
  merging, pushing, and the repository-visibility flip itself remain
  owner-only actions per the cross-task ledger.
