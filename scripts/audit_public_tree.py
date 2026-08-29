"""Audit the tracked tree for public-flip payload violations (D13 §8 P4).

Run before flipping this repo's visibility from private to public.
Exits non-zero (and prints every finding) when anything in the tracked-plus-
untracked-but-not-ignored tree looks like bulk data, a licence-evidence
bundle, a credential, a local filesystem path, raw geometry, or a MINEDEX
lineage marker outside code/docs. See `wa_mine_monitor.public_audit` for the
rule definitions.
"""

from __future__ import annotations

import sys
from pathlib import Path

from wa_mine_monitor import public_audit

SYNTHETIC_FIXTURE_ALLOWLIST = public_audit.SYNTHETIC_FIXTURE_ALLOWLIST
CREDENTIAL_FALSE_POSITIVE_ALLOWLIST = public_audit.CREDENTIAL_FALSE_POSITIVE_ALLOWLIST


def main(argv: list[str] | None = None) -> int:
    del argv  # No arguments: this audit always covers the whole repo tree.
    repo_root = Path(__file__).resolve().parent.parent
    files = public_audit.collect_repo_files(repo_root)
    findings = public_audit.audit_tree(
        repo_root,
        files,
        allowlist=SYNTHETIC_FIXTURE_ALLOWLIST,
        credential_false_positive_allowlist=CREDENTIAL_FALSE_POSITIVE_ALLOWLIST,
    )
    print(public_audit.render_report(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
