# Opsfleet Data Analysis Chat Assistant — full project handoff

> Snapshot: 30 October 2026. This document consolidates the user's conversation and decisions. Distinguish **implemented/observed** from **planned**. The actual local repository should be inspected before claiming that any code works.

> Later implementation updates: see `README.md`, `HLD/QUERY_SAFETY.md`, and
> `HLD/STAKEHOLDER_POLICY.md`. The source now includes a LangGraph CLI loop,
> bounded QueryService, brand-scoped execution, and a typed demographic tool.
> The previous free-form demographic SQL validator is documented in `HLD/archive/`.
> The historical module-status/next-step sections below are outdated.

## 0. Instructions for the next AI coding partner

- Work with the developer in **Russian** by default; Slack messages and deliverable documentation may be in English.
- **Teaching mode**: explain concepts, offer manageable exercises, review the developer's solution before moving on. Do not dump a huge end-to-end implementation unless explicitly asked for code. Keep changes minimal and explain every dependency or configuration change. The developer dislikes unexplained `pyproject.toml` changes and gratuitous architectural layers.
- The developer can code Python and works with production AI systems, but is new to LangGraph, LangChain, Langfuse, SQLGlot, and parts of Python packaging. This is a learning exercise as well as a job assessment.
- **Never execute model-generated SQL without appropriate deterministic validation and authorization.** Do not send PII or sensitive unfiltered query results to Gemini or to external tracing.
- The current user focus is building a working single-question-to-answer path, then expanding it to iterative multi-query reasoning. Do not lose sight of the assignment's mandatory safety, deletion confirmation, resilience, and observability.

## 1. Opportunity and recruiting context

- Company: **Opsfleet**. Role: AI Engineer, contractor arrangement.
- HR recruiter: **Emilija**. She advised asking clarifying questions and being proactive in communication. The developer had earlier missed the initial assignment window due to severe overtime; Opsfleet reopened the process and gave a second chance. Proactive communication is especially important.
- Technical stakeholders mentioned: **Vlad**, AI/team lead, and **Harry** (PM/stakeholder). Avoid assuming exact reporting lines beyond what the user says.
- User discussed **USD 6,000 gross/month B2B** with recruiter; no reported objection at that stage.
- Deliverable: public GitHub repository, runnable prototype, HLD/architecture and documentation.
- Assessment reportedly estimates 6–12 hours; the developer is taking longer because he is learning each part carefully. An earlier rough estimate for all remaining work was 25–40 hours at this learning pace. Do not treat this as a deadline or guarantee.

## 2. Assignment — goals and constraints

Design and prototype a **Data Analysis Chat Assistant** for nontechnical retail executives, backed by a read-only SQL source and a theoretical **Golden Knowledge Bucket** of curated `Question → SQL Query → Analyst Report` trios. The system must be extensible for charts, email reports, web trend analysis, etc.

Capabilities:
- Customer behavior, product performance, time-based metrics, schema questions, and multi-step analytical questions.
- Generate dynamic BigQuery SQL from natural-language questions; don't rely on a rigid fixed query set.
- Source dataset: `bigquery-public-data.thelook_ecommerce`, specifically `orders`, `order_items`, `products`, `users`.
- Favor newer Gemini via Google AI Studio for prototype; production design can describe Vertex AI.

Required considerations:
1. **Hybrid intelligence**: curated Golden Knowledge retrieval and updating, plus live BigQuery data.
2. **Safety and PII**: read-only analytic access; protect raw logs from leaks; each user's data/product scope must be enforced from trusted identity/entitlements, not by prompt alone. PII must never reach LLM.
3. **Destructive operations**: Saved Reports; delete own reports only, with an explicit preview and confirmation before deletion, including bulk deletes like “all reports mentioning Client X” or “from this conversation”.
4. **Continuous improvement**: user preferences (tables/bullets, depth) and system learning/feedback from past interactions.
5. **Resilience**: syntax errors, empty results, bounded retries, API downtime, no UI crash, guard against cost inflation.
6. **QA**: predeployment eval of question intent/correctness/UX.
7. **Observability**: deep per-step traces, metrics, debug analysis while preventing PII leakage.
8. **Persona management**: CEO tone can change weekly; nondevelopers must be able to edit instructions without redeployment.

Working prototype specifically **must implement** Safety/PII, destructive oversight, resilience, and observability; other components may be described in the HLD if time-constrained. CLI chat is acceptable. Docker optional; setup must work for another developer.

## 3. Stakeholder policy clarification — critical

The developer asked stakeholders about PII policy, and reported screenshots of a reply from Harry. From the discussion, the clarified rules were:
- **Direct PII must not reach the LLM at all**, not merely be stripped from final outputs.
- It's acceptable to refer to individual customers using `user_id` or a stable pseudonymous ID.
- Demographic and geographic analytical attributes (e.g., `age`, `gender`, `city`, `state`, `country`, `traffic_source`) should be used in **aggregate analysis**, not exposed as an individual customer's profile.
- `postal_code`, names, email, address and precise coordinates were treated as blocked PII for this assignment.

The project therefore changed from a **sensitivity** model (`SAFE`, `QUASI_IDENTIFIER`, `DIRECT_PII`) to an **access policy** model:

```python
class DataAccessLevel(str, Enum):
    ALLOWED = "allowed"
    AGGREGATE_ONLY = "aggregate_only"
    BLOCKED_PII = "blocked_pii"

class ColumnPolicy(BaseModel):
    access_level: DataAccessLevel
    description: str = ""
```

**Important design qualification:** column-level labels are just metadata, not enforcement. Even `ALLOWED` attributes may become sensitive in combinations; aggregate-only requires actual aggregate and suppression policies (e.g., small groups), not merely an aggregate function. Deterministic access authorization, robust SQL parsing, output lineage/guards and approved query patterns are needed. Avoid claiming simple SQLGlot rules make this production-secure.

## 4. Dataset schemas obtained from BigQuery

**orders**: `order_id INTEGER`, `user_id INTEGER`, `status STRING`, `gender STRING`, `created_at TIMESTAMP`, `returned_at TIMESTAMP`, `shipped_at TIMESTAMP`, `delivered_at TIMESTAMP`, `num_of_item INTEGER`.

**order_items**: `id INTEGER`, `order_id INTEGER`, `user_id INTEGER`, `product_id INTEGER`, `inventory_item_id INTEGER`, `status STRING`, `created_at TIMESTAMP`, `shipped_at TIMESTAMP`, `delivered_at TIMESTAMP`, `returned_at TIMESTAMP`, `sale_price FLOAT`.

**products**: `id INTEGER`, `cost FLOAT`, `category STRING`, `name STRING`, `brand STRING`, `retail_price FLOAT`, `department STRING`, `sku STRING`, `distribution_center_id INTEGER`.

**users**: `id INTEGER`, `first_name STRING`, `last_name STRING`, `email STRING`, `age INTEGER`, `gender STRING`, `state STRING`, `street_address STRING`, `postal_code STRING`, `city STRING`, `country STRING`, `latitude FLOAT`, `longitude FLOAT`, `traffic_source STRING`, `created_at TIMESTAMP`, `user_geom GEOGRAPHY`.

Joins: `order_items.order_id = orders.order_id`; `order_items.product_id = products.id`; `orders.user_id = users.id` (also `order_items.user_id` available).

Known `order_items.status` values investigated manually: `Complete`, `Shipped`, `Processing`, `Cancelled`, `Returned` (check real dataset if correctness depends on values). The agent should **independently discover unknown categorical values** when necessary (e.g., safe `SELECT DISTINCT status`), not blindly guess literals from the schema.

Business semantic caveat: `SUM(order_items.sale_price)` filtered to `status = 'Complete'` is a plausible definition of **completed-order revenue**, not universally authoritative `revenue`. Golden Knowledge/business rules should define metrics. Be careful about order status vs item status.

Example correct SQL for product-specific revenue ranking:
```sql
SELECT oi.product_id, p.name AS product_name,
       SUM(oi.sale_price) AS completed_order_revenue
FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi
JOIN `bigquery-public-data.thelook_ecommerce.products` AS p
  ON oi.product_id = p.id
WHERE oi.status = 'Complete'
GROUP BY oi.product_id, p.name
ORDER BY completed_order_revenue DESC
LIMIT 5;
```
Note: grouping solely by `p.name` is semantically flawed because different products can share a name.

## 5. Local setup

- Local Windows PowerShell project path: `D:\opsfleet_retail`.
- Python: 3.13; dependency manager: `uv`; environment `.venv`.
- GCP project used for quota/billing: `opsfleet-assessment-yerassyl`; authenticated through Google Cloud SDK / ADC; BigQuery public dataset accessible in previous test.
- The user's Google account was used with gcloud and ADC. Don't ask to paste tokens or `.env` values.
- Current directory decision after confusion: **root-level** `retail_agent/` package instead of `src/retail_agent/`, to avoid Hatchling packaging configuration. `main.py` sits in repository root. Existing code had some earlier inconsistent `from schema...` imports; normalize to `from retail_agent.schema...`.
- Build backend: hatchling in `pyproject.toml`. There were Hatchling editable-build discovery errors after directory moves; earlier assistant suspected missing `__init__.py` and suggested adding package markers. **Do not assume `uv sync` is resolved**: inspect the actual repository. If error persists, check actual paths and `pyproject.toml` instead of guessing.
- Dependencies already used/installed: `google-cloud-bigquery`, `google-genai`, `python-dotenv`, `pydantic`. `sqlglot` was the next recommended installation, not confirmed installed.
- `.env` contains `GEMINI_API_KEY` and possibly `GEMINI_MODEL`. `.env` **must be ignored** by Git; never commit or emit secrets.
- The developer tried `gemini-3.6-flash` but its quota of 20 RPD was insufficient, so the last agreed default was `gemini-3.5-flash-lite`; **validate actual model identifiers and project quotas against AI Studio before relying on them** (earlier assistants made model/version assertions that might be inaccurate or change over time).

## 6. Modules created so far (as reported; inspect source files)

Proposed current tree:
```
opsfleet_retail/
├── retail_agent/
│   ├── __init__.py
│   ├── data/
│   │   ├── __init__.py
│   │   └── bigquery_runner.py
│   ├── schema/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── catalog.py
│   │   └── service.py
│   └── llm/
│       ├── __init__.py
│       ├── client.py
│       └── sql_generator.py
├── main.py
├── pyproject.toml
├── uv.lock
└── .env  # not committed
```

- `BigQueryRunner`: Google BigQuery client; `get_table_schema(table_name)` returns a list of `{name,type,mode,description}` for fields; `execute_query(sql)` executes and returns pandas DataFrame. Currently a **low-level adapter**, not yet a safe executor (no dry-run/cost limit/authorization).
- `schema/models.py`: `DataAccessLevel` and `ColumnPolicy` Pydantic model.
- `schema/catalog.py`: `TABLE_POLICIES` for the four tables. Mapping currently designed as follows:
  - `users` **BLOCKED_PII**: `first_name`, `last_name`, `email`, `street_address`, `postal_code`, `latitude`, `longitude`, `user_geom`.
  - `users` **AGGREGATE_ONLY**: `age`, `gender`, `state`, `city`, `country`, `traffic_source`.
  - `users` **ALLOWED**: `id`, `created_at`.
  - `orders` **AGGREGATE_ONLY**: `gender`; **ALLOWED**: `order_id`, `user_id`, `status`, timestamps, `num_of_item`.
  - `order_items` all **ALLOWED**: `id`, `order_id`, `user_id`, `product_id`, `inventory_item_id`, `status`, timestamps, `sale_price`.
  - `products` all **ALLOWED**: `id`, `cost`, `category`, `name`, `brand`, `retail_price`, `department`, `sku`, `distribution_center_id`.
  - `TABLE_POLICIES = {'users': ..., 'orders': ..., 'order_items': ..., 'products': ...}`.
- `SchemaService`: takes `BigQueryRunner` by dependency injection; verifies requested table is allowlisted, fetches physical schema, rejects physical columns without policies (fail-closed), adds `access_level` to each column. **Does not currently detect stale catalog entries for columns deleted from BigQuery**. Does not enforce access itself.
- `GeminiClient`: `load_dotenv`, reads `GEMINI_API_KEY`, `genai.Client(api_key=...)`; `generate(prompt) -> str`, `generate_structured(prompt,response_schema) -> BaseModel` using Pydantic and Gemini JSON response schema; `close()`. Default model intended 3.5 Flash-Lite, check locally.
- `SQLGenerator`: accepts an injected `GeminiClient`; `generate(question, schema: dict[str, list[dict]]) -> SQLGeneration(sql, explanation)`; serializes enriched schema as JSON, prompts for read-only BigQuery SELECT with allowed tables, no blocked PII or `SELECT *`, aggregate-only restrictions, limit, assumptions. It **only generates text/structured SQL; it does not execute**.
- `main.py`: wiring to fetch enriched schema for all four tables and test SQL generation. The developer generated and printed SQL successfully for "What are the top 5 products by completed-order revenue?". An earlier Flash-Lite result grouped only by name; a later Gemini 3.6 test grouped by product ID and name, indicating good improvement on one example but not a meaningful benchmark.

Known learning: dependency injection — `SchemaService(bigquery_runner)` uses the runner to obtain fresh physical schema; `SQLGenerator(llm)` uses Gemini client; passing prepared schema into SQLGenerator rather than the whole service simplifies testing and decoupling. `SQLGeneration` separates machine-validated SQL from explanatory text; explanation is not hidden chain-of-thought and is not a reliable correctness guarantee.

## 7. Core product architecture / behavioral model

**Not a single-shot Text-to-SQL pipeline**. The developer explicitly wants the model to reason about data gaps and choose safe exploratory queries itself. Example: before filtering for `status='Complete'`, inspect valid statuses if not supplied by Golden Knowledge/known metadata.

Flow:
1. CLI request is associated with **trusted actor identity and product entitlements**.
2. Orchestrator has schema/policies, Golden Knowledge, preferences, budget and analysis history.
3. Gemini decides whether to make a tool call (e.g., query) or provide a final answer, and returns structured intent/SQL.
4. Deterministic SQL AST validator + product scope enforcement + BigQuery dry run/cost check.
5. Execute query with read-only credentials, bounded rows, timeout and cost.
6. Sanitize/validate results *before any response reaches LLM or external telemetry*.
7. Save bounded safe observation in agent state; loop to Gemini (possibly 5–6 queries).
8. If enough evidence, synthesize report; if step/token/cost budget exhausted or failure, provide honest partial answer/controlled error.
9. Final output guard and safe trace.

Possible LangGraph integration: use after tools are stable, for explicit stateful routing, conditional transitions, and bounded retries. **LangGraph is optional**, and can be used without LangChain. Prefer starting with direct Gemini SDK + Python functions until tool contracts are understood. Langfuse useful for observability once PII-safe instrumentation exists. LangChain not currently necessary.

**Safety pitfalls to tackle explicitly**:
- AST SELECT-only policy; reject multi-statement, DML/DDL, dynamic/external sources, functions that can exfiltrate, wildcard columns, unknown tables, poorly scoped CTEs/subqueries, unsafe aliasing and unauthorized joins.
- COLUMN POLICY IS NOT ROW-LEVEL AUTH. Each user's permitted products must be enforced via trusted claims and robust DB-level mechanisms where possible. Existing public dataset has no authenticated user-to-product mapping, so prototype needs a documented mock or explicit product-entitlement mapping; don't pretend it is production auth.
- `AGGREGATE_ONLY` cannot be secured simply by `GROUP BY`; consider small-group suppression, cross-filter leak, differencing attacks, and row-level combinations. In MVP, conservative restricted supported query shapes or approved views are safer than claiming full arbitrary SQL safety.
- Keep blocked columns away from LLM on both prompt and query-result paths; listing schema **names** is not itself leaking blocked **values**, but avoid sending unnecessary sensitive schema metadata.
- Avoid logging prompts, results, or identifiers unsanitized in third-party services.
- Pydantic structured outputs validate format, not SQL correctness or business truth.
- Iterative agent needs max SQL steps, retries per step, max scanned bytes, max result rows, latency/token budget and safe error handling.

## 8. Proposed next modules (NOT IMPLEMENTED YET)

### Phase A — safe working analytic agent
1. `safety/sql_validator.py` — SQLGlot AST, read-only allowlist, forbidden PII and unsafe expression rejection. This is the **next task**; start by parsing SQL with `parse_one(sql, read='bigquery')`, inspecting nodes (`exp.Table`, `exp.Column`) and comparing root to DELETE. Then implement progressively, with tests. `uv add sqlglot` (not confirmed run).
2. `security/access.py` — trusted entitlement mapping/product scope, never purely model-chosen.
3. `safety/result_sanitizer.py` — ensure no blocked/sensitive individual fields reach Gemini; reject unsafe results by default.
4. `data/query_service.py` — coordinate validation, authorization, dry run/cost caps, execute, sanitize, safe errors.
5. `agent/state.py` — typed state: question, safe observations, SQL histories, retries, budgets, etc.
6. `agent/orchestrator.py` — iterative think/query/observe/final loop; LangGraph candidate.
7. `main.py` — interactive CLI chat.

### Phase B — other assessment requirements
- `knowledge/retriever.py`: curated Golden Knowledge trios + update flow; no real bucket supplied, simulate examples or document production integration after checking stakeholder expectation.
- `reports/service.py`: saved reports with owner checks, preview and explicit confirmation for destructive actions; choose SQLite prototype, PostgreSQL production.
- `memory/service.py`: user preferences, stored typed facts with update/delete; consider reusable Python module; avoid gratuitous custom microservice.
- `config/persona.py`: versioned configurable persona and admin change without deploy.
- `observability/tracing.py`: Langfuse/OpenTelemetry, safe traces and metrics.

### Phase C — assessment deliverables
- SQL parser/authorization/PII unit tests; multi-step, empty-result, syntax repair, quota/API-down, denial, prompt injection and deletion-confirmation integration tests.
- Golden-set evaluation for intent, SQL validity, result correctness, report quality; failure budget/UX evaluation.
- HLD with main system diagram plus workflow/security/deletion diagrams, assumptions, tech choices, limitations and production path.
- README with environment setup, credential steps, screenshots/demo CLI, run/test commands, threat model, and future extensions.
- Prior rough effort estimates: A 10–16h, B 7–12h, C 8–12h at learning pace; more time for production-grade safety. Focus first on required prototype areas.

## 9. Questions/clarifications to send stakeholder if useful

Don't overwhelm Vlad with elementary questions. Bring a considered proposal and ask about requirement/assumption/tradeoff.
- Golden Knowledge Bucket is explicitly theoretical and no sample/link was provided. May we create representative trios or describe retrieval/update only?
- Product entitlements are absent from the public dataset: acceptable to mock a `user → accessible product IDs` mapping for prototype?
- Are aggregate-only demographic attributes allowed as grouped aggregates subject to minimum cohort size? Any threshold expectation?
- Gemini via Google AI Studio in prototype while describing Vertex AI for production acceptable?
- Does the team prefer LangGraph versus Google ADK/custom Python, or is it freely chosen with rationale?
- For preferences, is a small auditable custom memory module acceptable vs Mem0/other framework?

## 10. Files and diagrams

- `architecture-overview.mmd`: deliberately concise system-level diagram; should fit one screen.
- `agent-loop.mmd`: repeatable SQL reasoning flow, bounded repairs.
- `security-pii.mmd`: pre-LLM enforcement, data-access classes.
- `report-deletion.mmd`: owned saved report deletion confirmation.
- `production-hld.mmd`: technology deployment HLD (proposal only).

These are **Mermaid source files**. Render at mermaid.live; GitHub Markdown supports Mermaid fenced diagrams. Architecture is a design proposal, not proof of implementation.

## 11. First action for next coding agent

Ask to inspect existing `retail_agent/` and `pyproject.toml` if repository is connected, confirm the actual module state/imports, then help the developer learn SQLGlot AST and build a **fail-closed first-pass SQL validator** with three simple tests (valid SELECT, DELETE rejected, blocked PII column rejected). Do not execute generated SQL until authorization/product-scope and pre-LLM result filtering are also implemented.
