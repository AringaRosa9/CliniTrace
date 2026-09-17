import base64
import hashlib
import hmac
import secrets
from typing import Annotated, Any, Literal
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse, Response
from redis import Redis
from sqlalchemy import select, text

from app.db.models import encounters, patients
from app.db.s1 import audit, encounter_details, memberships
from app.db.session import transaction
from app.integrations.storage.s3 import Storage
from app.modules.documents.schema import (
    Association,
    AuditView,
    AuthConfig,
    DocumentList,
    DocumentView,
    EncounterCreate,
    EncounterView,
    JobView,
    LoginRequest,
    Me,
    ParseView,
    PatientCreate,
    PatientView,
    ProjectView,
    Ready,
    UploadResult,
)
from app.modules.documents.service import Service
from app.modules.identity.service import (
    decode,
    digest,
    issue_session,
    mapped_user,
    principal,
    signed,
    verify_oidc,
)
from app.modules.jobs.service import control, job_view

router = APIRouter(prefix="/api/v1")
Actor = Annotated[dict[str, Any], Depends(principal)]


def service(project_id: UUID, request: Request, actor: Actor) -> Service:
    return Service(request.app.state.settings, actor["id"], project_id, request.state.request_id)


Scoped = Annotated[Service, Depends(service)]


@router.get("/auth/config", response_model=AuthConfig, operation_id="getAuthConfig")
def auth_config(request: Request) -> Any:
    return {"provider": request.app.state.settings.identity_provider}


@router.post("/auth/synthetic", status_code=204, operation_id="syntheticLogin")
def synthetic_login(body: LoginRequest, request: Request) -> Response:
    cfg = request.app.state.settings
    if cfg.identity_provider != "synthetic" or cfg.app_env == "production":
        raise HTTPException(404)
    if request.headers.get("Origin") != cfg.public_origin:
        raise HTTPException(403)
    if not cfg.synthetic_access_code or not hmac.compare_digest(
        body.access_code, cfg.synthetic_access_code
    ):
        raise HTTPException(401)
    user = mapped_user(cfg, "synthetic", "operator")
    response = Response(status_code=204)
    issue_session(cfg, response, user)
    return response


@router.get("/auth/oidc/login", operation_id="oidcLogin")
def oidc_login(request: Request) -> RedirectResponse:
    cfg = request.app.state.settings
    if cfg.identity_provider != "oidc":
        raise HTTPException(404)
    state, nonce, verifier = (
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(48),
    )
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    query = urlencode(
        {
            "response_type": "code",
            "scope": "openid profile",
            "client_id": cfg.oidc_client_id,
            "redirect_uri": cfg.public_origin + "/api/v1/auth/oidc/callback",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(cfg.oidc_authorization_url + "?" + query)
    response.set_cookie(
        "bljgh_oidc",
        signed(cfg, {"sub": state, "nonce": nonce, "verifier": verifier, "kind": "oidc"}, 300),
        httponly=True,
        secure=cfg.app_env == "production",
        samesite="lax",
        max_age=300,
        path="/api/v1/auth/oidc",
    )
    return response


@router.get("/auth/oidc/callback", operation_id="oidcCallback")
def oidc_callback(request: Request, code: str, state: str) -> RedirectResponse:
    cfg = request.app.state.settings
    if cfg.identity_provider != "oidc":
        raise HTTPException(404)
    claims = decode(cfg, request.cookies.get("bljgh_oidc", ""))
    if claims.get("kind") != "oidc" or not hmac.compare_digest(claims["sub"], state):
        raise HTTPException(401)
    try:
        response = httpx.post(
            cfg.oidc_token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": cfg.oidc_client_id,
                "client_secret": cfg.oidc_client_secret,
                "redirect_uri": cfg.public_origin + "/api/v1/auth/oidc/callback",
                "code_verifier": claims["verifier"],
            },
            timeout=8,
        )
        response.raise_for_status()
        user = verify_oidc(cfg, response.json()["id_token"], claims["nonce"])
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(401) from None
    redirect = RedirectResponse("/documents", status_code=303)
    redirect.delete_cookie("bljgh_oidc", path="/api/v1/auth/oidc")
    issue_session(cfg, redirect, user)
    return redirect


@router.post("/auth/logout", status_code=204, operation_id="logout")
def logout(request: Request, actor: Actor) -> Response:
    cfg = request.app.state.settings
    token = request.cookies.get("bljgh_session", "")
    Redis.from_url(cfg.redis_url).setex("revoked:" + digest(token), cfg.session_seconds, "1")
    response = Response(status_code=204)
    response.delete_cookie("bljgh_session", path="/api/v1")
    return response


@router.get("/me", response_model=Me, operation_id="getMe")
def me(request: Request, actor: Actor) -> Any:
    with transaction(request.app.state.settings, user=actor["id"]) as conn:
        rows = (
            conn.execute(select(memberships).where(memberships.c.user_id == actor["id"]))
            .mappings()
            .all()
        )
    return {
        "id": actor["id"],
        "display_name": actor["display_name"],
        "csrf_token": actor["csrf_token"],
        "projects": [
            {
                "id": r["project_id"],
                "name": r["project_name"],
                "roles": r["roles"],
                "capabilities": r["capabilities"],
            }
            for r in rows
        ],
    }


@router.get("/projects", response_model=list[ProjectView], operation_id="listProjects")
def list_projects(request: Request, actor: Actor) -> Any:
    return me(request, actor)["projects"]


@router.get("/ready", response_model=Ready, operation_id="getReady")
def ready(request: Request) -> Ready:
    cfg = request.app.state.settings
    try:
        with transaction(cfg) as conn:
            conn.execute(text("SELECT 1"))
        Redis.from_url(cfg.redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
        Storage(cfg).client.head_bucket(Bucket=cfg.s3_bucket)
    except Exception:
        raise HTTPException(503) from None
    return Ready()


@router.post(
    "/projects/{project_id}/patients",
    response_model=PatientView,
    status_code=201,
    operation_id="createPatient",
)
def create_patient(body: PatientCreate, svc: Scoped) -> Any:
    return svc.patient(body)


@router.get(
    "/projects/{project_id}/patients", response_model=list[PatientView], operation_id="listPatients"
)
def list_patients(
    svc: Scoped,
    query: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Any:
    svc.require("import")
    with svc.tx() as conn:
        return (
            conn.execute(
                select(patients.c.id, patients.c.patient_key)
                .where(patients.c.patient_key.icontains(query, autoescape=True))
                .order_by(patients.c.patient_key, patients.c.id)
                .limit(limit)
            )
            .mappings()
            .all()
        )


@router.post(
    "/projects/{project_id}/encounters",
    response_model=EncounterView,
    status_code=201,
    operation_id="createEncounter",
)
def create_encounter(body: EncounterCreate, svc: Scoped) -> Any:
    return svc.encounter(body)


@router.get(
    "/projects/{project_id}/encounters",
    response_model=list[EncounterView],
    operation_id="listEncounters",
)
def list_encounters(patient_id: UUID, svc: Scoped) -> Any:
    svc.require("import")
    with svc.tx() as conn:
        return (
            conn.execute(
                select(
                    encounters.c.id,
                    encounters.c.patient_id,
                    encounters.c.kind,
                    encounter_details.c.occurred_on,
                    encounter_details.c.department,
                )
                .join(encounter_details, encounters.c.id == encounter_details.c.encounter_id)
                .where(encounters.c.patient_id == patient_id)
                .order_by(encounters.c.id)
            )
            .mappings()
            .all()
        )


@router.post(
    "/projects/{project_id}/documents",
    response_model=UploadResult,
    status_code=202,
    operation_id="uploadDocument",
)
def upload_document(
    svc: Scoped,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    patient_id: Annotated[UUID, Form()],
    encounter_id: Annotated[UUID, Form()],
    document_type: Annotated[Literal["outpatient", "laboratory"], Form()],
    source: Annotated[str, Form(min_length=1, max_length=200, pattern=r"\S")],
    authorization_reference: Annotated[str, Form(min_length=1, max_length=200, pattern=r"\S")],
    file: Annotated[UploadFile, File()],
) -> Any:
    filename = (file.filename or "").replace("\\", "/").split("/")[-1]
    if not filename or len(filename) > 255 or any(ord(c) < 32 for c in filename):
        raise HTTPException(422)
    return svc.upload(
        file.file,
        filename,
        idempotency_key,
        patient_id,
        encounter_id,
        document_type,
        source,
        authorization_reference,
    )


@router.get(
    "/projects/{project_id}/documents", response_model=DocumentList, operation_id="listDocuments"
)
def list_documents(
    svc: Scoped,
    query: Annotated[str, Query(max_length=200)] = "",
    status: Literal[
        "queued",
        "parsing",
        "parsed",
        "needs_ocr",
        "parse_failed",
        "cancelled",
        "extracting",
        "pending_review",
        "extraction_failed",
    ]
    | None = None,
    document_type: Literal["outpatient", "laboratory"] | None = None,
    cursor: Annotated[str | None, Query(max_length=1000)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Any:
    return svc.listing(query, status, document_type, cursor, limit)


@router.get(
    "/projects/{project_id}/documents/{document_id}",
    response_model=DocumentView,
    operation_id="getDocument",
)
def get_document(document_id: UUID, svc: Scoped) -> Any:
    result = svc.listing("", None, None, None, 1, document_id)["items"]
    if not result:
        raise HTTPException(404)
    return result[0]


@router.patch(
    "/projects/{project_id}/documents/{document_id}/association",
    response_model=DocumentView,
    operation_id="associateDocument",
)
def association(document_id: UUID, body: Association, svc: Scoped) -> Any:
    return svc.associate(document_id, body)


@router.get("/projects/{project_id}/documents/{document_id}/original", operation_id="getOriginal")
def original(document_id: UUID, svc: Scoped) -> Response:
    data, mime = svc.original(document_id)
    return Response(
        data,
        media_type=mime,
        headers={
            "Content-Disposition": 'attachment; filename="document"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.get(
    "/projects/{project_id}/parse-artifacts/{artifact_id}",
    response_model=ParseView,
    operation_id="getParseArtifact",
)
def preview(artifact_id: UUID, svc: Scoped) -> Any:
    return svc.preview(artifact_id)


@router.get(
    "/projects/{project_id}/parse-artifacts/{artifact_id}/pages/{page}", operation_id="getPageImage"
)
def page_image(artifact_id: UUID, page: int, svc: Scoped) -> Response:
    return Response(svc.preview(artifact_id, page), media_type="image/png")


@router.get("/projects/{project_id}/jobs/{job_id}", response_model=JobView, operation_id="getJob")
def get_job(job_id: UUID, svc: Scoped) -> Any:
    return job_view(svc, job_id)


@router.post(
    "/projects/{project_id}/jobs/{job_id}/cancel", response_model=JobView, operation_id="cancelJob"
)
def cancel_job(job_id: UUID, svc: Scoped) -> Any:
    return control(svc, job_id, "cancel")


@router.post(
    "/projects/{project_id}/jobs/{job_id}/retry", response_model=JobView, operation_id="retryJob"
)
def retry_job(job_id: UUID, svc: Scoped) -> Any:
    return control(svc, job_id, "retry")


@router.get(
    "/projects/{project_id}/audit-events", response_model=list[AuditView], operation_id="listAudit"
)
def list_audit(svc: Scoped, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> Any:
    svc.require("audit.read")
    with svc.tx() as conn:
        return [
            dict(row)
            for row in conn.execute(
                select(
                    audit.c.id,
                    audit.c.actor_id,
                    audit.c.action,
                    audit.c.target_id,
                    audit.c.request_id,
                    audit.c.details,
                    audit.c.created_at,
                )
                .order_by(audit.c.created_at.desc(), audit.c.id)
                .limit(limit)
            ).mappings()
        ]
