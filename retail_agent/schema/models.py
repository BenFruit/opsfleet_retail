from enum import Enum

from pydantic import BaseModel


class DataAccessLevel(str, Enum):
    # Can be used normally in analysis and returned at row level.
    ALLOWED = "allowed"

    # Can be used only in aggregated analysis.
    # Must not be exposed as individual-level customer attributes.
    AGGREGATE_ONLY = "aggregate_only"

    # Personally identifiable information.
    # Must be removed or masked before any data reaches the LLM.
    BLOCKED_PII = "blocked_pii"


class ColumnPolicy(BaseModel):
    access_level: DataAccessLevel
    description: str = ""