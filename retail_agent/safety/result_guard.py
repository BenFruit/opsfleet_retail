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


def check_text(text: str) -> str:
    # Supplemental patterns, NOT comprehensive name/address/PII detection.
    if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text) or re.search(r"(?<!\w)\+\d[\d ()-]{8,}\d(?!\w)", text):
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


def render_observation(observation: dict, *, max_rows: int = 200) -> str:
    """Render checked values; never invent pages or hidden rows."""
    def cell(value, column=""):
        if value is None:
            return "null"
        text = format(value, ".12g") if isinstance(value, float) else str(value)
        if any(word in column.lower() for word in ("revenue", "profit", "price", "cost", "spend", "amount")) and isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            text = f"{Decimal(str(value)):,.2f}"
        return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    rows = observation.get("rows", [])
    lines = ["The checked query returned no publishable rows."]
    if rows:
        columns = list(rows[0])
        lines = ["| " + " | ".join(cell(name) for name in columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
        lines += ["| " + " | ".join(cell(row[name], name) for name in columns) + " |" for row in rows[:max_rows]]
        if len(rows) > max_rows:
            lines.append(f"Showing {max_rows} of {len(rows)} available result rows.")
    if observation.get("truncated"):
        lines.append("Result was truncated; this is not necessarily a complete ranking or total.")
    context = observation.get("query_context", {})
    if context.get("filters") is not None:
        lines.append("Filters: " + json.dumps(context["filters"], ensure_ascii=False))
    for note in (context.get("age_average_basis"), observation.get("privacy_note")):
        if note:
            lines.append(note)
    return "\n".join(lines)


def remove_markdown_tables(text: str) -> str:
    """Keep model prose; the app supplies the authoritative current-turn tables."""
    lines = text.splitlines()
    kept = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines) and "|" in lines[index]:
            separators = lines[index + 1].strip().strip("|").split("|")
            if separators and all(re.fullmatch(r"\s*:?-{3,}:?\s*", part) for part in separators):
                index += 2
                while index < len(lines) and "|" in lines[index] and lines[index].strip():
                    index += 1
                continue
        kept.append(lines[index])
        index += 1
    return "\n".join(kept).strip()
