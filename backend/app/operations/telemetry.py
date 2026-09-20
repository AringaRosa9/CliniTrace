"""Low-cardinality telemetry. Never retain URLs, user input or clinical identifiers."""

import hmac
import json
import logging
import threading
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from redis import Redis
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.s1 import jobs, outbox
from app.db.session import transaction
from app.modules.documents.service import now

router = APIRouter()
log = logging.getLogger("clinical.telemetry")
log.setLevel(logging.INFO)
if not log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)


class RequestMetrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counts: dict[tuple[str, int], int] = defaultdict(int)
        self.seconds: dict[tuple[str, int], float] = defaultdict(float)
        self.buckets: dict[tuple[str, int, float], int] = defaultdict(int)
        self.bounds = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def observe(self, route: str, status: int, seconds: float, request_id: str) -> None:
        with self.lock:
            self.counts[route, status] += 1
            self.seconds[route, status] += seconds
            for bound in self.bounds:
                if seconds <= bound:
                    self.buckets[route, status, bound] += 1
        log.info(
            json.dumps(
                {
                    "event": "http.request",
                    "route": route,
                    "status": status,
                    "duration_ms": round(seconds * 1000),
                    "request_id": request_id,
                }
            )
        )

    def render(self) -> list[str]:
        lines = ["# TYPE bljgh_http_request_duration_seconds histogram"]
        with self.lock:
            for (route, status), count in sorted(self.counts.items()):
                labels = f'route="{route}",status="{status}"'
                name = "bljgh_http_request_duration_seconds"
                lines += [
                    f"{name}_count{{{labels}}} {count}",
                    f"{name}_sum{{{labels}}} {self.seconds[route, status]}",
                ]
                for bound in self.bounds:
                    lines.append(
                        f'{name}_bucket{{{labels},le="{bound}"}} '
                        f"{self.buckets[route, status, bound]}"
                    )
                lines.append(f'{name}_bucket{{{labels},le="+Inf"}} {count}')
        return lines


def redis_for(cfg: Settings) -> Redis:
    return Redis.from_url(cfg.redis_url, socket_connect_timeout=2, socket_timeout=2)


def heartbeat(cfg: Settings, component: str, identity: str = "main") -> None:
    # Hash node names so monitoring keys cannot reveal host names.
    import hashlib

    key = hashlib.sha256(identity.encode()).hexdigest()[:16]
    with redis_for(cfg) as client:
        client.set(f"bljgh:heartbeat:{component}:{key}", str(time.time()), ex=60)


def dependencies(cfg: Settings) -> list[str]:
    lines: list[str] = []
    with redis_for(cfg) as client:
        for component in ("worker", "dispatcher"):
            count = sum(1 for _ in client.scan_iter(f"bljgh:heartbeat:{component}:*"))
            lines.append(f'bljgh_component_live{{component="{component}"}} {count}')
        for queue in ("parsing", "extracting", "exporting"):
            lines.append(f'bljgh_broker_queue_depth{{kind="{queue}"}} {client.llen(queue)}')
    totals: dict[tuple[str, str], int] = defaultdict(int)
    oldest = 0.0
    expired = 0
    with transaction(cfg, worker=True) as conn:
        scopes = conn.execute(select(outbox.c.tenant_id, outbox.c.project_id).distinct()).all()
        pending = conn.execute(
            select(func.count()).select_from(outbox).where(outbox.c.delivered_at.is_(None))
        ).scalar_one()
    for tenant, project in scopes:
        with transaction(cfg, tenant=tenant, project=project, worker=True) as conn:
            for kind, status, count in conn.execute(
                select(jobs.c.kind, jobs.c.status, func.count()).group_by(
                    jobs.c.kind, jobs.c.status
                )
            ):
                if kind in ("parsing", "extracting", "exporting") and status in (
                    "queued",
                    "running",
                    "succeeded",
                    "failed",
                    "cancelled",
                ):
                    totals[kind, status] += count
            first = conn.execute(
                select(func.min(jobs.c.created_at)).where(jobs.c.status == "queued")
            ).scalar_one()
            if first:
                oldest = max(oldest, (now() - first).total_seconds())
            expired += conn.execute(
                select(func.count())
                .select_from(jobs)
                .where(jobs.c.status == "running", jobs.c.lease_until < now())
            ).scalar_one()
    for kind in ("parsing", "extracting", "exporting"):
        for status in ("queued", "running", "succeeded", "failed", "cancelled"):
            lines.append(f'bljgh_jobs{{kind="{kind}",status="{status}"}} {totals[kind, status]}')
    lines += [
        f"bljgh_outbox_pending {pending}",
        f"bljgh_oldest_queued_seconds {max(0, oldest)}",
        f"bljgh_expired_leases {expired}",
    ]
    return lines


@router.get("/internal/metrics", include_in_schema=False)
def metrics(request: Request) -> PlainTextResponse:
    cfg = request.app.state.settings
    expected = f"Bearer {cfg.metrics_token}"
    if not cfg.metrics_token or not hmac.compare_digest(
        request.headers.get("Authorization", "").encode(), expected.encode()
    ):
        raise HTTPException(404)
    lines = request.app.state.metrics.render()
    if not cfg.metrics_collect_dependencies:
        return PlainTextResponse("\n".join(lines) + "\n")
    try:
        lines.extend(dependencies(cfg))
        lines.append("bljgh_monitor_collection_success 1")
    except Exception:
        # Failed collection is explicit; it must never look like an empty, healthy queue.
        lines.append("bljgh_monitor_collection_success 0")
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
