from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.contracts.models import Contract


class ExportSelection(Contract):
    review_set_id: UUID
    expected_scope_revision: int = Field(ge=1)


class ExportCreate(Contract):
    selections: list[ExportSelection] = Field(min_length=1, max_length=100)
    reviewed_only: bool = True
    format: Literal["json", "csv"]
    purpose: str = Field(min_length=1, max_length=1000, pattern=r"\S")

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({x.review_set_id for x in self.selections}) != len(self.selections):
            raise ValueError("Duplicate review sets")
        return self


class ExportView(Contract):
    id: UUID
    job_id: UUID
    status: str
    format: str
    purpose: str
    reviewed_only: bool
    created_at: datetime
    expires_at: datetime
    sha256: str | None
    size: int | None
    error: dict[str, Any] | None
