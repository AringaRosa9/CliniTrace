from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contracts.mock import create_mock_app
from app.core.config import Settings, get_settings
from app.main import create_app


def test_health_and_uniform_error():
    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.get("/api/v1/health")
    assert response.json()["status"] == "ok"
    assert response.headers["cache-control"] == "no-store"
    UUID(response.headers["x-request-id"])
    response = client.get(
        "/api/v1/unknown?secret=clinical-text", headers={"X-Request-ID": "unsafe"}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert "clinical-text" not in response.text
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert "/api/v1/projects/{project_id}/documents" in client.get("/openapi.json").json()["paths"]
    assert client.get("/api/v1/me").status_code == 401


def test_production_rejects_synthetic():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            identity_provider="oidc",
            allow_synthetic_mock=True,
        )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, model_budget_cny=1)


def test_mock_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("ALLOW_SYNTHETIC_MOCK", "false")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        create_mock_app()
    monkeypatch.setenv("ALLOW_SYNTHETIC_MOCK", "true")
    get_settings.cache_clear()
    client = TestClient(create_mock_app())
    path = (
        "/api/v1/projects/00000000-0000-4000-8000-000000000010"
        "/evidence/00000000-0000-4000-8000-000000000001"
    )
    assert client.get(path).json()["quote"] == "剂量不详"
    assert client.get(path.replace("000000000010", "000000000011")).status_code == 404
    get_settings.cache_clear()
