from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, text

from app.core.config import Settings


@lru_cache
def engine_for(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


@contextmanager
def transaction(
    settings: Settings,
    *,
    tenant: UUID | None = None,
    project: UUID | None = None,
    user: UUID | None = None,
    issuer: str = "",
    subject: str = "",
    worker: bool = False,
) -> Iterator[Connection]:
    with engine_for(settings.database_url).begin() as conn:
        if settings.app_env == "production":
            login_elevated = conn.execute(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = session_user")
            ).scalar_one()
            if login_elevated:
                raise RuntimeError("Production application login must not bypass RLS")
        role = settings.worker_database_role if worker else settings.database_role
        if role not in ("bljgh_api", "bljgh_worker"):
            raise RuntimeError("Unknown application database role")
        conn.execute(text(f"SET LOCAL ROLE {role}"))
        elevated = conn.execute(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).scalar_one()
        if elevated:
            raise RuntimeError("Application database role must enforce RLS")
        for key, value in {
            "tenant_id": tenant,
            "project_id": project,
            "user_id": user,
            "issuer": issuer,
            "subject": subject,
        }.items():
            conn.execute(
                text("SELECT set_config(:key, :value, true)"),
                {"key": f"app.{key}", "value": str(value) if value else ""},
            )
        yield conn
