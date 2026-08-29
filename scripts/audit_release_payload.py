"""Audit a release version directory for public-flip payload violations
(D13 §8 P4).

Run against one Tier-0 release version directory (e.g. `dist/v0.1.0/`)
before publishing it. Exits non-zero (and prints every finding) when
anything in the directory falls outside the RC-artefact allowlist or
carries a credential, local filesystem path, raw geometry, or MINEDEX
lineage marker. See `wa_mine_monitor.public_audit` for the rule
definitions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wa_mine_monitor import public_audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version_dir", type=Path, help="Release version directory to audit")
    args = parser.parse_args(argv)

    try:
        findings = public_audit.audit_release_dir(args.version_dir)
    except public_audit.EmptyReleaseDirError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    scanned_files = sum(1 for p in args.version_dir.rglob("*") if p.is_file())
    print(public_audit.render_report(findings, scanned_files=scanned_files))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
