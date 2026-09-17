"""Explicit, read-only synthetic contract server; never used by the live application."""

import json
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException

from app.contracts.models import Evidence, JobResponse
from app.core.config import get_settings
from app.main import create_app


def create_mock_app() -> FastAPI:
    settings = get_settings()
    if settings.app_env not in ("development", "test") or not settings.allow_synthetic_mock:
        raise RuntimeError("Mock requires development/test and ALLOW_SYNTHETIC_MOCK=true")
    app = create_app(settings)
    examples = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "examples"
    # app/contracts/mock.py -> backend is parents[2]; repo is parents[3].
    project = UUID("00000000-0000-4000-8000-000000000010")

    @app.get("/api/v1/projects/{project_id}/evidence/{evidence_id}", response_model=Evidence)
    def evidence(project_id: UUID, evidence_id: UUID) -> Evidence:
        sample = Evidence.model_validate_json(
            json.dumps(json.loads((examples / "evidence.json").read_text())["response"])
        )
        if project_id != project or evidence_id != sample.id:
            raise HTTPException(404)
        return sample

    @app.get("/api/v1/projects/{project_id}/jobs/{job_id}", response_model=JobResponse)
    def job(project_id: UUID, job_id: UUID) -> JobResponse:
        sample = JobResponse.model_validate(
            json.loads((examples / "job.json").read_text())["response"]
        )
        if project_id != project or job_id != sample.id:
            raise HTTPException(404)
        return sample

    return app
