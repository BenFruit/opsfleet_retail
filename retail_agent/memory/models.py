from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


MemoryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class RememberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memories: list[MemoryText] = Field(min_length=1, max_length=5)


class UserMemoryError(RuntimeError):
    """Controlled messages only; never expose database/provider exception text."""
