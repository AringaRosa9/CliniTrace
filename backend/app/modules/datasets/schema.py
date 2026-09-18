from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.contracts.models import Contract


class DatasetFilters(Contract):
    patient: str = Field(default="", max_length=100)
    encounter_id: UUID | None = None
    document: str = Field(default="", max_length=255)
    status: Literal["approved", "pending_review", "returned"] | None = None


class DatasetCreate(Contract):
    name: str = Field(min_length=1, max_length=150, pattern=r"\S")
    filters: DatasetFilters


class DatasetView(DatasetCreate):
    id: UUID
    created_at: datetime


class DatasetRecord(Contract):
    review_set_id: UUID
    patient_id: UUID
    patient_key: str
    encounter_id: UUID
    scope_revision: int
    status: str
    document_count: int
    fact_count: int
    checked_count: int
    snapshot_id: UUID | None


class DatasetRecords(Contract):
    items: list[DatasetRecord]
    total: int
    approved_count: int
    document_count: int
    fact_count: int
    next_cursor: UUID | None
