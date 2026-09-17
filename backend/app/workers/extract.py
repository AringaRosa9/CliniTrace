"""Bounded extraction child; only its configured gateway may receive document data."""

import json
import sys
from pathlib import Path

from app.core.config import Settings
from app.integrations.llm.gateway import extract
from app.modules.extractions.pipeline import ExtractionFailure, assemble


def main() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (170, 175))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    path = Path(sys.argv[1])
    cfg = Settings.model_validate_json((path / "config.json").read_text())
    request = json.loads((path / "input").read_text())
    try:

        def record(value: dict[str, object]) -> None:
            (path / "raw.json").write_text(json.dumps(value, ensure_ascii=False))

        response = extract(cfg, request, record)
        (path / "raw.json").write_text(json.dumps(response, ensure_ascii=False))
        result = assemble(
            response["output"],
            request["parsed"],
            request["document_version_id"],
            request["parse_artifact_id"],
            request["configuration"]["template_version"],
            request["configuration"]["terminology_version"],
        )
        result["usage"] = response["usage"]
        (path / "result.json").write_text(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        code = exc.code if isinstance(exc, ExtractionFailure) else "EXTRACTION_INVALID"
        retry = exc.retryable if isinstance(exc, ExtractionFailure) else False
        (path / "error.json").write_text(json.dumps({"code": code, "retryable": retry}))
        sys.exit(1)


if __name__ == "__main__":
    main()
