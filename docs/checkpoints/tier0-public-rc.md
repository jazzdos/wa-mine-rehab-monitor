# Tier 0 public-RC flip checkpoint — 2026-08-29

Status: BUILT TO CHECKPOINT — owner actions pending

The D2/D10 public-flip checklist for the public-RC lane, per D13 §8 P6
(`docs/decisions/2026-08-16-d13-batches-c-g-detailing.md`). This is the
gate the repository-visibility change and the Tier 0 fallback release both
sit behind: D10 fixes the flip conditions, D13 §8 P6 fixes the exact
16-field schema below and the acceptance rule ("repository visibility
changes only after every field passes and the checkpoint is committed").
Nothing in this document authorizes anything by itself — the yaml block
below ships with every boolean `false`, and stays that way until the
live-execution task (Batch F/public-RC follow-on) runs every command,
records real evidence, and flips the machine-passable fields one at a
time.

## Checkpoint

```yaml
fields:
  d7_exclusion_passed: true
  fallback_release_passed: false
  licensing_matrix_reconciled: true
  attribution_tests_passed: true
  permitted_fixture_passed: true
  prohibited_fixture_passed: true
  staged_tree_audit_passed: true
  release_payload_audit_passed: false
  full_history_secret_scan_passed: false
  private_ci_green: false
  actions_logs_reviewed: false
  readme_claim_boundary_passed: true
  private_snapshot_verification_passed: false
  reconciliation_report_committed: false
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
    FALSE -- 2026-08-29 live run: step 3 (build-tier0-public-rc) refused
    (see fallback_release_passed note) so
    ~/data/wa-mine-monitor/releases/tier0-public-rc/2026.08.29 was never
    written. scripts/audit_release_payload.py against that path returned
    "0 finding(s) across 0 file(s)" only because it walked zero files --
    a vacuous result, not evidence of a clean payload. Full
    docs/reviews/2026-08-29-public-rc-audit.md.
  full_history_secret_scan_passed: >-
    FALSE -- 2026-08-29 live run: gitleaks not installed on this machine
    (command -v gitleaks found nothing). No alternative or hand-rolled
    scanner substituted. Required tool: gitleaks. Full
    docs/reviews/2026-08-29-public-rc-audit.md.
  readme_claim_boundary_passed: >-
    2026-08-29 live run: uv run pytest
    tests/test_public_wording.py::test_readme_carries_the_exact_d11_sentence_at_first_reference
    -q -- 1 passed. Full docs/reviews/2026-08-29-public-rc-audit.md.
  fallback_release_passed: >-
    FALSE -- 2026-08-29 live run: uv run wa-mine-monitor
    build-tier0-public-rc --config config/base.yaml --version 2026.08.29
    REFUSED (exit 1, stage "assembly"): "maus input carries unexpected
    extra column(s) beyond the exact maus_id+geometry shape: AREA,
    COUNTRY_NAME, ISO3_CODE". No packages were built; no release
    directory was written. This is the live maus_v2 snapshot's data not
    matching assemble_tier0_maus's strict shape gate -- the gate working
    as designed, not a code defect this task is authorized to fix. Full
    refusal JSON and analysis in docs/reviews/2026-08-29-public-rc-audit.md.
  private_snapshot_verification_passed: >-
    FALSE -- 2026-08-29 live run: build-tier0-public-rc reached the
    "assembly" refusal stage, which is reached only after gate 4
    (_verify_snapshot_or_refuse) passes for both dmirs_003_tenements and
    maus_v2 -- so both raw snapshots are known to have passed integrity
    verification. However the CLI never echoes the verify_snapshot
    {n_ok, n_bad, n_missing} triples except in the final success JSON,
    which was never reached, so the specific triple numbers this field
    asks for do not exist to cite. Full
    docs/reviews/2026-08-29-public-rc-audit.md.
  reconciliation_report_committed: >-
    FALSE -- 2026-08-29 live run: reconcile_packages never ran (assembly
    refused before it); there is no reconciliation report to commit.
  artefact_digests:
    docs/reviews/2026-08-29-public-rc-audit.md: 427f60f6c8ac7ed223625d281701b8912bfbf3770d0768a8d576c34920302aae
    evidence/provenance.yaml: 233892066ac10ab5a43c09e326040e6674a0d2dfe80d53ac78e2e4900b4f9a9a
```

Note: `evidence.artefact_digests` does not include the two `data_root:`
release run manifests the live-execution task instructed, because they
do not exist -- step 3 (`build-tier0-public-rc`) refused before writing
any release artefact or manifest. See `fallback_release_passed`'s
evidence note and `docs/reviews/2026-08-29-public-rc-audit.md`.

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

Live-execution run: 2026-08-29, worktree `.worktrees/public-rc-lane`,
commit `bd097f38167c468fe3ca4e4689e8ef15178c9651`, data root
`~/data/wa-mine-monitor`. Full detail (redacted audit output, refusal
analysis, independent-field test evidence) in
`docs/reviews/2026-08-29-public-rc-audit.md`; this section is the
per-command summary.

1. `uv run python bin/verify_evidence.py --ledger evidence/provenance.yaml --data-root ~/data/wa-mine-monitor`
   — exit 0. `{"counts": {"closed": 1, "digest_only": 9, "failed": 0,
   "skipped_offline": 0, "verified": 3}, "failures": []}`. PASS.
2. `uv run python scripts/audit_public_tree.py` — exit 0. "0 finding(s)
   across 0 file(s)". PASS.
3. `uv run wa-mine-monitor build-tier0-public-rc --config config/base.yaml --version 2026.08.29`
   — exit 1. **REFUSED**: `{"refusal": "maus input carries unexpected
   extra column(s) beyond the exact maus_id+geometry shape: AREA,
   COUNTRY_NAME, ISO3_CODE", "stage": "assembly"}`. Not worked around per
   the live-execution task's hard rule. No release directory, packages,
   or manifests were written.
4. `uv run python scripts/audit_release_payload.py ~/data/wa-mine-monitor/releases/tier0-public-rc/2026.08.29`
   — exit 0. "0 finding(s) across 0 file(s)" — **vacuous**: the target
   directory does not exist (step 3 refused), so zero files were walked.
   Not treated as a pass.
5. Full-history secret scan — `command -v gitleaks` found nothing;
   gitleaks is not installed on this machine. Scan not run. Required
   tool: gitleaks.
6. `docs/reviews/2026-08-29-public-rc-audit.md` written with the full
   redacted transcript and analysis for steps 1-5 plus the independent
   evidence below.
7. Independent evidence for fields not gated by step 3 (all from this
   same commit):
   - `uv run pytest tests/test_public_wording.py::test_licensing_matrix_names_the_two_packages tests/test_licence_conformance.py::test_licensing_matrix_reconciles_with_registry -q` — 2 passed.
   - `uv run pytest tests/test_attribution_rendering.py -q` — 4 passed.
   - `uv run pytest tests/test_public_audits.py::test_synthetic_fixture_allowlist_permits tests/test_public_audits.py::test_release_payload_audit_permits_the_rc_artefacts -q` — 2 passed.
   - `uv run pytest tests/test_public_audits.py -k "flagged" -q` — 17 passed, 9 deselected.
   - `uv run pytest tests/test_public_wording.py::test_readme_carries_the_exact_d11_sentence_at_first_reference -q` — 1 passed.
8. `uv run pytest tests/test_public_rc_checkpoint.py -q` — 88 passed.
   The committed checkpoint parses, digest verification returns
   `{"verified": 2, "failed": 0, "skipped_offline": 0}` (live, against
   this repo and the live data root), and
   `checkpoint_authorizes_flip` returns `False`.

Net result: this checkpoint does **not** authorize the public flip.
`fallback_release_passed`, `release_payload_audit_passed`,
`private_snapshot_verification_passed`,
`reconciliation_report_committed`, and `full_history_secret_scan_passed`
are all `false` because step 3's live-data refusal cascades through
them; the three owner-only fields remain untouched at `false`. Every
other field is genuinely `true` on its own independent evidence, cited
inline in `evidence` above.

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
