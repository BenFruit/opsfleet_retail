import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from retail_agent.agent.models import AgentAction, AgentLimits
from retail_agent.agent.orchestrator import AnalyticalAgent
from retail_agent.data.query_service import QueryLimits
from retail_agent.llm.embeddings import Embedder, EmbeddingError, vector_literal
from retail_agent.reports.models import ReportError, ReportRequest
from retail_agent.reports.repository import ReportRepository
from retail_agent.presentation import render_reports
from retail_agent.reports.service import ReportLibrary
from retail_agent.safety.result_guard import ResultGuardError
from retail_agent.security.access import Entitlements
from retail_agent.settings import Settings
from retail_agent.storage.postgres import connection_factory
from demo_access import DEMO_ENTITLEMENTS


VECTOR = [1.0] + [0.0] * 767
ID = "a" * 32


def row(report_id=ID, **extra):
    return {"report_id": report_id, "title": "Brand A report", "conversation_id": "c" * 32,
            "created_at": datetime.now(timezone.utc), "content": "Checked report text", **extra}


class ReportPolicyTests(unittest.TestCase):
    def setUp(self):
        self.repo = Mock(spec=ReportRepository)
        self.embedder = Mock(spec=Embedder)
        self.embedder.model = "gemini-embedding-2"
        self.embedder.embed.return_value = VECTOR
        self.library = ReportLibrary(self.repo, self.embedder, manager_id="manager_a", entitlements=DEMO_ENTITLEMENTS)

    def execute(self, **request):
        return self.library.execute(ReportRequest(**{"query": "", **request}), conversation_id="c" * 32,
                                    last_answer="Original answer\n| revenue |\n| --- |\n| 30 |",
                                    last_brands=["Brand A", "Brand B"])

    def test_save_uses_snapshot_and_trusted_identity(self):
        result = self.execute(operation="save", title="October")
        values = self.repo.save.call_args.kwargs
        self.assertEqual(values["owner"], "manager_a")
        self.assertEqual(values["brands"], ["Brand A", "Brand B"])
        self.assertIn("| 30 |", values["content"])
        self.assertEqual(values["report_id"], result["report_id"])
        self.assertEqual(len(result["report_id"]), 32)

    def test_missing_answer_never_calls_embedding_or_db(self):
        with self.assertRaises(ReportError):
            self.library.execute(ReportRequest(operation="save", query=""), conversation_id="c" * 32,
                                 last_answer=None, last_brands=[])
        self.embedder.embed.assert_not_called()
        self.repo.save.assert_not_called()

    def test_changed_brands_prevent_saving_old_answer(self):
        with patch.object(self.library, "entitlements", Entitlements({"manager_a": ["Brand A"]})):
            with self.assertRaises(ReportError):
                self.execute(operation="save")
        self.repo.save.assert_not_called()

    def test_embedding_failure_never_saves(self):
        self.embedder.embed.side_effect = EmbeddingError("Embedding unavailable")
        with self.assertRaises(EmbeddingError):
            self.execute(operation="save")
        self.repo.save.assert_not_called()

    def test_sensitive_text_not_sent_to_embedding(self):
        with self.assertRaises(ResultGuardError):
            self.library.execute(ReportRequest(operation="save", query=""), conversation_id="c" * 32,
                                 last_answer="secret@example.com", last_brands=["Brand A"])
        self.embedder.embed.assert_not_called()

    def test_search_is_bounded_and_access_scoped(self):
        self.repo.search.return_value = [row(similarity=0.8, content="x" * 8000) for _ in range(10)]
        result = self.execute(operation="search", query="Brand A")
        self.assertLess(len(result["reports"]), 10)
        self.assertLessEqual(len(result["reports"][0]["excerpt"]), 1500)
        self.assertEqual(self.repo.search.call_args.kwargs["owner"], "manager_a")
        self.assertEqual(self.repo.search.call_args.kwargs["brands"], ["Brand A", "Brand B"])

    def test_open_inaccessible_has_no_content(self):
        self.repo.open.return_value = None
        with self.assertRaisesRegex(ReportError, "not found or not accessible"):
            self.execute(operation="open", report_id=ID)

    def test_deletion_is_preview_then_fixed_ids_confirmed_once(self):
        self.repo.preview_delete.return_value = [row()]
        result = self.execute(operation="prepare_delete", scope="conversation")
        self.assertEqual(result["operation"], "prepare_delete")
        self.repo.delete.assert_not_called()
        self.repo.delete.return_value = 1
        self.assertIn("Deleted 1", self.library.confirm())
        self.repo.delete.assert_called_once_with((ID,), owner="manager_a", brands=["Brand A", "Brand B"])
        self.assertIn("No active", self.library.confirm())
        self.repo.preview_delete.assert_called_once()

    def test_delete_rechecks_current_access(self):
        self.repo.preview_delete.return_value = [row()]
        self.execute(operation="prepare_delete", scope="all")
        with patch.object(self.library, "entitlements", Entitlements({"manager_a": ["Brand A"]})):
            self.library.confirm()
        self.assertEqual(self.repo.delete.call_args.kwargs["brands"], ["Brand A"])

    def test_cancel_and_expiry_never_delete(self):
        self.repo.preview_delete.return_value = [row()]
        self.execute(operation="prepare_delete", scope="all")
        self.library.cancel()
        self.library.confirm()
        self.execute(operation="prepare_delete", scope="all")
        with patch("retail_agent.reports.service.monotonic", return_value=float("inf")):
            self.library.confirm()
        self.repo.delete.assert_not_called()

    def test_oversized_preview_is_not_partially_deletable(self):
        self.repo.preview_delete.return_value = [row() for _ in range(101)]
        with self.assertRaises(ReportError):
            self.execute(operation="prepare_delete", scope="all")
        self.assertIsNone(self.library.pending)

    def test_no_unrestricted_request_fields_or_delete_action(self):
        cases = [
            {"operation": "delete"}, {"operation": "save", "owner_id": "manager_b"},
            {"operation": "save", "content": "replacement"},
            {"operation": "search", "query": ""},
            {"operation": "prepare_delete"}, {"operation": "prepare_delete", "scope": "id"},
            {"operation": "open", "report_id": "x; DROP TABLE saved_reports"},
            {"operation": "list", "query": "ignored filter"},
        ]
        for request in cases:
            with self.subTest(request=request), self.assertRaises(ValueError):
                ReportRequest(**{"query": "", **request})

    def test_structured_output_schema_requires_query_even_for_delete_preview(self):
        self.assertIn("query", ReportRequest.model_json_schema()["required"])

    def test_empty_library_does_not_mention_deletion(self):
        for operation in ("list", "search"):
            self.assertEqual(render_reports({"operation": operation, "reports": []}), "No reports found.")
        self.assertIn("Nothing was deleted", render_reports({"operation": "prepare_delete", "reports": []}))


class ReportGraphTests(unittest.TestCase):
    def setUp(self):
        self.analyst = Mock()
        self.analyst.llm = SimpleNamespace(last_usage={})
        self.queries = Mock()
        self.queries.limits = QueryLimits()
        self.repo = Mock(spec=ReportRepository)
        self.embedder = Mock(model="gemini-embedding-2")
        self.embedder.embed.return_value = VECTOR
        self.library = ReportLibrary(self.repo, self.embedder, manager_id="manager_a", entitlements=DEMO_ENTITLEMENTS)
        self.agent = AnalyticalAgent(self.analyst, self.queries, manager_id="manager_a", entitlements=DEMO_ENTITLEMENTS, reports=self.library)

    def test_save_preserves_last_answer_across_history_trimming_and_report_messages(self):
        self.analyst.decide.return_value = AgentAction(action="answer", answer="Checked narrative")
        self.agent.ask("Analyze")
        self.analyst.decide.return_value = AgentAction(action="report", report=ReportRequest(operation="save", query=""))
        self.agent.history.clear()
        self.agent.ask("Save")
        self.agent.ask("Save again")
        self.assertEqual(self.repo.save.call_args.kwargs["content"], "Checked narrative")
        self.assertEqual(self.repo.save.call_count, 2)
        self.queries.execute.assert_not_called()

    def test_invalid_save_action_is_repaired_twice_without_duplicate_writes(self):
        self.agent.last_answer = "Original checked answer"
        self.agent.last_answer_brands = ["Brand A"]
        try:
            AgentAction.model_validate_json('{"action":"report","report":{"operation":"save"}}')
        except ValueError as error:
            invalid = error
        self.analyst.decide.side_effect = [invalid, invalid,
            AgentAction(action="report", report=ReportRequest(operation="save", query=""))]
        result = self.agent.ask("Save this")
        self.assertIn("Saved report", result.answer)
        self.repo.save.assert_called_once()
        self.assertEqual(result.model_calls, 3)

    def test_confirm_bypasses_model_and_natural_yes_does_not_delete(self):
        self.repo.preview_delete.return_value = [row()]
        self.analyst.decide.return_value = AgentAction(action="report", report=ReportRequest(operation="prepare_delete", scope="all", query=""))
        self.assertIn("/confirm", self.agent.ask("Delete all my reports").answer)
        self.repo.delete.assert_not_called()
        self.analyst.decide.return_value = AgentAction(action="answer", answer="Please request a new preview.")
        self.agent.ask("yes")
        self.repo.delete.assert_not_called()
        self.assertIn("No active", self.agent.ask("/confirm").answer)
        self.analyst.decide.return_value = AgentAction(action="report", report=ReportRequest(operation="prepare_delete", scope="all", query=""))
        self.agent.ask("Delete all my reports")
        calls = self.analyst.decide.call_count
        self.repo.delete.return_value = 1
        self.assertIn("Deleted 1", self.agent.ask("/confirm").answer)
        self.assertEqual(self.analyst.decide.call_count, calls)

    def test_reset_changes_conversation_and_clears_snapshot_and_confirmation(self):
        previous = self.agent.conversation_id
        self.agent.last_answer = "Old"
        self.repo.preview_delete.return_value = [row()]
        self.analyst.decide.return_value = AgentAction(action="report", report=ReportRequest(operation="prepare_delete", scope="conversation", query=""))
        self.agent.ask("Delete conversation reports")
        self.agent.reset()
        self.assertNotEqual(previous, self.agent.conversation_id)
        self.assertIsNone(self.agent.last_answer)
        self.assertIsNone(self.library.pending)

    def test_report_search_is_model_observation_with_deterministic_sources(self):
        self.repo.search.return_value = [row(similarity=0.9)]
        self.analyst.decide.side_effect = [
            AgentAction(action="report", report=ReportRequest(operation="search", query="Brand A")),
            AgentAction(action="answer", answer="The report discusses Brand A."),
        ]
        result = self.agent.ask("Find reports about Brand A")
        self.assertIn(ID, result.answer)
        self.assertEqual(result.queries, 0)
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(self.analyst.decide.call_args.kwargs["observations"][0]["operation"], "search")

    def test_report_search_budget_stops_loop(self):
        self.agent.limits = AgentLimits(max_report_calls=1)
        self.repo.search.return_value = [row(similarity=0.9)]
        self.analyst.decide.return_value = AgentAction(action="report", report=ReportRequest(operation="search", query="Brand A"))
        self.assertIn("budget", self.agent.ask("Find reports").answer)
        self.repo.search.assert_called_once()

    def test_empty_search_returns_no_reports_without_another_model_call(self):
        self.repo.search.return_value = []
        self.analyst.decide.return_value = AgentAction(action="report", report=ReportRequest(operation="search", query="Nike"))
        turn = self.agent.ask("Find my reports about Nike")
        self.assertEqual(turn.answer, "No reports found.")
        self.assertEqual(turn.model_calls, 1)

    def test_db_failure_is_not_reported_as_success_and_does_not_overwrite_snapshot(self):
        self.agent.last_answer = "Original"
        self.agent.last_answer_brands = ["Brand A"]
        self.repo.save.side_effect = ReportError("Report storage is unavailable.")
        self.analyst.decide.return_value = AgentAction(action="report", report=ReportRequest(operation="save", query=""))
        self.assertIn("unavailable", self.agent.ask("Save").answer)
        self.assertEqual(self.agent.last_answer, "Original")

    def test_embedding_failure_is_a_controlled_answer(self):
        self.agent.last_answer = "Original"
        self.agent.last_answer_brands = ["Brand A"]
        self.embedder.embed.side_effect = EmbeddingError("Embedding service is unavailable.")
        self.analyst.decide.return_value = AgentAction(action="report", report=ReportRequest(operation="save", query=""))
        self.assertIn("Embedding service is unavailable", self.agent.ask("Save").answer)
        self.repo.save.assert_not_called()


class EmbeddingTests(unittest.TestCase):
    def test_vector_validation(self):
        for v in ([], [0.0] * 768, [float("nan")] * 768, [True] * 768):
            with self.subTest(v=str(v[:1])), self.assertRaises(EmbeddingError):
                vector_literal(v)

    def test_google_request_format_dimensions_and_size_guard(self):
        gemini = Mock()
        gemini.client.models.embed_content.return_value = SimpleNamespace(embeddings=[SimpleNamespace(values=VECTOR)])
        embedder = Embedder(gemini)
        self.assertEqual(embedder.embed("document", document=True, title="Title"), VECTOR)
        call = gemini.client.models.embed_content.call_args.kwargs
        self.assertEqual(call["contents"], "title: Title | text: document")
        self.assertEqual(call["config"].output_dimensionality, 768)
        embedder.embed("Nike", document=False)
        self.assertEqual(gemini.client.models.embed_content.call_args.kwargs["contents"], "task: search result | query: Nike")
        with self.assertRaises(EmbeddingError):
            embedder.embed("x" * 8001, document=True)


@unittest.skipUnless(os.getenv("RETAIL_TEST_POSTGRES") == "1", "Opt-in local pgvector integration test")
class ReportPostgresTests(unittest.TestCase):
    def test_real_storage_owner_scope_search_and_frozen_delete(self):
        repo = ReportRepository(connection_factory(Settings()))
        owner = "test_" + uuid4().hex
        brands = ["Demo Brand"]
        ids = [uuid4().hex for _ in range(3)]
        conversation = uuid4().hex
        def save(i, user=owner):
            repo.save(report_id=ids[i], owner=user, brands=brands, conversation_id=conversation,
                      title="Nike demo", content="A historical Nike report", vector=VECTOR, model="gemini-embedding-2")
        try:
            save(0)
            save(1, owner + "_other")
            self.assertIsNone(repo.open(ids[0], owner=owner + "_other", brands=brands))
            self.assertIsNone(repo.open(ids[0], owner=owner, brands=["Other Brand"]))
            found = repo.search(owner=owner, brands=brands, query="Nike", vector=VECTOR, model="gemini-embedding-2")
            self.assertEqual([r["report_id"] for r in found], [ids[0]])
            preview = repo.preview_delete(ReportRequest(operation="prepare_delete", query="Nike"),
                                          owner=owner, brands=brands, conversation_id=conversation)
            save(2)  # Arrived after preview; must survive confirmation.
            self.assertEqual(repo.delete(tuple(r["report_id"] for r in preview), owner=owner, brands=brands), 1)
            self.assertIsNotNone(repo.open(ids[2], owner=owner, brands=brands))
            self.assertEqual(repo.preview_delete(ReportRequest(operation="prepare_delete", query="' OR true --"),
                                                owner=owner, brands=brands, conversation_id=conversation), [])
        finally:
            repo.delete(tuple(ids), owner=owner, brands=brands)
            repo.delete(tuple(ids), owner=owner + "_other", brands=brands)


if __name__ == "__main__":
    unittest.main()
