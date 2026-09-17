from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import select

from app.api.s1 import Scoped
from app.contracts.models import AsyncAccepted
from app.db.s1 import documents, versions
from app.db.s2 import runs
from app.modules.extractions import service
from app.modules.extractions.pipeline import candidates
from app.modules.extractions.schema import (
    Activation,
    Candidate,
    ExtractionAccepted,
    ExtractionConfigView,
    ExtractionCreate,
    ExtractionView,
    ReviewSetView,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}")


@router.get(
    "/extraction-config", response_model=ExtractionConfigView, operation_id="getExtractionConfig"
)
def config(svc: Scoped) -> Any:
    svc.require("documents.read")
    return {
        "provider": svc.settings.extraction_provider,
        "enabled": svc.settings.extraction_provider != "disabled"
        and (svc.settings.extraction_provider != "synthetic" or svc.settings.allow_synthetic_mock),
        "ocr_provider": svc.settings.ocr_provider,
        "terminology_version": svc.settings.terminology_version,
    }


@router.post(
    "/documents/{document_id}/extractions",
    response_model=ExtractionAccepted,
    status_code=202,
    operation_id="createExtraction",
)
def create_extraction(
    document_id: UUID,
    body: ExtractionCreate,
    svc: Scoped,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> Any:
    return service.create(svc, document_id, body, idempotency_key)


@router.get(
    "/documents/{document_id}/extractions",
    response_model=list[ExtractionView],
    operation_id="listExtractions",
)
def list_extractions(document_id: UUID, svc: Scoped) -> Any:
    svc.require("original.read")
    with svc.tx() as conn:
        if not conn.execute(select(documents.c.id).where(documents.c.id == document_id)).first():
            raise HTTPException(404)
        ids = (
            conn.execute(
                select(runs.c.id)
                .join(versions, versions.c.id == runs.c.document_version_id)
                .where(versions.c.document_id == document_id)
                .order_by(runs.c.created_at.desc())
                .limit(20)
            )
            .scalars()
            .all()
        )
    return [service.view(svc, rid) for rid in ids]


@router.get("/extractions/{run_id}", response_model=ExtractionView, operation_id="getExtraction")
def get_extraction(run_id: UUID, svc: Scoped) -> Any:
    return service.view(svc, run_id)


@router.post(
    "/documents/{document_id}/active-run",
    response_model=ReviewSetView,
    operation_id="activateExtraction",
)
def activate(document_id: UUID, body: Activation, svc: Scoped) -> Any:
    return service.activate(svc, document_id, body)


@router.get(
    "/encounters/{encounter_id}/review-set",
    response_model=ReviewSetView,
    operation_id="getEncounterReviewSet",
)
def review(encounter_id: UUID, svc: Scoped) -> Any:
    return service.review(svc, encounter_id)


@router.get(
    "/terminology/candidates",
    response_model=list[Candidate],
    operation_id="getTerminologyCandidates",
)
def terminology(
    svc: Scoped, query: Annotated[str, Query(min_length=1, max_length=200)], version: str
) -> Any:
    svc.require("documents.read")
    if version != svc.settings.terminology_version:
        raise HTTPException(422)
    return candidates(query, version)


@router.post(
    "/documents/{document_id}/ocr",
    response_model=AsyncAccepted,
    status_code=202,
    operation_id="startOCR",
)
def start_ocr(
    document_id: UUID,
    svc: Scoped,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> Any:
    return service.start_ocr(svc, document_id, idempotency_key)
