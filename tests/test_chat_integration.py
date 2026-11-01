import io
import json
import logging
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from main import run_chat
from retail_agent.agent.models import AgentAction
from retail_agent.agent.orchestrator import AnalyticalAgent
from retail_agent.data.bigquery_runner import BigQueryRunner, QueryExecution
from retail_agent.data.query_service import QueryService
from retail_agent.llm.analyst import Analyst
from retail_agent.llm.client import GeminiClient
from retail_agent.observability.logging import SafeJsonFormatter
from retail_agent.schema.catalog import TABLE_POLICIES
from retail_agent.security.access import MANAGER_BRANDS, load_brand_access


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
        agent = AnalyticalAgent(Analyst(llm, schema), QueryService(runner), manager_id="manager_a")
        turn = agent.ask("What is completed revenue?")
        self.assertTrue(turn.answer.startswith("Completed-item revenue is 30."))
        self.assertIn("| revenue |", turn.answer)
        self.assertIn("| 30.00 |", turn.answer)
        self.assertEqual(turn.queries, 2)
        self.assertEqual(turn.model_calls, 3)
        runner.execute_query.assert_not_called()
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

    def test_access_config_loads_only_after_full_validation(self):
        original = dict(MANAGER_BRANDS)
        with patch.dict(MANAGER_BRANDS), patch("pathlib.Path.read_text", return_value='{"demo": ["A"]}'):
            load_brand_access("unused.json")
            self.assertEqual(MANAGER_BRANDS, {"demo": frozenset({"A"})})
        self.assertEqual(MANAGER_BRANDS, original)
        for data in ('[]', '{}', '{"demo": "A"}', '{"demo": [null]}'):
            with self.subTest(data=data), patch("pathlib.Path.read_text", return_value=data), self.assertRaises(ValueError):
                load_brand_access("unused.json")
            self.assertEqual(MANAGER_BRANDS, original)

    def test_json_formatter_drops_arbitrary_extras_and_exception_text(self):
        record = logging.LogRecord("retail_agent.agent", logging.INFO, "file", 1, "agent_event", (), None)
        record.turn_id = "id"
        record.prompt = "SECRET"
        record.exc_info = (RuntimeError, RuntimeError("SECRET"), None)
        text = SafeJsonFormatter().format(record)
        self.assertNotIn("SECRET", text)
        self.assertEqual(json.loads(text)["turn_id"], "id")


class GeminiClientTests(unittest.TestCase):
    def test_constructor_model_override_timeout_and_disabled_retries(self):
        with patch("retail_agent.llm.client.load_dotenv"), patch.dict(os.environ, {"GEMINI_API_KEY": "fake", "GEMINI_MODEL": "env-model"}, clear=True), patch("retail_agent.llm.client.genai.Client") as sdk:
            client = GeminiClient(model="explicit-model")
            self.assertEqual(client.model, "explicit-model")
            options = sdk.call_args.kwargs["http_options"]
            self.assertEqual(options.timeout, 30_000)
            self.assertEqual(options.retry_options.attempts, 1)
            client.close()
            sdk.return_value.close.assert_called_once()

    def test_structured_action_system_instruction_and_usage(self):
        with patch("retail_agent.llm.client.load_dotenv"), patch.dict(os.environ, {"GEMINI_API_KEY": "fake", "GEMINI_MODEL": "env-model"}, clear=True), patch("retail_agent.llm.client.genai.Client") as sdk:
            sdk.return_value.models.generate_content.return_value = SimpleNamespace(
                text='{"action": "answer", "answer": "Schema information"}',
                usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, total_token_count=15),
            )
            client = GeminiClient()
            action = client.generate_structured("input", AgentAction, system_instruction="rules")
            self.assertEqual(action.action, "answer")
            self.assertEqual(client.last_usage["total_tokens"], 15)
            config = sdk.return_value.models.generate_content.call_args.kwargs["config"]
            self.assertEqual(config.system_instruction, "rules")
            self.assertEqual(config.max_output_tokens, 4096)
            self.assertIsNone(config.response_schema)
            self.assertFalse(config.response_json_schema["additionalProperties"])

    def test_missing_model_is_not_replaced_by_unverified_default(self):
        with patch("retail_agent.llm.client.load_dotenv"), patch.dict(os.environ, {"GEMINI_API_KEY": "fake"}, clear=True), self.assertRaises(ValueError):
            GeminiClient()


if __name__ == "__main__":
    unittest.main()
