import json
import sqlite3
import unittest
from unittest.mock import Mock

from sqlglot import exp, parse_one

from retail_agent.agent.models import AgentAction
from retail_agent.agent.orchestrator import AnalyticalAgent
from retail_agent.data.bigquery_runner import BigQueryRunner, QueryExecution
from retail_agent.data.demographics import COHORT_COLUMN, DemographicRequest, build_demographic_query
from retail_agent.data.query_service import QueryLimits, QueryResult, QueryService
from retail_agent.llm.analyst import Analyst
from retail_agent.llm.client import GeminiClient
from retail_agent.safety.result_guard import ResultGuardError, make_observation
from retail_agent.safety.sql_validator import validate_sql
from retail_agent.schema.catalog import TABLE_POLICIES
from retail_agent.security.query_scope import apply_brand_scope


def request(**kwargs):
    return DemographicRequest(**kwargs)


class DemographicTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        # SQLite lacks DATE_TRUNC; only the monthly fixture needs this shim.
        self.db.create_function("DATE_TRUNC", 2, lambda unit, value: value[:7] + "-01" if unit == "MONTH" and value else None)
        for table, columns in TABLE_POLICIES.items():
            fields = ", ".join(f'"{name}"' for name in columns)
            self.db.execute(f'CREATE TABLE "{table}" ({fields})')
        self.db.executemany("INSERT INTO products (id, brand, name, cost) VALUES (?, ?, ?, ?)", [
            (1, "Brand A", "Visible product", 2), (2, "Brand C", "Other product", 3),
        ])
        self.db.executemany("INSERT INTO users (id, age, state) VALUES (?, ?, ?)", [
            *[(i, 24, "CA") for i in range(1, 7)], (7, 24, "NY"), (8, 25, "NY"), (9, 24, "TX"),
        ])
        items = [(i, i, 1, 10, "Complete") for i in range(1, 9)]
        items += [(10 + i, 1, 1, 10, "Complete") for i in range(4)]
        items += [(20 + i, 7, 1, 1000, "Complete") for i in range(5)]
        items += [(40 + i, 9, 2, 10, "Complete") for i in range(20)]
        self.db.executemany("INSERT INTO order_items (id, user_id, product_id, sale_price, status) VALUES (?, ?, ?, ?, ?)", items)

    def execute_sqlite(self, sql, brands=("Brand A",)):
        # Test-only dialect/parameter adaptation. Production binds parameters.
        tree = parse_one(sql, read="bigquery")
        for predicate in list(tree.find_all(exp.In)):
            if predicate.args.get("unnest") is not None:
                predicate.replace(exp.In(this=predicate.this.copy(), expressions=[exp.Literal.string(b) for b in brands]))
        for table in tree.find_all(exp.Table):
            table.set("catalog", None)
            table.set("db", None)
        cursor = self.db.execute(tree.sql(dialect="sqlite"))
        names = [column[0] for column in cursor.description]
        return tuple(dict(zip(names, row)) for row in cursor.fetchall())

    def execute(self, spec, minimum=5):
        sql, columns, context = build_demographic_query(spec, minimum)
        rows = self.execute_sqlite(sql)
        result = QueryResult("query-id", rows, False, 1, 1, 1, minimum, columns, context)
        return result, make_observation("", result, max_chars=12000)

    def test_exact_age_and_range_count_distinct_authorized_buyers(self):
        for filters, expected in (({"age_from": 24, "age_to": 24}, 7), ({"age_from": 20, "age_to": 30}, 8)):
            with self.subTest(filters=filters):
                result, observation = self.execute(request(filters=filters))
                self.assertEqual(observation["rows"], [{"customer_count": expected}])
                self.assertEqual(result.rows[0][COHORT_COLUMN], expected)
                self.assertNotIn(COHORT_COLUMN, json.dumps(observation))

    def test_suppression_precedes_ranking_and_limit(self):
        _, observation = self.execute(request(group_by=["state"], metrics=["revenue"], order_by="revenue", limit=1))
        self.assertEqual(observation["rows"], [{"state": "CA", "revenue": 100}])

    def test_many_purchases_by_one_buyer_do_not_create_a_safe_cohort(self):
        _, observation = self.execute(request(filters={"age_from": 24, "age_to": 24, "state": "NY"}))
        self.assertEqual(observation["rows"], [])
        self.assertIn("do not conclude zero", observation["privacy_note"])

    def test_nullable_average_does_not_publish_one_person_in_a_large_group(self):
        self.db.execute("UPDATE users SET age = NULL WHERE id <= 5")
        _, observation = self.execute(request(metrics=["average_age"]))
        self.assertEqual(observation["rows"], [])

    def test_average_age_counts_each_customer_once(self):
        _, observation = self.execute(request(metrics=["average_age"]))
        self.assertEqual(observation["rows"][0]["average_age"], 24.125)
        self.assertIn("distinct buyers", observation["query_context"]["age_average_basis"])

    def test_threshold_is_configurable(self):
        _, observation = self.execute(request(group_by=["state"], order_by="customer_count"), minimum=2)
        self.assertEqual([row["state"] for row in observation["rows"]], ["CA", "NY"])

    def test_month_and_status_dimensions_use_trusted_expressions(self):
        self.db.execute("UPDATE order_items SET created_at = '2026-01-01'")
        _, observation = self.execute(request(group_by=["month", "status"], filters={"age_from": 24, "age_to": 24, "status": "all"}))
        self.assertEqual(observation["rows"], [{"month": "2026-01-01", "status": "Complete", "customer_count": 7}])

    def test_product_name_grouping_also_uses_product_id(self):
        _, observation = self.execute(request(group_by=["product_name"], filters={"age_from": 24, "age_to": 24}, metrics=["revenue"]))
        self.assertEqual(observation["rows"][0]["product_id"], 1)

    def test_filter_strings_are_literals_not_sql_fragments(self):
        _, observation = self.execute(request(filters={"age_from": 24, "brand": "Brand A' OR TRUE --"}))
        self.assertEqual(observation["rows"], [])

    def test_non_demographic_product_revenue_has_no_threshold(self):
        sql = "SELECT SUM(oi.sale_price) AS revenue FROM `bigquery-public-data.thelook_ecommerce.order_items` AS oi"
        rows = self.execute_sqlite(apply_brand_scope(sql), brands=("Brand C",))
        self.assertEqual(rows, ({"revenue": 200},))  # One buyer; no demographics.

    def test_request_cannot_express_customer_ids_or_arbitrary_expressions(self):
        cases = [
            {"group_by": ["user_id"]}, {"group_by": ["email"]},
            {"filters": {"age_from": 24, "user_id": 1}},
            {"filters": {"age_from": 30, "age_to": 20}},
            {"filters": {"age_from": 24}, "metrics": ["MIN(u.age)"]},
            {"group_by": ["state"], "sql": "SELECT 1"},
            {"group_by": ["state"], "order_by": "revenue DESC; DELETE FROM x"},
            {"group_by": ["state"], "limit": 10000}, {"group_by": ["state", "state"]},
            {"group_by": ["brand"], "metrics": ["revenue"]},  # Use ordinary SQL.
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                request(**data)

    def test_free_form_sql_cannot_access_demographics_even_through_ctes(self):
        for sql in (
            "SELECT COUNT(*) AS n FROM `bigquery-public-data.thelook_ecommerce.users` u WHERE u.age=24",
            "WITH x AS (SELECT u.age AS a FROM `bigquery-public-data.thelook_ecommerce.users` u) SELECT AVG(a) AS n FROM x",
            "SELECT u.email FROM `bigquery-public-data.thelook_ecommerce.users` u",
        ):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_sql(sql)

    def test_second_guard_hides_small_values_even_if_runner_misbehaves(self):
        result = QueryResult("id", ({"state": "DO_NOT_PUBLISH", "revenue": 999, COHORT_COLUMN: 1},), False, 1, 1, 1, 5, ("state", "revenue"))
        observation = make_observation("", result, max_chars=12000)
        self.assertNotIn("DO_NOT_PUBLISH", json.dumps(observation))
        self.assertEqual(observation["rows"], [])

    def test_invalid_cohort_counts_fail_closed(self):
        for count in (None, True, "10", -1):
            with self.subTest(count=count), self.assertRaises(ResultGuardError):
                result = QueryResult("id", ({"customer_count": 10, COHORT_COLUMN: count},), False, 1, 1, 1, 5, ("customer_count",))
                make_observation("", result, max_chars=1000)

    def test_full_graph_uses_structured_tool_and_never_sends_suppressed_values(self):
        llm = Mock(spec=GeminiClient)
        llm.last_usage = {}
        llm.generate_structured.side_effect = [
            AgentAction(action="demographic", demographic=request(group_by=["state"], metrics=["revenue"])),
            AgentAction(action="answer", answer="There are no publishable groups."),
        ]
        runner = Mock(spec=BigQueryRunner)
        runner.dry_run.return_value = 10
        runner.execute_scoped_query.return_value = QueryExecution(
            ({"state": "DO_NOT_PUBLISH", "revenue": 999, COHORT_COLUMN: 1},), False, 10, 10,
        )
        service = QueryService(runner, QueryLimits(minimum_cohort_size=6))
        agent = AnalyticalAgent(Analyst(llm, {}), service, manager_id="manager_a")
        turn = agent.ask("Which states have the most revenue?")
        prompt = llm.generate_structured.call_args.args[0]
        self.assertNotIn("DO_NOT_PUBLISH", prompt + turn.answer)
        self.assertNotIn(COHORT_COLUMN, prompt)
        self.assertIn("suppressed small group", prompt)
        self.assertIn(">= 6", runner.execute_scoped_query.call_args.args[0])
        self.assertEqual(runner.execute_scoped_query.call_args.kwargs["allowed_brands"], ("Brand A", "Brand B"))


if __name__ == "__main__":
    unittest.main()
