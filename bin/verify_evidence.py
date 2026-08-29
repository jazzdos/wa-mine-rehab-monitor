#!/usr/bin/env python3
"""Shim for the evidence verifier; logic lives in wa_mine_monitor.evidence so
the mypy/ruff battery (src scripts) covers it."""
import sys

from wa_mine_monitor.evidence import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
