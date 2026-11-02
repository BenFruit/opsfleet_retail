import math
import os

from google.genai import types

from retail_agent.observability.tracing import trace_generation
from retail_agent.reports.models import ReportError
from retail_agent.safety.result_guard import check_text


DIMENSIONS = 768


def vector_literal(values: list[float]) -> str:
    if len(values) != DIMENSIONS or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ReportError("Embedding has an invalid shape or nonfinite values.")
    if not any(values):
        raise ReportError("Embedding must not be a zero vector.")
    return "[" + ",".join(str(float(v)) for v in values) + "]"


class ReportEmbedder:
    def __init__(self, gemini_client):
        self.client = gemini_client.client
        self.model = os.getenv("REPORTS_EMBEDDING_MODEL", "gemini-embedding-2")
        if self.model != "gemini-embedding-2":
            raise ValueError("This MVP supports gemini-embedding-2 only; changing models needs reindexing")

    def embed(self, text: str, *, document: bool, title: str = "", observation_name: str = "report_embedding") -> list[float]:
        check_text(text)
        check_text(title)
        # Bound conservatively in UTF-8 bytes, never silently truncate a report.
        formatted = f"title: {title or 'none'} | text: {text}" if document else f"task: search result | query: {text}"
        if len(formatted.encode("utf-8")) > 8_000:
            raise ReportError("Report is too large to embed in this MVP. Request a shorter answer.")
        try:
            with trace_generation(self.model, formatted, None, name=observation_name) as span:
                result = self.client.models.embed_content(
                    model=self.model, contents=formatted,
                    config=types.EmbedContentConfig(output_dimensionality=DIMENSIONS),
                )
                if not result.embeddings or len(result.embeddings) != 1:
                    raise ReportError("Embedding service returned no usable vector.")
                values = list(result.embeddings[0].values or [])
                vector_literal(values)
                if span is not None:
                    span.update(output={"dimensions": DIMENSIONS})
                return values
        except ReportError:
            raise
        except Exception:
            raise ReportError("Embedding service is unavailable; the operation was not completed. Try again later.") from None
