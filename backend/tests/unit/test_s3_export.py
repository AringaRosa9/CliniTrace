import pytest

from app.modules.exports.service import csv_safe


@pytest.mark.parametrize(
    "value",
    [
        "=1+1",
        " +cmd",
        "\t@SUM(1)",
        "\r-2",
        "\n=3",
        "\x00=1",
        "\ufeff=1",
        "-7",
        "\u3000=1",
        "\u2003+1",
    ],
)
def test_formula_injection(value):
    assert csv_safe(value) == "'" + value


@pytest.mark.parametrize("value", ['中文,"引号"\n换行', "7.00", "正常", ""])
def test_regular_text(value):
    assert csv_safe(value) == value


def test_csv_roundtrip_multiple_evidence_and_all_text_columns():
    import csv
    import io
    import json
    from pathlib import Path

    from app.modules.exports.service import render

    fact = json.loads(
        (Path(__file__).resolve().parents[3] / "packages/contracts/examples/fact.json").read_text()
    )["response"]
    fact["value"].update(raw='中文,"引号"\n下一行', normalized=None)
    fact["evidence"] = [
        fact["evidence"][0],
        {**fact["evidence"][0], "id": "second-evidence", "quote": "另一处证据\n第二行"},
    ]
    fact["reason"] = " \t=HYPERLINK()"
    fact["review_status"] = "draft"
    record = {
        "id": "scope",
        "snapshot_id": None,
        "scope_revision": 1,
        "review_rules_version": "review-1.0.0",
        "patient_id": "patient",
        "patient_key": "\u3000+SUM(1)",
        "encounter_id": "encounter",
        "facts": [fact],
        "relations": [
            {"source_id": fact["fact_id"], "target_id": "related", "kind": "medications.dose"}
        ],
        "members": [],
        "configurations": {},
        "codings": [],
    }
    manifest = {
        "export_id": "export",
        "purpose": "\t@SUM(1)",
        "records": [record],
        "null_semantics": "null requires missing_reason",
    }
    csv_data = render(manifest, "csv").decode("utf-8-sig")
    row = list(csv.DictReader(io.StringIO(csv_data)))[0]
    assert row["purpose"] == "'\t@SUM(1)"
    assert row["patient_key"] == "'\u3000+SUM(1)"
    assert row["reason"] == "' \t=HYPERLINK()"
    assert row["raw"] == '中文,"引号"\n下一行'
    assert row["normalized"] == "" and row["missing_reason"] == "explicitly_unknown"
    assert row["review_status"] == "draft"
    assert json.loads(row["evidence_json"]) == fact["evidence"]
    assert json.loads(row["relations_json"])[0]["target_id"] == "related"
    assert json.loads(render(manifest, "json"))["purpose"] == manifest["purpose"]
