import os
import re
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from retail_agent.agent.models import AgentAction
from retail_agent.agent.orchestrator import AnalyticalAgent
from retail_agent.data.query_service import QueryLimits, QueryResult
from retail_agent.memory.models import MAX_ENTRIES, MAX_ENTRY_CHARS, RememberRequest, UserMemoryError
from retail_agent.memory.repository import MemoryRepository
from retail_agent.memory.service import UserMemory, explicit_remember, memory_key
from retail_agent.settings import Settings
from retail_agent.storage.postgres import connection_factory
from demo_access import DEMO_ENTITLEMENTS


VECTOR = [1.0] + [0.0] * 767
SQL = "SELECT SUM(oi.sale_price) AS revenue FROM `bigquery-public-data.thelook_ecommerce.order_items` oi"


def remember(text="Prefers bullet points."):
    return AgentAction(action="remember", remember=RememberRequest(memories=[text]))


class MemoryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.repo = Mock(spec=MemoryRepository)
        self.repo.load.return_value = []
        self.repo.remember.return_value = 1
        self.embedder = Mock()
        self.embedder.model = "gemini-embedding-2"
        self.embedder.embed.return_value = VECTOR
        self.memory = UserMemory(self.repo, self.embedder, manager_id="manager_a")

    def test_explicit_intent_not_one_off_or_negated(self):
        for text in ("Use bullets, remember this.", "Запомни: предпочитаю списки."):
            self.assertTrue(explicit_remember(text))
        for text in ("This report in bullets.", "Do not remember this.", "Don't remember my name.", "Не запоминай это"):
            self.assertFalse(explicit_remember(text))
        with self.assertRaises(UserMemoryError):
            self.memory.remember(RememberRequest(memories=["Prefers bullets."]), question="This report in bullets", turn_id="a" * 32)
        self.repo.remember.assert_not_called()

    def test_embedding_per_new_string_and_owner_is_code_supplied(self):
        request = RememberRequest(memories=["Prefers bullet points.", "The analyst's name is Yerassyl."])
        self.memory.remember(request, question="Remember my name and preferred format", turn_id="a" * 32)
        self.assertEqual(self.embedder.embed.call_count, 2)
        self.assertEqual(self.repo.remember.call_args.args[0], "manager_a")
        self.assertEqual(len(self.repo.remember.call_args.args[1]), 2)
        self.assertEqual(self.repo.remember.call_args.args[2], "gemini-embedding-2")

    def test_exact_duplicates_are_not_embedded_or_saved_twice(self):
        self.repo.load.return_value = [{"memory_id": "a" * 32, "content": "Prefers bullet points."}]
        result = self.memory.remember(RememberRequest(memories=["  PREFERS   bullet points. "]),
                                      question="Remember my formatting", turn_id="a" * 32)
        self.assertTrue(result["saved"])
        self.assertEqual(result["count"], 0)
        self.embedder.embed.assert_not_called()
        self.repo.remember.assert_not_called()

    def test_duplicate_entries_in_one_request_have_one_embedding(self):
        self.memory.remember(RememberRequest(memories=["Prefers bullets.", "prefers bullets."]),
                             question="Remember this", turn_id="a" * 32)
        self.embedder.embed.assert_called_once()

    def test_all_embeddings_must_succeed_before_atomic_write(self):
        self.embedder.embed.side_effect = [VECTOR, RuntimeError("SECRET provider error")]
        with self.assertRaises(UserMemoryError) as error:
            self.memory.remember(RememberRequest(memories=["Prefers bullets.", "Prefers English."]),
                                 question="Remember these preferences", turn_id="a" * 32)
        self.assertNotIn("SECRET", str(error.exception))
        self.repo.remember.assert_not_called()

    def test_memory_cap_and_text_bounds(self):
        self.repo.load.return_value = [{"memory_id": uuidv, "content": f"Preference {i}"} for i, uuidv in enumerate([uuid4().hex for _ in range(20)])]
        with self.assertRaises(UserMemoryError):
            self.memory.remember(RememberRequest(memories=["New preference"]), question="Remember this", turn_id="a" * 32)
        self.embedder.embed.assert_not_called()
        for items in ([], [""], ["x" * 201], ["x"] * 6):
            with self.subTest(items=len(items)), self.assertRaises(ValueError):
                RememberRequest(memories=items)

    def test_contact_details_are_not_embedded(self):
        with self.assertRaises(UserMemoryError):
            self.memory.remember(RememberRequest(memories=["Email: secret@example.com"]),
                                 question="Remember this", turn_id="a" * 32)
        self.embedder.embed.assert_not_called()

    def test_read_and_forget_are_owner_scoped(self):
        self.memory.load()
        self.repo.load.assert_called_once_with("manager_a")
        self.repo.forget.return_value = 1
        self.memory.forget("b" * 32)
        self.repo.forget.assert_called_once_with("manager_a", "b" * 32)
        with self.assertRaises(UserMemoryError):
            self.memory.forget("arbitrary SQL")

    def test_action_cannot_smuggle_other_tools_or_owner(self):
        for payload in (
            {"action": "remember", "remember": {"memories": ["Prefers bullets."], "owner_id": "other"}},
            {"action": "query", "sql": "SELECT 1", "remember": {"memories": ["Prefers bullets."]}},
            {"action": "remember", "sql": "SELECT 1", "remember": {"memories": ["Prefers bullets."]}},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                AgentAction.model_validate(payload)


class MemoryLimitTests(unittest.TestCase):
    def test_repository_fetches_one_row_beyond_the_limit(self):
        conn = Mock()

        @contextmanager
        def connect():
            yield conn

        MemoryRepository(connect).load("owner")
        sql, params = conn.execute.call_args.args
        self.assertIn("LIMIT %s", sql)
        self.assertEqual(params, ("owner", MAX_ENTRIES + 1))

    def test_entry_length_matches_the_database_column(self):
        migration = next((Path(__file__).resolve().parents[1] / "docker/reports-postgres").glob("003_*.sql"))
        column = re.search(r"content VARCHAR\((\d+)\)", migration.read_text(encoding="utf-8"))
        self.assertEqual(int(column.group(1)), MAX_ENTRY_CHARS)


class MemoryGraphTests(unittest.TestCase):
    def setUp(self):
        self.analyst = Mock()
        self.analyst.llm = SimpleNamespace(last_usage={})
        self.queries = Mock()
        self.queries.limits = QueryLimits()
        self.queries.execute.return_value = QueryResult("id", ({"revenue": 30},), False, 1, 1, 1)
        self.memory = Mock(spec=UserMemory)
        self.memory.load.return_value = []
        self.memory.remember.return_value = {"status": "memory", "saved": True, "count": 1, "message": "Memory saved: 1 new entry."}
        self.agent = AnalyticalAgent(self.analyst, self.queries, manager_id="manager_a", entitlements=DEMO_ENTITLEMENTS, memory=self.memory)

    def test_both_orderings_complete_query_and_remember_in_one_turn(self):
        for ordering in ("remember_first", "query_first"):
            with self.subTest(ordering=ordering):
                self.setUp()
                actions = [remember(), AgentAction(action="query", sql=SQL)]
                if ordering == "query_first":
                    actions.reverse()
                self.analyst.decide.side_effect = [*actions, AgentAction(action="answer", answer="- Revenue is 30.")]
                result = self.agent.ask("Analyze revenue in bullet points, remember this format.")
                self.assertIn("Revenue is 30", result.answer)
                self.assertIn("Memory saved", result.answer)
                self.assertEqual(result.model_calls, 3)
                self.assertEqual(result.queries, 1)
                self.memory.remember.assert_called_once()

    def test_current_turn_and_next_question_receive_updated_memory(self):
        entries = [{"memory_id": "a" * 32, "text": "Prefers bullets."}]
        self.memory.load.side_effect = [[], entries, entries]
        self.analyst.decide.side_effect = [remember(), AgentAction(action="answer", answer="Noted."),
                                            AgentAction(action="answer", answer="- Hello.")]
        self.agent.ask("Remember bullets")
        self.assertEqual(self.analyst.decide.call_args.kwargs["user_memory"]["items"], entries)
        self.agent.reset()
        self.agent.ask("What do you know about my preferences?")
        self.assertEqual(self.analyst.decide.call_args.kwargs["user_memory"]["items"], entries)
        self.memory.forget.assert_not_called()

    def test_failed_memory_does_not_prevent_analysis(self):
        self.memory.remember.side_effect = UserMemoryError("Memory was not saved.")
        self.analyst.decide.side_effect = [remember(), AgentAction(action="query", sql=SQL),
                                            AgentAction(action="answer", answer="Revenue is 30.")]
        result = self.agent.ask("Analyze revenue and remember bullets")
        self.assertIn("Revenue is 30", result.answer)
        self.assertIn("not saved", result.answer)

    def test_unavailable_memory_does_not_crash_ordinary_analysis(self):
        self.memory.load.side_effect = UserMemoryError("Unavailable")
        self.analyst.decide.return_value = AgentAction(action="answer", answer="Available schema")
        self.assertEqual(self.agent.ask("What data exists?").answer, "Available schema")
        self.assertEqual(self.analyst.decide.call_args.kwargs["user_memory"]["status"], "unavailable")

    def test_repeated_remember_does_not_repeat_side_effects(self):
        self.analyst.decide.side_effect = [remember(), remember(), AgentAction(action="answer", answer="Done.")]
        self.agent.ask("Remember bullets")
        self.memory.remember.assert_called_once()

    def test_memory_commands_bypass_model_and_reset_does_not_forget(self):
        self.memory.forget.return_value = "Forgot 1 memory entries."
        self.assertEqual(self.agent.ask("/memories").answer, "No saved user memories.")
        self.agent.ask("/forget " + "a" * 32)
        self.memory.forget.assert_called_once_with("a" * 32)
        self.agent.reset()
        self.analyst.decide.assert_not_called()


@unittest.skipUnless(os.getenv("RETAIL_TEST_POSTGRES") == "1", "Opt-in local PostgreSQL memory test")
class MemoryPostgresTests(unittest.TestCase):
    def test_embeddings_exact_duplicates_owner_isolation_and_forget(self):
        repo = MemoryRepository(connection_factory(Settings()))
        owner, other = "memory_test_" + uuid4().hex, "memory_test_" + uuid4().hex
        entry = {"text": "Prefers bullets.", "key": memory_key("Prefers bullets."), "vector": VECTOR}
        try:
            self.assertEqual(repo.remember(owner, [entry], "gemini-embedding-2", uuid4().hex), 1)
            self.assertEqual(repo.remember(owner, [entry], "gemini-embedding-2", uuid4().hex), 0)
            self.assertEqual(repo.remember(other, [entry], "gemini-embedding-2", uuid4().hex), 1)
            own = repo.load(owner)
            self.assertEqual(len(own), 1)
            restarted = UserMemory(MemoryRepository(connection_factory(Settings())), Mock(), manager_id=owner)
            self.assertEqual(restarted.load()[0]["text"], "Prefers bullets.")
            self.assertEqual(repo.forget(other, own[0]["memory_id"]), 0)
            with repo.connection() as conn:
                size = conn.execute("SELECT vector_dims(embedding) AS dimensions FROM user_memories WHERE owner_id=%s", (owner,)).fetchone()
                self.assertEqual(size["dimensions"], 768)
            self.assertEqual(repo.forget(owner, own[0]["memory_id"]), 1)
            self.assertEqual(len(repo.load(other)), 1)
        finally:
            repo.forget(owner)
            repo.forget(other)


if __name__ == "__main__":
    unittest.main()
