from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.contracts.models import Contract


class Term(Contract):
    code: str = Field(min_length=1, max_length=100, pattern=r"\S")
    display: str = Field(min_length=1, max_length=300, pattern=r"\S")
    aliases: list[str] = Field(min_length=1, max_length=30)
    context: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class TerminologyImport(Contract):
    version: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]+$")
    system: str = Field(min_length=1, max_length=200, pattern=r"\S")
    authorization_reference: str = Field(max_length=1000)
    synthetic: bool = False
    terms: list[Term] = Field(min_length=1, max_length=5000)


class TerminologyView(Contract):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    version: str
    payload: TerminologyImport
    digest: str
    created_at: datetime
    active: bool = False


class MappingRequest(Contract):
    revision_id: UUID
    terminology_id: UUID
    expected_sequence: int = Field(ge=0)
    status: Literal["confirmed", "rejected", "pending"]
    code: str | None = Field(default=None, max_length=100)
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class MappingView(Contract):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    fact_id: UUID
    revision_id: UUID
    terminology_id: UUID
    sequence: int
    payload: dict[str, object]
    actor_id: UUID
    created_at: datetime
