# Agent instructions — Opsfleet retail assessment

## Start here

Communicate with the developer in Russian. Deliverable documentation and CLI
examples may be English. Keep solutions minimal, explain meaningful tradeoffs,
and distinguish implemented behavior, tested examples, limitations, approved
compromises and production proposals. Do not rely on a previous chat session.

Reading order:
1. `HLD/HANDOFF.md` — current decisions, issues, verification and next priorities.
2. `README.md` — setup, configuration, CLI and portability.
3. `HLD/README.md`, `HLD/task.txt`, `HLD/STAKEHOLDER_POLICY.md`.
4. `HLD/PRODUCTION_HLD.md` and relevant focused HLD documents.
5. Actual source/tests before asserting or changing behavior.

## Repository map

- `main.py`: startup dependencies and CLI commands.
- `retail_agent/agent/`: Pydantic actions, limits and LangGraph orchestration.
- `retail_agent/llm/`: direct Gemini SDK, mandatory analyst instructions, style loader.
- `retail_agent/data/`, `schema/`, `safety/`, `security/`: query execution,
  curated source policy, SQL/result guards and trusted brand rewriting.
- `retail_agent/reports/`: parameterized repository, embeddings and report lifecycle.
- `retail_agent/memory/`: bounded explicit owner-scoped persistent entries.
- `retail_agent/observability/`: allowlisted JSON logs and optional Langfuse.
- `docker/reports-postgres/`: ordered idempotent migrations 001, 002, 003.
- `tests/`: mocked unit/regression tests and opt-in local PostgreSQL integration.
- `docker-compose.yaml`: reports DB plus independent optional Langfuse stack.

## Commands (PowerShell, repository root)

```powershell
uv sync
docker compose up -d --wait reports-postgres
uv run python -B main.py --manager manager_a
uv run python -B -m unittest discover -s tests -q
$env:RETAIL_TEST_POSTGRES='1'
uv run python -B -m unittest discover -s tests -q
docker compose config --quiet
git diff --check
git status --short
```

The default tests need no cloud credentials or paid API calls. PostgreSQL tests
require the local migrated DB and clean up their own fixtures. Do not make paid
or persistent live calls unless appropriate to the task; use temporary test owners
and never change the developer's existing reports/memories. Remove the opt-in
environment variable when returning to offline-only tests if needed.

## Non-negotiable boundaries and approved scope

- Model output is untrusted. Prompts, persona, memory and retrieved knowledge do
  not grant access or authorize destructive actions.
- Customer direct PII is blocked before LLM query observations; demographic data
  is aggregate-only. Stable customer IDs can be used in non-demographic finance.
- Every physical source is scoped to trusted startup brands. Keep current startup
  mapping and full prompt brand list unless the user requests a redesign.
- One unified `query` action supports ordinary SQL and a restricted demographic
  subset. Do not remove structural/contributor checks merely to make SQL succeed.
- Demographic k defaults to 5, configurable minimum 2; finance has no k threshold.
- SQL errors have initial attempt plus two repairs within shared budgets. Never
  blindly replay ambiguous SQL executions or report/memory writes.
- Report save copies the previous completed answer and checked tables. Delete
  freezes at most 100 IDs, needs exact separate `/confirm` within five minutes,
  rechecks owner/brands and deletes transactionally. New questions/cancel/reset/restart
  invalidate pending confirmation. Memory cannot supply confirmation.
- Explicit `remember` / `запомни` is required. Own operator name is allowed by
  developer decision, not verified identity. Customer PII rules are unchanged.
- Memory loads all owner entries, max 20 × 200 characters; normalized exact dedup
  only. Updating/superseding conflicts is absent. User declined fixes after the
  latest manual failures; document them, do not silently relax persistence intent.
- PostgreSQL 17/pgvector, `gemini-embedding-2`, 768 dimensions are current choices.
  Qdrant and selective semantic memory retrieval are future options only.
- Persona Google Doc is style-only, fetched once per question; mandatory policy
  remains in code. Local Langfuse is optional; LangSmith export is disabled.
- Comprehensive trace redaction, cross-question inference protection and real
  authentication/DB-enforced isolation are NOT implemented.

## Safe continuation and publication

Inspect `git status` and unfamiliar changes first. Never reset/discard user work,
delete Docker volumes, rewrite history, commit or push without explicit permission.
Do not put `.env`, local access configuration, ADC, traces, notebooks with sensitive
outputs or database backups into public Git. Fixed Compose demo credentials are
intentional localhost-only values, not production secrets. Prefer `python -B` for
checks. Historical tracked bytecode has been removed from the index (local files
preserved); do not force-add ignored generated files.

Git transfers code/docs, not local DB data, remote persona contents or credentials.
Fresh-machine setup and assessment-quality evaluation remain explicit next steps.
Update `HLD/HANDOFF.md` and relevant focused docs after significant decisions;
keep Mermaid diagrams and production-vs-MVP labels synchronized. Do not claim
the regression suite (205 offline tests) or isolated live successes prove semantic correctness.
