@AGENTS.md

## Claude Code notes

- This is the developer's local Windows checkout; run commands from the
  repository root in PowerShell (see "Commands" in AGENTS.md). Docker Desktop
  hosts `reports-postgres` on 127.0.0.1:5433.
- Current work: the refactoring plan in `HLD/REFACTOR_PLAN.md`. Do one step at
  a time, run the full suite with `RETAIL_TEST_POSTGRES=1`, then `git diff --check`,
  and commit only when the developer asks (authorship rule in AGENTS.md).
- `logs/` is ignored scratch space; old `logs/*.cmd` files were helper scripts
  from a remote session and are not needed here.
