profile: research-code
status: active
standards: ~/Documents/standards/STANDARDS.md

@AGENTS.md

## Claude Code

- Before writing or editing Python, run kit:code-standards;
  HCL/Terraform standards apply once infra work starts.
- Use the installed kit chain: kit:build-flow -> kit:verify ->
  kit:finish-branch; kit:debugging before fixes.
- No project-local skills or agents until a workflow here proves
  repeatable across several sessions; use global skills meanwhile.
