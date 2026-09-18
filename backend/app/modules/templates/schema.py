from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.contracts.models import Contract


class TemplateContent(Contract):
    schema_definition: dict[str, Any]
    guide: str = Field(min_length=1, max_length=20000, pattern=r"\S")
    positive_examples: list[dict[str, Any]] = Field(min_length=1, max_length=10)
    negative_examples: list[dict[str, Any]] = Field(min_length=1, max_length=10)
    evidence_rules: list[str] = Field(min_length=1, max_length=20)


class TemplateCreate(TemplateContent):
    name: str = Field(min_length=1, max_length=150, pattern=r"\S")
    document_type: Literal["outpatient", "laboratory"]


class TemplateUpdate(TemplateContent):
    expected_revision: int = Field(ge=1)


class TemplatePublish(Contract):
    expected_revision: int = Field(ge=1)
    version: str = Field(
        pattern=r"^(outpatient|laboratory)-[0-9]+\.[0-9]+\.[0-9]+$", max_length=100
    )


class TemplateView(Contract):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    name: str
    document_type: str
    revision: int
    payload: TemplateContent
    created_at: datetime


class TemplateVersionView(Contract):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    template_id: UUID
    version: str
    payload: dict[str, Any]
    digest: str
    created_at: datetime


class ValidationReport(Contract):
    valid: bool
    errors: list[str]
    compatibility: str
