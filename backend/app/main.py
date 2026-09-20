import time
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Message

from app.api.s1 import router
from app.api.s2 import router as s2_router
from app.api.s3 import router as s3_router
from app.api.s4 import router as s4_router
from app.contracts.models import ErrorResponse, HealthResponse
from app.core.config import Settings, get_settings
from app.modules.identity.service import principal
from app.operations.telemetry import RequestMetrics
from app.operations.telemetry import router as operations_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="临床数据结构化平台 API", version="0.1.0")
    app.state.settings = settings
    app.state.metrics = RequestMetrics()

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.monotonic()
        try:
            request_id = UUID(request.headers.get("X-Request-ID", ""))
        except ValueError:
            request_id = uuid4()
        request.state.request_id = request_id
        if settings.maintenance_mode and request.method not in ("GET", "HEAD", "OPTIONS"):
            return error(request, 503, "MAINTENANCE", "Service temporarily read-only")
        response: Response
        total = 0
        cap = settings.max_upload_bytes + 64 * 1024
        if request.method == "POST" and request.url.path.endswith("/documents"):
            try:
                principal(request)
            except HTTPException as exc:
                response = error(
                    request,
                    exc.status_code,
                    "UNAUTHENTICATED" if exc.status_code == 401 else "FORBIDDEN",
                    "Authentication required",
                )
                response.headers["Cache-Control"] = "no-store"
                response.headers["X-Request-ID"] = str(request_id)
                return response
            # Bound the multipart parser itself, including requests without Content-Length.
            cap = settings.max_upload_bytes + 64 * 1024
            receive = request._receive
            total = 0

            async def bounded_receive() -> Message:
                nonlocal total
                message = await receive()
                total += len(message.get("body", b""))
                if total > cap:
                    raise HTTPException(413)
                return message

            request._receive = bounded_receive
        try:
            response = await call_next(request)
        except Exception:
            # Deliberately do not log body, exception strings, or sensitive input.
            response = error(request, 500, "INTERNAL_ERROR", "Internal service error")
        if total > cap:
            response = error(request, 413, "FILE_TOO_LARGE", "Upload exceeds configured limit")
        response.headers["X-Request-ID"] = str(request_id)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        route = request.scope.get("route")
        route_name = getattr(route, "path", "unmatched")
        if route_name != "/internal/metrics":
            app.state.metrics.observe(
                route_name, response.status_code, time.monotonic() - started, str(request_id)
            )
        return response

    def error(request: Request, status: int, code: str, message: str) -> JSONResponse:
        payload = ErrorResponse(
            code=code,
            message=message,
            request_id=request.state.request_id,
            retryable=status in (429, 503),
        )
        return JSONResponse(
            status_code=status,
            content=payload.model_dump(mode="json"),
            headers={
                "X-Request-ID": str(request.state.request_id),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def dependency_error(request: Request, exc: Exception) -> JSONResponse:
        return error(request, 503, "SERVICE_UNAVAILABLE", "Dependency temporarily unavailable")

    for exception_type in (RedisError, SQLAlchemyError, BotoCoreError, ClientError):
        app.add_exception_handler(exception_type, dependency_error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error(request, 422, "VALIDATION_ERROR", "Request validation failed")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        codes = {
            400: "BAD_REQUEST",
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "VERSION_CONFLICT",
            413: "FILE_TOO_LARGE",
            415: "UNSUPPORTED_MEDIA_TYPE",
            429: "RATE_LIMITED",
            501: "NOT_IMPLEMENTED",
            503: "SERVICE_UNAVAILABLE",
        }
        return error(
            request,
            exc.status_code,
            codes.get(exc.status_code, "HTTP_ERROR"),
            "Resource unavailable or request rejected",
        )

    @app.get("/api/v1/health", response_model=HealthResponse, operation_id="getHealth")
    async def health() -> HealthResponse:
        """Liveness only. Does not claim database, storage, or workers are ready."""
        return HealthResponse()

    app.include_router(operations_router)
    app.include_router(router)
    app.include_router(s2_router)
    app.include_router(s3_router)
    app.include_router(s4_router)
    return app


app = create_app()
