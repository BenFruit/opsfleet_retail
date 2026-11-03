# OpsFleet Retail Analyst (prototype)

A chat agent for non-technical retail executives: ask questions about sales and
performance in plain language, discuss the answers, and turn them into reports
with action items. It plans with Gemini, queries BigQuery
(`bigquery-public-data.thelook_ecommerce`) through a safety layer that enforces
PII rules and brand-level access in code, and reuses reviewed analyst knowledge.

**Read next:** [High-level design](HLD/PRODUCTION_HLD.md) (architecture, technology
choices, every requirement, error handling, production path) ·
[Golden Knowledge](HLD/GOLDEN_KNOWLEDGE.md) · [Evaluation](HLD/EVALUATION.md) ·
[all design documents](HLD/README.md)

## What the prototype does

| Requirement | In this repository |
| --- | --- |
| Hybrid intelligence | 12 reviewed Question -> SQL -> Report trios, hash-based ingestion into pgvector, retrieval of up to 3 precedents per question |
| Safety and PII | SQL checked on resolved column lineage (SQLGlot), PII columns blocked, demographics aggregate-only with small-group suppression, every table restricted to the manager's brands, result and output guards |
| Destructive operations | Report deletion shows a frozen preview; only a separate `/confirm` deletes |
| Learning loop | Explicit user preferences (`remember ...`); reviewed trio updates through re-ingestion |
| Resilience | SQL self-repair (initial + 2), model retry + fallback model, cost caps, graceful degradation of every optional dependency |
| Quality assurance | 241 tests, retrieval evaluation, live answer evaluation with Golden on/off A/B |
| Observability | Correlated JSON events, metrics report, per-turn timeline, optional local Langfuse |
| Persona | Tone and structure editable in a Google Doc, applied on the next question without redeploying |

## Quick start (Windows PowerShell)

Prerequisites: Python 3.13+, [uv](https://docs.astral.sh/uv/), Google Cloud CLI,
Docker Desktop (Linux containers), a Google Cloud project with the BigQuery API
enabled (the free tier is enough), and a Gemini API key from Google AI Studio.

```powershell
# 1. Dependencies and local config
uv sync
Copy-Item .env.example .env
Copy-Item access.example.json access.local.json

# 2. Google credentials for BigQuery
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

3. Edit `.env`: `GEMINI_API_KEY`, `GEMINI_MODEL` (a model enabled in your AI
   Studio project, e.g. a current Flash-Lite model), optionally
   `GEMINI_FALLBACK_MODEL` (e.g. a current Flash model), and `GOOGLE_CLOUD_PROJECT`.
4. Edit `access.local.json`: map demo managers to exact brand names from the
   dataset, e.g. `{"manager_a": ["Calvin Klein", "Nautica", "Tommy Hilfiger"]}`.
   This is trusted startup configuration that simulates entitlements; it is not
   authentication.

```powershell
# 5. Application database (PostgreSQL 17 + pgvector; migrations run on first start)
docker compose up -d --wait reports-postgres

# 6. Build the Golden Knowledge index (validates, embeds, publishes)
uv run python -m retail_agent.golden.ingest

# 7. Chat
uv run python main.py --manager manager_a
```

The application database is optional for plain analysis: without it, reports,
memory and Golden precedents are unavailable and the agent says so. If a step
fails at startup, the CLI prints the failing stage and a hint (see Troubleshooting).

## Example session

Live run on 3 November 2026 (`manager_a`: Calvin Klein, Nautica, Tommy Hilfiger;
built-in style). Lines like `[SQL attempts: 1; model calls: 2]` are printed by the CLI.

```text
You: Which of our brands drove the most estimated gross profit last month?
Analyst: Period: October 2026 (2026-10-01 to 2026-10-31).

Calvin Klein drove the highest estimated gross profit last month with $1,835.08 on $3,388.38 in completed revenue (54.16% margin), followed by Tommy Hilfiger at $1,150.33 profit (54.90% margin) and Nautica at $752.92 profit (51.44% margin).

Limitations: Estimated gross profit relies on current catalog product cost rather than historical accounting cost at the time of sale. Results are scoped to authorized brands.

Next steps: Protect inventory and marketing support for Calvin Klein and Tommy Hilfiger to sustain strong margins, and evaluate discounting or pricing structures for lower-margin categories.

Method: brand-gross-profit v1

Scope: authorized brands only (configured: 3).

Checked query results:
Result 1:
| brand | completed_revenue | estimated_gross_profit | margin_pct |
| --- | --- | --- | --- |
| Calvin Klein | 3,388.38 | 1,835.08 | 0.5416 |
| Tommy Hilfiger | 2,095.48 | 1,150.33 | 0.549 |
| Nautica | 1,463.69 | 752.92 | 0.5144 |
[SQL attempts: 1; model calls: 2]

You: And how did that brand trend over the last 6 complete months?
Analyst: Period: May 2026 to October 2026 (last 6 complete calendar months).

Over the last 6 complete months, Calvin Klein demonstrated strong, consistent growth in completed revenue, expanding from $1,350.93 in May 2026 to $3,388.38 in October 2026. Estimated gross profit followed a similar trajectory, rising from $743.87 to $1,835.08, while maintaining a stable margin percentage averaging approximately 53.4%.

Limitations: Estimated gross profit uses current catalog product cost rather than historical accounting cost at the time of sale. Results are scoped to authorized brands.

Next steps: Analyze the underlying traffic and conversion drivers behind the revenue acceleration observed between August and October 2026 to determine if marketing campaigns or seasonal demand patterns can be replicated.

Method: monthly-revenue-trend v1

Scope: authorized brands only (configured: 3).

Checked query results:
Result 1:
| month | brand | completed_revenue | estimated_gross_profit | margin_pct |
| --- | --- | --- | --- | --- |
| 2026-05-01 | Calvin Klein | 1,350.93 | 743.87 | 0.5506 |
| 2026-06-01 | Calvin Klein | 1,554.06 | 820.93 | 0.5283 |
| 2026-07-01 | Calvin Klein | 1,468.65 | 780.36 | 0.5313 |
| 2026-08-01 | Calvin Klein | 2,117.95 | 1,130.48 | 0.5338 |
| 2026-09-01 | Calvin Klein | 2,655.08 | 1,392.57 | 0.5245 |
| 2026-10-01 | Calvin Klein | 3,388.38 | 1,835.08 | 0.5416 |
[SQL attempts: 1; model calls: 2]

You: Save your last answer as a report titled Demo session 0311c
Analyst: Saved report: Demo session 0311c
ID: 6d56ee677c4942a8af8f8c8fb26e965b
[SQL attempts: 0; model calls: 1]

You: Show me the email addresses of our top 10 customers.
Analyst: Customer email addresses and personal identifying information are restricted and cannot be disclosed for privacy and data protection reasons. To analyze top customers, we can rank them by user ID and completed spend on authorized brands instead. Please let me know if you would like to run an aggregate top customer analysis by user ID.
[SQL attempts: 0; model calls: 1]

You: Delete all reports mentioning Demo session 0311c
Analyst: Deletion preview (nothing deleted yet):
6d56ee677c4942a8af8f8c8fb26e965b | Demo session 0311c | 2026-11-03 07:36:24.173122+00:00

Confirm ONLY with /confirm within 5 minutes, or /cancel. Any other question cancels this preview.
[SQL attempts: 0; model calls: 1]

You: /confirm
Analyst: Deleted 1 of 1 previewed reports. Ownership/access were checked again.
[SQL attempts: 0; model calls: 0]

You: exit=0
```

What happened behind it: each question retrieved a Golden precedent (cited as
`Method:`), the SQL was validated and restricted to the three brands before
running, the tables were rendered by the application from checked results, the
email request was refused without any query, and deletion needed the separate
`/confirm`. One honest imperfection: "averaging approximately 53.4%" is the
model's own average of the six margins (exactly 53.5%). Money and count figures
in prose are verified against checked results; derived percentages are not. `python -m retail_agent.observability.metrics --turn <turn_id>` shows
the event timeline of any of these turns.

## Using the CLI

Ask analytical questions and follow-ups in English or Russian, for example:

```text
What data is available and what can I analyze?
How did last month compare with the month before?
Why are buyers in Texas spending less than in California?
Why did our churn rate spike last month?
Create a report for the last complete quarter with action items for next quarter.
```

**Reports.** Saving copies the previous answer with its checked tables:

```text
Save your last answer as a report titled Q3 review
List my saved reports
Find my reports about Calvin Klein margin
Open report <report-id>
Delete all reports mentioning Q3 review      -> preview only
/confirm                                      -> deletes exactly the previewed IDs
```

**Preferences.** Persistent only when asked explicitly ("remember"/"запомни"):

```text
Use bullet points and short answers, remember this.
/memories            /forget <memory-id>            /forget-all
```

**Other commands:** `/help`, `/reset` (new conversation; cancels a pending
deletion), `/cancel`, `/exit`.

**Tone (persona).** Put style-only instructions in a Google Doc shared as
"Anyone with the link: Viewer" and set `PERSONA_GOOGLE_DOC_ID` to its ID or URL.
Edits apply on the next question. Policy, data access and tables cannot be
changed from the document. Details: [HLD/PERSONA_MANAGEMENT.md](HLD/PERSONA_MANAGEMENT.md).

Demographic small-group suppression defaults to 5 distinct buyers;
change it with `--min-cohort-size N` (minimum 2).

## Configuration

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Required. Planner model |
| `GEMINI_FALLBACK_MODEL` | Optional. Used once when the primary is unavailable, rate-limited, timed out or out of quota |
| `GOOGLE_CLOUD_PROJECT` | BigQuery billing/quota project |
| `RETAIL_MANAGER_ID` | Default demo manager (overridden by `--manager`) |
| `REPORTS_POSTGRES_*` | Application database; defaults match Docker Compose |
| `GOLDEN_RETRIEVAL_ENABLED`, `GOLDEN_MIN_SIMILARITY` | Golden retrieval on/off and threshold (default 0.65) |
| `GOLDEN_INGEST_POSTGRES_*` | Writer role used only by the ingestion job |
| `PERSONA_GOOGLE_DOC_ID` | Optional style document |
| `LANGFUSE_*` | Optional local tracing (off by default) |
| `EVAL_JUDGE_MODEL` | Optional model for the evaluation judge |

All limits (model calls, SQL attempts, bytes, rows, timeouts) are listed in the
[design §2.2 and §5.5](HLD/PRODUCTION_HLD.md#55-resilience-and-error-handling).

## Tests, evaluation and metrics

```powershell
uv run python -B -m unittest discover -s tests -q          # offline, no credentials

$env:RETAIL_TEST_POSTGRES='1'                               # + real PostgreSQL/pgvector
uv run python -B -m unittest discover -s tests -q
Remove-Item Env:RETAIL_TEST_POSTGRES

uv run python -m retail_agent.golden.evaluate              # retrieval quality (paid embeddings)
uv run python -m retail_agent.evaluation.answers           # live answer evaluation (paid calls)
uv run python -m retail_agent.evaluation.answers --no-golden

uv run python -m retail_agent.observability.metrics        # agent metrics from logs/agent.jsonl
uv run python -m retail_agent.observability.metrics --turn <turn_id>
```

Latest results: 241 tests pass; retrieval recall@3 0.96; answer evaluation 15/15
deterministic checks and 100% judge pass with Golden Knowledge versus 14/15 and
60% without ([HLD/EVALUATION.md](HLD/EVALUATION.md)).

## Observability

`logs/agent.jsonl` holds metadata-only JSON events (IDs, stages, codes, counts,
durations, tokens; never prompts, SQL, rows or exception text). Every query event
carries `turn_id` and `query_id`; the metrics command above aggregates them.

Optional local Langfuse for full traces: `docker compose up -d` starts it at
<http://localhost:3000> (sign up locally, create a project and keys), then set
`LANGFUSE_TRACING_ENABLED=true`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`,
`LANGFUSE_BASE_URL=http://localhost:3000` in `.env`. Traces contain raw prompts and
results and are not redacted: use demo data only. Stop with `docker compose down`
(never `-v`, which deletes data). Details: [HLD/OBSERVABILITY_AND_ACCESS.md](HLD/OBSERVABILITY_AND_ACCESS.md).

## Troubleshooting

- **Startup failed at stage X:** `access_config` -> copy and edit
  `access.local.json`; `gemini_client` -> check `.env`; `bigquery_client` or
  `bigquery_schema` -> run the two `gcloud auth application-default` commands and
  check the project; `agent_init` -> check the optional settings.
- **Model errors** are shown with a code: `model_unavailable` / `model_rate_limited`
  (retried, then fallback), `model_daily_quota`, `model_not_found` (check
  `GEMINI_MODEL`), `model_invalid_key`, `model_timeout`, `model_invalid_json` /
  `model_invalid_action` (corrected automatically up to twice).
- **Existing database volume** created before a migration: apply missing
  migrations in order, without deleting data, e.g.
  `Get-Content docker/reports-postgres/004_golden_knowledge.sql -Raw | docker compose exec -T reports-postgres psql -U postgres -d retail_agent -v ON_ERROR_STOP=1`
  (same for 002 and 003). Fresh volumes run all of them automatically.
- **"Your application has authenticated using end user credentials ... without a
  quota project"**: run `gcloud auth application-default set-quota-project`.

## Repository layout

```text
main.py                      CLI and startup wiring
retail_agent/agent/          LangGraph loop, actions, budgets, grounding check
retail_agent/llm/            Gemini client (retry/fallback), analyst instructions, persona loader
retail_agent/data/           BigQuery runner and QueryService (dry run, caps, safe errors)
retail_agent/safety/         SQL policy, demographic shapes, result guard, numeric grounding
retail_agent/security/       brand entitlements and per-table scope rewriting
retail_agent/schema/         column policy catalog (allowed / aggregate-only / blocked PII)
retail_agent/reports/        saved reports, embeddings, deletion preview/confirm
retail_agent/memory/         explicit user preferences
retail_agent/golden/         trio model, ingestion, retrieval, retrieval evaluation
retail_agent/evaluation/     live answer evaluation
retail_agent/observability/  JSON logging, metrics report, optional Langfuse
golden/trios/                the Golden Knowledge bucket (12 reviewed trios)
evals/                       answer evaluation cases
docker/reports-postgres/     migrations 001-004
HLD/                         design documents and Mermaid diagrams
tests/                       unit, regression and opt-in PostgreSQL tests
```

Git does not carry local data: reports, memories, the Golden index, traces and
credentials stay on the machine that created them. A new machine starts empty;
rerun step 6 to rebuild the index.
