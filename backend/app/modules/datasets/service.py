from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.db.models import encounters, patients
from app.db.s1 import documents
from app.db.s2 import review_sets
from app.db.s3 import checks, datasets, snapshots
from app.modules.datasets.schema import DatasetCreate, DatasetFilters
from app.modules.documents.service import Service, now


def read_access(svc: Service) -> None:
    if "review" not in svc.member["capabilities"]:
        svc.require("export.reviewed")


def records(
    svc: Service, filters: DatasetFilters, cursor: UUID | None = None, limit: int = 20
) -> dict[str, Any]:
    read_access(svc)
    stmt = (
        select(review_sets, patients.c.patient_key, patients.c.id.label("patient_id"))
        .join(encounters, encounters.c.id == review_sets.c.encounter_id)
        .join(patients, patients.c.id == encounters.c.patient_id)
    )
    if filters.patient:
        stmt = stmt.where(patients.c.patient_key.icontains(filters.patient, autoescape=True))
    if filters.encounter_id:
        stmt = stmt.where(encounters.c.id == filters.encounter_id)
    if filters.document:
        stmt = stmt.where(
            select(documents.c.id)
            .where(
                documents.c.encounter_id == encounters.c.id,
                documents.c.filename.icontains(filters.document, autoescape=True),
            )
            .exists()
        )
    if filters.status:
        stmt = stmt.where(review_sets.c.status == filters.status)
    with svc.tx() as conn:
        rows = conn.execute(stmt.order_by(review_sets.c.id)).mappings().all()
        items = []
        for r in rows:
            count = sum(len(m["fact_revision_ids"]) for m in r["members"])
            doc_count = conn.execute(
                select(func.count())
                .select_from(documents)
                .where(documents.c.encounter_id == r["encounter_id"])
            ).scalar_one()
            checked = conn.execute(
                select(func.count())
                .select_from(checks)
                .where(
                    checks.c.review_set_id == r["id"],
                    checks.c.scope_revision == r["scope_revision"],
                )
            ).scalar_one()
            snapshot = conn.execute(
                select(snapshots.c.id).where(
                    snapshots.c.review_set_id == r["id"],
                    snapshots.c.scope_revision == r["scope_revision"],
                )
            ).scalar_one_or_none()
            items.append(
                {
                    "review_set_id": r["id"],
                    "patient_id": r["patient_id"],
                    "patient_key": r["patient_key"],
                    "encounter_id": r["encounter_id"],
                    "scope_revision": r["scope_revision"],
                    "status": r["status"],
                    "document_count": doc_count,
                    "fact_count": count,
                    "checked_count": checked,
                    "snapshot_id": snapshot,
                }
            )
        page = [i for i in items if cursor is None or i["review_set_id"] > cursor][: limit + 1]
        return {
            "items": page[:limit],
            "total": len(items),
            "approved_count": sum(i["status"] == "approved" for i in items),
            "document_count": sum(i["document_count"] for i in items),
            "fact_count": sum(i["fact_count"] for i in items),
            "next_cursor": page[limit - 1]["review_set_id"] if len(page) > limit else None,
        }


def create(svc: Service, body: DatasetCreate) -> dict[str, Any]:
    read_access(svc)
    value: dict[str, Any] = {
        "id": uuid4(),
        "name": body.name,
        "filters": body.filters.model_dump(mode="json"),
        "created_at": now(),
    }
    with svc.tx() as conn:
        conn.execute(datasets.insert().values(**svc.scope, actor_id=svc.actor, **value))
        svc.event(conn, "dataset.create", value["id"])
    return value
