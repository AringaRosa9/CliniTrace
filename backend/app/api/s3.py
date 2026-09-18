from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Response
from sqlalchemy import select

from app.api.s1 import Scoped
from app.contracts.models import ExportAccepted, FactRevision
from app.db.s3 import datasets, exports
from app.modules.datasets import service as dataset_service
from app.modules.datasets.schema import DatasetCreate, DatasetFilters, DatasetRecords, DatasetView
from app.modules.exports import service as export_service
from app.modules.exports.schema import ExportCreate, ExportView
from app.modules.reviews import service
from app.modules.reviews.schema import (
    Approval,
    CheckRequest,
    Correction,
    DispositionRequest,
    Exclusion,
    HistoryView,
    ReturnRequest,
    ReviewWorkspace,
    SnapshotView,
    Supplement,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}")
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


@router.get("/review-sets", response_model=DatasetRecords, operation_id="listReviewSets")
def queue(
    svc: Scoped,
    patient: Annotated[str, Query(max_length=100)] = "",
    encounter_id: UUID | None = None,
    document: Annotated[str, Query(max_length=255)] = "",
    status: str | None = None,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Any:
    if status not in (None, "approved", "pending_review", "returned"):
        raise HTTPException(422)
    return dataset_service.records(
        svc,
        DatasetFilters(
            patient=patient, encounter_id=encounter_id, document=document, status=status
        ),
        cursor,
        limit,
    )


@router.get("/review-sets/{sid}", response_model=ReviewWorkspace, operation_id="getReviewWorkspace")
def workspace(sid: UUID, svc: Scoped) -> Any:
    return service.get(svc, sid)


@router.patch("/facts/{fid}", response_model=FactRevision, operation_id="updateFact")
def edit(fid: UUID, body: Correction, svc: Scoped) -> Any:
    return service.edit(svc, fid, body)


@router.post("/facts/{fid}/exclude", response_model=FactRevision, operation_id="excludeFact")
def exclude(fid: UUID, body: Exclusion, svc: Scoped) -> Any:
    return service.edit(svc, fid, body)


@router.post("/facts", response_model=FactRevision, status_code=201, operation_id="supplementFact")
def supplement(body: Supplement, svc: Scoped) -> Any:
    return service.supplement(svc, body)


@router.post(
    "/review-sets/{sid}/fact-checks", response_model=ReviewWorkspace, operation_id="checkFact"
)
def check(sid: UUID, body: CheckRequest, svc: Scoped) -> Any:
    return service.check(svc, sid, body)


@router.post(
    "/issues/{iid}/dispositions", response_model=ReviewWorkspace, operation_id="disposeIssue"
)
def dispose(iid: str, body: DispositionRequest, svc: Scoped) -> Any:
    return service.dispose(svc, iid, body)


@router.post("/reviews", response_model=SnapshotView, status_code=201, operation_id="createReview")
def approve(body: Approval, svc: Scoped) -> Any:
    return service.approve(svc, body)


@router.post(
    "/review-sets/{sid}/return", response_model=ReviewWorkspace, operation_id="returnReview"
)
def return_review(sid: UUID, body: ReturnRequest, svc: Scoped) -> Any:
    return service.return_material(svc, sid, body)


@router.get(
    "/review-sets/{sid}/events", response_model=HistoryView, operation_id="getReviewHistory"
)
def history(sid: UUID, svc: Scoped) -> Any:
    return service.history(svc, sid)


@router.get("/datasets", response_model=list[DatasetView], operation_id="listDatasets")
def list_datasets(svc: Scoped) -> Any:
    dataset_service.read_access(svc)
    with svc.tx() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(
                    datasets.c.id, datasets.c.name, datasets.c.filters, datasets.c.created_at
                ).order_by(datasets.c.created_at.desc())
            ).mappings()
        ]


@router.post("/datasets", response_model=DatasetView, status_code=201, operation_id="createDataset")
def create_dataset(body: DatasetCreate, svc: Scoped) -> Any:
    return dataset_service.create(svc, body)


@router.get(
    "/datasets/{did}/records", response_model=DatasetRecords, operation_id="getDatasetRecords"
)
def dataset_records(
    did: UUID,
    svc: Scoped,
    cursor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Any:
    dataset_service.read_access(svc)
    with svc.tx() as conn:
        row = conn.execute(
            select(datasets.c.filters).where(datasets.c.id == did)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404)
    return dataset_service.records(svc, DatasetFilters.model_validate(row), cursor, limit)


@router.post(
    "/exports", response_model=ExportAccepted, status_code=202, operation_id="createExport"
)
def create_export(body: ExportCreate, svc: Scoped, idempotency_key: Key) -> Any:
    return export_service.create(svc, body, idempotency_key)


@router.get("/exports", response_model=list[ExportView], operation_id="listExports")
def list_exports(svc: Scoped) -> Any:
    svc.require("export.reviewed")
    with svc.tx() as conn:
        ids = list(
            conn.execute(
                select(exports.c.id)
                .where(exports.c.actor_id == svc.actor)
                .order_by(exports.c.created_at.desc())
                .limit(50)
            ).scalars()
        )
    items = []
    for eid in ids:
        try:
            items.append(export_service.view(svc, eid))
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
    return items


@router.get("/exports/{eid}", response_model=ExportView, operation_id="getExport")
def export_view(eid: UUID, svc: Scoped) -> Any:
    return export_service.view(svc, eid)


@router.get("/exports/{eid}/download", operation_id="downloadExport")
def download(eid: UUID, svc: Scoped) -> Response:
    data, fmt = export_service.download(svc, eid)
    return Response(
        data,
        media_type="application/json" if fmt == "json" else "text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="clinical-{eid}.{fmt}"'},
    )
