import io
import json
import logging
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from pydantic import ValidationError
from google.genai import errors as genai_errors

from retail_agent.cli import run_chat
from retail_agent.agent.models import AgentAction
from retail_agent.agent.orchestrator import AnalyticalAgent
from retail_agent.data.bigquery_runner import BigQueryRunner, QueryExecution
from retail_agent.data.query_service import QueryService
from retail_agent.llm.analyst import Analyst
from retail_agent.llm.client import GeminiClient
from retail_agent.observability.logging import SafeJsonFormatter
from retail_agent.schema.catalog import TABLE_POLICIES
from retail_agent.security.access import Entitlements
from retail_agent.settings import Settings
from demo_access import DEMO_ENTITLEMENTS


class ChatIntegrationTests(unittest.TestCase):
    def test_real_graph_analyst_service_scope_and_guard_with_fake_backends(self):
        llm = Mock(spec=GeminiClient)
        llm.last_usage = {}
        llm.generate_structured.side_effect = [
            AgentAction(action="query", sql="SELECT DISTINCT oi.status FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi"),
            AgentAction(action="query", sql="SELECT SUM(oi.sale_price) AS revenue FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi WHERE oi.status = 'Complete'"),
            AgentAction(action="answer", answer="Completed-item revenue is 30."),
        ]
        schema = {
            table: [{"name": name, "type": "STRING", "description": "DO_NOT_FORWARD"} for name in columns]
            for table, columns in TABLE_POLICIES.items()
        }
        runner = Mock(spec=BigQueryRunner)
        runner.dry_run.return_value = 50
        runner.execute_scoped_query.side_effect = [
            QueryExecution(({"status": "Complete"},), False, 50, 50),
            QueryExecution(({"revenue": 30},), False, 50, 50),
        ]
        agent = AnalyticalAgent(Analyst(llm, schema), QueryService(runner), manager_id="manager_a", entitlements=DEMO_ENTITLEMENTS)
        turn = agent.ask("What is completed revenue?")
        self.assertTrue(turn.answer.startswith("Completed-item revenue is 30."))
        self.assertIn("| revenue |", turn.answer)
        self.assertIn("| 30.00 |", turn.answer)
        self.assertEqual(turn.queries, 2)
        self.assertEqual(turn.model_calls, 3)
        for call in runner.execute_scoped_query.call_args_list:
            self.assertIn("@allowed_brands", call.args[0])
            self.assertEqual(call.kwargs["allowed_brands"], ("Brand A", "Brand B"))
        last_prompt = json.loads(llm.generate_structured.call_args.args[0])
        self.assertEqual(last_prompt["observations"][1]["rows"], [{"revenue": 30}])
        user_columns = {column["name"] for column in last_prompt["schema"]["users"]}
        self.assertIn("age", user_columns)
        self.assertIn("state", user_columns)
        self.assertNotIn("email", user_columns)
        self.assertNotIn("first_name", user_columns)
        self.assertNotIn("DO_NOT_FORWARD", llm.generate_structured.call_args.args[0])

    def test_cli_survives_failed_request_and_accepts_next_question(self):
        agent = Mock()
        agent.ask.side_effect = [RuntimeError("SECRET"), SimpleNamespace(answer="30", queries=1, model_calls=2)]
        output = io.StringIO()
        with patch("builtins.input", side_effect=["Question 1", "Question 2", "/exit"]), patch("sys.stdout", output):
            run_chat(agent)
        self.assertEqual(agent.ask.call_count, 2)
        self.assertIn("Analyst: 30", output.getvalue())
        self.assertNotIn("SECRET", output.getvalue())

    def test_cli_reset_help_and_empty_input(self):
        agent = Mock()
        with patch("builtins.input", side_effect=["", "/help", "/reset", "/exit"]), patch("sys.stdout", io.StringIO()):
            run_chat(agent)
        agent.reset.assert_called_once()
        agent.ask.assert_not_called()

    def test_access_config_is_fully_validated(self):
        with patch("pathlib.Path.read_text", return_value='{"demo": ["A"], "empty": []}'):
            entitlements = Entitlements.from_file("unused.json")
        self.assertEqual(entitlements.brands_for("demo"), frozenset({"A"}))
        for data in ('[]', '{}', '{"demo": "A"}', '{"demo": [null]}', '{"demo": [" "]}'):
            with self.subTest(data=data), patch("pathlib.Path.read_text", return_value=data), self.assertRaises(ValueError):
                Entitlements.from_file("unused.json")

    def test_unknown_or_empty_identity_is_denied(self):
        entitlements = Entitlements({"demo": ["A"], "empty": []})
        for manager in ("unknown", "empty"):
            with self.subTest(manager=manager), self.assertRaises(PermissionError):
                entitlements.brands_for(manager)

    def test_json_formatter_drops_arbitrary_extras_and_exception_text(self):
        record = logging.LogRecord("retail_agent.agent", logging.INFO, "file", 1, "agent_event", (), None)
        record.turn_id = "id"
        record.prompt = "SECRET"
        record.exc_info = (RuntimeError, RuntimeError("SECRET"), None)
        text = SafeJsonFormatter().format(record)
        self.assertNotIn("SECRET", text)
        self.assertEqual(json.loads(text)["turn_id"], "id")


class BootstrapTests(unittest.TestCase):
    def test_chat_and_evaluation_share_wiring_and_optional_parts(self):
        from retail_agent.bootstrap import build_agent

        connect, embedder = Mock(), Mock()
        full = build_agent(Mock(), {}, Mock(), manager_id="manager_a", entitlements=DEMO_ENTITLEMENTS,
                           connect=connect, embedder=embedder, golden=True, memory=True,
                           persona_doc_id="doc", golden_min_similarity=0.7)
        self.assertIs(full.reports.repository._connect, connect)
        self.assertIs(full.memory.embedder, embedder)
        self.assertIs(full.golden.repository.connection, connect)
        self.assertEqual(full.golden.min_similarity, 0.7)
        self.assertEqual(full.analyst.persona_provider.doc_id, "doc")
        self.assertEqual(full.allowed_brands, ["Brand A", "Brand B"])
        bare = build_agent(Mock(), {}, Mock(), manager_id="manager_b", entitlements=DEMO_ENTITLEMENTS,
                           connect=connect, embedder=embedder, golden=False, memory=False,
                           persona_doc_id="", golden_min_similarity=0.65)
        self.assertIsNone(bare.golden)
        self.assertIsNone(bare.memory)
        self.assertIsNotNone(bare.reports)


class StartupDiagnosticsTests(unittest.TestCase):
    """cli.main with every external dependency of the application replaced."""

    def setUp(self):
        from retail_agent import application, cli

        self.application, self.cli = application, cli
        self.settings = Settings(_env_file=None, gemini_api_key="fake", gemini_model="m")
        for name in ("configure_logging", "configure_tracing", "SchemaService", "build_agent", "connection_factory",
                     "Embedder", "QueryService"):
            self.enterContext(patch.object(application, name))
        self.shutdown_tracing = self.enterContext(patch.object(application, "shutdown_tracing"))
        self.entitlements = self.enterContext(patch.object(application, "Entitlements"))
        self.gemini = self.enterContext(patch.object(application, "GeminiClient"))
        self.bigquery = self.enterContext(patch.object(application, "BigQueryRunner"))
        self.logger = self.enterContext(patch.object(application, "logger"))
        self.run_chat = self.enterContext(patch.object(cli, "run_chat"))
        self.stdout = self.enterContext(patch("sys.stdout", new_callable=io.StringIO))

    def run_main(self, settings=None):
        with patch.object(self.cli, "Settings", return_value=settings or self.settings):
            return self.cli.main([])

    def test_failure_reports_stage_and_error_class_but_not_exception_text(self):
        self.bigquery.side_effect = RuntimeError("SECRET credentials path")
        self.assertEqual(self.run_main(), 1)
        self.assertIn("stage 'bigquery_client' (RuntimeError)", self.stdout.getvalue())
        self.assertIn("gcloud auth application-default login", self.stdout.getvalue())
        self.logger.error.assert_called_once_with(
            "startup_failed", extra={"stage": "bigquery_client", "error_type": "RuntimeError"},
        )
        self.assertNotIn("SECRET", self.stdout.getvalue() + repr(self.logger.mock_calls))
        self.run_chat.assert_not_called()
        self.shutdown_tracing.assert_called_once()
        self.gemini.from_settings.return_value.close.assert_called_once()

    def test_access_config_problems_get_direct_messages(self):
        for error, expected in ((FileNotFoundError("SECRET path"), "Access config not found"),
                                (PermissionError("SECRET"), "no authorized brands")):
            with self.subTest(error=type(error).__name__):
                self.entitlements.from_file.side_effect = error
                self.assertEqual(self.run_main(), 1)
                self.assertIn(expected, self.stdout.getvalue())
                self.assertNotIn("SECRET", self.stdout.getvalue())
        self.logger.error.assert_not_called()
        self.gemini.from_settings.assert_not_called()

    def test_missing_gemini_setting_stops_before_clients(self):
        self.assertEqual(self.run_main(Settings(_env_file=None, gemini_api_key="fake")), 1)
        self.assertIn("Set GEMINI_MODEL in your .env", self.stdout.getvalue())
        self.gemini.from_settings.assert_not_called()

    def test_successful_start_runs_chat_then_closes_clients(self):
        self.assertEqual(self.run_main(), 0)
        self.run_chat.assert_called_once_with(self.application.build_agent.return_value)
        self.gemini.from_settings.return_value.close.assert_called_once()
        self.bigquery.return_value.client.close.assert_called_once()
        self.shutdown_tracing.assert_called_once()
        self.assertEqual(self.logger.info.call_args.args[0], "startup_completed")

    def test_invalid_settings_and_cohort_flag(self):
        with patch.object(self.cli, "Settings", side_effect=ValidationError.from_exception_data(
                "Settings", [{"type": "int_parsing", "loc": ("reports_postgres_port",), "input": "SECRET"}])):
            self.assertEqual(self.cli.main([]), 1)
        self.assertIn("REPORTS_POSTGRES_PORT", self.stdout.getvalue())
        self.assertNotIn("SECRET", self.stdout.getvalue())
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            self.cli.parse_options(self.settings, ["--min-cohort-size", "1"])
        options = self.cli.parse_options(self.settings, ["--manager", "manager_b"])
        self.assertEqual((options.manager, options.min_cohort_size), ("manager_b", 5))


class GeminiClientTests(unittest.TestCase):
    def test_constructor_model_override_timeout_and_disabled_retries(self):
        with patch("retail_agent.llm.client.genai.Client") as sdk:
            client = GeminiClient("fake", "explicit-model")
            self.assertEqual(client.model, "explicit-model")
            options = sdk.call_args.kwargs["http_options"]
            self.assertEqual(options.timeout, 30_000)
            self.assertEqual(options.retry_options.attempts, 1)
            client.close()
            sdk.return_value.close.assert_called_once()

    def test_structured_action_system_instruction_and_usage(self):
        with patch("retail_agent.llm.client.genai.Client") as sdk:
            generate = sdk.return_value.models.generate_content
            generate.return_value = SimpleNamespace(
                text='{"action": "answer", "answer": "Schema information"}',
                usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, total_token_count=15),
            )
            client = GeminiClient("fake", "env-model")
            action = client.generate_structured("input", AgentAction, system_instruction="rules")
            self.assertEqual(action.action, "answer")
            self.assertEqual(client.last_usage["total_tokens"], 15)
            config = generate.call_args.kwargs["config"]
            self.assertEqual(config.system_instruction, "rules")
            self.assertEqual(config.max_output_tokens, 4096)
            self.assertIsNone(config.response_schema)
            self.assertFalse(config.response_json_schema["additionalProperties"])

    def test_missing_model_is_not_replaced_by_unverified_default(self):
        for key, model in (("fake", ""), ("", "model")):
            with self.subTest(key=bool(key)), patch("retail_agent.llm.client.genai.Client"), self.assertRaises(ValueError):
                GeminiClient(key, model)

    def test_from_settings_passes_key_models_and_judge_override(self):
        settings = Settings(_env_file=None, gemini_api_key="fake", gemini_model="primary",
                            gemini_fallback_model="backup")
        with patch("retail_agent.llm.client.genai.Client") as sdk:
            client = GeminiClient.from_settings(settings)
            judge = GeminiClient.from_settings(settings, model="judge")
        self.assertEqual(sdk.call_args.kwargs["api_key"], "fake")
        self.assertEqual((client.model, client.fallback_model), ("primary", "backup"))
        self.assertEqual(judge.model, "judge")


OK_RESPONSE = SimpleNamespace(
    text='{"action": "answer", "answer": "ok"}',
    usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=1, total_token_count=2),
)


def api_error(status, message="SECRET", retry_after=None):
    response = httpx.Response(status, headers={"Retry-After": retry_after}) if retry_after else None
    return genai_errors.APIError(status, {"error": {"message": message}}, response)


class GeminiRetryTests(unittest.TestCase):
    def make_client(self, outcomes, fallback=""):
        sdk = self.enterContext(patch("retail_agent.llm.client.genai.Client"))
        self.generate = sdk.return_value.models.generate_content
        self.generate.side_effect = outcomes
        self.sleep = Mock()
        self.logger = self.enterContext(patch("retail_agent.llm.client.logger"))
        return GeminiClient("fake", "primary", fallback_model=fallback, sleep=self.sleep)

    def models_called(self):
        return [call.kwargs["model"] for call in self.generate.call_args_list]

    def test_transient_error_is_retried_once_on_the_same_model(self):
        client = self.make_client([api_error(503), OK_RESPONSE])
        self.assertEqual(client.generate_structured("p", AgentAction).answer, "ok")
        self.assertEqual(self.models_called(), ["primary", "primary"])
        self.sleep.assert_called_once()
        self.assertLessEqual(self.sleep.call_args.args[0], client.max_delay_seconds)
        self.assertEqual(client.last_usage["model"], "primary")

    def test_persistent_outage_switches_to_fallback_and_never_returns_to_primary(self):
        client = self.make_client([api_error(503), api_error(503), OK_RESPONSE], fallback="backup")
        client.generate_structured("p", AgentAction)
        self.assertEqual(self.models_called(), ["primary", "primary", "backup"])
        self.assertEqual(client.last_usage["model"], "backup")
        client = self.make_client([api_error(503), api_error(503), api_error(503)], fallback="backup")
        with self.assertRaises(genai_errors.APIError):
            client.generate_structured("p", AgentAction)
        self.assertEqual(self.models_called(), ["primary", "primary", "backup"])

    def test_without_fallback_the_error_is_raised_after_bounded_attempts(self):
        client = self.make_client([api_error(503), api_error(503), OK_RESPONSE])
        with self.assertRaises(genai_errors.APIError):
            client.generate_structured("p", AgentAction)
        self.assertEqual(self.generate.call_count, client.max_attempts)

    def test_daily_quota_timeout_and_long_retry_after_skip_to_fallback_without_waiting(self):
        for error in (api_error(429, "Quota perDay exceeded"), httpx.ReadTimeout("SECRET"), api_error(429, retry_after="60")):
            with self.subTest(error=type(error).__name__):
                client = self.make_client([error, OK_RESPONSE], fallback="backup")
                client.generate_structured("p", AgentAction)
                self.assertEqual(self.models_called(), ["primary", "backup"])
                self.sleep.assert_not_called()

    def test_short_retry_after_is_honoured(self):
        client = self.make_client([api_error(429, retry_after="3"), OK_RESPONSE])
        client.generate_structured("p", AgentAction)
        self.sleep.assert_called_once_with(3.0)

    def test_invalid_output_and_permanent_errors_are_not_retried(self):
        invalid = SimpleNamespace(text='{"action": "SECRET"}', usage_metadata=None)
        for outcome in (invalid, api_error(400), api_error(401)):
            with self.subTest(outcome=outcome):
                client = self.make_client([outcome, OK_RESPONSE], fallback="backup")
                with self.assertRaises(Exception):
                    client.generate_structured("p", AgentAction)
                self.assertEqual(self.generate.call_count, 1)

    def test_retry_log_is_metadata_only(self):
        client = self.make_client([api_error(503, "SECRET body"), OK_RESPONSE])
        client.generate_structured("SECRET prompt", AgentAction)
        name, = (call.args[0] for call in self.logger.warning.call_args_list)
        extra = self.logger.warning.call_args.kwargs["extra"]
        self.assertEqual(name, "model_retry")
        self.assertEqual((extra["error_code"], extra["attempt"], extra["http_status"]), ("model_unavailable", 1, 503))
        self.assertNotIn("SECRET", repr(self.logger.mock_calls))

    def test_fallback_equal_to_primary_is_ignored(self):
        client = self.make_client([], fallback="primary")
        self.assertIsNone(client.fallback_model)


if __name__ == "__main__":
    unittest.main()
