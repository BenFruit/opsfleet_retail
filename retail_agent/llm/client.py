import os

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from typing import TypeVar
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class ModelResponseError(RuntimeError):
    """A controlled failure with no raw provider response attached."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def describe_model_error(error: Exception) -> tuple[str, str, dict]:
    """Classify failures without exposing provider bodies, keys, or input values."""
    metadata = {"error_type": type(error).__name__}
    if isinstance(error, ModelResponseError):
        return error.code, str(error), metadata
    if isinstance(error, genai_errors.APIError):
        status = error.code
        metadata["http_status"] = status
        # Inspect only to select fixed messages. Never return/log raw contents.
        provider_message = (error.message or "").lower()
        if status == 429:
            if any(marker in provider_message for marker in ("perday", "per_day", "per day", "daily")):
                code, message = "model_daily_quota", "Gemini daily quota is exhausted (HTTP 429). Check AI Studio quotas or use another enabled model."
            else:
                code, message = "model_rate_limited", "Gemini quota or rate limit was reached (HTTP 429). Check AI Studio quotas and try later."
        elif status in {500, 502, 503, 504}:
            code, message = "model_unavailable", f"Gemini is temporarily unavailable or overloaded (HTTP {status}). Try again later."
        elif status in {401, 403}:
            code, message = "model_access_denied", f"Gemini denied access (HTTP {status}). Check the API key, project restrictions, and model access."
        elif status == 404:
            code, message = "model_not_found", "Gemini model or endpoint was not found (HTTP 404). Check GEMINI_MODEL and its API availability."
        elif status == 400:
            if any(marker in provider_message for marker in ("api_key_invalid", "api key not valid", "api key expired")):
                code, message = "model_invalid_key", "Gemini rejected the API key (HTTP 400). Check GEMINI_API_KEY."
            elif any(marker in provider_message for marker in ("schema", "additionalproperties", "response_mime_type", "responsemimetype")):
                code, message = "model_schema_rejected", "Gemini rejected the structured-output schema or configuration (HTTP 400). Check model support and the response schema."
            else:
                code, message = "model_request_rejected", "Gemini rejected the request (HTTP 400). Check model capabilities and request configuration."
        else:
            code, message = "model_api_error", f"Gemini API returned HTTP {status}. Check model/API configuration."

        response = error.response
        if response is not None:
            retry_after = response.headers.get("Retry-After", "")
            if isinstance(retry_after, str) and retry_after.isascii() and retry_after.isdecimal() and len(retry_after) <= 5:
                seconds = min(int(retry_after), 3600)
                metadata["retry_after_seconds"] = seconds
                message += f" Retry after approximately {seconds} seconds."
        return code, message, metadata
    if isinstance(error, ValidationError):
        issues = error.errors(include_input=False, include_context=False, include_url=False)
        # Field names/types only. Never include input, Pydantic messages, or ctx.
        metadata["validation_fields"] = sorted({
            str(part) if part in {"action", "sql", "answer"} else "<other>"
            for issue in issues[:5] for part in issue["loc"]
        })
        metadata["validation_error_count"] = len(issues)
        if any(issue["type"] == "json_invalid" for issue in issues):
            return "model_invalid_json", "Gemini returned invalid or incomplete JSON, not a usable action.", metadata
        return "model_invalid_action", "Gemini returned a structured response that does not match the required tool/answer format.", metadata
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "model_timeout", "The Gemini request timed out. Try again later or use a faster enabled model.", metadata
    if isinstance(error, httpx.TransportError):
        return "model_connection_failed", "Could not connect to Gemini. Check the network or proxy and try again.", metadata
    if isinstance(error, (ValueError, TypeError)):
        return "model_configuration_error", "The model call failed local SDK/request validation. Check SDK compatibility and response-schema configuration.", metadata
    return "model_failed", "The model is unavailable or an unexpected local error occurred. See the safe error type in logs/agent.jsonl.", metadata


def _response_text(response) -> str:
    candidates = getattr(response, "candidates", None) or []
    finish = getattr(candidates[0], "finish_reason", None) if candidates else None
    finish = getattr(finish, "value", finish)
    if finish == "MAX_TOKENS":
        raise ModelResponseError("model_output_limit", "Gemini reached its output-token limit before completing the response.")
    if finish in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION"}:
        raise ModelResponseError("model_response_blocked", "Gemini withheld the response under its safety/content policy.")
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None)
    if block_reason and getattr(block_reason, "value", block_reason) != "BLOCKED_REASON_UNSPECIFIED":
        raise ModelResponseError("model_response_blocked", "Gemini blocked the request under its safety/content policy.")
    if not response.text:
        raise ModelResponseError("model_empty_response", "Gemini returned no text. This is different from an API overload or quota error.")
    return response.text


class GeminiClient:
    def __init__(self, model: str | None = None):
        load_dotenv()

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured")

        self.model = model or os.getenv("GEMINI_MODEL")
        if not self.model:
            raise ValueError("Set GEMINI_MODEL to a model ID enabled in your AI Studio project")
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=30_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        self.last_usage: dict[str, int] = {}

    def generate(self, prompt: str) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=2048,
            ),
        )

        return _response_text(response)

    def generate_structured(
        self, prompt: str, response_schema: type[T], *, system_instruction: str | None = None,
    ) -> T:
        self.last_usage = {}
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                system_instruction=system_instruction,
                max_output_tokens=4096,
                response_mime_type="application/json",
                # Pass JSON Schema as-is. The typed response_schema conversion
                # maps additionalProperties into additional_properties, which
                # this Gemini endpoint rejects. Local Pydantic validation stays strict.
                response_json_schema=response_schema.model_json_schema(),
            ),
        )

        usage = response.usage_metadata
        if usage is not None:
            self.last_usage = {
                "input_tokens": usage.prompt_token_count or 0,
                "output_tokens": usage.candidates_token_count or 0,
                "total_tokens": usage.total_token_count or 0,
            }

        return response_schema.model_validate_json(_response_text(response))

    def close(self) -> None:
        self.client.close()
