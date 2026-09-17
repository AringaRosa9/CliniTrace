from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.contracts.models import Contract, FactRevision


class ExtractionCreate(Contract):
    template_version: Literal["outpatient-1.0.0", "laboratory-1.0.0"]
    parse_artifact_id: UUID


class ExtractionAccepted(Contract):
    run_id: UUID
    job_id: UUID
    status_url: str


class Activation(Contract):
    run_id: UUID
    expected_scope_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class Issue(Contract):
    id: str
    rule: str
    severity: Literal["blocking", "warning", "info"]
    fact_revision_ids: list[UUID]
    message: str
    can_accept_unknown: bool
    status: Literal["open"] = "open"


class Candidate(Contract):
    code: str
    display: str
    system: str
    version: str
    basis: str


class Coding(Contract):
    fact_id: UUID
    version: str
    status: Literal["pending", "unmapped"]
    reason: str
    candidates: list[Candidate]


class Relation(Contract):
    id: UUID
    source_id: UUID
    target_id: UUID
    kind: str


class ExtractionView(Contract):
    id: UUID
    job_id: UUID
    document_version_id: UUID
    parse_artifact_id: UUID
    configuration: dict[str, Any]
    input_digest: str
    created_at: datetime
    status: str
    error: dict[str, Any] | None
    duration_ms: int | None
    attempt_measurements: list[dict[str, Any]]
    usage: dict[str, Any] | None
    facts: list[FactRevision]
    relations: list[Relation]
    issues: list[Issue]
    codings: list[Coding]


class ReviewSetView(Contract):
    id: UUID
    encounter_id: UUID
    scope_revision: int
    status: str
    members: list[dict[str, Any]]
    issues: list[Issue]


class ExtractionConfigView(Contract):
    provider: str
    enabled: bool
    ocr_provider: str
    terminology_version: str
