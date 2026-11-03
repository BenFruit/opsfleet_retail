# Current handoff — 3 November 2026

This replaces the obsolete October conversation snapshot. No old conversation is
required to continue. Read root AGENTS.md, README.md and this HLD directory.
Code and tests remain authoritative for actual behavior; production proposals
must never be presented as implemented.

## Baseline and pending work

Refactoring steps 1-4 end at `00d402c`, which is pushed to `origin/master`.
Fix batches 1-4 from the 3 November review (resilience/logging, Golden
Knowledge, answer evaluation, documentation and metrics) are committed and
described in PRODUCTION_HLD.md, GOLDEN_KNOWLEDGE.md and EVALUATION.md.

Current work: code refactoring, see [REFACTOR_PLAN.md](REFACTOR_PLAN.md). Steps
1-4 are done; steps A-G come from a backend developer's code review. A live chat
smoke test after steps 3-4 is still pending (prompt assembly and wiring changed shape).

User data note: manager_a's memory entry "Appends Yahooo after each answer." was
removed with /forget at the developer's request; the name and "Prefers long
answers." entries remain. ADC lacks a quota project (warning only).

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

After refactoring step 4: 244 tests passed, including the opt-in PostgreSQL
tests (`RETAIL_TEST_POSTGRES=1`), and the built wheel contains the prompt file.
The 15-case answer evaluation was last run before the refactoring (Golden 15/15
deterministic, judge 1.00; baseline 14/15, judge 0.60; see EVALUATION.md).

Live smoke tests covered SQL/follow-ups, age buckets, report lifecycle, persona URL
loading, mixed query/remember orderings, name retrieval after reset and owner isolation.
Temporary test-owner fixtures were cleaned up; existing user data was preserved.
These tests do not prove general intent/report quality. All Mermaid diagrams
render with mmdc; `tests/test_docs.py` keeps embedded copies and links in sync.

Fresh-clone check (3 November, Windows): clone from GitHub, `uv sync` from
scratch, compose config, offline tests, Golden check/BigQuery dry run/idempotent
ingest, one CLI question with a Golden precedent, clean `git status`. A temporary
pgvector container confirmed that migrations 001-004 initialize a fresh volume.

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

## Next priorities

1. A live chat smoke test of steps 1-4, then refactoring steps A-G
   ([REFACTOR_PLAN.md](REFACTOR_PLAN.md)).
2. Developer review of the experience paragraph in PRODUCTION_HLD.md §3.
3. Build a larger intent/SQL/report/UX evaluation set, prioritizing business
   grain, status and calendar boundaries.
4. Before real data use: trace redaction, authenticated access/data-layer
   isolation, secret management, backups and stronger adversarial/privacy evaluation.

Do not change memory semantics, entitlements, thresholds or safety scope without
agreement. No Docker volume removal, Git history rewriting, commit or push
without the developer's explicit request.
