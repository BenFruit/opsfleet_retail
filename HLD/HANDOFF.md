# Current handoff — 3 November 2026

This replaces the obsolete October conversation snapshot. No old conversation is
required to continue. Read root AGENTS.md, README.md and this HLD directory.
Code and tests remain authoritative for actual behavior; production proposals
must never be presented as implemented.

## Baseline and pending work

Last committed baseline: `a2ffeb3 feat: add persistent user memory and consolidate
architecture docs`. Uncommitted on top of it (3 November review, fix batch 1):

- `check_text` phone pattern no longer withholds analytical prose such as
  `+12 (2023-09-01 - 2023-09-30)`: candidates need 10-15 digits and no ISO date/year range.
- Every `query_*` log event carries `turn_id` and `query_id`; failed query
  observations and `agent_observation` carry `query_id`; backend failures log the
  exception class name.
- `GeminiClient` retries transient planning failures once (bounded backoff,
  Retry-After <= 8 s) and optionally calls `GEMINI_FALLBACK_MODEL` once; each
  retry logs `model_retry`, and `agent_model_completed` records the answering model.
- Startup failures print/log the failing stage and exception class (never its text).

Fix batch 2 (Golden Knowledge, uncommitted): 12 reviewed trios in `golden/trios/`,
`retail_agent/golden/` (model, hash-based atomic ingestion, hybrid retrieval,
evaluation), migration 004 with a separate `golden_ingest` writer role, retrieval
wired before planning, `Method:` citation, 16 new tests. Live: migration applied to
the existing volume, BigQuery dry run passed for all trios, ingestion idempotent,
retrieval eval recall@3 0.96 at threshold 0.65, two live questions retrieved the
expected trio. `customer-churn-proxy` was revised to v2 after the live run.
The configured Google Doc persona currently appends "Yahooo" to answers: test
content to remove before the demo. ADC lacks a quota project (warning only).

Fix batch 3 (answer evaluation, uncommitted): `retail_agent/evaluation/answers.py`
and `evals/answer_cases.json` (15 live cases, reference SQL, LLM judge, A/B with
and without Golden); numeric grounding check in the agent (`safety/grounding.py`)
with one SQL correction or a visible note; prompt rule against prose arithmetic;
`quarterly-business-review` v2. Live result: Golden 15/15 deterministic, judge
1.00; baseline 14/15, judge 0.60. The judge missed a wrong prose total; numeric
faithfulness is therefore deterministic. Details in EVALUATION.md.

Older `model_failed` codes in existing local logs predate error classification;
current failures are classified. No commit or push has been authorized.

## Implemented

- One host Python CLI; direct Google Gemini SDK and LangGraph nodes `plan`,
  `query`, `reports`, `remember`. No separate agent/memory HTTP service.
- Read-only BigQuery analysis of the four assignment tables; SQLGlot policy
  validation, per-source trusted brand scoping, dry run and bounded results.
- Demographic aggregate subset, default k=5 (minimum configurable k=2), common
  contributing buyers checked before model context. No k threshold for ordinary finance.
- Saved Reports: previous completed answer plus checked tables, embeddings and
  owner/brand-scoped search/open; frozen-ID transactional deletion after `/confirm`.
- PostgreSQL 17/pgvector for reports and memories, independent of Langfuse's DB.
- Explicit owner-scoped user memory, normalized exact deduplication, embeddings,
  bounded full-context loading and direct inspection/deletion commands.
- Optional style-only Google Doc refreshed once per question; accepts ID or URL.
- Allowlisted local JSON logs and opt-in local Langfuse; implicit LangSmith export disabled.

## Decisions that must survive the handoff

- Keep the assessment MVP small and understandable. Mandatory prototype topics
  are safety/access, report deletion, resilience, observability and CLI. Other
  assignment requirements may be design-only. Memory/persona are extra implemented features.
- Unified model-authored SQL replaced the typed demographic action. Do not silently
  reintroduce that tool or loosen demographic checks to accept unsupported SQL.
- Customer direct PII must not reach the model. Demographics are aggregate-only;
  stable customer IDs are allowed for non-demographic finance. Brands define access.
- Startup manager mapping and full authorized-brand list in prompts intentionally
  remain unchanged. Demo manager selection is NOT authentication.
- Operator's voluntarily supplied own name is allowed on explicit remember request
  by developer approval, not a stakeholder relaxation of customer PII rules.
- Persistence needs explicit `remember` / `запомни`; one-off style does not authorize it.
  Query and remember may occur sequentially in the same turn, with one final answer.
- Save copies the previous completed response; analysis and saving are separate turns.
- Fixed Compose demo credentials and loopback ports are deliberate local choices.
  No `.env.langfuse` is needed. Do not delete volumes to resolve migration problems.
- PostgreSQL/pgvector is the current store; Qdrant is only a possible future option.
- Cross-question inference protection and comprehensive trace sanitization remain deferred.
- Harry allowed justified frameworks and AI-generated Golden Knowledge samples;
  retrieval implementation is optional. Vlad allowed memory to remain design-only.

## Known issues — do not silently fix in this documentation task

The latest manual test showed incomplete extraction: a request to remember both
name and long-answer preference stored only the preference. Name persisted on a
second request. A later implicit change to short answers caused an unnecessary
`remember` action, correctly rejected by the explicit-intent gate. The old long-answer
preference remains. There is no update/supersession or semantic deduplication;
even explicit remembering can append conflicting preferences. The developer declined
fixes for now. Do not edit their saved entries as part of tests or documentation.

Other limitations:

- Model intent recognition, style adherence and analytic correctness are not guaranteed.
- Observed analytical errors included missing `Complete`, partial/rolling months
  described as full months, unsupported causal claims and invented age-bin summaries.
  Stronger instructions/regressions are not comprehensive evaluation.
- `AVG(age)` over purchase joins is purchase-weighted, not distinct-buyer-weighted.
- Demographic CTEs/subqueries/windows/set operations and conditional/nested aggregates
  are unsupported. Explain rejection rather than pretend another analysis is equivalent.
- Pattern checks are not complete free-text PII detection. Enabled Langfuse may
  retain raw state, memory, prompts, proposed SQL, observations and output.
- No real login, DB-enforced owner/brand isolation, production deployment, durable
  chat/session recovery or automatic system-learning implementation. Golden retrieval
  is implemented; its threshold is calibrated on 30 cases only.
- Report input has an 8,000-byte embedding bound; oversized snapshots fail, not truncate.

## Verification record

After fix batch 1, 205 tests passed offline (2 opt-in DB tests skipped). The
DB-integration run below was last done on the 194-test pre-handoff baseline:

```powershell
$env:RETAIL_TEST_POSTGRES='1'
uv run python -B -m unittest discover -s tests -q
```

Live smoke tests covered SQL/follow-ups, age buckets, report lifecycle, persona URL
loading, mixed query/remember orderings, name retrieval after reset and owner isolation.
Temporary test-owner fixtures were cleaned up; existing user data was preserved.
These tests do not prove general intent/report quality. A fresh-machine setup has
not yet been exercised. Compose configuration and local Markdown links also passed
validation. All five Mermaid diagrams render with mmdc (checked 3 November).

## Moving to another computer

Clone/sync Git, run `uv sync`, recreate ignored `.env` and `access.local.json`,
authenticate Google ADC and set its quota project. Set real dataset brands rather
than template `Brand A/B/C`. Start reports-postgres with Docker Desktop/Linux containers.
Fresh volumes apply migrations 001–003; existing volumes need missing migrations
applied in order as documented in README. Optional Langfuse needs a new local
project and its keys. Google Doc ID/URL also needs local configuration.

Git does not carry PostgreSQL volumes, reports, memories, Langfuse traces/accounts,
Google ADC, logs or the remote Google Doc contents. A clean clone starts with empty
local application data. Transfer needed data through reviewed backup/restore, not
public Git. Do not copy credentials into documentation or commits.

## Publication checks and next priorities

1. Review `git status`/diff and include new memory files, migration, tests and docs.
2. Ten generated `.pyc` files were removed from Git's index only; local copies were
   preserved and existing ignore rules prevent re-addition. These removals are staged.
   `inspect_schema.ipynb`, inspection scripts and `access.example copy.json` were
   preserved. The duplicate access file contains placeholders; the notebook contains
   public schema inspection outputs and a local warning/path, while notebook and
   `inspect_schema.py` include a developer-specific Google project ID. They are not
   needed by the CLI. Decide whether to include these exploratory artifacts publicly.
   This was a narrow hygiene check, not a complete secret/history audit. Previously
   committed personal context remains in history after editing; no history was rewritten.
3. Reproduce setup on a clean computer and record failures honestly.
4. Build a small intent/SQL/report/UX evaluation dataset and test the production-design
   assumptions, prioritizing business grain, status and calendar boundaries.
5. Done: Golden Knowledge (GOLDEN_KNOWLEDGE.md) and answer evaluation (EVALUATION.md).
   Next: framework/experience section, production diagrams, documentation condensation.
6. Before real data use: trace redaction, authenticated access/data-layer isolation,
   secret management, backups and stronger adversarial/privacy evaluation.

Do not change memory semantics, entitlements, thresholds or safety scope without
agreement. No Docker volume removal, Git history rewriting, commit or push is
part of this handoff request.
