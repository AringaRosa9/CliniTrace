"""Real RLS, source evidence, independent identities, immutable releases and contamination gates."""

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.db.s1 import memberships, users
from app.db.s4 import evaluations, template_versions
from app.db.session import transaction
from app.modules.identity.service import signed
from app.workers.runtime import execute
from tests.integration.test_s1 import env as s1_env
from tests.integration.test_s2 import extract_doc
from tests.integration.test_s3 import edit, get, post

env = s1_env
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="Requires migrated local infrastructure"
)
CAPS = [
    "import",
    "documents.read",
    "original.read",
    "review",
    "audit.read",
    "templates.manage",
    "terminology.manage",
    "terminology.map",
    "quality.read",
    "quality.errors.read",
    "quality.annotate",
    "quality.review",
    "quality.adjudicate",
    "quality.manage",
]


def setup(env):
    with env["engine"].begin() as conn:
        conn.execute(
            memberships.update()
            .where(memberships.c.user_id == env["actor"])
            .values(capabilities=CAPS)
        )
    return extract_doc(env)


def as_actor(env, actor=None, capabilities=None):
    actor = actor or uuid4()
    with env["engine"].begin() as conn:
        if not conn.execute(select(users.c.id).where(users.c.id == actor)).first():
            conn.execute(
                users.insert().values(
                    id=actor,
                    issuer="synthetic",
                    subject=str(actor),
                    display_name="Independent synthetic reviewer",
                )
            )
            conn.execute(
                memberships.insert().values(
                    id=uuid4(),
                    tenant_id=env["tenant"],
                    project_id=env["project"],
                    user_id=actor,
                    project_name="Test",
                    roles=["reviewer"],
                    capabilities=capabilities or CAPS,
                )
            )
    token = signed(
        env["cfg"],
        {"sub": str(actor), "iss": "synthetic", "kind": "session", "csrf": "test-csrf"},
        3600,
    )
    env["client"].cookies.set("bljgh_session", token, path="/api/v1")
    return actor


def template(env):
    base = env["client"].get(env["base"] + "/templates/base/outpatient").json()
    body = {
        "name": "合成门诊",
        "document_type": "outpatient",
        "schema_definition": base,
        "guide": "核对原文，不推测未知值",
        "positive_examples": [
            {
                "schema_version": "outpatient-1.0.0",
                "diagnoses": [],
                "history": [],
                "medications": [],
                "duration": [],
            }
        ],
        "negative_examples": [{}],
        "evidence_rules": ["同一解析版本的原文证据"],
    }
    draft = post(env, "/templates", body, 201)
    version = post(
        env,
        f"/templates/{draft['id']}/publish",
        {"expected_revision": 1, "version": "outpatient-1.1.0"},
        201,
    )
    return draft, version, body


def sample(env, accepted, version, split="test"):
    return post(
        env,
        "/quality/samples",
        {
            "run_id": accepted["run_id"],
            "patient_group": "synthetic-patient-a",
            "split": split,
            "source": "合成工程夹具",
            "authorization_reference": "synthetic-only",
            "synthetic": True,
            "difficulty_tags": ["negation", "unknown_dose"],
            "template_version_id": version["id"],
        },
        201,
    )


def labels(env, accepted):
    value = env["client"].get(env["base"] + "/extractions/" + accepted["run_id"]).json()
    return {"facts": value["facts"], "relations": value["relations"], "codings": []}


def test_template_publish_race_terms_authorization_and_pinned_extraction(env):
    doc, accepted = setup(env)
    draft, version, body = template(env)
    assert post(env, f"/templates/{draft['id']}/validate", None)["valid"]
    body.pop("name")
    body.pop("document_type")
    body["guide"] = "第二版指南"
    url = env["base"] + f"/templates/{draft['id']}"
    with ThreadPoolExecutor(2) as pool:
        codes = list(
            pool.map(
                lambda _: (
                    env["client"].patch(url, json={**body, "expected_revision": 1}).status_code
                ),
                range(2),
            )
        )
    assert sorted(codes) == [200, 409]
    assert (
        env["client"].get(env["base"] + "/template-versions").json()[0]["payload"]["guide"]
        != body["guide"]
    )
    terms = {
        "version": "test-terms-1.0.0",
        "system": "synthetic",
        "authorization_reference": "",
        "synthetic": True,
        "terms": [
            {
                "code": "SYN-1",
                "display": "2型糖尿病",
                "aliases": ["2型糖尿病"],
                "context": "合成诊断",
            }
        ],
    }
    t = post(env, "/terminology/versions", terms, 201)
    post(env, f"/terminology/versions/{t['id']}/activate", None, 422)
    terms.update(version="test-terms-1.1.0", authorization_reference="synthetic-only")
    t = post(env, "/terminology/versions", terms, 201)
    post(env, f"/terminology/versions/{t['id']}/activate", None)
    response = env["client"].post(
        env["base"] + f"/documents/{doc['id']}/extractions",
        json={"parse_artifact_id": doc["artifact_id"], "template_version": version["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    run = response.json()
    execute(str(env["tenant"]), str(env["project"]), run["job_id"], env["cfg"])
    value = env["client"].get(env["base"] + "/extractions/" + run["run_id"]).json()
    assert value["status"] == "succeeded", value
    assert value["configuration"]["template_digest"] == version["digest"]
    assert value["configuration"]["terminology_digest"] == t["digest"]
    assert value["codings"][0]["candidates"][0]["code"] == "SYN-1"
    sid = env["client"].get(env["base"] + "/review-sets").json()["items"][0]["review_set_id"]
    w = get(env, sid)
    f = next(f for f in w["facts"] if f["field_path"] == "diagnoses")
    mapping = {
        "revision_id": f["revision_id"],
        "terminology_id": t["id"],
        "expected_sequence": 0,
        "status": "confirmed",
        "code": "INVENTED",
        "reason": "核对合成原文",
    }
    post(env, f"/facts/{f['fact_id']}/mapping", mapping, 422)
    mapping["code"] = "SYN-1"
    post(env, f"/facts/{f['fact_id']}/mapping", mapping, 201)
    post(env, f"/facts/{f['fact_id']}/mapping", mapping, 409)
    assert get(env, sid)["scope_revision"] == w["scope_revision"] + 1
    assert (
        next(c for c in get(env, sid)["codings"] if c["fact_id"] == f["fact_id"])["status"]
        == "confirmed"
    )
    with (
        pytest.raises(DBAPIError),
        transaction(env["cfg"], tenant=env["tenant"], project=env["project"]) as conn,
    ):
        conn.execute(template_versions.delete())


def test_independent_review_adjudication_freeze_replay_and_permissions(env):
    _, accepted = setup(env)
    _, version, _ = template(env)
    s = sample(env, accepted, version)
    annotation = {
        "stage": "annotation",
        "labels": labels(env, accepted),
        "reason": "合成标注",
        "active_seconds": 12,
    }
    post(env, f"/quality/samples/{s['id']}/annotations", annotation, 201)
    annotation["stage"] = "review"
    post(env, f"/quality/samples/{s['id']}/annotations", annotation, 403)
    reviewer = as_actor(env)
    disputed = deepcopy(annotation)
    disputed["labels"]["facts"] = []
    disputed["labels"]["relations"] = []
    assert (
        post(env, f"/quality/samples/{s['id']}/annotations", disputed, 201)["state"] == "disputed"
    )
    post(env, "/quality/datasets", {"name": "未裁决", "sample_ids": [s["id"]]}, 422)
    annotation["stage"] = "adjudication"
    post(env, f"/quality/samples/{s['id']}/annotations", annotation, 403)
    as_actor(env)
    assert (
        post(env, f"/quality/samples/{s['id']}/annotations", annotation, 201)["state"] == "accepted"
    )
    gold = post(env, "/quality/datasets", {"name": "冻结合成测试 v1", "sample_ids": [s["id"]]}, 201)
    body = {
        "name": "同版本重放",
        "dataset_id": gold["id"],
        "predictions": {s["id"]: accepted["run_id"]},
    }
    a = post(env, "/quality/evaluations", body, 201)
    b = post(env, "/quality/evaluations", body, 201)
    assert a["payload"]["report"] == b["payload"]["report"]
    assert a["payload"]["report"]["micro"]["f1"] == 1
    assert "errors" not in a["payload"]["report"] and "records" not in a["payload"]
    error_view = env["client"].get(env["base"] + f"/quality/evaluations/{a['id']}/errors").json()
    assert len(error_view["payload"]["records"]) == 1
    assert (
        env["client"]
        .get(env["base"] + f"/quality/evaluations/{a['id']}/compare/{b['id']}")
        .status_code
        == 200
    )
    as_actor(env, capabilities=["quality.read"])
    assert env["client"].get(env["base"] + "/quality/evaluations").status_code == 200
    assert (
        env["client"].get(env["base"] + f"/quality/evaluations/{a['id']}/errors").status_code == 403
    )
    assert (
        env["client"].get(env["base"] + f"/quality/datasets/{gold['id']}/manifest").status_code
        == 403
    )
    assert (
        env["client"]
        .get(f"/api/v1/projects/{env['other_project']}/quality/evaluations")
        .status_code
        == 404
    )
    with transaction(env["cfg"], tenant=env["other_tenant"], project=env["other_project"]) as conn:
        assert not conn.execute(select(evaluations)).all()
    with (
        pytest.raises(DBAPIError),
        transaction(env["cfg"], tenant=env["tenant"], project=env["project"]) as conn,
    ):
        conn.execute(evaluations.delete())
    assert reviewer != env["actor"]


@pytest.mark.parametrize("split", ["test", "train"])
def test_correction_second_review_and_bidirectional_contamination_gate(env, split):
    _, accepted = setup(env)
    _, version, _ = template(env)
    sample(env, accepted, version, split)
    sid = env["client"].get(env["base"] + "/review-sets").json()["items"][0]["review_set_id"]
    w = get(env, sid)
    assert edit(env, w, w["facts"][0]).status_code == 200
    candidate = env["client"].get(env["base"] + "/quality/corrections").json()[0]
    body = {"decision": "accepted", "reason": "独立核对证据"}
    post(env, f"/quality/corrections/{candidate['id']}/review", body, 403)
    release = {"name": "纠错回流", "candidate_ids": [candidate["id"]]}
    post(env, "/quality/correction-releases", release, 422)
    as_actor(env)
    post(env, f"/quality/corrections/{candidate['id']}/review", body)
    post(env, "/quality/correction-releases", release, 409 if split == "test" else 201)
    if split == "train":
        response = env["client"].post(
            env["base"] + "/quality/samples",
            json={
                "run_id": accepted["run_id"],
                "patient_group": "synthetic-patient-a",
                "split": "test",
                "source": "synthetic",
                "authorization_reference": "synthetic",
                "synthetic": True,
                "difficulty_tags": ["unknown"],
                "template_version_id": version["id"],
            },
        )
        assert response.status_code == 409


def test_patient_group_split_race_and_review_activity_idempotency(env):
    _, first = setup(env)
    _, second = extract_doc(env)
    _, version, _ = template(env)

    def register(which):
        accepted, split = which
        response = env["client"].post(
            env["base"] + "/quality/samples",
            json={
                "run_id": accepted["run_id"],
                "patient_group": "same-cross-source-patient",
                "split": split,
                "source": "synthetic",
                "authorization_reference": "synthetic-only",
                "synthetic": True,
                "difficulty_tags": ["negation"],
                "template_version_id": version["id"],
            },
        )
        return response.status_code

    with ThreadPoolExecutor(2) as pool:
        codes = list(pool.map(register, [(first, "test"), (second, "train")]))
    assert sorted(codes) == [201, 409]
    sid = env["client"].get(env["base"] + "/review-sets").json()["items"][0]["review_set_id"]
    activity = {"session_id": str(uuid4()), "active_seconds": 12}
    assert post(env, f"/review-sets/{sid}/activity", activity)["recorded_seconds"] == 12
    assert post(env, f"/review-sets/{sid}/activity", activity)["recorded_seconds"] == 0
    activity["active_seconds"] = 15
    assert post(env, f"/review-sets/{sid}/activity", activity)["recorded_seconds"] == 3
    with pytest.raises(DBAPIError), env["engine"].begin() as conn:
        conn.execute(
            template_versions.update()
            .where(template_versions.c.id == UUID(version["id"]))
            .values(digest="x" * 64)
        )
