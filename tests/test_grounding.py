import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from retail_agent.agent.models import AgentAction
from retail_agent.agent.orchestrator import AnalyticalAgent
from retail_agent.data.query_service import QueryLimits, QueryResult, QueryService
from retail_agent.llm.analyst import Analyst
from retail_agent.safety.grounding import figures, ungrounded_figures
from demo_access import DEMO_ENTITLEMENTS


SQL = ("SELECT p.category AS category, SUM(oi.sale_price) AS revenue FROM "
       "`bigquery-public-data.thelook_ecommerce.order_items` AS oi JOIN "
       "`bigquery-public-data.thelook_ecommerce.products` AS p ON oi.product_id = p.id GROUP BY category")
TOTAL_SQL = "SELECT SUM(oi.sale_price) AS revenue FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi"
ROWS = ({"category": "Suits", "revenue": 3403.2}, {"category": "Outerwear", "revenue": 2420.77},
        {"category": "Sweaters", "revenue": "1351.06"})
OBS = [{"status": "ok", "rows": list(ROWS)}]


class GroundingTests(unittest.TestCase):
    def test_checked_values_sums_and_differences_are_grounded(self):
        text = "Suits earned $3,403.20, Outerwear $2,420.77; together $5,823.97, a gap of $982.43."
        self.assertEqual(ungrounded_figures(text, OBS), [])

    def test_mental_arithmetic_over_many_rows_is_flagged(self):
        # 3403.20 + 2420.77 + 1351.06 = 7175.03 is a three-row total: must come from SQL.
        self.assertEqual(ungrounded_figures("Quarter revenue was $7,175.03 from 172 orders.", OBS), ["$7,175.03", "172"])

    def test_rounding_dates_years_percent_ranks_and_ids_are_ignored(self):
        text = ("Revenue about $3,403 (Q3 2026, 2026-07-01 to 2026-09-30) grew 33.1% for the top 5 "
                "categories; win back within 60 days. Method: product-comparison v2")
        self.assertEqual(ungrounded_figures(text, OBS), [])
        self.assertEqual([raw for raw, _, _ in figures("99.57% and 99.57 percent")], [])

    def test_numbers_the_user_stated_and_no_evidence_are_not_flagged(self):
        self.assertEqual(ungrounded_figures("You asked for the top 250 customers.", OBS, context="top 250 customers"), [])
        self.assertEqual(ungrounded_figures("Revenue was $9,999.99", []), [])


class AgentGroundingTests(unittest.TestCase):
    def setUp(self):
        self.analyst = Mock(spec=Analyst)
        self.analyst.llm = SimpleNamespace(last_usage={})
        self.service = Mock(spec=QueryService)
        self.service.limits = QueryLimits(maximum_bytes_billed=1000)
        self.service.execute.side_effect = [
            QueryResult("q1", ROWS, False, 10, 10, 10, columns=("category", "revenue")),
            QueryResult("q2", ({"revenue": 7175.03},), False, 10, 10, 10, columns=("revenue",)),
        ]
        self.agent = AnalyticalAgent(self.analyst, self.service, manager_id="manager_a", entitlements=DEMO_ENTITLEMENTS)
        self.logger = self.enterContext(patch("retail_agent.agent.orchestrator.logger"))

    def test_ungrounded_total_triggers_one_sql_correction(self):
        self.analyst.decide.side_effect = [
            AgentAction(action="query", sql=SQL),
            AgentAction(action="answer", answer="Total revenue was $7,175.03."),
            AgentAction(action="query", sql=TOTAL_SQL),
            AgentAction(action="answer", answer="Total revenue was $7,175.03."),
        ]
        turn = self.agent.ask("Quarter revenue by category and total")
        feedback = self.analyst.decide.call_args_list[2].kwargs["observations"][-1]
        self.assertEqual(feedback["code"], "ungrounded_numbers")
        self.assertIn("$7,175.03", feedback["message"])
        self.assertNotIn("could not be matched", turn.answer)
        self.assertEqual(turn.queries, 2)
        self.assertIsNotNone(self.agent.last_answer)  # Feedback is not a failed query.

    def test_when_budget_is_spent_the_answer_is_delivered_with_a_note(self):
        self.agent.limits = type(self.agent.limits)(max_action_retries=1)
        self.analyst.decide.side_effect = [
            AgentAction(action="query", sql=SQL),
            AgentAction(action="answer", answer="Total revenue was $7,175.03."),
            AgentAction(action="answer", answer="Total revenue was $7,175.03 again."),
        ]
        turn = self.agent.ask("Quarter revenue by category and total")
        self.assertIn("could not be matched to the checked results", turn.answer)
        self.assertIn("$7,175.03", turn.answer)
        self.assertIn("| Suits |", turn.answer)  # Authoritative tables are still shown.


if __name__ == "__main__":
    unittest.main()
