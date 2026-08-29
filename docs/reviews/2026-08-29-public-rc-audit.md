# Public-RC live-execution audit — 2026-08-29

Task: live execution of the D13 §8 P6 public-flip checkpoint chain
(`docs/checkpoints/tier0-public-rc.md`), run from the worktree at
`.worktrees/public-rc-lane`, commit `bd097f38167c468fe3ca4e4689e8ef15178c9651`,
against the live data root `~/data/wa-mine-monitor`.

Tool versions: `uv 0.9.29`, `python 3.12.12`, `ruff 0.16.3`.

**Outcome: the fallback-release build (step 3) REFUSED. This is a real
finding, not a tooling error, and it blocks every field that depends on a
built release payload. Per the live-execution task's hard rule, the
refusal was not worked around — it is recorded verbatim below and the
build was not retried, patched, or re-run against modified source data.**

## Step 1 — evidence verifier

Command: `uv run python bin/verify_evidence.py --ledger evidence/provenance.yaml --data-root ~/data/wa-mine-monitor`

Exit: 0

```json
{
  "counts": {
    "closed": 1,
    "digest_only": 9,
    "failed": 0,
    "skipped_offline": 0,
    "verified": 3
  },
  "failures": []
}
```

Result: PASS (`failed: 0`).

## Step 2 — staged-tree public-payload audit

Command: `uv run python scripts/audit_public_tree.py`

Exit: 0

```
0 finding(s) across 0 file(s)
```

Result: PASS, zero findings across the tracked+untracked (git
`ls-files --cached --others --exclude-standard`) tree, including the
worktree's untracked `docs/plans/*.md` files.

## Step 3 — build-tier0-public-rc — REFUSED

Command: `uv run wa-mine-monitor build-tier0-public-rc --config config/base.yaml --version 2026.08.29`

Exit: 1 (confirmed by direct re-run without a pipe; an earlier `tee`-piped
invocation masked this as exit 0 from the shell, which was a transcript
artefact of the pipeline, not the command's own exit status)

Refusal JSON (verbatim):

```json
{
  "refusal": "maus input carries unexpected extra column(s) beyond the exact maus_id+geometry shape: AREA, COUNTRY_NAME, ISO3_CODE",
  "stage": "assembly"
}
```

Analysis: the CLI reached the `assembly` stage, which per `cli.py`'s
`build_tier0_public_rc_cmd` is AFTER gate 1 (version-shape), gate 2
(output-not-exists), gate 3 (both raw snapshots exist), and gate 4
(`_verify_snapshot_or_refuse` on both `dmirs_003_tenements` and
`maus_v2`). Neither snapshot-missing nor a verification-failure refusal
was raised, so both raw snapshots passed integrity verification before
assembly began. The refusal itself comes from `public_rc.
assemble_tier0_maus`'s strict extra-column gate: the live `maus_v2`
snapshot's `wa_extract.gpkg` carries three columns beyond the exact
`maus_id`+`geometry` shape that package requires (`AREA`,
`COUNTRY_NAME`, `ISO3_CODE`). Per the cross-task ledger's design intent
("maus refuses any extra column" — deliberately stricter than the
tenements package's disclosed-drop allowance), this is the code working
as designed: it refuses to silently ship or silently drop unexpected
columns from a third-party source rather than guessing what they mean.

Consequence: no release directory was written.
`~/data/wa-mine-monitor/releases/tier0-public-rc/2026.08.29` does not
exist. No run manifests exist. No `verify_snapshot` triples were ever
echoed (the CLI only echoes the final success JSON, which was never
reached), so `private_snapshot_verification_passed` cannot cite the
specific `{n_ok, n_bad, n_missing}` numbers the live-execution task asked
for, even though gate 4 is known to have passed for both sources.

This is upstream of this task's scope to fix: resolving it requires a
product decision (extend `assemble_tier0_maus`'s column allowlist, or
have Maus v2 fetch/curation drop the extra columns upstream with
disclosure, mirroring the tenements package) that this live-execution
task is not authorized to make.

## Step 4 — release-payload audit (vacuous — no payload exists)

Command: `uv run python scripts/audit_release_payload.py ~/data/wa-mine-monitor/releases/tier0-public-rc/2026.08.29`

Exit: 0

```
0 finding(s) across 0 file(s)
```

This "0 finding(s) across 0 file(s)" result is **vacuous, not a pass**:
the target directory does not exist because step 3 refused before
writing anything, so `public_audit.audit_release_dir` walked zero files.
It does not constitute evidence that a clean release payload exists, and
`release_payload_audit_passed` is recorded `false` on that basis.

## Step 5 — full-history secret scan

`command -v gitleaks` found nothing on `PATH`; `gitleaks` is not
installed on this machine.

Result: `full_history_secret_scan_passed: false`. Required tool:
`gitleaks` (any recent release providing `gitleaks git --no-banner .` or
the older `gitleaks detect` subcommand), run from the MAIN checkout root
(`/Users/jarrodbaker/Documents/wa-mine-rehab-monitor`) so the scan covers
the shared full history. No alternative or hand-rolled scanner was
substituted, per the live-execution task's explicit instruction.

## Independent field evidence (not gated by step 3)

The following checkpoint fields do not depend on the fallback-release
build and were verified directly against the current test suite (run
from the worktree, same commit):

```
uv run pytest tests/test_public_wording.py::test_licensing_matrix_names_the_two_packages \
  tests/test_licence_conformance.py::test_licensing_matrix_reconciles_with_registry -q
2 passed in 0.26s
```
→ `licensing_matrix_reconciled: true`.

```
uv run pytest tests/test_attribution_rendering.py -q
4 passed in 0.31s
```
→ `attribution_tests_passed: true`.

```
uv run pytest tests/test_public_audits.py::test_synthetic_fixture_allowlist_permits \
  tests/test_public_audits.py::test_release_payload_audit_permits_the_rc_artefacts -q
2 passed in 0.01s
```
→ `permitted_fixture_passed: true` (the permitted-content fixtures — what
IS allowed to ship — pass their gates).

```
uv run pytest tests/test_public_audits.py -k "flagged" -q
17 passed, 9 deselected in 0.05s
```
→ `prohibited_fixture_passed: true` (every prohibited-content fixture —
bulk extensions, evidence-bundle markers, credentials, local paths,
geometry, MINEDEX tokens — is correctly flagged/refused).

```
uv run pytest tests/test_public_wording.py::test_readme_carries_the_exact_d11_sentence_at_first_reference -q
1 passed in 0.01s
```
→ `readme_claim_boundary_passed: true` — the exact D11 claim-boundary
sentence appears at the first product reference in `README.md`.

`d7_exclusion_passed: true` — cites the closed D7 adjudication recorded
in `docs/checkpoints/tier0-result.md`: `decision: "licence conflict;
redistribution closed"`, `contrary_notice: true`,
`minedex_redistribution_allowed: false`. This is an exclusion record, not
a grant of permission, and `checkpoint_authorizes_flip` independently
refuses if the note's language reads otherwise.

## Anomaly noted, out of scope for this checkpoint

While gathering independent evidence, the full
`tests/test_licence_conformance.py` file was run and one unrelated test
failed:

```
uv run pytest tests/test_licence_conformance.py -q
...
FAILED tests/test_licence_conformance.py::test_every_literal_redistribute_use_is_exempted_or_absent
  literal redistribute_public= use(s) outside licence.py with no exemption
  recorded: ['cli.py:2916', 'cli.py:3370', ... 34 locations]
1 failed, 66 passed in 1.31s
```

This is unrelated to `licensing_matrix_reconciled` (a different test in
the same file, verified passing above, in isolation) but is a real,
currently-failing test in the repository. It is recorded here for
visibility; fixing it is out of scope for this live-execution task.

## Fields left false and why

| Field | Value | Why |
|---|---|---|
| `fallback_release_passed` | false | step 3 refused; no packages were built |
| `release_payload_audit_passed` | false | step 4's result is vacuous (no payload directory exists) |
| `private_snapshot_verification_passed` | false | gate 4 is known to have run and passed for both sources (the refusal stage was `assembly`, after gate 4), but the CLI never echoed the `verify_snapshot` triples because it refused before the final success JSON |
| `reconciliation_report_committed` | false | `reconcile_packages` never ran; there is nothing to commit a reconciliation report about |
| `full_history_secret_scan_passed` | false | `gitleaks` not installed |
| `private_ci_green`, `actions_logs_reviewed`, `public_flip_authorized` | false | OWNER-ONLY, untouched by this task |

Because `fallback_release_passed` (and the fields chained from it) are
false, `public_rc.checkpoint_authorizes_flip` returns `False` on this
checkpoint regardless of every other field — the flip remains
unauthorized, as it must.

## Next step (not taken by this task)

The blocking issue is a live-data mismatch, not a code defect: the
`maus_v2` `wa_extract.gpkg` snapshot carries `AREA`, `COUNTRY_NAME`,
`ISO3_CODE` beyond the `maus_id`+`geometry` shape
`assemble_tier0_maus` requires. Resolving it needs an owner/design
decision on how those columns should be handled (drop with disclosure
like the tenements package, or extend the allowed shape) before
`build-tier0-public-rc` can be re-run.
