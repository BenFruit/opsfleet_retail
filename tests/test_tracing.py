import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from retail_agent.observability import tracing


class TracingTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(tracing, "_client", None))
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def test_disabled_by_default(self):
        self.assertEqual(tracing.get_callbacks(), [])
        with tracing.trace_generation("model", "prompt", None) as observation:
            self.assertIsNone(observation)

    def test_missing_configuration_does_not_initialize_or_use_cloud(self):
        os.environ["LANGFUSE_TRACING_ENABLED"] = "true"
        with patch("langfuse.get_client") as get_client:
            self.assertEqual(tracing.get_callbacks(), [])
            get_client.assert_not_called()

    def test_callback_generation_and_shutdown(self):
        os.environ.update({
            "LANGFUSE_TRACING_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "pk-test",
            "LANGFUSE_SECRET_KEY": "sk-test",
            "LANGFUSE_BASE_URL": "http://localhost:3000",
        })
        client = Mock()
        callback = Mock()
        with patch("langfuse.get_client", return_value=client):
            with patch("langfuse.langchain.CallbackHandler", return_value=callback):
                self.assertEqual(tracing.get_callbacks(), [callback])
        tracing.trace_generation("test-model", "question", "instructions")
        client.start_as_current_observation.assert_called_once_with(
            as_type="generation", name="Gemini", model="test-model",
            input={"prompt": "question", "system_instruction": "instructions"},
        )
        tracing.shutdown_tracing()
        client.shutdown.assert_called_once()
        self.assertIsNone(tracing._client)

    def test_shutdown_failure_does_not_crash_cli(self):
        client = Mock()
        client.shutdown.side_effect = RuntimeError("sensitive endpoint error")
        tracing._client = client
        with patch.object(tracing, "logger") as logger:
            tracing.shutdown_tracing()
        self.assertNotIn("sensitive endpoint error", str(logger.mock_calls))
        self.assertIsNone(tracing._client)

    def test_real_langfuse_callback_nests_generation_in_graph_without_network(self):
        from langfuse import Langfuse
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        from retail_agent.agent.models import AgentAction
        from retail_agent.agent.orchestrator import AnalyticalAgent
        from retail_agent.data.query_service import QueryLimits

        os.environ.update({
            "LANGFUSE_TRACING_ENABLED": "true",
            "LANGFUSE_PUBLIC_KEY": "pk-offline-span-test",
            "LANGFUSE_SECRET_KEY": "sk-offline-span-test",
            "LANGFUSE_BASE_URL": "http://localhost:3000",
            "LANGSMITH_TRACING": "true",  # Must not enable a second exporter.
        })
        exporter = InMemorySpanExporter()
        client = Langfuse(span_exporter=exporter)
        self.addCleanup(tracing.shutdown_tracing)
        self.addCleanup(client.shutdown)

        analyst = Mock()
        analyst.llm = SimpleNamespace(last_usage={})

        def decide(**kwargs):
            with tracing.trace_generation("offline-model", "demo question", "instructions") as span:
                span.update(output={"action": "answer"}, usage_details={"input": 10, "output": 5})
            return AgentAction(action="answer", answer="Demo answer")

        analyst.decide.side_effect = decide
        service = Mock()
        service.limits = QueryLimits()
        agent = AnalyticalAgent(analyst, service, manager_id="manager_a")
        self.assertEqual(agent.ask("Describe available data").answer, "Demo answer")
        client.flush()
        spans = {span.name: span for span in exporter.get_finished_spans()}
        self.assertIn("retail_question", spans)
        self.assertIn("plan", spans)
        self.assertIn("Gemini", spans)
        self.assertEqual(spans["Gemini"].context.trace_id, spans["retail_question"].context.trace_id)
        self.assertEqual(spans["Gemini"].parent.span_id, spans["plan"].context.span_id)
