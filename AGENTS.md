# wa-mine-rehab-monitor

## What this project is

Statewide, descriptive spectral-chronology monitor for WA MINEDEX mine
sites, built on Digital Earth Australia Fractional Cover and SWIR-based
indices (see README.md). This is the volume-driven, one-cloud,
Terraform+orchestrator+dashboard portfolio piece named in
`~/.claude/CLAUDE.md`'s career-target section, but that framing does not
apply here: per the fixed owner decision in
`docs/plans/2026-08-15-wa-mine-rehab-monitor-design.md` (§1, "Owner
decisions, fixed"), cloud/Terraform is out of scope for this project —
not deferred to a later batch. Python-only, no cloud infra, by design.

Claim boundary: outputs are spectral detections, never compliance or
performance findings, never operational rehabilitation dates.

## Conventions

- Toolchain: uv, Python 3.12 (`.python-version`), src layout
  (`src/wa_mine_monitor/`), CLI entry point `wa-mine-monitor` (typer).
- Verification battery, CI order (`.github/workflows/test.yml`):
  `uv run ruff check src tests`, `uv run ruff format --check src tests`,
  `uv run mypy src scripts`, `uv run pytest -q -rs`.
- Dated snapshots, run manifests, fail-closed licence gates at the
  export boundary — read `docs/ROADMAP.md`, then `docs/decisions/` and
  `docs/amendments-and-limitations.md`, before touching acquisition,
  licence, or owners code. Superseded working docs live in
  `docs/archive/`.
- No project-local skills or agents until a workflow here proves
  repeatable across several sessions.

## Machines

MacBook Air (mps) is the daily driver; luminosity and the Windows GPU
box are not part of this project's normal loop.
