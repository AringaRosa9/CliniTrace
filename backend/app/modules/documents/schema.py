from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.contracts.models import Contract

Nonempty = str


class LoginRequest(Contract):
    access_code: str = Field(min_length=1, max_length=200)


class ProjectView(Contract):
    id: UUID
    name: str
    roles: list[str]
    capabilities: list[str]


class Me(Contract):
    id: UUID
    display_name: str
    csrf_token: str
    projects: list[ProjectView]


class AuthConfig(Contract):
    provider: Literal["synthetic", "oidc"]


class PatientCreate(Contract):
    patient_key: str = Field(min_length=1, max_length=100, pattern=r"\S")
    source: str = Field(min_length=1, max_length=100, pattern=r"\S")
    source_key: str = Field(min_length=1, max_length=200, pattern=r"\S")


class PatientView(Contract):
    id: UUID
    patient_key: str


class EncounterCreate(Contract):
    patient_id: UUID
    kind: Literal["outpatient", "laboratory"]
    occurred_on: date | None = None
    department: str = Field(default="", max_length=100)
    source: str = Field(min_length=1, max_length=100, pattern=r"\S")
    source_key: str = Field(min_length=1, max_length=200, pattern=r"\S")


class EncounterView(Contract):
    id: UUID
    patient_id: UUID
    kind: str
    occurred_on: date | None
    department: str


class Association(Contract):
    patient_id: UUID
    encounter_id: UUID
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500, pattern=r"\S")


class DocumentView(Contract):
    id: UUID
    patient_key: str
    encounter_date: date | None
    department: str
    max_attempts: int
    patient_id: UUID
    encounter_id: UUID
    document_type: str
    filename: str
    processing_status: str
    revision: int
    created_at: datetime
    version_id: UUID
    mime: str
    size: int
    job_id: UUID
    job_status: str
    attempt: int
    progress: int
    error: dict[str, Any] | None
    cancel_requested: bool
    artifact_id: UUID | None = None


class DocumentList(Contract):
    items: list[DocumentView]
    next_cursor: str | None


class AttemptView(Contract):
    generation: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    error_code: str | None


class JobView(Contract):
    id: UUID
    status: str
    stage: Literal["parsing", "extracting"] = "parsing"
    attempt: int
    progress: int
    error: dict[str, Any] | None
    created_at: datetime
    cancel_requested: bool
    heartbeat_at: datetime | None
    attempts: list[AttemptView]


class ParseBlock(Contract):
    id: str
    text: str
    start: int
    end: int
    bbox: list[float] | None


class ParsePage(Contract):
    page: int
    width: float
    height: float
    rotation: int
    transform: list[float]
    needs_ocr: bool
    blocks: list[ParseBlock]
    image: bool
    quality: dict[str, Any] | None = None
    tables: list[dict[str, Any]] = Field(default_factory=list)


class ParseView(Contract):
    id: UUID
    parser_version: str
    text_version: str
    text: str
    pages: list[ParsePage]
    needs_ocr: bool


class AuditView(Contract):
    id: UUID
    actor_id: UUID
    action: str
    target_id: UUID
    request_id: UUID
    details: dict[str, Any]
    created_at: datetime


class UploadResult(Contract):
    document_id: UUID
    document_version_id: UUID
    job_id: UUID
    status_url: str
    processing_status: str
    duplicate: bool = False


class Ready(Contract):
    status: Literal["ready"] = "ready"
