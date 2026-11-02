"""Opt-in Langfuse tracing. Payload redaction is deferred; demo inputs only."""

import logging
import os
from contextlib import nullcontext


logger = logging.getLogger(__name__)
_client = None


def get_callbacks() -> list:
    """Initialize only on opt-in, never fall back to a cloud endpoint."""
    global _client
    if os.getenv("LANGFUSE_TRACING_ENABLED", "false").lower() != "true":
        return []
    if not all(os.getenv(key) for key in (
        "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL",
    )):
        logger.warning("tracing_unavailable", extra={"error_code": "tracing_configuration"})
        return []
    try:
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler

        _client = get_client()
        return [CallbackHandler()]
    except Exception:
        logger.warning("tracing_unavailable", extra={"error_code": "tracing_initialization"})
        return []


def trace_generation(model: str, prompt: str, system_instruction: str | None):
    if _client is None or os.getenv("LANGFUSE_TRACING_ENABLED", "false").lower() != "true":
        return nullcontext(None)
    return _client.start_as_current_observation(
        as_type="generation", name="Gemini", model=model,
        input={"prompt": prompt, "system_instruction": system_instruction},
    )


def shutdown_tracing() -> None:
    global _client
    if _client is not None:
        try:
            _client.shutdown()
        except Exception:
            logger.warning("tracing_unavailable", extra={"error_code": "tracing_shutdown"})
        finally:
            _client = None
