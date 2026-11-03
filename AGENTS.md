# Agent instructions — Opsfleet retail assessment

## Start here

Communicate with the developer in Russian. Deliverable documentation and CLI
examples may be English. Keep solutions minimal, explain meaningful tradeoffs,
and distinguish implemented behavior, tested examples, limitations, approved
compromises and production proposals. Do not rely on a previous chat session.

Reading order:
1. `HLD/HANDOFF.md` — current decisions, issues, verification and next priorities.
   Current refactoring work: `HLD/REFACTOR_PLAN.md`.
2. `README.md` — setup, configuration, CLI and portability.
3. `HLD/README.md`, `HLD/task.txt`, `HLD/STAKEHOLDER_POLICY.md`.
4. `HLD/PRODUCTION_HLD.md` and relevant focused HLD documents.
5. Actual source/tests before asserting or changing behavior.

## Repository map

- `main.py` -> `retail_agent/cli.py` (arguments, chat input loop) ->
  `retail_agent/application.py` (`ChatApplication`: named startup stages, safe
  startup errors, client/tracing shutdown). `retail_agent/settings.py`: `.env` values.
- `retail_agent/agent/`: Pydantic actions and limits; `orchestrator.py` (LangGraph
  loop, nodes, budgets), `answer.py` (final-answer checks and repair feedback),
  `commands.py` (exact slash commands, no model), `conversation.py` (history).
- `retail_agent/llm/`: direct Gemini SDK, `errors.py` (table-driven failure codes and
  empty-response reasons), shared `Embedder`, analyst, `persona.py` (`GoogleDocReader`
  fetches the style doc, `PersonaStyle` keeps current/fallback style);
  the mandatory policy prompt is `llm/prompts/analyst_policy.md`.
- `retail_agent/bootstrap.py`: agent wiring shared by the chat application and answer
  evaluation. `retail_agent/presentation.py`: user-facing Markdown rendering.
- `retail_agent/data/`, `schema/`, `safety/`, `security/`: query execution
  (`data/errors.py` maps BigQuery failures to stable safe codes),
  curated source policy, SQL/result guards, `Entitlements` (loaded once at
  startup, passed explicitly) and trusted brand rewriting.
- `retail_agent/storage/postgres.py`: one PostgreSQL connection factory shared by
  reports, memory and Golden; driver errors become a fixed `StorageError`.
- `retail_agent/reports/`: parameterized repository and report lifecycle.
- `retail_agent/memory/`: bounded explicit owner-scoped persistent entries.
- `retail_agent/golden/`, `golden/`: Golden Knowledge trios (bucket), ingestion,
  retrieval and retrieval evaluation; the agent role reads the index only.
- `retail_agent/evaluation/`, `evals/`: live answer evaluation (paid calls; results in logs/eval).
- `retail_agent/safety/grounding.py`: prose figures must match checked results.
- `retail_agent/observability/metrics.py`: agent metrics and per-turn timeline from logs.
- `HLD/PRODUCTION_HLD.md` is the main design; `tests/test_docs.py` keeps embedded
  Mermaid diagrams equal to `HLD/*.mmd` and relative links valid.
- `retail_agent/observability/`: allowlisted JSON logs and optional Langfuse.
- `docker/reports-postgres/`: ordered idempotent migrations 001-004.
- `tests/` (pytest runner, tests written as `unittest.TestCase`): `unit/<package>/`
  mirrors `retail_agent/`; `integration/` runs the agent graph with fake backends,
  `integration/postgres/` needs `RETAIL_TEST_POSTGRES=1`; `support/` has shared helpers.
- `docker-compose.yaml`: reports DB plus independent optional Langfuse stack.

## Commands (PowerShell, repository root)

```powershell
uv sync
docker compose up -d --wait reports-postgres
uv run python -B main.py --manager manager_a
uv run python -B -m pytest
$env:RETAIL_TEST_POSTGRES='1'
uv run python -B -m pytest
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

Commits (developer's rule): every commit is authored by the developer only. No
`Co-Authored-By`, session links or other AI/tool attribution trailers in commit
messages or PR descriptions. Commit on the current branch; never create new
branches. Commit and push only when the developer asks.

Git transfers code/docs, not local DB data, remote persona contents or credentials.
Update `HLD/HANDOFF.md` and relevant focused docs after significant decisions;
keep Mermaid diagrams and production-vs-MVP labels synchronized. Do not claim
the regression suite (262 tests) or the 15-case answer evaluation or isolated live successes prove semantic correctness.
