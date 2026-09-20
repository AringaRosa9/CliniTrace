"""Internal collector with a worker-only DB login; never publish its port externally."""

from fastapi import FastAPI

from app.core.config import get_settings
from app.operations.telemetry import RequestMetrics, router

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.state.settings = get_settings()
app.state.metrics = RequestMetrics()
app.include_router(router)
