# Refactoring plan — 3 November 2026

Code quality pass after the functional work. Behaviour, safety scope, prompts and
thresholds must not change; every step keeps the full suite green (including
`RETAIL_TEST_POSTGRES=1`) and is one commit in the developer's name.

## Done (pushed to `origin/master`)

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

## Done locally (not pushed)

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
  BigQuery dry run.

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
- **B. Application class.** `main.py` becomes a few lines; startup stages and
  wiring move to an application class, the input loop to `cli.py`. Finding 2.
- **C. Error classifiers and statics.** Table-driven `llm/errors.py`
  (`describe_model_error`, empty-response reasons) and a BigQuery error
  classifier; remove the static helpers listed above. Findings 5, 7, 8.
- **D. `QueryService.execute` as explicit stages** (authorize, validate,
  dry run, execute, guard) with unchanged error codes. Finding 4.
- **E. Persona.** Split Google Doc reading from current-style tracking,
  rename, `parse_google_doc_id` as a function, default style in
  `llm/prompts/`. Findings 10-12.
- **F. Memory.** One limit constant, docstrings. Findings 14, 15.
- **G. Tests.** Reorganize after the code settles: `tests/unit/<package>/`
  mirroring `retail_agent/`, `tests/integration/` (graph with fake backends,
  PostgreSQL), `tests/support/` (demo entitlements, fakes, builders). Test pure
  modules such as `agent/answer.py` directly instead of through the whole graph.
  Open decision: move from `unittest` to `pytest` (runs existing tests unchanged,
  so migration can be gradual).

More review comments from the same developer are pending and may extend A-G.
