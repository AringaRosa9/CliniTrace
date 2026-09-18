from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.contracts.models import Contract, FactRevision
from app.modules.extractions.schema import Relation


class GoldCoding(Contract):
    fact_id: UUID
    system: str
    version: str
    code: str | None


class Labels(Contract):
    facts: list[FactRevision] = Field(max_length=500)
    relations: list[Relation] = Field(default_factory=list, max_length=500)
    codings: list[GoldCoding] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def links(self) -> Self:
        ids = {f.fact_id for f in self.facts}
        if len(ids) != len(self.facts) or any(
            r.source_id not in ids or r.target_id not in ids for r in self.relations
        ):
            raise ValueError("Duplicate facts or dangling relation")
        if any(c.fact_id not in ids for c in self.codings) or len(
            {c.fact_id for c in self.codings}
        ) != len(self.codings):
            raise ValueError("Invalid coding reference")
        return self


class SampleCreate(Contract):
    run_id: UUID
    patient_group: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"\S",
        description="Controlled cross-source patient group; stored as project-scoped digest",
    )
    split: Literal["train", "dev", "test", "external"]
    source: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    authorization_reference: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    synthetic: bool = False
    difficulty_tags: list[str] = Field(min_length=1, max_length=20)
    template_version_id: UUID


class AnnotationCreate(Contract):
    stage: Literal["annotation", "review", "adjudication"]
    labels: Labels
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    active_seconds: int = Field(ge=0, le=86400)


class FreezeRequest(Contract):
    name: str = Field(min_length=1, max_length=150, pattern=r"\S")
    sample_ids: list[UUID] = Field(min_length=1, max_length=300)


class EvaluationCreate(Contract):
    dataset_id: UUID
    name: str = Field(min_length=1, max_length=150, pattern=r"\S")
    predictions: dict[UUID, UUID] = Field(
        min_length=1, max_length=300, description="Sample ID to immutable extraction run ID"
    )


class CorrectionReview(Contract):
    decision: Literal["accepted", "rejected"]
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class CorrectionRelease(Contract):
    name: str = Field(min_length=1, max_length=150, pattern=r"\S")
    candidate_ids: list[UUID] = Field(min_length=1, max_length=300)


class LedgerView(Contract):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    actor_id: UUID
    created_at: datetime
    payload: dict[str, Any]


class SampleView(LedgerView):
    run_id: UUID
    patient_id: UUID
    patient_group: str
    split: str
    state: str
    annotations: list[LedgerView]


class GoldVersionView(Contract):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    name: str
    digest: str
    manifest: dict[str, Any]
    created_at: datetime


class EvaluationView(LedgerView):
    dataset_id: UUID
    name: str


class CorrectionView(LedgerView):
    revision_id: UUID
    patient_id: UUID
    decision: str
    review: dict[str, Any] | None


class ReviewActivity(Contract):
    session_id: UUID
    active_seconds: int = Field(ge=0, le=28800)
