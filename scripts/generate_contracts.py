"""Deterministic export; never opens a connection or performs model calls."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.contracts.api import contract_app
from app.contracts.models import LabTemplate, OutpatientTemplate
from app.core.config import Settings
from app.main import create_app


def write(path: str, value: object) -> None:
    (ROOT / path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


schema = contract_app.openapi()
schema["info"]["description"] = "S1-S2 runtime plus planned S3 review/export contracts."
schema["components"]["securitySchemes"] = {
    "OIDC": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
}
write("packages/contracts/openapi/v1.json", schema)
write(
    "packages/contracts/openapi/runtime.json",
    create_app(Settings(_env_file=None)).openapi(),
)
for name, model in [("outpatient", OutpatientTemplate), ("laboratory", LabTemplate)]:
    value = model.model_json_schema()
    value["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    value["$id"] = f"urn:bljgh:schema:{name}:1.0.0"
    write(f"packages/contracts/json-schema/{name}-1.0.0.json", value)
