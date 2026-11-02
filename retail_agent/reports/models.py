from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["save", "search", "open", "list", "prepare_delete"]
    title: str = Field(default="", max_length=255)
    query: str = Field(max_length=1000, description="Nonempty search text for search or matching deletion. Empty string for all other operations.")
    report_id: str = Field(default="", max_length=32)
    scope: Literal["matching", "conversation", "all", "id"] = "matching"

    @model_validator(mode="after")
    def check_request(self):
        if self.operation == "save" and (self.query or self.report_id or self.scope != "matching"):
            raise ValueError("save accepts a title only")
        if self.operation != "save" and self.title:
            raise ValueError("title is only used for save")
        if self.operation == "search" and (not self.query.strip() or self.report_id or self.scope != "matching"):
            raise ValueError("search requires query only")
        if self.operation == "open" and (not self.report_id or self.query or self.scope != "matching"):
            raise ValueError("open requires report_id only")
        if self.report_id and (len(self.report_id) != 32 or any(c not in "0123456789abcdef" for c in self.report_id)):
            raise ValueError("report_id must be a lowercase hex ID")
        if self.operation == "list" and (self.query or self.report_id or self.scope != "matching"):
            raise ValueError("list accepts no filters")
        if self.operation == "prepare_delete":
            if self.scope == "matching" and (not self.query.strip() or self.report_id):
                raise ValueError("matching deletion requires query only")
            if self.scope == "id" and (not self.report_id or self.query):
                raise ValueError("ID deletion requires report_id only")
            if self.scope in {"all", "conversation"} and (self.query or self.report_id):
                raise ValueError("all/conversation deletion accepts no other filters")
        return self


class ReportError(RuntimeError):
    """Fixed user-safe message; backend errors never become model feedback."""
