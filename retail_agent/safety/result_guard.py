"""The last data boundary before observations reach Gemini."""

import json
import math
import re
from datetime import date, datetime, time
from decimal import Decimal

from retail_agent.safety.demographic_sql import COHORT_COLUMN
from retail_agent.data.query_service import QueryResult
from retail_agent.safety.sql_validator import SQLValidationError, validate_query


class ResultGuardError(ValueError):
    pass


_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_PHONE_CANDIDATE = re.compile(r"(?<!\w)\+\d[\d ()-]{6,}\d(?!\w)")
# Analytical prose such as "+12 (2023-09-01 - 2023-09-30)" or "+3 (2024 - 2023)"
# is not a phone number: exclude ISO dates and year ranges from candidates.
_DATE_LIKE = re.compile(
    r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?"
    r"|(?:19|20)\d{2}\s*-\s*(?:19|20)\d{2}"
)


def _looks_like_phone(candidate: str) -> bool:
    # E.164 numbers have at most 15 digits; real numbers have at least 10.
    digits = sum(character.isdigit() for character in candidate)
    return 10 <= digits <= 15 and not _DATE_LIKE.search(candidate)


def check_text(text: str) -> str:
    # Supplemental patterns, NOT comprehensive name/address/PII detection.
    if _EMAIL.search(text) or any(_looks_like_phone(match.group()) for match in _PHONE_CANDIDATE.finditer(text)):
        raise ResultGuardError("Text contains a potentially sensitive value")
    return text


def _scalar(value):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, (float, Decimal)):
        if not math.isfinite(value):
            raise ResultGuardError("Nonfinite numbers are unsupported")
        return str(value) if isinstance(value, Decimal) else value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, str):
        return check_text(value)
    raise ResultGuardError("Only scalar query results are supported")


def make_observation(sql: str, result: QueryResult, *, max_chars: int) -> dict:
    """Service-owned columns/context are not supplied by the model as proof."""
    minimum = result.minimum_cohort_size
    if minimum is not None and (type(minimum) is not int or minimum < 2 or not result.columns):
        raise ResultGuardError("Demographic results require trusted columns and cohort policy")
    try:
        columns = result.columns or tuple(validate_query(sql).named_selects)
    except SQLValidationError:
        raise ResultGuardError("Query output metadata cannot be verified") from None
    if len(columns) != len(set(columns)):
        raise ResultGuardError("Output column names must be unique")
    for name in columns:
        check_text(name)
    expected = set(columns) | ({COHORT_COLUMN} if minimum is not None else set())
    context = result.query_context
    check_text(json.dumps(context, ensure_ascii=False))
    observation = {"status": "empty", "query_id": result.query_id, "rows": [], "truncated": result.truncated, "query_context": context}
    if minimum is not None:
        observation["privacy_note"] = (
            f"Only groups with at least {minimum} distinct contributing buyers are publishable. "
            "Rankings cover publishable groups only. Empty output may mean no data OR a suppressed small group; do not conclude zero buyers."
        )
    if len(json.dumps(observation, ensure_ascii=False)) > max_chars:
        raise ResultGuardError("Observation metadata exceeds its budget")
    # Validate every row even if the observation budget only permits a prefix.
    full = False
    for row in result.rows:
        if set(row) != expected:
            raise ResultGuardError("Returned columns do not match the prepared query")
        if minimum is not None:
            count = row[COHORT_COLUMN]
            if type(count) is not int or count < 0:
                raise ResultGuardError("Invalid trusted cohort count")
            if count < minimum:
                continue
        safe = {name: _scalar(row[name]) for name in columns}
        if full:
            continue
        observation["rows"].append(safe)
        observation["status"] = "ok"
        if len(json.dumps(observation, ensure_ascii=False)) > max_chars:
            observation["rows"].pop()
            if not observation["rows"]:
                raise ResultGuardError("One result row exceeds the observation budget")
            observation["truncated"] = full = True
    return observation
