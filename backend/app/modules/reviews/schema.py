from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.contracts.models import ClinicalTime, ClinicalValue, Contract, Evidence, FactRevision
from app.modules.documents.schema import AuditView
from app.modules.extractions.schema import Relation


class ScopeVersion(Contract):
    expected_scope_revision: int = Field(ge=1)


class Correction(ScopeVersion):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    value: ClinicalValue
    evidence: list[Evidence] = Field(min_length=1, max_length=30)
    event_time: ClinicalTime | None = None


class Exclusion(ScopeVersion):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class Supplement(ScopeVersion):
    review_set_id: UUID
    run_id: UUID
    entity_group_id: UUID | None = None
    field_path: str = Field(min_length=1, max_length=200)
    value: ClinicalValue
    evidence: list[Evidence] = Field(min_length=1, max_length=30)
    event_time: ClinicalTime | None = None
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class CheckRequest(ScopeVersion):
    fact_revision_id: UUID


class DispositionRequest(ScopeVersion):
    review_set_id: UUID
    status: Literal["resolved", "accepted_unknown"]
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class ReturnRequest(ScopeVersion):
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    required_material: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class Approval(ScopeVersion):
    review_set_id: UUID
    final_confirmation: Literal[True]


class ReviewIssue(Contract):
    id: str
    rule: str
    severity: Literal["blocking", "warning", "info"]
    fact_revision_ids: list[UUID]
    message: str
    can_accept_unknown: bool
    status: Literal["open", "resolved", "accepted_unknown"] = "open"
    reason: str | None = None


class ReviewDocument(Contract):
    id: UUID
    version_id: UUID
    filename: str
    run_id: UUID | None
    parse_artifact_id: UUID | None


class ReviewWorkspace(Contract):
    review_rules_version: Literal["review-1.0.0"] = "review-1.0.0"
    id: UUID
    encounter_id: UUID
    patient_id: UUID
    patient_key: str
    scope_revision: int
    status: str
    members: list[dict[str, Any]]
    documents: list[ReviewDocument]
    facts: list[FactRevision]
    originals: list[FactRevision]
    relations: list[Relation]
    issues: list[ReviewIssue]
    checked_revision_ids: list[UUID]
    snapshot_id: UUID | None
    configurations: dict[str, Any]
    codings: list[dict[str, Any]]


class SnapshotView(Contract):
    id: UUID
    review_set_id: UUID
    scope_revision: int
    created_at: datetime
    actor_id: UUID
    payload: dict[str, Any]


class HistoryView(Contract):
    events: list[AuditView]
    snapshots: list[SnapshotView]
