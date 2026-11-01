"""Trusted, bounded query path. Results still require a pre-LLM result guard."""

import json
import logging
import math
from dataclasses import dataclass, field
from time import monotonic
from typing import Any
from uuid import uuid4

from google.api_core import exceptions as google_errors

from retail_agent.data.bigquery_runner import BigQueryRunner
from retail_agent.data.demographics import DemographicRequest, build_demographic_query
from retail_agent.security.access import get_allowed_brands
from retail_agent.security.query_scope import apply_brand_scope
from retail_agent.safety.sql_validator import SQLValidationError, validate_query


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryLimits:
    maximum_bytes_billed: int = 100_000_000
    max_rows: int = 200
    max_result_bytes: int = 256_000
    max_sql_chars: int = 20_000
    timeout_seconds: float = 30.0
    api_timeout_seconds: float = 10.0
    minimum_cohort_size: int = 5

    def __post_init__(self):
        if type(self.minimum_cohort_size) is not int or self.minimum_cohort_size < 2:
            raise ValueError("minimum_cohort_size must be an integer of at least 2")
        for name in ("maximum_bytes_billed", "max_rows", "max_result_bytes", "max_sql_chars"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("timeout_seconds", "api_timeout_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")


class QueryServiceError(Exception):
    """Stable, safe error contract; never includes SQL/backend exception text."""

    def __init__(self, code: str, message: str, *, stage: str = "unknown"):
        super().__init__(message)
        self.code = code
        self.stage = stage


@dataclass(frozen=True)
class QueryResult:
    """Internal data, NOT a promise that rows are safe to send to an LLM."""

    query_id: str
    rows: tuple[dict[str, Any], ...] = field(repr=False)
    truncated: bool
    estimated_bytes: int
    bytes_processed: int | None
    bytes_billed: int | None
    minimum_cohort_size: int | None = None
    columns: tuple[str, ...] = ()
    query_context: dict = field(default_factory=dict, repr=False)


class QueryService:
    def __init__(self, runner: BigQueryRunner, limits: QueryLimits | None = None):
        self.runner = runner
        self.limits = limits if limits is not None else QueryLimits()

    def execute(
        self, sql: str | None, *, manager_id: str, scan_budget_bytes: int | None = None,
        demographic: DemographicRequest | None = None,
    ) -> QueryResult:
        """Use identity supplied by the application, never identity from the LLM.

        Errors are safe for caller-controlled handling. No retries or model calls
        occur here. The orchestrator must enforce an additional per-session
        query/retry/cost budget. The limits below apply to ONE request only.
        """
        query_id = uuid4().hex
        started = monotonic()
        stage = "authorization"
        logger.info("query_started", extra={"query_id": query_id})
        try:
            byte_limit = self.limits.maximum_bytes_billed
            if scan_budget_bytes is not None:
                if type(scan_budget_bytes) is not int or scan_budget_bytes <= 0:
                    raise QueryServiceError("cost_limit", "The question scan budget is exhausted.")
                byte_limit = min(byte_limit, scan_budget_bytes)
            try:
                allowed_brands = tuple(sorted(get_allowed_brands(manager_id)))
            except PermissionError:
                raise QueryServiceError("access_denied", "No authorized brands for this user.") from None

            stage = "validation"
            if demographic is None and (not isinstance(sql, str) or len(sql) > self.limits.max_sql_chars):
                raise QueryServiceError("invalid_sql", "SQL is missing or exceeds the supported size.")
            try:
                if demographic is not None:
                    if sql:
                        raise ValueError("Choose exactly one tool")
                    scoped_sql, columns, context = build_demographic_query(demographic, self.limits.minimum_cohort_size)
                else:
                    tree = validate_query(sql)
                    columns = tuple(tree.named_selects)
                    context = {"tool": "sql", "executed_sql": tree.sql(dialect="bigquery")}
                    scoped_sql = apply_brand_scope(sql)
            except SQLValidationError as error:
                raise QueryServiceError("invalid_sql", f"SQL rejected: {error}") from None
            except (ValueError, RecursionError):
                message = "Invalid demographic request; check dimensions, metrics, filters and ranges." if demographic is not None else "SQL is invalid or outside the supported safety policy."
                raise QueryServiceError("invalid_sql", message) from None

            stage = "dry_run"
            estimated_bytes = self.runner.dry_run(
                scoped_sql,
                allowed_brands=allowed_brands,
                api_timeout_seconds=self.limits.api_timeout_seconds,
            )
            if type(estimated_bytes) is not int or estimated_bytes < 0:
                raise QueryServiceError("estimate_unavailable", "Could not verify the query scan estimate.")
            if estimated_bytes > byte_limit:
                raise QueryServiceError("cost_limit", "Query exceeds the configured scan budget.")
            logger.info("query_dry_run_completed", extra={
                "query_id": query_id, "estimated_bytes": estimated_bytes,
            })

            stage = "execution"
            execution = self.runner.execute_scoped_query(
                scoped_sql,
                allowed_brands=allowed_brands,
                maximum_bytes_billed=byte_limit,
                max_rows=self.limits.max_rows,
                timeout_seconds=self.limits.timeout_seconds,
                api_timeout_seconds=self.limits.api_timeout_seconds,
                query_id=query_id,
            )

            stage = "result_bounds"
            if len(execution.rows) > self.limits.max_rows:
                raise QueryServiceError("result_limit", "Query returned too many rows.")
            try:
                # BQ dates/decimals use their string form for this size estimate.
                size = len(json.dumps(execution.rows, default=str, ensure_ascii=False).encode("utf-8"))
            except (TypeError, ValueError, RecursionError):
                raise QueryServiceError("invalid_result", "Query result has an unsupported format.") from None
            if size > self.limits.max_result_bytes:
                raise QueryServiceError("result_limit", "Query result exceeds the configured response budget.")

            result = QueryResult(
                query_id=query_id, rows=execution.rows, truncated=execution.truncated,
                estimated_bytes=estimated_bytes, bytes_processed=execution.bytes_processed,
                bytes_billed=execution.bytes_billed,
                minimum_cohort_size=self.limits.minimum_cohort_size if demographic is not None else None,
                columns=columns, query_context=context,
            )
            logger.info("query_completed", extra={
                "query_id": query_id, "row_count": len(result.rows),
                "truncated": result.truncated, "result_bytes": size,
                "bytes_processed": result.bytes_processed, "bytes_billed": result.bytes_billed,
                "duration_ms": round((monotonic() - started) * 1000),
            })
            return result
        except QueryServiceError as error:
            error.stage = stage
            self._log_failure(
                query_id, stage, error.code, started,
                validation_reason=str(error) if stage == "validation" else None,
            )
            raise
        except Exception as error:
            code, message = self._safe_backend_error(error)
            self._log_failure(query_id, stage, code, started)
            raise QueryServiceError(code, message, stage=stage) from None

    def execute_demographic(
        self, request: DemographicRequest, *, manager_id: str, scan_budget_bytes: int | None = None,
    ) -> QueryResult:
        return self.execute(None, demographic=request, manager_id=manager_id, scan_budget_bytes=scan_budget_bytes)

    @staticmethod
    def _log_failure(
        query_id: str, stage: str, code: str, started: float,
        validation_reason: str | None = None,
    ) -> None:
        logger.warning("query_failed", extra={
            "query_id": query_id, "stage": stage, "error_code": code,
            "duration_ms": round((monotonic() - started) * 1000),
            "validation_reason": validation_reason,
        })

    @staticmethod
    def _safe_backend_error(error: Exception) -> tuple[str, str]:
        if isinstance(error, (TimeoutError, google_errors.DeadlineExceeded)):
            return "timeout", "Query timed out. Try a smaller scope."
        if isinstance(error, google_errors.BadRequest):
            text = (error.message or "").upper()
            if any(name in text for name in ("TIMESTAMP_SUB", "TIMESTAMP_ADD")) and "DOES NOT SUPPORT" in text:
                return "query_rejected", "BigQuery TIMESTAMP_SUB/ADD do not support calendar MONTH/QUARTER/YEAR arithmetic. Use DATE_SUB/DATE_ADD and compare DATE values for calendar periods."
            if "NO MATCHING SIGNATURE" in text and "TIMESTAMP" in text and "DATE" in text:
                return "query_rejected", "BigQuery date/time types do not match. Convert timestamps with DATE(...) when comparing them with DATE boundaries."
            return "query_rejected", "BigQuery rejected the query. It may need to be revised."
        if isinstance(error, google_errors.Forbidden):
            return "backend_denied", "BigQuery access or quota is unavailable."
        if isinstance(error, (google_errors.TooManyRequests, google_errors.ResourceExhausted)):
            return "rate_limited", "BigQuery quota or rate limit reached. Try again later."
        if isinstance(error, (google_errors.ServiceUnavailable, google_errors.InternalServerError)):
            return "backend_unavailable", "BigQuery is temporarily unavailable."
        return "query_failed", "Query could not be completed."
