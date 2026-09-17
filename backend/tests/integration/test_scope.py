"""Run only against a disposable migrated local/CI database with DDL privileges."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="Set RUN_DB_TESTS=1 for disposable PostgreSQL integration",
)


def test_scope_foreign_keys_rls_and_pool_context():
    from app.core.config import Settings

    engine = create_engine(Settings().database_url, pool_size=1, max_overflow=0)
    tenant, other_tenant, project, other_project, patient = [str(uuid4()) for _ in range(5)]
    role = "scope_test_" + uuid4().hex
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS'))
            conn.execute(
                text(f'GRANT SELECT, INSERT ON tenants, projects, patients, encounters TO "{role}"')
            )
            conn.execute(
                text("INSERT INTO tenants VALUES (:t, 'Synthetic A'), (:o, 'Synthetic B')"),
                {"t": tenant, "o": other_tenant},
            )
            conn.execute(
                text("INSERT INTO projects VALUES (:p, :t, 'Project A'), (:o, :t, 'Project B')"),
                {"p": project, "o": other_project, "t": tenant},
            )
            conn.execute(
                text("INSERT INTO patients VALUES (:id, :t, :p, 'synthetic-001')"),
                {"id": patient, "t": tenant, "p": project},
            )
            # Composite FK rejects an encounter that links a patient from another project.
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(
                    text("INSERT INTO encounters VALUES (:id, :t, :p, :patient, 'outpatient')"),
                    {"id": str(uuid4()), "t": tenant, "p": other_project, "patient": patient},
                )
            conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            assert conn.execute(text("SELECT count(*) FROM patients")).scalar_one() == 0
            conn.execute(
                text(
                    "SELECT set_config('app.tenant_id', :t, true), "
                    "set_config('app.project_id', :p, true)"
                ),
                {"t": tenant, "p": project},
            )
            assert conn.execute(text("SELECT count(*) FROM patients")).scalar_one() == 1
            conn.execute(
                text("SELECT set_config('app.project_id', :p, true)"), {"p": other_project}
            )
            assert conn.execute(text("SELECT count(*) FROM patients")).scalar_one() == 0
            with pytest.raises(ProgrammingError), conn.begin_nested():
                conn.execute(
                    text("INSERT INTO patients VALUES (:id, :t, :p, 'forbidden')"),
                    {"id": str(uuid4()), "t": tenant, "p": project},
                )
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": other_tenant})
            assert conn.execute(text("SELECT count(*) FROM projects")).scalar_one() == 0
        finally:
            transaction.rollback()
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT nullif(current_setting('app.tenant_id', true), '')")
            ).scalar_one()
            is None
        )
        assert (
            conn.execute(
                text("SELECT nullif(current_setting('app.project_id', true), '')")
            ).scalar_one()
            is None
        )
    engine.dispose()
