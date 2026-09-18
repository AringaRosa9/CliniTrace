"""Explicit administrator CLI for approved OIDC mappings and project capabilities."""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.core.config import Settings
from app.db.models import projects
from app.db.s1 import audit, memberships, users
from app.modules.documents.service import now
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("manifest", type=Path, help="Approved JSON membership manifest")
args = parser.parse_args()
value = json.loads(args.manifest.read_text())
required = {
    "project_id",
    "issuer",
    "subject",
    "display_name",
    "roles",
    "capabilities",
    "operator_id",
    "reason",
}
if set(value) != required or not value["reason"].strip():
    raise SystemExit("Manifest fields or reason invalid")
allowed = {"import", "documents.read", "original.read", "audit.read", "review", "export.reviewed", "export.draft", "templates.manage", "terminology.manage", "terminology.map", "quality.read", "quality.errors.read", "quality.annotate", "quality.review", "quality.adjudicate", "quality.manage"}
if not set(value["capabilities"]) <= allowed:
    raise SystemExit("Unknown project capability")
cfg = Settings(_env_file=Path(__file__).resolve().parents[2] / "backend" / ".env")
if cfg.app_env == "production" and value["issuer"] != cfg.oidc_issuer:
    raise SystemExit("Issuer must match institution configuration")
with create_engine(cfg.database_url).begin() as conn:
    project = (
        conn.execute(select(projects).where(projects.c.id == UUID(value["project_id"])))
        .mappings()
        .one()
    )
    actor = UUID(value["operator_id"])
    if not conn.execute(select(users.c.id).where(users.c.id == actor)).first():
        raise SystemExit("Approved operator mapping required")
    user = conn.execute(
        select(users.c.id).where(
            users.c.issuer == value["issuer"], users.c.subject == value["subject"]
        )
    ).scalar_one_or_none()
    if user is None:
        user = uuid4()
        conn.execute(
            users.insert().values(
                id=user,
                issuer=value["issuer"],
                subject=value["subject"],
                display_name=value["display_name"],
            )
        )
    existing = (
        conn.execute(
            select(memberships).where(
                memberships.c.user_id == user, memberships.c.project_id == project["id"]
            )
        )
        .mappings()
        .first()
    )
    member = {
        "id": uuid4(),
        "tenant_id": project["tenant_id"],
        "project_id": project["id"],
        "user_id": user,
        "project_name": project["name"],
        "roles": value["roles"],
        "capabilities": value["capabilities"],
    }
    conn.execute(
        insert(memberships)
        .values(**member)
        .on_conflict_do_update(
            index_elements=["project_id", "user_id"],
            set_={"roles": value["roles"], "capabilities": value["capabilities"]},
        )
    )
    conn.execute(
        audit.insert().values(
            id=uuid4(),
            tenant_id=project["tenant_id"],
            project_id=project["id"],
            actor_id=actor,
            action="membership.provision",
            target_id=user,
            request_id=uuid4(),
            created_at=now(),
            details={
                "reason": value["reason"],
                "before": existing["capabilities"] if existing else [],
                "after": value["capabilities"],
            },
        )
    )
print("Approved identity mapping and project capabilities saved; audit appended.")
