from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["development", "test", "production"] = "development"
    identity_provider: Literal["synthetic", "oidc"] = "synthetic"
    extraction_provider: Literal["disabled", "synthetic", "gateway"] = "disabled"
    allow_synthetic_mock: bool = False
    database_url: str = "postgresql+psycopg://bljgh:local-only@127.0.0.1:25432/bljgh"
    redis_url: str = "redis://127.0.0.1:26379/0"
    s3_endpoint_url: str = "http://127.0.0.1:59000"
    s3_access_key: str = "local-bljgh"
    s3_secret_key: str = "local-only-change-me"
    s3_bucket: str = "clinical-documents"
    session_secret: str = "development-only-session-secret-change-before-production"
    synthetic_access_code: str = ""
    metrics_token: str = ""
    metrics_collect_dependencies: bool = False
    maintenance_mode: bool = False
    session_seconds: int = Field(default=3600, ge=60)
    public_origin: str = "http://127.0.0.1:13000"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_authorization_url: str = ""
    oidc_token_url: str = ""
    oidc_jwks_url: str = ""
    database_role: str = "bljgh_api"
    worker_database_role: str = "bljgh_worker"
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    max_document_pages: int = Field(default=100, gt=0)
    max_image_pixels: int = Field(default=40_000_000, gt=0)
    task_timeout_seconds: int = Field(default=180, gt=0)
    worker_concurrency: int = Field(default=2, ge=1, le=8)
    max_attempts: int = Field(default=3, ge=1, le=5)
    model_budget_cny: Literal[0] = 0
    ocr_provider: Literal["disabled", "tesseract"] = "disabled"
    ocr_language: str = "chi_sim+eng"
    ocr_timeout_seconds: int = Field(default=30, ge=1, le=120)
    extraction_gateway_url: str = ""
    extraction_gateway_token: str = ""
    extraction_authorization: str = ""
    extraction_model: str = ""
    extraction_timeout_seconds: int = Field(default=30, ge=1, le=60)
    extraction_max_chars: int = Field(default=60000, ge=1, le=200000)
    extraction_max_output_bytes: int = Field(default=2000000, ge=1024, le=8000000)
    extraction_max_repairs: int = Field(default=1, ge=0, le=1)
    extraction_requests_per_minute: int = Field(default=10, ge=1, le=120)
    extraction_max_inflight: int = Field(default=2, ge=1, le=8)
    terminology_version: Literal["synthetic-terms-1.0.0", "unmapped-1.0.0"] = "unmapped-1.0.0"

    @model_validator(mode="after")
    def safe_environment(self) -> Self:
        if self.app_env == "production" and (
            self.identity_provider == "synthetic"
            or self.extraction_provider == "synthetic"
            or self.allow_synthetic_mock
        ):
            raise ValueError("Synthetic identity/extraction/mock is forbidden in production")
        if self.extraction_provider == "gateway":
            from urllib.parse import urlsplit

            url = urlsplit(self.extraction_gateway_url)
            if (
                not self.extraction_authorization
                or not self.extraction_model
                or url.username
                or url.password
                or url.query
                or url.fragment
                or (
                    url.scheme != "https"
                    and not (
                        self.app_env != "production"
                        and url.scheme == "http"
                        and url.hostname in ("127.0.0.1", "localhost")
                    )
                )
            ):
                raise ValueError("Approved gateway, pinned model and authorization are required")
        if self.app_env == "production" and self.terminology_version.startswith("synthetic"):
            raise ValueError("Synthetic terminology is forbidden in production")
        if self.app_env == "production":
            from urllib.parse import urlsplit

            for endpoint in (
                self.public_origin,
                self.oidc_issuer,
                self.oidc_jwks_url,
                self.oidc_authorization_url,
                self.oidc_token_url,
            ):
                url = urlsplit(endpoint)
                if (
                    url.scheme != "https"
                    or not url.hostname
                    or url.username
                    or url.password
                    or url.fragment
                ):
                    raise ValueError(
                        "Production identity endpoints require HTTPS without credentials"
                    )
            if self.s3_access_key == "local-bljgh" or self.s3_secret_key == "local-only-change-me":
                raise ValueError("Production requires independent storage credentials")
            if len(self.metrics_token) < 32:
                raise ValueError("Production requires an independent monitoring token")
            if len(self.session_secret) < 40 or self.session_secret.startswith("development-"):
                raise ValueError("Production requires an independent session secret")
            if not all(
                (
                    self.oidc_issuer,
                    self.oidc_client_id,
                    self.oidc_jwks_url,
                    self.oidc_authorization_url,
                    self.oidc_token_url,
                )
            ):
                raise ValueError("OIDC configuration is required")
            if not self.public_origin.startswith("https://"):
                raise ValueError("Production requires HTTPS")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
