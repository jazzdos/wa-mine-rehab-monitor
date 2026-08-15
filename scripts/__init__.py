"""Placeholder so `mypy src scripts` has a source file to type-check.

`scripts/` currently holds no runnable script -- Task 5 wires the CI
workflow and local battery to check `src scripts` together before any real
script exists. Passed a directory with zero `.py`/`.pyi` files as an
explicit target, mypy exits 2 with "There are no .py[i] files in directory
'scripts'" -- a hard usage error, not a type-check failure, which would
break every CI run and every local battery invocation until a real script
landed. This file exists solely to make that directory non-empty for mypy;
delete it once a real script is added under `scripts/` (Batch B onward).
"""

from __future__ import annotations
