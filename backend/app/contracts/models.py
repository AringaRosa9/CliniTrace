"""Shared S0 wire models. S1 runtime extensions live in modules/documents/schema.py."""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorResponse(Contract):
    code: str
    message: str
    details: dict[str, str | int | list[str]] = Field(default_factory=dict)
    request_id: UUID
    retryable: bool = False


class HealthResponse(Contract):
    status: Literal["ok"] = "ok"
    service: Literal["clinical-data-api"] = "clinical-data-api"
    version: Literal["0.1.0"] = "0.1.0"


class MissingReason(StrEnum):
    MISSING = "missing"
    NOT_MENTIONED = "not_mentioned"
    EXPLICITLY_UNKNOWN = "explicitly_unknown"
    NOT_APPLICABLE = "not_applicable"
    UNREADABLE = "unreadable"


class TextSpan(Contract):
    start: int = Field(ge=0, description="Unicode code point offset, inclusive")
    end: int = Field(gt=0, description="Unicode code point offset, exclusive")

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("Span end must follow start")
        return self


Coordinate = Annotated[float, Field(ge=0, le=1)]


class BoundingBox(Contract):
    x0: Coordinate
    y0: Coordinate
    x1: Coordinate
    y1: Coordinate

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("Bounding box must have positive area")
        return self


class Evidence(Contract):
    id: UUID
    document_version_id: UUID
    parse_artifact_id: UUID
    text_version: str = Field(min_length=1)
    page: int = Field(ge=1)
    span: TextSpan
    quote: str = Field(min_length=1)
    boxes: list[BoundingBox] = Field(default_factory=list)
    block_id: str | None = None


class ClinicalTime(Contract):
    precision: Literal["year", "month", "day", "datetime", "relative", "unknown"]
    value: str | None = None
    original_text: str | None = None
    anchor_fact_revision_id: UUID | None = None
    derivation: str | None = None


class ClinicalValue(Contract):
    raw: str = Field(min_length=1)
    normalized: str | None = Field(description="Exact decimal serialized as string; never float")
    kind: Literal["text", "decimal", "date"]
    unit: str | None = None
    comparator: Literal["=", "<", ">", "<=", ">="] | None = None
    missing_reason: MissingReason | None = None
    assertion: Literal["affirmed", "negated", "suspected"] = "affirmed"
    experiencer: Literal["patient", "family", "other", "unknown"] = "patient"

    @model_validator(mode="after")
    def null_semantics(self) -> Self:
        if (self.normalized is None) != (self.missing_reason is not None):
            raise ValueError("Null requires a missing reason; a present value cannot have one")
        if self.normalized == "":
            raise ValueError("Use null and a missing reason instead of empty string")
        if self.kind == "decimal" and self.normalized is not None:
            from decimal import Decimal, InvalidOperation

            try:
                if not Decimal(self.normalized).is_finite():
                    raise ValueError("Decimal must be finite")
            except InvalidOperation as exc:
                raise ValueError("Invalid exact decimal") from exc
        return self


class FactRevision(Contract):
    fact_id: UUID
    revision_id: UUID
    revision: int = Field(ge=1)
    entity_group_id: UUID
    field_path: str = Field(min_length=1)
    value: ClinicalValue
    event_time: ClinicalTime | None = None
    evidence: list[Evidence] = Field(min_length=1)
    excluded: bool = False
    reason: str | None = None


class FactPatch(Contract):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, pattern=r"\S")
    value: ClinicalValue
    evidence_ids: list[UUID] = Field(min_length=1)


class ScopeMember(Contract):
    document_version_id: UUID
    extraction_run_id: UUID
    fact_revision_ids: list[UUID]


class FactCheck(Contract):
    fact_revision_id: UUID
    checked: Literal[True]


class ReviewRequest(Contract):
    review_set_id: UUID
    expected_scope_revision: int = Field(ge=1)
    fact_checks: list[FactCheck] = Field(min_length=1)
    final_confirmation: Literal[True]


class ReviewSnapshot(Contract):
    id: UUID
    review_set_id: UUID
    scope_revision: int = Field(ge=1)
    encounter_id: UUID
    members: list[ScopeMember] = Field(min_length=1)
    reviewed_at: AwareDatetime
    reviewer_id: UUID
    status: Literal["approved"] = "approved"


class AsyncAccepted(Contract):
    job_id: UUID
    status_url: str


class UploadAccepted(AsyncAccepted):
    document_id: UUID
    document_version_id: UUID
    processing_status: Literal["queued"] = "queued"


class JobResponse(Contract):
    id: UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    stage: Literal["parsing", "extracting", "validating", "exporting"] | None = None
    attempt: int = Field(ge=0)
    progress: int | None = Field(default=None, ge=0, le=100)
    error: ErrorResponse | None = None
    created_at: AwareDatetime
    cost_amount: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")
    cost_currency: Literal["CNY", "USD"] | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class ExportRequest(Contract):
    review_snapshot_ids: list[UUID] = Field(default_factory=list)
    draft_fact_revision_ids: list[UUID] = Field(default_factory=list)
    reviewed_only: bool = True
    format: Literal["json", "csv"]
    purpose: str = Field(min_length=1, pattern=r"\S")

    @model_validator(mode="after")
    def explicit_drafts(self) -> Self:
        if self.reviewed_only and self.draft_fact_revision_ids:
            raise ValueError("Draft revisions require explicit reviewed_only=false")
        if not self.review_snapshot_ids and not self.draft_fact_revision_ids:
            raise ValueError("Export scope must not be empty")
        return self


class ExportAccepted(AsyncAccepted):
    export_id: UUID
    reviewed_only: bool
    manifest_version: Literal["1.0"] = "1.0"


class ExtractedItem(Contract):
    entity_group_id: UUID
    value: ClinicalValue
    evidence: list[Evidence] = Field(min_length=1)
    event_time: ClinicalTime | None = None


class Medication(Contract):
    name: ExtractedItem
    dose: ExtractedItem | None = None
    frequency: ExtractedItem | None = None


class OutpatientTemplate(Contract):
    schema_version: Literal["outpatient-1.0.0"] = "outpatient-1.0.0"
    diagnoses: list[ExtractedItem]
    history: list[ExtractedItem]
    medications: list[Medication]
    duration: list[ExtractedItem]


class LabObservation(Contract):
    name: ExtractedItem
    result: ExtractedItem
    specimen: ExtractedItem | None = None
    method: ExtractedItem | None = None
    collected_at: ClinicalTime | None = None


class LabTemplate(Contract):
    schema_version: Literal["laboratory-1.0.0"] = "laboratory-1.0.0"
    observations: list[LabObservation]


def validate_quote(evidence: Evidence, text: str, text_version: str) -> None:
    if evidence.text_version != text_version:
        raise ValueError("Text version mismatch")
    if (
        evidence.span.end > len(text)
        or text[evidence.span.start : evidence.span.end] != evidence.quote
    ):
        raise ValueError("Evidence quote does not match code point span")
