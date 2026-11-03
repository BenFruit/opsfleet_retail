# Production HLD — proposed target, not deployed

## 1. Scope and implementation boundary

The audience is nontechnical retail executives asking multi-step analytical
questions over `bigquery-public-data.thelook_ecommerce`: orders, order_items,
products and users. The goal is evidence-backed reports with actionable hypotheses,
not unsupported causal certainty. See task.txt and STAKEHOLDER_POLICY.md.

| Requirement | Current prototype | Production target |
| --- | --- | --- |
| Hybrid intelligence | 12 reviewed trios, validated hash-based ingestion into pgvector, thresholded hybrid retrieval before planning, retrieval eval (GOLDEN_KNOWLEDGE.md) | Cloud Storage bucket, PR-reviewed publishing job, tenant filters, reranker, feedback-driven trio promotion |
| Safety/access | AST validation, brand rewriting, demographic contributor checks, result guards | Authenticated identity, data-layer isolation, privacy controls and adversarial validation |
| Destructive operations | Frozen preview IDs, separate exact confirmation, transactional owner/brand recheck | Durable signed confirmation, idempotency and audit |
| User learning | Explicit short memories with embeddings and full bounded context | Consent-aware selective retrieval and correction lifecycle |
| System learning | Tests and manual prompt changes | Reviewed offline improvement and staged release |
| Resilience | Bounded repairs/costs, safe errors, optional-service degradation | Deadlines, cancellation, admission control, outage isolation |
| QA/UX | Regression suite and isolated live smoke tests | Intent/report evaluation, usability studies and release gates |
| Observability | Safe JSON logs; optional raw local Langfuse | Sanitized traces, dashboards, SLOs and alerts |
| Persona agility | Style-only public-link Google Doc | Authenticated versioned admin console and rollback |

Mandatory prototype requirements are implemented to the documented limited scope,
not certified production security. Read HANDOFF.md for known observed failures.

## 2. Services and technology choices

Use one modular Python application on **Cloud Run** behind an authenticated HTTPS
API; retain the CLI as a client and add a web client only when needed. LangGraph
expresses explicit bounded transitions and supports testing; it is not an access
control or correctness mechanism. Direct Gemini SDK avoids another model abstraction
for the current Google-only prototype. The developer should state their own framework
experience in the submission; repository documentation cannot invent it.

Production identities come from the organization's OIDC identity provider through
an authenticated gateway/application middleware. An authoritative tenant/brand
entitlement provider supplies trusted scope. Never accept manager/brand claims from
the model, prompt or client. Keep authorization in the application and enforce it
again through reviewed BigQuery authorized views, row policies or entitlement-table
joins compatible with the chosen identity architecture. A shared service account
alone does not magically enforce individual-user row access.

Use **BigQuery** for read-only analytical execution: existing data locality, dry runs,
job IDs/cancellation and billed-byte limits. Govern business metrics in curated
views/definitions. Use separate least-privilege service accounts and avoid raw PII
access when safe views suffice. The prototype ADC credential can have broader
permissions; SQL validation is currently the application read-only boundary.

Use a tested **Gemini model on Vertex AI** for managed production IAM/region controls;
the MVP uses AI Studio with `GEMINI_MODEL` explicitly configured. Benchmark supported
model versions rather than hard-code a claim that a model is newest/best. Keep
generation and embedding model/version separately recorded. Validate availability,
contractual retention and regional processing before migrating provider endpoints.

Use **Cloud SQL PostgreSQL with pgvector** for reports, memory, durable sessions,
confirmation state, idempotency and knowledge-index metadata. It fits transactions
and the modest initial vector corpus without an extra vector service. Current local
DB has reports/memory only. Add owner/tenant policies and validate connection pooling,
private connectivity, HA, backups and point-in-time restore. Qdrant is an alternative
only if measured scale/features justify another operational dependency.

Use **Cloud Storage** for immutable versioned Golden Knowledge artifacts/evaluation
snapshots; **Cloud Run Jobs** for controlled ingestion/evaluation; a **Cloud Tasks**
queue for bounded asynchronous indexing/export jobs. These are proposed, not needed
to run the CLI. Use Secret Manager, managed TLS and Cloud Monitoring/Logging;
sanitized OpenTelemetry/Langfuse can provide model-step drilldown. No Kubernetes,
multi-agent fleet or mandatory Redis service is needed for the application MVP.

## 3. Online data flow and trust boundaries

1. Authenticate the user and resolve current tenant/brands outside model context.
   Validate input length/sensitivity and create request/trace/session IDs.
2. Snapshot mandatory policy, schema/metric versions, approved persona and selected
   owner memory. Fetch authorized Golden candidates before planning. Treat every
   retrieved field as data, not executable instructions.
3. Plan one typed action at a time: query, report, remember or answer. Future tools
   receive their own typed payloads, permissions, budgets and confirmation policy.
4. SQL passes source/column/AST policy, trusted scope rewriting, demographic
   contributor checks, dry run and execution bounds. The model never receives raw
   prohibited source columns. Sanitize/bound results before model observations.
5. Explain findings using checked evidence, explicit date/status/grain/formula,
   uncertainty and proposed validation/actions. Show checked tables separately.
6. Save only on explicit request; use parameterized library operations, not LLM SQL.
   Refresh entitlements for opening/searching/deleting historical reports.
7. Emit sanitized operational metadata and bounded reviewed evaluation samples.

Question/history/profile/checked results and embedding text leave the host for Google
in the MVP. Local pgvector/Langfuse does not make inference offline. Classify and
minimize business data sent to providers as well as direct PII. Model-generated SQL
may reference forbidden data before rejection, so rejected actions must also be
sanitized before telemetry export.

## 4. Golden Knowledge: format, ingestion and retrieval

The prototype implements the core of this section; see GOLDEN_KNOWLEDGE.md for the
actual trio format, ingestion job, retrieval parameters and calibration results.
The example below is the fuller production artifact.

Golden Knowledge is a reviewed reusable analytical method, not a cache of universally
true historical answers. Store one immutable JSON artifact per version, for example:

```json
{
  "id": "brand-monthly-margin",
  "version": 1,
  "status": "approved",
  "question": "Which authorized brands drove completed gross profit last month?",
  "sql_template": "<reviewed read-only SQL with documented date parameters>",
  "analyst_report": "<sanitized example with evidence, caveats and next actions>",
  "metric_definitions": ["completed_item_revenue", "estimated_gross_profit"],
  "grain": "brand by complete calendar month",
  "schema_version": "thelook-policy-v1",
  "visibility": "shared-sanitized",
  "tags": ["brand", "margin", "monthly"],
  "reviewer": "<reviewer-id>",
  "approved_at": "<UTC timestamp>",
  "provenance": "synthetic-reviewed"
}
```

Additional metadata includes applicable tenant/brands for restricted examples,
artifact hash, source/evidence freshness, model/embedding version and superseded-by.
Do not publish private tenant facts under a shared example. Synthetic/AI-generated
samples are permitted by stakeholder clarification but must be labeled and reviewed.

Ingestion validates schema, sanitization, metric/grain/date assumptions, SQL safety
and reviewer approval. Store the immutable artifact in Cloud Storage; index concise
question/method text and metadata in pgvector. Publish the new active index version
atomically only after evaluation. Withdraw stale/bad entries and keep audit history.
Do not silently re-embed with a different model; version/rebuild the index.

At query time, filter authorized tenant/visibility and compatible schema/metrics
**before ranking**. Hybrid lexical/vector retrieval selects candidates; rerank a
small set, use relevance thresholds and bounded top-k (initial proposal: up to 3).
Mount excerpts with IDs/versions and provenance within a token budget. Historical
numbers are examples, not current facts. Rebind current dates/brands and revalidate
generated SQL through the same execution boundary. Never execute a retrieved SQL
template verbatim merely because it was reviewed for another scope.

If retrieval is unavailable or low-confidence, continue with schema/approved metric
definitions and disclose limitations or ask for clarification; do not fabricate an
expert precedent. Track retrieval precision/recall@k and downstream utility separately.

## 5. Memory, persona and reviewed system learning

These are three independent mechanisms:

- **User memory:** explicit owner-specific preference/profile consent; current MVP
  loads all max-20 entries. Production adds pinned preferences plus budgeted relevant
  long-tail retrieval, TTL/provenance, inspection, correction/supersession and deletion.
  Similarity only proposes duplicates; it cannot merge contradictions safely.
- **Persona:** global presentation configuration, never authorization/policy. Proposed
  admin UI supports editor/reviewer roles, drafts, preview on fixed examples,
  validation, versioned publish/rollback and audit. Snapshot one version per turn;
  retain last-good configuration during outages. Google Doc is the current MVP only.
- **System learning:** no online autonomous prompt/model rewriting. Capture consented,
  sanitized failure/feedback candidates, cluster recurring issues, and have analysts
  review corrected question/SQL/report trios or metric/prompt changes. Version the
  resulting artifacts, run held-out regression/safety/UX checks, then approve a
  staged release. Monitor canary quality and roll back by version on regressions.

User likes are weak feedback, not proof of numerical correctness. Do not promote
private memory, unverified model output or injected instructions into shared Golden
Knowledge. Prevent tenant leakage and evaluation contamination. Fine-tuning or a
distilled request classifier is optional after sufficient labeled evidence; neither
replaces SQL/result/access controls. See REQUEST_SAFETY_CLASSIFIER.md.

## 6. Reports and high-stakes deletion

Current save stores the previous completed answer plus checked tables and a trusted
brand provenance scope. Search is owner/brand-scoped; semantic neighbors are discovery,
not deletion selection. Literal matching/current-conversation/IDs/all scopes create
a frozen maximum-100-ID preview. Exact `/confirm` within five minutes bypasses the
LLM, rechecks access and deletes transactionally. Any new question/cancel/reset/restart
cancels. Never report success after ambiguous commit errors. See SAVED_REPORTS.md.

For distributed production, store pending scope/IDs/count/hash/owner/session/expiry
server-side and issue a cryptographically bound single-use confirmation token.
Recheck current entitlements and consume the token inside the deletion transaction;
use an idempotency key and durable audit outcome. Do not expand the candidate set
at confirmation time. Multiple batches need explicit previews; no hidden all-library
expansion beyond the cap. Define retention/backup deletion separately from live-row
deletion; backup expiry is not immediate physical erasure.

## 7. Safety and remaining privacy risk

Deterministic SQL guards and trusted scoping are implemented application boundaries;
they are not a proof of anonymity or perfect parser coverage. Customer names/email/
street/postal/precise location are prohibited; demographic attributes are aggregate-only.
Stable IDs are permitted only for non-demographic analysis. Restrict joins and metric
contributors, not merely output names. A demographic predicate followed by IDs leaks
attributes even when those attributes are not selected.

Current k suppression does not stop differencing across questions, auxiliary-knowledge
attacks or all extreme-statistic inference. Proposed production choices include
reviewed safe datasets/metrics, composition-aware query budgets, consistent suppression
and differential privacy where utility/privacy needs justify it. These require explicit
policy decisions and testing; do not imply they exist in the prototype.

Keep schema, retrieved documents, persona, memory and tool results distinct from
mandatory policy. Test indirect injection, encoded disclosure, malicious stored
strings and tool/confirmation bypass. Free-text sensitive-data classification and
safe telemetry need stronger controls than current email/phone regexes.

## 8. Reliability, budgets and failure handling

MVP defaults: 7 generative calls, 5 SQL attempts, 3 consecutive SQL failures (initial
plus 2 repairs), 2 malformed-action corrections, 5 report calls, 1 memory write attempt
and 300 MB scan accounting per turn. Per query: 100 MB billed bytes, 200 rows, 256 KB,
30-second wait and 10-second API timeout. Gemini has 30-second HTTP timeout; SDK
transport retries are disabled and the client retries transient planning failures
once (bounded backoff/Retry-After <= 8 s), then optionally uses one configured
fallback model. Retries are logged and do not consume the agent's 7-call budget.
Embedding calls are separate from generative counters and are not retried.

Validation/syntax errors may get controlled bounded repair. Empty output may mean
no data or privacy suppression: check supported filters/date assumptions without
relaxing authorization or k. If unresolved, state no publishable result, not zero.
Rate limits, authorization denials, unsafe results and ambiguous executions are not
blindly replayed. Unknown execution billing reserves the ceiling. Budgets are not
an account-wide cap or hard end-to-end deadline.

Production adds request deadlines, cancellation propagated to BigQuery jobs,
concurrency/admission quotas and per-tenant cost controls. Retry only explicitly
safe/idempotent transient reads with bounded jitter/backoff and Retry-After; use
circuit breakers and provider outage alerts. Never fail open on authorization.
Memory/persona/Golden outages can degrade to marked-empty/default context; reports
outage leaves analysis available but cannot claim saves/deletes succeeded. Durable
job IDs/idempotency let operators resolve uncertain outcomes before retrying.

## 9. Evaluation and UX release gates

Build a versioned dataset with questions, authorized scope, fixed data fixtures or
snapshot dates, expected metrics/grain/status/date windows, acceptable SQL/result
invariants, required caveats and prohibited outputs. Include ambiguous and multi-intent
requests, follow-ups, absent data, mixed-brand baskets and adversarial prompts.

Evaluate layers independently:

- Retrieval: relevance/recall@k, stale precedent use and unauthorized leakage.
- SQL/result: execution success, scope isolation, reference numeric results,
  contributor/privacy invariants and correct metric/time semantics. Equivalent SQL
  is acceptable; string equality is not a semantic oracle.
- Reports: answer coverage, numerical faithfulness, date/status/formula consistency,
  uncertainty, actionability and unsupported causal claims. Combine deterministic
  checks with blinded analyst rubric; calibrate any LLM judge against humans.
- Memory/persona: complete fact extraction, explicit intent, negation, current-turn
  precedence, conflicts, formatting adherence and owner isolation.
- Failure/UX: controlled outages/timeouts, no duplicate writes, understandable no-data
  messages, successful deletion confirmation/cancel and restoration after reset.

Run all regressions in CI; use opt-in DB integration and a separately budgeted live
smoke suite. Block releases on any critical privacy/access/confirmation regression.
Proposed initial quality gate: >=90% analyst-approved intent/report cases on a
held-out representative set, with zero critical safety violations; calibrate with
stakeholders rather than claim an achieved score. Version model, prompt, policy,
schema, persona and knowledge in every evaluation run.

Test UX with representative nontechnical executives: ask them to perform analytical
tasks and report search/delete flows without coaching. Measure completion rate,
turns/time to useful answer, correction/abandonment, confirmation comprehension,
perceived usefulness and calibrated trust. Do not reward confident wrong reports.
Fresh-machine setup must be tested separately; 205 unit/integration tests do not
establish portability or executive usability.

## 10. Observability and operations

Production emits sanitized trace-linked events for request, planning, validation,
dry run, execution, result checks, retrieval, report/memory operations and final answer.
Record safe reason codes, stage duration, model/version, token/cost/scan accounting,
repair counts, budget stops, empty/suppressed results and permission/confirmation
outcomes. No raw PII, secrets, rejected SQL literals or profile text by default.
Debug payload capture needs separate consent, restricted access and short retention.

Dashboards track end-to-end p50/p95 latency, service/model error rates, SQL success
and repair recovery, quality review scores, retrieval usefulness, memory failures,
confirmation abandonment, cost per completed task and per-tenant usage. Distinguish
handled error observations from infrastructure span status. Correlate by request/turn
ID and policy/model versions, not personal identifiers. Alert on unusual denial/leakage
signals, dependency failures, rising cost/repair rates and backup failures.

Proposed initial SLO discussion: 99.5% monthly request availability excluding explicit
policy/user denials, and p95 <60 seconds for agreed single-query tasks under expected
load. These are targets to load-test and negotiate, not current measurements; complex
analyses need separate classes. Define error budget/incident ownership, runbooks,
RPO/RTO through restore drills and retention/data residency with stakeholders.

Current Langfuse exports raw graph/model data when enabled; comprehensive redaction
is deferred. Keep it demo-only until the export boundary is tested with sensitive
canaries on success and failure paths. Logging outages must not break analysis.

## 11. Rollout and extension

Start with the local reproducible CLI and reviewed evaluation examples. Validate a
clean clone, then pilot authenticated read-only production analysis on safe views.
Add durable reports/confirmation, reviewed knowledge retrieval and admin persona;
roll out by tenant/canary with versioned rollback. Review privacy/export, restore,
cost and load gates before expanding use.

Charts, email and web search are future typed adapters, not unrestricted model tools.
Each needs explicit data classification, outbound destination controls, authorization,
timeouts/budgets and relevant confirmation. Email/export is a data-disclosure operation;
web content is untrusted and cannot change policy. Keep the orchestration modular
without deploying a separate service for every action.
