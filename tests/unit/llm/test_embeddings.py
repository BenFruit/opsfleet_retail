import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from retail_agent.llm.embeddings import Embedder, EmbeddingError, vector_literal


VECTOR = [1.0] + [0.0] * 767


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
