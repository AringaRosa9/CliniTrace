from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.s1 import Scoped
from app.db.s4 import (
    correction_releases,
    correction_reviews,
    corrections,
    evaluations,
    gold_versions,
    mappings,
    samples,
    template_versions,
    templates,
    terminologies,
)
from app.modules.extractions.pipeline import model_for
from app.modules.quality import service as qs
from app.modules.quality.schema import (
    AnnotationCreate,
    CorrectionRelease,
    CorrectionReview,
    CorrectionView,
    EvaluationCreate,
    EvaluationView,
    FreezeRequest,
    GoldVersionView,
    ReviewActivity,
    SampleCreate,
    SampleView,
)
from app.modules.templates import service as ts
from app.modules.templates.schema import (
    TemplateCreate,
    TemplatePublish,
    TemplateUpdate,
    TemplateVersionView,
    TemplateView,
    ValidationReport,
)
from app.modules.terminology import service as terms
from app.modules.terminology.schema import (
    MappingRequest,
    MappingView,
    TerminologyImport,
    TerminologyView,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}")


def asset_access(svc: Scoped) -> None:
    if not set(svc.member["capabilities"]) & {
        "templates.manage",
        "terminology.manage",
        "terminology.map",
        "quality.annotate",
        "quality.review",
        "quality.adjudicate",
        "quality.manage",
        "import",
        "review",
    }:
        raise HTTPException(403)


@router.get("/templates", response_model=list[TemplateView], operation_id="listTemplates")
def list_templates(svc: Scoped) -> Any:
    asset_access(svc)
    with svc.tx() as conn:
        return list(
            conn.execute(select(templates).order_by(templates.c.created_at.desc())).mappings()
        )


@router.get("/templates/base/{kind}", operation_id="getBaseTemplate", response_model=dict[str, Any])
def base_template(kind: str, svc: Scoped) -> Any:
    asset_access(svc)
    if kind not in ("outpatient", "laboratory"):
        raise HTTPException(404)
    return model_for(kind + "-1.0.0").model_json_schema()


@router.post(
    "/templates", response_model=TemplateView, status_code=201, operation_id="createTemplate"
)
def create_template(body: TemplateCreate, svc: Scoped) -> Any:
    return ts.create(svc, body)


@router.patch("/templates/{tid}", response_model=TemplateView, operation_id="updateTemplate")
def update_template(tid: UUID, body: TemplateUpdate, svc: Scoped) -> Any:
    return ts.update(svc, tid, body)


@router.post(
    "/templates/{tid}/validate", response_model=ValidationReport, operation_id="validateTemplate"
)
def validate_template(tid: UUID, svc: Scoped) -> Any:
    svc.require("templates.manage")
    with svc.tx() as conn:
        row = qs.one(conn, templates, tid)
        return ts.validate(row["document_type"], row["payload"])


@router.post(
    "/templates/{tid}/publish",
    response_model=TemplateVersionView,
    status_code=201,
    operation_id="publishTemplate",
)
def publish_template(tid: UUID, body: TemplatePublish, svc: Scoped) -> Any:
    return ts.publish(svc, tid, body)


@router.get(
    "/template-versions",
    response_model=list[TemplateVersionView],
    operation_id="listTemplateVersions",
)
def list_template_versions(svc: Scoped) -> Any:
    asset_access(svc)
    with svc.tx() as conn:
        return list(
            conn.execute(
                select(template_versions).order_by(template_versions.c.created_at.desc())
            ).mappings()
        )


@router.get(
    "/terminology/versions",
    response_model=list[TerminologyView],
    operation_id="listTerminologyVersions",
)
def list_terms(svc: Scoped) -> Any:
    asset_access(svc)
    with svc.tx() as conn:
        current = terms.active(conn)
        return [
            dict(r) | {"active": current is not None and current["id"] == r["id"]}
            for r in conn.execute(
                select(terminologies).order_by(terminologies.c.created_at.desc())
            ).mappings()
        ]


@router.post(
    "/terminology/versions",
    response_model=TerminologyView,
    status_code=201,
    operation_id="importTerminology",
)
def import_terms(body: TerminologyImport, svc: Scoped) -> Any:
    return terms.import_version(svc, body)


@router.post(
    "/terminology/versions/{tid}/activate",
    response_model=TerminologyView,
    operation_id="activateTerminology",
)
def activate_terms(tid: UUID, svc: Scoped) -> Any:
    return terms.activate(svc, tid)


@router.get(
    "/terminology/versions/{tid}/search",
    response_model=list[dict[str, str]],
    operation_id="searchTerminology",
)
def search_terms(
    tid: UUID, svc: Scoped, q: Annotated[str, Query(min_length=1, max_length=300)]
) -> Any:
    asset_access(svc)
    with svc.tx() as conn:
        return terms.search(qs.one(conn, terminologies, tid)["payload"], q)


@router.post(
    "/facts/{fid}/mapping",
    response_model=MappingView,
    status_code=201,
    operation_id="decideMapping",
)
def decide_mapping(fid: UUID, body: MappingRequest, svc: Scoped) -> Any:
    return terms.decide(svc, fid, body)


@router.get(
    "/facts/{fid}/mappings", response_model=list[MappingView], operation_id="getMappingHistory"
)
def mapping_history(fid: UUID, svc: Scoped) -> Any:
    svc.require("original.read")
    asset_access(svc)
    with svc.tx() as conn:
        from app.db.s2 import facts

        qs.one(conn, facts, fid)
        svc.event(conn, "mapping.history.read", fid)
        return list(
            conn.execute(
                select(mappings).where(mappings.c.fact_id == fid).order_by(mappings.c.sequence)
            ).mappings()
        )


@router.get("/quality/samples", response_model=list[SampleView], operation_id="listQualitySamples")
def list_samples(svc: Scoped) -> Any:
    svc.require("quality.errors.read")
    svc.require("original.read")
    with svc.tx() as conn:
        svc.event(conn, "quality.samples.read", svc.project)
        return [
            qs.sample_view(conn, dict(r))
            for r in conn.execute(select(samples).order_by(samples.c.created_at.desc())).mappings()
        ]


@router.post(
    "/quality/samples",
    response_model=SampleView,
    status_code=201,
    operation_id="createQualitySample",
)
def create_sample(body: SampleCreate, svc: Scoped) -> Any:
    return qs.create_sample(svc, body)


@router.post(
    "/quality/samples/{sid}/annotations",
    response_model=SampleView,
    status_code=201,
    operation_id="annotateQualitySample",
)
def annotate(sid: UUID, body: AnnotationCreate, svc: Scoped) -> Any:
    return qs.annotate(svc, sid, body)


@router.post(
    "/quality/datasets",
    response_model=GoldVersionView,
    status_code=201,
    operation_id="freezeGoldDataset",
)
def freeze(body: FreezeRequest, svc: Scoped) -> Any:
    return qs.freeze(svc, body)


@router.get(
    "/quality/datasets", response_model=list[GoldVersionView], operation_id="listGoldDatasets"
)
def list_gold(svc: Scoped) -> Any:
    svc.require("quality.read")
    with svc.tx() as conn:
        return [
            dict(r) | {"manifest": {k: v for k, v in r["manifest"].items() if k != "members"}}
            for r in conn.execute(
                select(gold_versions).order_by(gold_versions.c.created_at.desc())
            ).mappings()
        ]


@router.get(
    "/quality/datasets/{gid}/manifest",
    response_model=GoldVersionView,
    operation_id="getGoldManifest",
)
def gold_manifest(gid: UUID, svc: Scoped) -> Any:
    svc.require("quality.errors.read")
    svc.require("original.read")
    with svc.tx() as conn:
        row = qs.one(conn, gold_versions, gid)
        svc.event(conn, "gold.manifest.read", gid)
        return row


@router.post(
    "/quality/evaluations",
    response_model=EvaluationView,
    status_code=201,
    operation_id="runEvaluation",
)
def evaluate(body: EvaluationCreate, svc: Scoped) -> Any:
    return qs.public_evaluation(qs.evaluate_version(svc, body))


@router.get(
    "/quality/evaluations", response_model=list[EvaluationView], operation_id="listEvaluations"
)
def list_evaluations(svc: Scoped) -> Any:
    svc.require("quality.read")
    with svc.tx() as conn:
        return [
            qs.public_evaluation(dict(r))
            for r in conn.execute(
                select(evaluations).order_by(evaluations.c.created_at.desc())
            ).mappings()
        ]


@router.get(
    "/quality/evaluations/{eid}/errors",
    response_model=EvaluationView,
    operation_id="getEvaluationErrors",
)
def evaluation_errors(eid: UUID, svc: Scoped) -> Any:
    svc.require("quality.errors.read")
    svc.require("original.read")
    with svc.tx() as conn:
        row = qs.one(conn, evaluations, eid)
        svc.event(conn, "evaluation.errors.read", eid)
        return row


@router.get(
    "/quality/evaluations/{eid}/compare/{other}",
    response_model=dict[str, Any],
    operation_id="compareEvaluations",
)
def compare(eid: UUID, other: UUID, svc: Scoped) -> Any:
    svc.require("quality.read")
    with svc.tx() as conn:
        a, b = (qs.one(conn, evaluations, x) for x in (eid, other))
        if (
            a["dataset_id"] != b["dataset_id"]
            or a["payload"]["protocol"] != b["payload"]["protocol"]
        ):
            raise HTTPException(409)
        return {"baseline": qs.public_evaluation(a), "candidate": qs.public_evaluation(b)}


@router.get(
    "/quality/corrections",
    response_model=list[CorrectionView],
    operation_id="listCorrectionCandidates",
)
def list_corrections(svc: Scoped) -> Any:
    svc.require("quality.errors.read")
    svc.require("original.read")
    with svc.tx() as conn:
        reviews = {
            r["candidate_id"]: dict(r) for r in conn.execute(select(correction_reviews)).mappings()
        }
        svc.event(conn, "corrections.read", svc.project)
        return [
            dict(r)
            | {
                "decision": reviews[r["id"]]["decision"] if r["id"] in reviews else "pending",
                "review": reviews.get(r["id"]),
            }
            for r in conn.execute(
                select(corrections).order_by(corrections.c.created_at.desc())
            ).mappings()
        ]


@router.post(
    "/quality/corrections/{cid}/review",
    response_model=dict[str, Any],
    operation_id="reviewCorrection",
)
def review_correction(cid: UUID, body: CorrectionReview, svc: Scoped) -> Any:
    return qs.review_correction(svc, cid, body)


@router.post(
    "/quality/correction-releases",
    response_model=GoldVersionView,
    status_code=201,
    operation_id="releaseCorrections",
)
def release_corrections(body: CorrectionRelease, svc: Scoped) -> Any:
    return qs.release_corrections(svc, body)


@router.get(
    "/quality/correction-releases",
    response_model=list[GoldVersionView],
    operation_id="listCorrectionReleases",
)
def list_releases(svc: Scoped) -> Any:
    svc.require("quality.errors.read")
    svc.require("original.read")
    with svc.tx() as conn:
        svc.event(conn, "correction.releases.read", svc.project)
        return list(
            conn.execute(
                select(correction_releases).order_by(correction_releases.c.created_at.desc())
            ).mappings()
        )


@router.post(
    "/review-sets/{sid}/activity",
    response_model=dict[str, int],
    operation_id="recordReviewActivity",
)
def review_activity(sid: UUID, body: ReviewActivity, svc: Scoped) -> Any:
    return qs.record_activity(svc, sid, body.session_id, body.active_seconds)
