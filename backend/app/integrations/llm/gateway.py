"""Approved, zero-charge schema gateway. No tools, redirect or implicit provider fallback."""

import json
import time
from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

import httpx
from pydantic import ValidationError
from redis import Redis

from app.core.config import Settings
from app.modules.extractions.pipeline import PROMPT, ExtractionFailure, model_for


def extract(
    cfg: Settings, request: dict[str, Any], record: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    if cfg.extraction_provider != "gateway":
        return _extract(cfg, request, record)
    import hashlib

    key = "model-inflight:" + hashlib.sha256(cfg.extraction_gateway_url.encode()).hexdigest()
    token = str(uuid4())
    limiter = Redis.from_url(cfg.redis_url, socket_timeout=3)
    acquired = limiter.eval(
        "redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',ARGV[1]); "
        "if redis.call('ZCARD',KEYS[1]) >= tonumber(ARGV[3]) then return 0 end; "
        "redis.call('ZADD',KEYS[1],ARGV[2],ARGV[4]); redis.call('EXPIRE',KEYS[1],600); return 1",
        1,
        key,
        str(time.time()),
        str(time.time() + cfg.task_timeout_seconds + 60),
        str(cfg.extraction_max_inflight),
        token,
    )
    if not acquired:
        raise ExtractionFailure("MODEL_CONCURRENCY_LIMIT", True)
    try:
        return _extract(cfg, request, record)
    finally:
        limiter.zrem(key, token)


def _extract(
    cfg: Settings, request: dict[str, Any], record: Callable[[dict[str, Any]], None] | None
) -> dict[str, Any]:
    if len(request["parsed"]["text"]) > cfg.extraction_max_chars:
        raise ExtractionFailure("MODEL_INPUT_LIMIT")
    if cfg.extraction_provider == "disabled":
        raise ExtractionFailure("EXTRACTION_DISABLED")
    if cfg.extraction_provider == "synthetic":
        if cfg.app_env == "production" or not cfg.allow_synthetic_mock:
            raise ExtractionFailure("SYNTHETIC_NOT_ENABLED")
        from app.integrations.llm.synthetic import extract_synthetic

        value = extract_synthetic(request)
        return {
            "output": value,
            "responses": [value],
            "usage": {
                "provider": "synthetic",
                "request_ids": [],
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_amount": "0",
                "cost_currency": "CNY",
                "measurement": "synthetic_no_model_call",
            },
        }
    model = model_for(request["configuration"]["template_version"])
    responses: list[Any] = []
    usage: dict[str, Any] = {
        "provider": "gateway",
        "request_ids": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_amount": "0",
        "cost_currency": "CNY",
        "measurement": "provider_reported",
    }
    limiter = Redis.from_url(cfg.redis_url, socket_timeout=3)
    for repair in range(cfg.extraction_max_repairs + 1):
        # Atomic fixed-window quota shared by all workers/processes on this gateway/model.
        import hashlib

        key = (
            "model-rate:"
            + hashlib.sha256(
                (cfg.extraction_gateway_url + cfg.extraction_model).encode()
            ).hexdigest()
            + ":"
            + str(int(time.time() // 60))
        )
        count = limiter.eval(
            "local n=redis.call('INCR',KEYS[1]); "
            "if n==1 then redis.call('EXPIRE',KEYS[1],120) end; return n",
            1,
            key,
        )
        if int(cast(str, count)) > cfg.extraction_requests_per_minute:
            raise ExtractionFailure("MODEL_RATE_LIMIT", True)
        body = {
            "model": cfg.extraction_model,
            "instruction": PROMPT,
            "schema": model.model_json_schema(),
            "max_output_bytes": cfg.extraction_max_output_bytes,
            "max_cost_cny": "0",
            "request_id": request["run_id"] + f"-{request['generation']}-{repair}",
            "data": {
                "document_version_id": request["document_version_id"],
                "parse_artifact_id": request["parse_artifact_id"],
                "parsed": request["parsed"],
            },
            "repair": repair > 0,
        }
        if repair:
            body["invalid_output"] = responses[-1]["output"]
            body["repair_instruction"] = (
                "Fix JSON schema structure only; do not invent evidence or facts."
            )
        if record:
            record(
                {
                    "responses": responses,
                    "usage": None,
                    "usage_complete": False,
                    "pending_request_id": body["request_id"],
                }
            )
        try:
            with httpx.Client(
                timeout=cfg.extraction_timeout_seconds, follow_redirects=False, trust_env=False
            ) as client:
                with client.stream(
                    "POST",
                    cfg.extraction_gateway_url,
                    json=body,
                    headers={"Authorization": f"Bearer {cfg.extraction_gateway_token}"}
                    if cfg.extraction_gateway_token
                    else {},
                ) as response:
                    if response.status_code == 429:
                        raise ExtractionFailure("MODEL_RATE_LIMIT", True)
                    if response.status_code >= 500:
                        raise ExtractionFailure("MODEL_UNAVAILABLE", True)
                    if response.status_code != 200:
                        raise ExtractionFailure("MODEL_REQUEST_REJECTED")
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > cfg.extraction_max_output_bytes:
                            raise ExtractionFailure("MODEL_OUTPUT_LIMIT")
            if record:
                record(
                    {
                        "responses": responses + [{"raw_response": data.decode(errors="replace")}],
                        "usage": None,
                        "usage_complete": False,
                    }
                )
            value = json.loads(data)
            responses.append(value)
            if record:
                record({"responses": responses, "usage": None, "usage_complete": False})
            if value["model"] != cfg.extraction_model or value["finish_reason"] != "stop":
                raise ExtractionFailure("MODEL_VERSION_OR_TRUNCATION")
            metrics = value["usage"]
            if metrics["cost_currency"] != "CNY" or str(metrics["cost_amount"]) not in (
                "0",
                "0.0",
                "0.00",
            ):
                raise ExtractionFailure("MODEL_BUDGET_EXCEEDED")
            for key in ("input_tokens", "output_tokens"):
                if type(metrics[key]) is not int or metrics[key] < 0:
                    raise ExtractionFailure("MODEL_USAGE_INVALID")
                usage[key] += metrics[key]
            if not isinstance(value["request_id"], str) or not value["request_id"]:
                raise ExtractionFailure("MODEL_USAGE_INVALID")
            usage["request_ids"].append(value["request_id"])
            if record:
                record({"responses": responses, "usage": usage, "usage_complete": True})
            try:
                output = model.model_validate(value["output"]).model_dump(mode="json")
                return {"output": output, "responses": responses, "usage": usage}
            except ValidationError:
                if repair == cfg.extraction_max_repairs:
                    raise ExtractionFailure("MODEL_SCHEMA_INVALID") from None
        except httpx.TimeoutException:
            raise ExtractionFailure("MODEL_TIMEOUT", True) from None
        except httpx.HTTPError:
            raise ExtractionFailure("MODEL_UNAVAILABLE", True) from None
        except (KeyError, TypeError, ValueError):
            raise ExtractionFailure("MODEL_RESPONSE_INVALID") from None
    raise ExtractionFailure("MODEL_SCHEMA_INVALID")
