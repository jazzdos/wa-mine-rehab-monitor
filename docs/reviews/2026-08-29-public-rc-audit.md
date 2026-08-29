# Public-RC live-execution audit — 2026-08-29

Task: live execution of the D13 §8 P6 public-flip checkpoint chain
(`docs/checkpoints/tier0-public-rc.md`), run from the worktree at
`.worktrees/public-rc-lane` against the live data root
`~/data/wa-mine-monitor`.

Tool versions: `uv 0.9.29`, `python 3.12.12`, `ruff 0.16.3`,
`gitleaks 8.30.1`.

This audit ran twice. **Run 1** (commit
`bd097f38167c468fe3ca4e4689e8ef15178c9651`) refused at the
fallback-release build: the live `maus_v2` snapshot carries three source
columns (`AREA`, `COUNTRY_NAME`, `ISO3_CODE`) beyond the exact
`maus_id`+geometry shape the original `assemble_tier0_maus` gate
required. That refusal was recorded verbatim, not worked around, and is
preserved below. **Run 2** (commit
`9d7366f8a4eb9262ed5ee7eb226f00020a41c8b5` plus the fixes it motivated)
re-executed the full chain after the root cause was fixed; every
machine-checkable step now passes on real evidence.

## Run 1 refusal (historical record)

Command: `uv run wa-mine-monitor build-tier0-public-rc --config
config/base.yaml --version 2026.08.29` — exit 1, refusal JSON verbatim:

```json
{
  "refusal": "maus input carries unexpected extra column(s) beyond the exact maus_id+geometry shape: AREA, COUNTRY_NAME, ISO3_CODE",
  "stage": "assembly"
}
```

Root cause (established before any fix, per kit:debugging):
`sources/maus.py`'s `clip_to_wa` clips the global Maus v2 polygons to WA
and adds `maus_id`, but never selects columns — so the live
`wa_extract.gpkg` legitimately carries the pinned Maus v2 source's own
columns (`ISO3_CODE`, `COUNTRY_NAME`, `AREA`; confirmed directly against
the GeoPackage table). The plan's "exact maus_id+geometry" assumption
was wrong about the snapshot, not the snapshot wrong about the plan.

Fix (commit `9d7366f`): `assemble_tier0_maus` now drops exactly the
closed allowlist `MAUS_BENIGN_SOURCE_COLUMNS = ("AREA", "COUNTRY_NAME",
"ISO3_CODE")` with disclosure — the dropped names are returned to the
CLI, echoed in the command output, and recorded in both public run
manifests' `resolved_args.dropped_source_columns`. Any column beyond
`maus_id` + geometry + that allowlist still refuses (the allowlist is
closed, unlike the tenements package's open drop-and-disclose, because
an unknown column cannot have come from the pinned Maus v2 source).
Covered by `tests/test_public_rc.py::
test_maus_drops_known_source_columns_with_disclosure` and
`::test_maus_refuses_extra_column_even_alongside_benign_ones`.

Run 1 also found two payload-audit findings classes in the public run
manifests (local paths, MINEDEX lineage tokens, and after committing,
credential + geometry shapes): `provenance.collect_git_state`
deliberately embeds the full working-tree diff — including `--no-index`
diffs of untracked files such as `docs/plans/*.md` — which is correct
for internal manifests (reconstructability) but a leak vector for the
two manifests that ship inside the public release payload. Fix: the
`build-tier0-public-rc` command builds a `public_git_state` for its two
public manifests with `diff` emptied, `diff_omitted_for_public_payload:
true`, and `diff_sha256` carrying the digest of the omitted diff, so the
omission is disclosed and the diff remains verifiable privately. Covered
by `tests/test_public_rc.py::
test_public_manifests_omit_dirty_tree_diff_with_disclosure`.

## Run 2 — full chain, current state

### Step 1 — evidence verifier

Command: `uv run python bin/verify_evidence.py --ledger
evidence/provenance.yaml --data-root ~/data/wa-mine-monitor`

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

### Step 2 — staged-tree public-payload audit

Command: `uv run python scripts/audit_public_tree.py`

Exit: 0 — `0 finding(s) across 0 file(s)` over the tracked+untracked
(`git ls-files --cached --others --exclude-standard`) tree, including
the worktree-untracked `docs/plans/*.md` files.

Note: the first run of this step after `.gitleaks.toml` was added
flagged that file (`credential (credential-shaped content)`, exit 1) —
its allowlist regexes necessarily quote the synthetic planted fixtures
verbatim, the same structural self-match as `public_audit.py` itself.
`.gitleaks.toml` was reviewed and added deliberately to
`CREDENTIAL_FALSE_POSITIVE_ALLOWLIST` (credential rule only; all other
rules still run against it), and the audit re-run to the clean result
above.

### Step 3 — build-tier0-public-rc

Command: `uv run wa-mine-monitor build-tier0-public-rc --config
config/base.yaml --version 2026.08.29`

Exit: 0. Success echo (paths abbreviated): version `2026.08.29`, counts
`{"maus": 1753, "tenements": 30456}`, `tenements_snapshot_date` and
`maus_snapshot_date` both `2026-08-16`, tenements
`dropped_source_columns` = 34 named columns (holder/address/date
administrative fields), `maus_dropped_source_columns = ["AREA",
"COUNTRY_NAME", "ISO3_CODE"]`.

Written to
`~/data/wa-mine-monitor/releases/tier0-public-rc/2026.08.29/`:
`tier0-tenements.parquet` (30,456 rows), `tier0-maus-wa.parquet`
(1,753 rows), `RELEASE_NOTES.md`, and one run manifest per package.

Snapshot verification (gate 4, `_verify_snapshot_or_refuse`), recorded
in the manifests' `resolved_args`:

- `dmirs_003_tenements`: `{"n_ok": 2, "n_bad": 0, "n_missing": 0}`
- `maus_v2`: `{"n_ok": 2, "n_bad": 0, "n_missing": 0}`

Both public manifests record `git.sha =
9d7366f8a4eb9262ed5ee7eb226f00020a41c8b5`, `git.dirty = true` (the
post-commit test/doc edits), `git.diff = ""` with
`diff_omitted_for_public_payload: true` and a `diff_sha256` of the
omitted diff, per the public-payload boundary above.

### Step 4 — release-payload audit

Command: `uv run python scripts/audit_release_payload.py
~/data/wa-mine-monitor/releases/tier0-public-rc/2026.08.29`

Exit: 0 — `0 finding(s) across 0 file(s); 5 file(s) scanned`. All five
release files (two parquet packages, two run manifests, release notes)
were walked and none produced a finding. Non-vacuous: the file count is
printed precisely so an empty-directory scan can never masquerade as a
pass again.

### Step 5 — full-history secret scan

Tool: `gitleaks 8.30.1` (installed via Homebrew for this task; run 1
recorded it absent). Run from the worktree, which shares the repository
object store with the main checkout, with `--log-opts=--all` so every
ref — main, this feature branch, and all other branches — is covered.

Baseline scan (default rules, no config):
`gitleaks git --no-banner --log-opts=--all .` — 75 commits scanned,
**2 findings**, both adjudicated as synthetic planted fixtures:

1. `aws-access-token` — `tests/test_public_audits.py` (commit
   `9d7366f8`): the planted fake key in the AWS-key credential-detector
   test — an AKIA-prefixed, sequential-alphabet 16-letter string the
   test writes into a fixture file to prove the detector fires. Fake by
   construction — not a rotatable secret. (Neither the literal nor the
   test's own name is repeated here, since both match the credential
   sniff; see the test file and `.gitleaks.toml`.)
2. `generic-api-key` — `tests/test_cli.py` (commit `dd70b54b`): the
   planted `api_token` value of the manifest secret-scrubbing test
   (a "hunter2"-style joke token), today documented at
   `tests/test_cli.py:219` as the offending `input_value` the scrub
   test asserts is redacted. Fake by construction.

Both fixtures were then allowlisted in `.gitleaks.toml` (narrow,
literal-string regexes; config extends the default ruleset; the file's
header forbids allowlisting anything but synthetic fixtures). Final
scan: `gitleaks git --no-banner --log-opts=--all --config
.gitleaks.toml .` — **75 commits scanned, no leaks found**, exit 0.

Result: PASS — full history clean after adjudicating the two synthetic
fixtures.

### Step 6 — reconciliation

`reconcile_packages` ran inside the successful build (it gates the
final success echo); the reconciled row counts (`tenements: 30456`,
`maus: 1753`) and the per-package digests are recorded in this
committed report and in the two run manifests named in the checkpoint's
`artefact_digests`.

## Independent field evidence (unchanged from run 1)

The following fields were verified directly against the test suite and
remain green (re-confirmed by the full battery on the final tree):

- `licensing_matrix_reconciled`: `uv run pytest
  tests/test_public_wording.py::test_licensing_matrix_names_the_two_packages
  tests/test_licence_conformance.py::test_licensing_matrix_reconciles_with_registry
  -q` — 2 passed.
- `attribution_tests_passed`: `uv run pytest
  tests/test_attribution_rendering.py -q` — 4 passed.
- `permitted_fixture_passed`: `uv run pytest
  tests/test_public_audits.py::test_synthetic_fixture_allowlist_permits
  tests/test_public_audits.py::test_release_payload_audit_permits_the_rc_artefacts
  -q` — 2 passed.
- `prohibited_fixture_passed`: `uv run pytest tests/test_public_audits.py
  -k "flagged" -q` — 17 passed, 9 deselected.
- `readme_claim_boundary_passed`: `uv run pytest
  tests/test_public_wording.py::test_readme_carries_the_exact_d11_sentence_at_first_reference
  -q` — 1 passed.
- `d7_exclusion_passed`: cites the closed D7 adjudication in
  `docs/checkpoints/tier0-result.md` (`decision: "licence conflict;
  redistribution closed"`, `contrary_notice: true`,
  `minedex_redistribution_allowed: false`) — exclusion evidence, not
  permission; `checkpoint_authorizes_flip` independently refuses if the
  note reads as permission.

Run 1's noted anomaly (`test_every_literal_redistribute_use_is_exempted_or_absent`
failing on stale `cli.py:<lineno>` keys) was fixed by an
order-preserving remap of the `EXEMPTIONS` keys after each `cli.py`
edit; the test passes on the final tree.

## Post-review hardening (codex diff review, 2026-08-29)

The pre-merge codex review of the branch diff returned three findings;
disposition:

1. **Confirmed and fixed** — the release-payload audit accepted any
   `tier0-*.parquet` filename and never opened parquet content, so a
   substituted or extra package (e.g. carrying MINEDEX columns) passed
   unexamined. Fixed in `public_audit.py`: the release allowlist now
   names the two authorised package files exactly (any other
   `tier0-*.parquet` is a `bulk_format` finding), and each authorised
   package's parquet schema is read and must equal the exact ordered
   `public_rc` field tuple — unreadable-as-parquet and schema mismatch
   are both `package_schema` findings with fixed, content-free notes.
   Covered by three new tests in `tests/test_public_audits.py`; the
   permitted-fixture test now writes real parquet with the authorised
   schemas. The live payload audit re-run passes (0 findings, 5 files).
2. **Refuted (spec-consistent)** — "`licence_state` not wired into
   `export_public`". The approved design fixes `export_public`'s
   runtime column gate as the controlling gate (D13 P1 acceptance) and
   enforces state/boolean consistency via the conformance invariant
   `entry.redistribute_public is (entry.licence_state is
   LicenceState.PUBLIC)` over every registered source, so the divergent
   row the finding posits cannot exist without a failing test.
3. **Refuted as designed, docstring hardened** — "`
   checkpoint_authorizes_flip` doesn't verify digests". The two-call
   design (booleans+wording vs digest verification) is the plan's
   explicit authorization story; the docstring now states that the
   boolean check is half of authorization and must be paired with
   `verify_checkpoint_digests(...)["failed"] == 0`.

## Fields left false and why

| Field | Value | Why |
|---|---|---|
| `private_ci_green` | false | OWNER-ONLY, untouched by this task |
| `actions_logs_reviewed` | false | OWNER-ONLY, untouched by this task |
| `public_flip_authorized` | false | OWNER-ONLY, untouched by this task |

Every machine-checkable field is now true on cited evidence. Because
the three owner-only fields are false,
`public_rc.checkpoint_authorizes_flip` still returns `False` — the flip
remains unauthorized until the owner reviews this evidence, runs CI,
reads the Actions logs, and personally sets `public_flip_authorized`.
No agent may run `gh repo edit` or `git push` to act on this
checkpoint.
