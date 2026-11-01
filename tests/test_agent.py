import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from langsmith import get_tracing_context
from google.genai import errors as genai_errors

from retail_agent.agent.models import AgentAction, AgentLimits
from retail_agent.agent.orchestrator import AnalyticalAgent
from retail_agent.data.query_service import QueryLimits, QueryResult, QueryService, QueryServiceError
from retail_agent.llm.analyst import Analyst


STATUS_SQL = "SELECT DISTINCT oi.status FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi"
REVENUE_SQL = "SELECT SUM(oi.sale_price) AS revenue FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi"


def query(sql=REVENUE_SQL):
    return AgentAction(action="query", sql=sql)


def answer(text="Revenue is 30. This uses the selected status policy."):
    return AgentAction(action="answer", answer=text)


def result(rows=({"revenue": 30},), *, billed=10):
    return QueryResult("test-id", rows, False, 10, 10, billed)


class AnalyticalAgentTests(unittest.TestCase):
    def setUp(self):
        self.analyst = Mock(spec=Analyst)
        self.analyst.llm = SimpleNamespace(last_usage={"input_tokens": 10, "output_tokens": 5})
        self.service = Mock(spec=QueryService)
        self.service.limits = QueryLimits(maximum_bytes_billed=1000)
        self.service.execute.return_value = result()
        self.agent = AnalyticalAgent(self.analyst, self.service, manager_id="manager_a")
        self.logger = self.enterContext(patch("retail_agent.agent.orchestrator.logger"))

    def test_one_query_then_answer(self):
        self.analyst.decide.side_effect = [query(), answer()]
        turn = self.agent.ask("What is our revenue?")
        self.assertIn("Revenue is 30", turn.answer)
        self.assertEqual(turn.queries, 1)
        self.assertEqual(turn.model_calls, 2)
        self.assertEqual(turn.scan_bytes_used, 10)
        self.service.execute.assert_called_once_with(
            REVENUE_SQL, manager_id="manager_a", scan_budget_bytes=300_000_000,
        )
        observation = self.analyst.decide.call_args.kwargs["observations"][0]
        self.assertEqual(observation["rows"], [{"revenue": 30}])
        self.assertIn("| revenue |", turn.answer)
        self.assertIn("| 30.00 |", turn.answer)

    def test_placeholder_after_failed_query_is_not_accepted_as_final_result(self):
        self.analyst.decide.side_effect = [query(), answer("Let me run a query to get that list.")]
        self.service.execute.side_effect = QueryServiceError("invalid_sql", "SQL rejected: unsupported construct")
        turn = self.agent.ask("Give me the top 10 brands")
        self.assertNotIn("Let me run", turn.answer)
        self.assertIn("[invalid_sql]", turn.answer)
        self.assertIn("unsupported construct", turn.answer)

    def test_final_query_failure_includes_safe_cause(self):
        self.analyst.decide.return_value = query()
        self.service.execute.side_effect = QueryServiceError("invalid_sql", "Column is ambiguous; use an explicit table alias")
        turn = self.agent.ask("Give me the top 10 brands")
        self.assertIn("[invalid_sql]", turn.answer)
        self.assertIn("Column is ambiguous", turn.answer)

    def test_model_receives_access_metadata_separate_from_budget(self):
        self.analyst.decide.return_value = answer("Scope explanation")
        self.agent.ask("Give me top 10 brands")
        context = self.analyst.decide.call_args.kwargs
        self.assertEqual(context["access_context"]["authorized_brands"], ["Brand A", "Brand B"])
        self.assertNotIn("authorized_brand_count", context["budget"])

    def test_first_question_survives_history_trimming(self):
        self.agent = AnalyticalAgent(self.analyst, self.service, manager_id="manager_a", limits=AgentLimits(max_history_turns=2))
        self.analyst.decide.return_value = answer("Brief answer")
        for question in ("Hello", "Two", "Three", "What was my first question?"):
            self.agent.ask(question)
        session = self.analyst.decide.call_args.kwargs["session_context"]
        self.assertEqual(session["first_question"], "Hello")
        self.assertTrue(session["history_truncated"])

    def test_reset_also_clears_first_question(self):
        self.analyst.decide.return_value = answer("Brief answer")
        self.agent.ask("Before reset")
        self.agent.reset()
        self.agent.ask("After reset")
        self.assertEqual(self.analyst.decide.call_args.kwargs["session_context"]["first_question"], "After reset")

    def test_all_current_query_results_are_rendered(self):
        self.analyst.decide.side_effect = [query(STATUS_SQL), query(), answer("Two analyses completed")]
        self.service.execute.side_effect = [result(({"status": "Complete"},)), result()]
        turn = self.agent.ask("Do both analyses")
        self.assertIn("| status |", turn.answer)
        self.assertIn("| revenue |", turn.answer)

    def test_exploration_then_analysis(self):
        self.analyst.decide.side_effect = [query(STATUS_SQL), query(), answer()]
        self.service.execute.side_effect = [result(({"status": "Complete"},)), result()]
        turn = self.agent.ask("Analyze completed revenue")
        self.assertEqual(turn.queries, 2)
        self.assertEqual(turn.model_calls, 3)
        self.assertEqual(self.analyst.decide.call_args.kwargs["observations"][0]["rows"], [{"status": "Complete"}])

    def test_follow_up_receives_checked_context(self):
        self.analyst.decide.side_effect = [query(), answer(), answer("The earlier figure was 30.")]
        self.agent.ask("What is revenue?")
        turn = self.agent.ask("Explain that number")
        history = self.analyst.decide.call_args.kwargs["history"]
        self.assertEqual(history[0]["question"], "What is revenue?")
        self.assertEqual(history[0]["observations"][0]["rows"], [{"revenue": 30}])
        self.assertEqual(turn.queries, 0)

    def test_reset_clears_history(self):
        self.analyst.decide.return_value = answer()
        self.agent.ask("Question")
        self.agent.reset()
        self.assertEqual(self.agent.history, [])

    def test_invalid_sql_can_be_repaired_once(self):
        self.analyst.decide.side_effect = [query("SELECT * FROM x"), query(), answer()]
        self.service.execute.side_effect = [QueryServiceError("invalid_sql", "SQL is unsupported."), result()]
        turn = self.agent.ask("Revenue?")
        self.assertEqual(turn.queries, 2)
        self.assertEqual(turn.scan_bytes_used, 10)
        self.assertEqual(self.analyst.decide.call_args.kwargs["observations"][0]["code"], "invalid_sql")

    def test_dry_run_repair_receives_previous_sql_and_does_not_spend_scan_budget(self):
        bad_sql = REVENUE_SQL + " WHERE oi.created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 MONTH)"
        self.analyst.decide.side_effect = [query(bad_sql), query(), answer()]
        self.service.execute.side_effect = [
            QueryServiceError("query_rejected", "Use DATE_SUB for calendar months.", stage="dry_run"),
            result(),
        ]
        turn = self.agent.ask("Did revenue change last month?")
        self.assertEqual(turn.queries, 2)
        self.assertEqual(turn.scan_bytes_used, 10)
        observation = self.analyst.decide.call_args.kwargs["observations"][0]
        self.assertEqual(observation["attempted_sql"], bad_sql)
        self.assertIn("DATE_SUB", observation["message"])
        self.assertNotIn(bad_sql, repr(self.logger.mock_calls))

    def test_two_consecutive_errors_stop_loop(self):
        self.analyst.decide.return_value = query()
        self.service.execute.side_effect = QueryServiceError("query_rejected", "Please revise the query.")
        turn = self.agent.ask("Revenue?")
        self.assertEqual(turn.queries, 2)
        self.assertIn("stopped", turn.answer)

    def test_empty_result_can_trigger_new_query(self):
        self.analyst.decide.side_effect = [query(), query(), answer()]
        self.service.execute.side_effect = [result(()), result()]
        turn = self.agent.ask("Revenue?")
        self.assertEqual(turn.queries, 2)
        self.assertEqual(self.analyst.decide.call_args.kwargs["observations"][0]["status"], "empty")

    def test_max_queries_stops_model_that_keeps_querying(self):
        self.agent = AnalyticalAgent(self.analyst, self.service, manager_id="manager_a", limits=AgentLimits(max_queries=2))
        self.analyst.decide.return_value = query()
        turn = self.agent.ask("Revenue?")
        self.assertEqual(turn.queries, 2)
        self.assertEqual(self.service.execute.call_count, 2)
        self.assertIn("budget", turn.answer)

    def test_max_model_calls_is_enforced(self):
        self.agent = AnalyticalAgent(self.analyst, self.service, manager_id="manager_a", limits=AgentLimits(max_model_calls=1))
        self.analyst.decide.return_value = query()
        turn = self.agent.ask("Revenue?")
        self.assertEqual(turn.model_calls, 1)
        self.assertEqual(turn.queries, 1)

    def test_question_budget_is_forwarded_and_exhaustion_stops_queries(self):
        self.agent = AnalyticalAgent(self.analyst, self.service, manager_id="manager_a", limits=AgentLimits(max_scan_bytes=10))
        self.analyst.decide.return_value = query()
        turn = self.agent.ask("Revenue?")
        self.assertEqual(turn.queries, 1)
        self.assertEqual(self.service.execute.call_args.kwargs["scan_budget_bytes"], 10)

    def test_timeout_is_not_automatically_replayed(self):
        self.analyst.decide.return_value = query()
        self.service.execute.side_effect = QueryServiceError("timeout", "Query timed out.")
        turn = self.agent.ask("Revenue?")
        self.assertEqual(turn.queries, 1)
        self.assertEqual(turn.scan_bytes_used, 1000)
        self.service.execute.assert_called_once()

    def test_backend_down_returns_controlled_answer(self):
        self.analyst.decide.return_value = query()
        self.service.execute.side_effect = QueryServiceError("backend_unavailable", "Unavailable.")
        turn = self.agent.ask("Revenue?")
        self.assertIn("could not fully", turn.answer)
        self.assertEqual(turn.queries, 1)

    def test_model_down_does_not_escape(self):
        self.analyst.decide.side_effect = RuntimeError("SECRET backend content")
        turn = self.agent.ask("Revenue?")
        self.assertIn("model is unavailable", turn.answer)
        self.assertNotIn("SECRET", turn.answer)
        self.assertNotIn("SECRET", repr(self.logger.mock_calls))

    def test_provider_reason_is_visible_in_cli_answer_and_safe_logs(self):
        self.analyst.decide.side_effect = genai_errors.ServerError(503, {"error": {"message": "SECRET response body"}})
        turn = self.agent.ask("Revenue?")
        self.assertIn("[model_unavailable]", turn.answer)
        self.assertIn("HTTP 503", turn.answer)
        emitted = repr(self.logger.mock_calls)
        self.assertIn("http_status", emitted)
        self.assertIn("503", emitted)
        self.assertIn("ServerError", emitted)
        self.assertNotIn("SECRET", turn.answer + emitted)

    def test_invalid_response_is_not_reported_as_provider_overload(self):
        try:
            AgentAction.model_validate_json('{"action":"SECRET"}')
        except ValueError as error:
            self.analyst.decide.side_effect = error
        turn = self.agent.ask("Revenue?")
        self.assertIn("[model_invalid_action]", turn.answer)
        self.assertNotIn("overloaded", turn.answer)
        self.assertNotIn("SECRET", turn.answer + repr(self.logger.mock_calls))

    def test_unsafe_result_is_not_sent_back_to_model(self):
        self.analyst.decide.return_value = query()
        self.service.execute.return_value = result(({"revenue": "secret@example.com"},))
        turn = self.agent.ask("Revenue?")
        self.assertEqual(self.analyst.decide.call_count, 1)
        self.assertNotIn("secret@example.com", turn.answer)
        self.assertNotIn("secret@example.com", json.dumps(self.agent.history))

    def test_unexpected_result_column_is_withheld(self):
        self.analyst.decide.return_value = query()
        self.service.execute.return_value = result(({"email": "secret@example.com"},))
        self.agent.ask("Revenue?")
        self.assertEqual(self.analyst.decide.call_count, 1)

    def test_direct_contact_details_are_rejected_before_model_call(self):
        turn = self.agent.ask("Find secret@example.com")
        self.assertIn("remove personal", turn.answer)
        self.analyst.decide.assert_not_called()
        self.assertEqual(self.agent.history, [])

    def test_bad_final_text_is_not_displayed(self):
        self.analyst.decide.return_value = answer("Contact secret@example.com")
        self.assertNotIn("secret@example.com", self.agent.ask("Question").answer)

    def test_oversized_and_empty_questions_do_not_call_model(self):
        self.agent.ask(" ")
        self.agent.ask("x" * 5000)
        self.analyst.decide.assert_not_called()

    def test_history_is_bounded(self):
        self.agent = AnalyticalAgent(self.analyst, self.service, manager_id="manager_a", limits=AgentLimits(max_history_turns=2))
        self.analyst.decide.return_value = answer("Brief answer")
        for question in ("One", "Two", "Three"):
            self.agent.ask(question)
        self.assertEqual([turn["question"] for turn in self.agent.history], ["Two", "Three"])

    def test_external_langsmith_tracing_is_disabled_even_if_environment_enables_it(self):
        def decide(**kwargs):
            self.assertIs(get_tracing_context()["enabled"], False)
            return answer("Schema information")

        self.analyst.decide.side_effect = decide
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true"}):
            self.agent.ask("What data is available?")

    def test_action_shape_is_validated(self):
        for data in (
            {"action": "query"}, {"action": "answer"},
            {"action": "query", "sql": "SELECT 1", "answer": "both"},
            {"action": "delete", "answer": "delete"},
            {"action": "query", "sql": "SELECT 1", "manager_id": "other"},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                AgentAction(**data)


if __name__ == "__main__":
    unittest.main()
