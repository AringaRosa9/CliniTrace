"""Create an independent synthetic browser-test reviewer; never usable in production."""

import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from app.core.config import Settings
from app.db.models import projects
from app.db.s1 import memberships, users
from app.db.s4 import annotations, samples
from app.modules.documents.service import Service
from app.modules.quality.schema import AnnotationCreate
from app.modules.quality.service import annotate
from sqlalchemy import create_engine, select

cfg = Settings(_env_file=None)
if cfg.app_env == "production" or cfg.identity_provider != "synthetic":
    raise SystemExit("Only local synthetic tests")
project_id, sample_id = UUID(sys.argv[1]), UUID(sys.argv[2])
actor = uuid4()
with create_engine(cfg.database_url).begin() as conn:
    sample = (
        conn.execute(
            select(samples).where(
                samples.c.id == sample_id, samples.c.project_id == project_id
            )
        )
        .mappings()
        .one()
    )
    if (
        not sample["payload"]["synthetic"]
        or sample["payload"]["source"] != "浏览器合成工程样本"
    ):
        raise SystemExit("Browser synthetic fixture required")
    project = (
        conn.execute(select(projects).where(projects.c.id == project_id))
        .mappings()
        .one()
    )
    first = (
        conn.execute(
            select(annotations).where(
                annotations.c.sample_id == sample_id,
                annotations.c.stage == "annotation",
            )
        )
        .mappings()
        .one()
    )
    conn.execute(
        users.insert().values(
            id=actor,
            issuer="synthetic",
            subject=str(actor),
            display_name="S4 independent synthetic test reviewer",
        )
    )
    conn.execute(
        memberships.insert().values(
            id=uuid4(),
            tenant_id=project["tenant_id"],
            project_id=project_id,
            user_id=actor,
            project_name=project["name"],
            roles=["synthetic-test-reviewer"],
            capabilities=["quality.review", "original.read"],
        )
    )
svc = Service(cfg, actor, project_id, uuid4())
annotate(
    svc,
    sample_id,
    AnnotationCreate(
        stage="review",
        labels=first["payload"]["labels"],
        reason="独立身份合成浏览器夹具验证；不是医学复核",
        active_seconds=0,
    ),
)
print("Synthetic independent review recorded.")
