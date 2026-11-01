from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator
from retail_agent.data.demographics import DemographicRequest


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["query", "demographic", "answer"]
    sql: str = Field(default="", max_length=20_000)
    demographic: DemographicRequest | None = None
    answer: str = Field(default="", max_length=8_000)

    @model_validator(mode="after")
    def check_action(self):
        if self.action == "query" and (not self.sql.strip() or self.answer or self.demographic is not None):
            raise ValueError("query requires SQL only")
        if self.action == "demographic" and (self.demographic is None or self.sql or self.answer):
            raise ValueError("demographic requires a structured request only")
        if self.action == "answer" and (not self.answer.strip() or self.sql or self.demographic is not None):
            raise ValueError("answer requires text and empty SQL")
        return self


@dataclass(frozen=True)
class AgentLimits:
    max_queries: int = 5
    max_model_calls: int = 7
    max_consecutive_errors: int = 2
    max_scan_bytes: int = 300_000_000
    max_input_chars: int = 4_000
    max_history_chars: int = 20_000
    max_history_turns: int = 6
    max_observation_chars: int = 12_000

    def __post_init__(self):
        for name, value in vars(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


class AgentState(TypedDict):
    question: str
    history: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    action: AgentAction | None
    answer: str
    queries: int
    model_calls: int
    consecutive_errors: int
    scan_bytes_used: int
    turn_id: str


@dataclass(frozen=True)
class TurnResult:
    answer: str = field(repr=False)
    queries: int
    model_calls: int
    scan_bytes_used: int
