# Refactoring plan — 3 November 2026

Code quality pass after the functional work. Behaviour, safety scope, prompts and
thresholds must not change; every step keeps the full suite green (including
`RETAIL_TEST_POSTGRES=1`) and is one commit in the developer's name.

## Steps 1-4 (pushed to `origin/master`)

| Commit | Step |
|---|---|
| `be34447` | 1. Dead/unsafe code removed: `execute_query` (bypassed all guards), `sql_generator.py`, unused `generate()`/`validate_sql`, pandas. |
| `b54605c` | 2. Shared `storage/postgres.py`; `Embedder` moved to `llm/`; immutable `Entitlements` passed explicitly; `QueryService` takes trusted `allowed_brands`. |
| `fe07aad` | 3. Orchestrator split: `agent/commands.py`, `agent/answer.py`, `agent/conversation.py`. |
| `00d402c` | 4. `blocked_unscoped` catalog level (num_of_item); `presentation.py`; prompt in `llm/prompts/analyst_policy.md` (byte-identical); `bootstrap.build_agent` shared by CLI and evaluation. |

244 tests pass with PostgreSQL.

Live chat smoke test after steps 1-4 (3 November, temporary owner
`smoke_test_owner` with manager_a's brands, Langfuse on): top products, a
follow-up, age-band revenue with a Golden `Method:` citation, `remember`, report
save/list/delete preview/`/confirm`, `/memories`, `/forget-all`. All flows worked;
no warnings or errors in the log; the owner's reports and memories were cleaned up.
Answer-quality notes, not refactoring regressions: the age-band prose ordered
"followed by 30-34 ($1,087.33) and 60-64 ($1,163.86)" wrongly and called the
lowest band "mid-tier" (figures themselves match the table, so the grounding
check passes); the `remember` turn also restated the previous answer.

## Steps A-G from the review (pushed to `origin/master`, ending at `6f6fb70`)

- **A. Settings.** `retail_agent/settings.py` (pydantic-settings, frozen) is built
  once by each entry point (chat, answer evaluation, Golden ingest/evaluate);
  CLI flags default to its values. `GeminiClient(api_key, model, fallback_model=)`
  (+ `from_settings`), `Embedder(llm, model)`, `connection_factory(settings)`,
  `PersonaProvider(doc_id)`, `GoldenKnowledge(min_similarity=)`,
  `configure_tracing(settings)` (Langfuse client created once with explicit keys).
  `build_agent` takes `persona_doc_id` and `golden_min_similarity`. No
  `os.getenv`/`os.environ`/`load_dotenv` remains in `retail_agent/` (a test
  enforces it); python-dotenv is now only a transitive dependency. Behaviour
  changes: empty variables use defaults; booleans also accept `1/0/yes/no`;
  an invalid value (e.g. non-numeric port) stops startup and prints only the
  variable names; `.env` is read from the current directory. 251 tests pass with
  PostgreSQL; live: one chat question with Golden and Langfuse enabled, Golden
  BigQuery dry run. Commit `455fe2d`.
- **B. Application class.** `main.py` only calls `retail_agent.cli.main`.
  `cli.py` parses flags into `ChatOptions`, prints startup messages and runs the
  input loop (`run_chat`, unchanged). `application.py` has `ChatApplication`
  (context manager: `start()` builds the agent in named stages, exit closes
  tracing, Gemini and BigQuery clients) and `startup_stage`, which replaces the
  mutable `stage` variable and turns failures into `StartupError` with the same
  safe messages, hints and `startup_failed` log as before. 255 tests pass.
  Commit `f22b583`.
- **C. Error classifiers and statics.** `llm/errors.py`: `ModelResponseError`,
  `describe_model_error` driven by tables (HTTP status, provider-message markers,
  exception classes) and `response_text`, whose docstring explains the three
  places Gemini reports a missing answer (finish reason, prompt block, empty
  text). `data/errors.py`: `classify_backend_error` (exception-class table plus
  BadRequest repair hints). Statics became module functions: `brand_parameters`,
  `report_summary`, golden `query_text`, `route_after_plan` (new direct test),
  `_log_query_failure`. Codes and messages are unchanged.
  `PersonaProvider.document_id` is left for step E. 256 tests pass.
  Commit `b02835a`.
- **D. `QueryService.execute` as explicit stages.** `execute` now reads as five
  `with run.stage(...)` blocks (authorization, validation, dry_run, execution,
  result_bounds) calling `_byte_limit`, `_authorized_brands`, `_validate`,
  `_dry_run` and `_result_size`. `_QueryRun` holds query/turn IDs and timing
  and owns the stage-tagged failure handling that the mutable `stage` variable
  and one large try/except did before. Error codes, messages, stages and log
  events are unchanged; a new test checks the stage tag in both the error and
  the `query_failed` log for every stage. 257 tests pass. Commit `9a90b53`.
- **E. Persona.** `PersonaProvider` is split into `GoogleDocReader` (ID parsing,
  export URL, timeout, content-type and size checks; failures become
  `DocumentReadError` with the same `persona_*` codes) and `PersonaStyle`
  (current style, last-good fallback, `persona_loaded`/`persona_load_failed`
  logs). `parse_google_doc_id` is a function; the built-in style moved to
  `llm/prompts/default_style.md` with identical text (version hash
  `4a007cf25a0a` is asserted) and ships in the wheel. `build_agent` creates a
  reader only when a document is configured. 260 tests pass. Commit `f4bab44`.
- **F. Memory.** `memory/models.py` now has a module docstring and the limits
  `MAX_ENTRIES` (20), `MAX_ENTRY_CHARS` (200, equal to the `VARCHAR(200)`
  column, checked by a test) and `MAX_ENTRIES_PER_REQUEST` (5);
  `RememberRequest` documents why extra fields are forbidden. The repository
  uses `LIMIT %s` with `MAX_ENTRIES + 1` (explained: detect an over-limit store)
  and the service/repository messages use the constant; texts are unchanged.
  262 tests pass. Commit `746df30`.
- **G1. Test layout and pytest.** pytest is a dev dependency and the runner
  (`uv run python -B -m pytest`); tests stay `unittest.TestCase` classes. Test
  classes were moved, unchanged, into `tests/unit/<package>/` (mirrors
  `retail_agent/`), `tests/integration/` (agent graph with fake backends, chat
  loop) and `tests/integration/postgres/` (opt-in real DB); `demo_access.py`
  moved to `tests/support/`, and `support/paths.py` gives `REPO_ROOT` instead of
  `parents[1]`. Mixed files were split by class; unused imports and helpers were
  dropped. Checked: the same 262 `Class.test` names before and after, and every
  class body is identical except the two that read repository files. pytest uses
  `--import-mode=importlib` so equal file names in different folders are allowed.
  Commit `57b0063`.
- **G2. Direct tests of pure modules.** `tests/unit/agent/test_answer.py`
  (feedback codes, partial answer, repair request after only failures, partial
  stop without budget, model tables replaced by checked ones, one ungrounded
  repair then a visible note, earlier-turn evidence, report/memory output),
  `test_conversation.py` (which answers can be saved, history trimming) and
  `test_commands.py` (exact commands, confirm/cancel, memory commands cancel a
  pending deletion). Builders in `tests/support/agent_state.py`. Recorded, not
  changed: a single turn larger than `max_history_chars` empties the history,
  the new turn included. 278 tests pass. Commit `6f6fb70`.

## Review findings (backend developer, 3 November) still open

Some screenshots predated steps 1-4; these are the items that still apply.

1. `main.py` parses CLI arguments and several classes read the environment
   themselves (`load_dotenv` in `GeminiClient`, `os.getenv` in embeddings,
   storage, Golden, persona, tracing). Configuration should be read once and
   passed in.
2. `main.py` still owns the startup flow and the chat loop.
4. `QueryService.execute` is ~100 lines driven by a `stage` variable.
5. Static helpers that do not belong to their class:
   `QueryService._safe_backend_error`, `QueryService._log_failure`,
   `BigQueryRunner._brand_parameters`, `ReportLibrary._summary`,
   `PersonaProvider.document_id`, `GoldenKnowledge.query_text`,
   `AnalyticalAgent._after_plan`. (`@classmethod` constructors such as
   `Entitlements.from_file` are idiomatic and stay.)
7. `describe_model_error` is a long if/elif ladder.
8. `_response_text` hides "why Gemini returned no text" behind unexplained ifs.
9. See 1 (`GeminiClient` must receive key/model, not load `.env`).
10-12. `PersonaProvider`: unclear name, static `document_id`, one class both
   fetches a Google Doc and keeps the current/fallback style. Note: only the doc
   ID comes from the environment; the class validates, size-limits and caches
   the fetched text.
14. `memory/models.py` lacks explanations (it is the schema of the model's
   `remember` action: 1-5 entries, 1-200 characters, no extra fields).
15. `MemoryRepository` uses a magic `LIMIT 21` (limit 20 + 1 to detect
   overflow); the limit 20 is duplicated in the repository and the service.

Closed by steps 1-4: 3 (commands extracted), 6 (prompt in a file),
13 (`sql_generator.py` removed), parts of 2 and 4 (injection, `allowed_brands`).

## Next steps (one commit each)

- ~~A. Settings~~ (done above). One `Settings` object (pydantic-settings) built once from
  `.env` and CLI flags; components receive values through constructors. No
  `os.getenv`/`load_dotenv` inside classes. Findings 1, 9.
- ~~B. Application class~~ (done above). `main.py` becomes a few lines; startup stages and
  wiring move to an application class, the input loop to `cli.py`. Finding 2.
- ~~C. Error classifiers and statics~~ (done above). Table-driven `llm/errors.py`
  (`describe_model_error`, empty-response reasons) and a BigQuery error
  classifier; remove the static helpers listed above. Findings 5, 7, 8.
- ~~D. `QueryService.execute` as explicit stages~~ (done above) (authorize, validate,
  dry run, execute, guard) with unchanged error codes. Finding 4.
- ~~E. Persona~~ (done above). Split Google Doc reading from current-style tracking,
  rename, `parse_google_doc_id` as a function, default style in
  `llm/prompts/`. Findings 10-12.
- ~~F. Memory~~ (done above). One limit constant, docstrings. Findings 14, 15.
- ~~G. Tests~~ (G1 and G2 done above). Reorganize after the code settles: `tests/unit/<package>/`
  mirroring `retail_agent/`, `tests/integration/` (graph with fake backends,
  PostgreSQL), `tests/support/` (demo entitlements, fakes, builders). Test pure
  modules such as `agent/answer.py` directly instead of through the whole graph.
  Decided: pytest as the runner, existing tests unchanged (G1); G2 adds direct
  tests of pure modules.

## Second review (3 November): reports, memory, embeddings, SQL safety

Screenshots partly predate steps A-F: the `> 20` memory check (F), the
`os.getenv` embedding model (A) and `ReportLibrary._summary` (C) are already
fixed. What still applies, and the plan:

| # | Comment | Status / decision |
|---|---|---|
| 1 | Logic in `UserMemory`, only DB access in the repository; no `remember`/`forget` names there | Agree. Dedup moves to `ON CONFLICT DO NOTHING` on the existing `UNIQUE (owner_id, normalized_key)`. The 20-entry cap must still be checked inside the locked transaction (concurrent requests), so the repository gets a storage primitive `add(owner, entries, max_entries)` instead of business naming. |
| 2 | `memory_key`, `explicit_remember` into a separate module | Agree: `memory/intent.py` (explicit-remember gate, normalized key). |
| 3 | The embedder should only embed, not depend on the LLM client | Agree on the dependency: `Embedder` reaches into `GeminiClient.client`. It will own a genai client built from the API key. Disagree on removing its `check_text`: it is the last check before text leaves for an external API, kept as defense in depth. |
| 4 | `ReportRequest` validator: enum/dict instead of an if-chain | Agree: `StrEnum` operations/scopes and a table of required/allowed fields per operation. Not a pydantic discriminated union: that would change the JSON schema the model sees. |
| 5 | Delete-preview clauses: same | Agree: scope -> SQL clause table; named limits instead of `LIMIT 101/20/10`. |
| 6 | `ReportLibrary.execute`: helpers/classes, dict dispatch | Agree: one handler method per operation, dispatch table, named limits (excerpt 1500, payload 9000, preview TTL 300 s, 100 IDs). |
| 7 | Demographic validator needs a class | Agree: `DemographicQuery` with one method per rule group; allowed joins/dimensions as tables. |
| 8 | `safety/`, `security/` as a whole | See step K. |

Findings for 8: the split does not follow responsibility (`security/query_scope.py`
rewrites SQL and imports `safety`; `safety/demographic_sql.py` also rewrites
SQL with `add_cohort_guard`); SQL is parsed and validated twice per query
(`QueryService` and again inside `apply_brand_scope`); `check_text` is a general
sensitive-text check used by memory, reports, embeddings and Golden but lives in
`result_guard.py`; `result_guard.py` imports `data.QueryResult` (safety depends on
data) and can re-parse SQL to recover columns that `QueryService` always supplies;
`validate_query` is one 60-line function; `_scoped_source` picks predicates with
if/elif.

Steps (one commit each; behaviour, error messages, prompts and model-facing
schemas unchanged):

- **H. Reports.** Comments 4-6. Done:
  - `ReportOperation`/`DeleteScope` (`StrEnum`) and `_REQUEST_SHAPES`, a table
    of the one filter each (operation, scope) needs. The model-facing fields stay
    `Literal` (a test keeps them equal to the enums). On all 480 combinations of
    operation, scope, title, query and report_id the new validator accepts and
    rejects exactly as before; the first error message differs only when a
    request breaks several rules (not shown to the model).
  - Repository: `_DELETE_FILTERS` (scope -> clause, value) replaces the
    if/elif; `MAX_SEARCH_RESULTS`, `MAX_LISTED`, `MAX_DELETE_IDS + 1` replace
    `LIMIT 10/20/101`.
  - `ReportLibrary.execute` dispatches to `_save`, `_search`, `_list`, `_open`,
    `_prepare_delete` with a `_ReportCall` context; `SEARCH_EXCERPT_CHARS`,
    `SEARCH_PAYLOAD_CHARS`, `DELETE_PREVIEW_SECONDS` name the numbers.
  - Regression found and fixed: step F's `RememberRequest` docstring had entered
    the JSON schema sent to Gemini (pydantic uses class docstrings as schema
    descriptions). It is a comment again (schema equal to step E) and
    `tests/support/agent_action_schema.json` is now a snapshot test of the whole
    model-facing action schema.
- **I. Memory.** Comments 1-2. Done: `memory/intent.py` holds
  `explicit_remember` and `memory_key`. `MemoryRepository` is storage only:
  `list(owner, limit)`, `add(owner, entries, max_entries, ...)` and
  `delete(owner, memory_id=None)`. Duplicates are skipped by
  `ON CONFLICT (owner_id, normalized_key) DO NOTHING`; the capacity check runs
  after the inserts under the owner lock and raises `MemoryCapacityError`, which
  rolls the transaction back. `UserMemory` owns the rules and messages (same
  texts) and translates that error. New PostgreSQL test: overflow rolls back the
  whole insert and duplicates do not count. 283 tests pass.
- **J. Embedder.** Comment 3. Done: `Embedder(client, model)` takes a genai SDK
  client instead of reaching into `GeminiClient.client`; `Embedder.from_settings`
  builds its own client with the same transport policy (30 s timeout, no SDK
  retries) and `close()` releases it (chat application, answer evaluation and
  Golden jobs close it). Golden ingest/evaluate no longer create a chat client,
  so they need only `GEMINI_API_KEY`. The sensitive-text check stays in
  `embed()` (last step before an external API), now with a test that such text
  never reaches the SDK. 284 tests pass.
- **K. SQL policy package.** `retail_agent/sql/` with `validator.py` (validate
  once; forbidden node types as a table; table/column checks as functions),
  `demographics.py` (`DemographicQuery` class, comment 7) and `scope.py` (brand
  scope rewrite plus cohort guard; per-table predicates as a table; takes the
  validated tree). `safety/` keeps output guards only: `result_guard.py`,
  `sensitive_text.py` (`check_text`), `grounding.py`; `security/access.py`
  stays. About 13 references in README/AGENTS/HLD are updated with it.
