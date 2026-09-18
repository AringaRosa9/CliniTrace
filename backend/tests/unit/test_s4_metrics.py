from copy import deepcopy
from uuid import uuid4

from app.modules.extractions.pipeline import model_for
from app.modules.quality.metrics import evaluate, label_digest
from app.modules.templates.service import validate


def fact(field="history", value="病史", assertion="negated", start=0):
    return {
        "fact_id": str(uuid4()),
        "field_path": field,
        "value": {"normalized": value, "assertion": assertion},
        "evidence": [
            {"document_version_id": "doc", "page": 1, "span": {"start": start, "end": start + 3}}
        ],
    }


def record(gold, predicted):
    return {
        "sample_id": "sample",
        "patient_id": "a",
        "prediction_patient_id": "a",
        "document_type": "outpatient",
        "gold": {"facts": gold, "relations": [], "codings": []},
        "prediction": {"facts": predicted, "relations": [], "codings": []},
    }


def test_duplicates_empty_denominators_and_high_risk():
    a = fact()
    report = evaluate([record([a], [a, a])])
    assert report["micro"]["tp"] == 1 and report["micro"]["fp"] == 1
    assert report["relaxed_span"]["tp"] == 1
    report = evaluate(
        [
            record(
                [a, fact("medications.dose", "未知", assertion="affirmed")],
                [fact(assertion="affirmed")],
            )
        ]
    )
    assert report["high_risk"]["negation_to_affirmed"] == {"count": 1, "denominator": 1}
    assert report["high_risk"]["dose_error"]["count"] == 1
    assert report["time"]["accuracy"] is None and report["cost"]["total"] is None
    assert evaluate([])["micro"]["f1"] is None


def test_ids_order_graphs_and_relaxed_matching():
    a, b = fact(), fact("medications.dose", "5")
    r = record([a, b], [b, a])
    r["gold"]["relations"] = [
        {"kind": "dose", "source_id": a["fact_id"], "target_id": b["fact_id"]}
    ]
    r["prediction"]["relations"] = deepcopy(r["gold"]["relations"])
    assert label_digest(r["gold"]) == label_digest(r["prediction"])
    assert evaluate([r])["relation"]["f1"] == 1
    r["prediction"]["relations"][0].update(source_id=b["fact_id"], target_id=a["fact_id"])
    assert evaluate([r])["relation"]["f1"] == 0
    assert "relation" in evaluate([r])["errors"][0]["strata"]
    r = record([a], [fact(start=1)])
    assert evaluate([r])["strict_span"]["f1"] == 0
    assert evaluate([r])["relaxed_span"]["f1"] == 1


def test_template_guard_rejects_weakening_remote_refs_and_bad_examples():
    base = model_for("outpatient-1.0.0").model_json_schema()
    content = {
        "schema_definition": base,
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
        "evidence_rules": ["须原文证据"],
    }
    assert validate("outpatient", content)["valid"]
    bad = deepcopy(content)
    bad["schema_definition"]["$defs"]["Evidence"]["required"] = []
    assert not validate("outpatient", bad)["valid"]
    bad = deepcopy(content)
    bad["schema_definition"]["$ref"] = "https://example.invalid/schema"
    assert not validate("outpatient", bad)["valid"]
    bad = deepcopy(content)
    bad["schema_definition"]["$defs"]["MissingReason"]["enum"].remove("explicitly_unknown")
    assert not validate("outpatient", bad)["valid"]
    bad = deepcopy(content)
    bad["negative_examples"] = content["positive_examples"]
    assert not validate("outpatient", bad)["valid"]


def test_hundred_synthetic_records_replay_and_new_dose_risk():
    import random

    rows = [record([fact()], [fact()]) for _ in range(100)]
    for i, row in enumerate(rows):
        row["sample_id"] = str(i)
        row["cost_amount"] = "0"
    initial = evaluate(rows)
    random.Random(42).shuffle(rows)
    replayed = evaluate(rows)
    assert initial == replayed
    assert initial["sample_count"] == 100 and initial["micro"]["f1"] == 1
    assert initial["cost"]["total"] == "0"
    assert evaluate([record([], [fact("medications.dose", "5", assertion="affirmed")])])[
        "high_risk"
    ]["dose_error"] == {"count": 1, "denominator": 1}


def test_offline_runner_verifies_digest_and_replays(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from app.modules.extractions.pipeline import digest
    from app.modules.quality.metrics import PROTOCOL

    rows = [record([fact()], [fact()])]
    payload = {
        "protocol": PROTOCOL,
        "records": rows,
        "records_digest": digest(rows),
        "dataset_digest": "synthetic-digest",
        "report": evaluate(rows),
    }
    source, output = tmp_path / "input.json", tmp_path / "output.json"
    source.write_text(json.dumps({"payload": payload}))
    runner = Path(__file__).resolve().parents[3] / "evaluation/runners/evaluate.py"
    command = [sys.executable, str(runner), str(source), "--output", str(output)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    assert json.loads(output.read_text())["report"] == payload["report"]
    payload["records"][0]["sample_id"] = "tampered"
    source.write_text(json.dumps({"payload": payload}))
    assert subprocess.run(command, capture_output=True).returncode != 0


def test_shifted_evidence_does_not_hide_negation_risk():
    r = record([fact()], [fact(assertion="affirmed", start=1)])
    assert evaluate([r])["high_risk"]["negation_to_affirmed"]["count"] == 1
