"""Design-time OpenAPI only. Never mount these unimplemented routes in the live app."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import Header, HTTPException

from app.contracts.models import (
    ErrorResponse,
    Evidence,
    ExportAccepted,
    ExportRequest,
    FactPatch,
    FactRevision,
    ReviewRequest,
    ReviewSnapshot,
)
from app.core.config import Settings
from app.main import create_app

contract_app = create_app(Settings(_env_file=None))
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
project = "/api/v1/projects/{project_id}"
errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 413, 415, 422, 429)
}
planned = {"x-implementation-stage": "S1-S3", "security": [{"OIDC": []}]}


@contract_app.patch(
    project + "/facts/{fact_id}",
    response_model=FactRevision,
    operation_id="updateFact",
    responses=errors,
    openapi_extra=planned,
)
def update_fact(project_id: UUID, fact_id: UUID, body: FactPatch) -> FactRevision:
    raise HTTPException(501)


@contract_app.get(
    project + "/evidence/{evidence_id}",
    response_model=Evidence,
    operation_id="getEvidence",
    responses=errors,
    openapi_extra=planned,
)
def get_evidence(project_id: UUID, evidence_id: UUID) -> Evidence:
    raise HTTPException(501)


@contract_app.post(
    project + "/reviews",
    response_model=ReviewSnapshot,
    status_code=201,
    operation_id="createReview",
    responses=errors,
    openapi_extra=planned,
)
def create_review(project_id: UUID, body: ReviewRequest, idempotency_key: Key) -> ReviewSnapshot:
    raise HTTPException(501)


@contract_app.post(
    project + "/exports",
    response_model=ExportAccepted,
    status_code=202,
    operation_id="createExport",
    responses=errors,
    openapi_extra=planned,
)
def create_export(project_id: UUID, body: ExportRequest, idempotency_key: Key) -> ExportAccepted:
    raise HTTPException(501)
