from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import select

from app.db.s4 import template_versions, templates
from app.modules.documents.service import Service, now
from app.modules.extractions.pipeline import digest, model_for
from app.modules.templates.schema import TemplateCreate, TemplatePublish, TemplateUpdate


def validate(kind: str, content: dict[str, Any]) -> dict[str, Any]:
    schema = content["schema_definition"]
    base = model_for(kind + "-1.0.0").model_json_schema()
    errors: list[str] = []

    # Schema may tighten supported fields, but cannot remove the grounding/semantic contract.
    def compatible(old: Any, new: Any, path: str = "$") -> None:
        if isinstance(old, dict):
            if not isinstance(new, dict):
                errors.append(path + "：不能改变结构")
                return
            for key, value in old.items():
                if key in ("title", "description", "default"):
                    continue
                if key == "required":
                    if not set(value) <= set(new.get(key, [])):
                        errors.append(path + "：不能移除必填约束")
                elif key == "enum":
                    if path.startswith("$.$defs.") and set(new.get(key, [])) != set(value):
                        errors.append(path + "：事实语义枚举必须完整保留，包括否定和明确未知")
                    if not new.get(key) or not set(new[key]) <= set(value):
                        errors.append(path + "：枚举只能收窄")
                elif key in ("minItems", "minLength"):
                    if not isinstance(new.get(key), int) or new[key] < value:
                        errors.append(path + "：不能放宽证据约束")
                elif key not in new:
                    errors.append(path + "." + key + "：缺少契约字段")
                else:
                    compatible(value, new[key], path + "." + key)
            if path.endswith(".properties") and set(new) != set(old):
                errors.append(path + "：新增字段需要升级抽取适配器")
        elif old != new:
            errors.append(path + "：不兼容的类型或语义变更")

    def refs(value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ("$ref", "$dynamicRef") and (
                    not isinstance(v, str) or not v.startswith("#/$defs/")
                ):
                    errors.append("仅允许本地 $defs 引用")
                refs(v)
        elif isinstance(value, list):
            for v in value:
                refs(v)

    refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
        compatible(base, schema)
        if not errors:
            validator = Draft202012Validator(schema)
            if any(not validator.is_valid(x) for x in content["positive_examples"]):
                errors.append("正例未通过 Schema 校验")
            if any(validator.is_valid(x) for x in content["negative_examples"]):
                errors.append("反例必须被 Schema 拒绝")
    except (SchemaError, ValueError, TypeError, RecursionError):
        errors.append("Schema 无效")
    if not all(s.strip() for s in content["evidence_rules"]):
        errors.append("证据规则不能为空")
    return {
        "valid": not errors,
        "errors": errors[:30],
        "compatibility": "compatible" if not errors else "blocked",
    }


def create(svc: Service, body: TemplateCreate) -> dict[str, Any]:
    svc.require("templates.manage")
    row = dict(
        id=uuid4(),
        **svc.scope,
        name=body.name,
        document_type=body.document_type,
        revision=1,
        payload=body.model_dump(exclude={"name", "document_type"}),
        actor_id=svc.actor,
        created_at=now(),
    )
    with svc.tx() as conn:
        conn.execute(templates.insert().values(**row))
        svc.event(conn, "template.create", row["id"])
    return row


def update(svc: Service, tid: UUID, body: TemplateUpdate) -> dict[str, Any]:
    svc.require("templates.manage")
    with svc.tx() as conn:
        row = (
            conn.execute(select(templates).where(templates.c.id == tid).with_for_update())
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404)
        if row["revision"] != body.expected_revision:
            raise HTTPException(409)
        changes = dict(
            payload=body.model_dump(exclude={"expected_revision"}), revision=row["revision"] + 1
        )
        conn.execute(templates.update().where(templates.c.id == tid).values(**changes))
        svc.event(conn, "template.edit", tid, {"revision": changes["revision"]})
        return dict(row) | changes


def publish(svc: Service, tid: UUID, body: TemplatePublish) -> dict[str, Any]:
    svc.require("templates.manage")
    with svc.tx() as conn:
        row = (
            conn.execute(select(templates).where(templates.c.id == tid).with_for_update())
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404)
        if row["revision"] != body.expected_revision:
            raise HTTPException(409)
        if not body.version.startswith(row["document_type"] + "-") or body.version.endswith(
            "-1.0.0"
        ):
            raise HTTPException(422)
        if not validate(row["document_type"], row["payload"])["valid"]:
            raise HTTPException(422)
        payload = row["payload"] | {
            "document_type": row["document_type"],
            "guide_version": body.version,
            "draft_revision": row["revision"],
        }
        result = dict(
            id=uuid4(),
            **svc.scope,
            template_id=tid,
            version=body.version,
            payload=payload,
            digest=digest(payload),
            actor_id=svc.actor,
            created_at=now(),
        )
        conn.execute(template_versions.insert().values(**result))
        svc.event(conn, "template.publish", result["id"], {"digest": result["digest"]})
        return result
