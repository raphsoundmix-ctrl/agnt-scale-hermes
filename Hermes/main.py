"""
Hermes Gateway — MAO.ai Agent Runtime
Runs in WSL2 on :7777, accessed by FastAPI Docker via host.docker.internal:7777
"""
import asyncio
import os
import secrets
import uuid
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from routers import health, skills, agnt_agent

class _CorrelationDefault(logging.Filter):
    """Background tasks (event listener, visual worker) log without a
    correlation_id, but the format string requires it — without this
    filter every such record raised a KeyError traceback to stderr.
    Inject a default so any record formats cleanly."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = "-"
        return True


logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper()),
    format="%(asctime)s [%(levelname)s] %(name)s req=%(correlation_id)s: %(message)s",
)
for _h in logging.getLogger().handlers:
    _h.addFilter(_CorrelationDefault())
logger = logging.getLogger("hermes")


# CORS — только из доверенных origin (FastAPI proxy).
# Hermes НЕ должен быть доступен браузерам напрямую.
ALLOWED_ORIGINS = [o.strip() for o in os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:8000,http://host.docker.internal:8000",
).split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        f"🚀 Hermes Gateway starting on :{settings.HERMES_PORT}",
        extra={"correlation_id": "boot"},
    )
    logger.info(f"📡 LLM: {settings.OPENROUTER_MODEL}", extra={"correlation_id": "boot"})

    yield
    logger.info("Hermes Gateway shutting down", extra={"correlation_id": "boot"})


app = FastAPI(
    title="Hermes Gateway",
    description="MAO.ai Agent Runtime — handles Skills, Memory, LLM calls",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.APP_ENV != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Correlation-Id"],
)


# ─── Internal auth middleware (C2 / S-CRITICAL) ──────────────────────
# Hermes is NOT public — it should only be reachable from the FastAPI
# Backend on the internal Docker network. Even so, defence-in-depth:
# every protected route requires a shared secret in X-Internal-Token.
# Health endpoints are public so Docker / k8s probes work without auth.
_PUBLIC_PATHS: set[str] = {"/", "/health", "/healthz", "/health/ready", "/docs", "/openapi.json"}


@app.middleware("http")
async def require_internal_token(request: Request, call_next):
    path = request.url.path
    # Allow health/docs without token. Everything else (/skills/*, /agent/*) is protected.
    if path in _PUBLIC_PATHS or path.startswith("/health"):
        return await call_next(request)

    expected = settings.HERMES_INTERNAL_TOKEN or ""
    presented = request.headers.get("X-Internal-Token", "")

    # Production: empty expected token = misconfig → refuse all traffic.
    if not expected:
        if settings.APP_ENV == "production":
            logger.error(
                "HERMES_INTERNAL_TOKEN is empty in production — refusing request",
                extra={"correlation_id": request.headers.get("X-Correlation-Id", "no-token")},
            )
            return JSONResponse(
                status_code=503,
                content={"detail": "Hermes misconfigured: internal token missing"},
            )
        # Dev: warn once per request but allow (so local hacking is not painful).
        logger.warning(
            "HERMES_INTERNAL_TOKEN not set — allowing in dev only",
            extra={"correlation_id": request.headers.get("X-Correlation-Id", "no-token")},
        )
        return await call_next(request)

    # Constant-time compare to prevent timing attacks.
    if not presented or not secrets.compare_digest(expected, presented):
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized: invalid or missing X-Internal-Token"},
        )

    return await call_next(request)


@app.middleware("http")
async def correlation_and_errors(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-Id", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id

    try:
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = correlation_id
        return response
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unhandled exception", extra={"correlation_id": correlation_id})
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal agent error",
                "correlation_id": correlation_id,
            },
            headers={"X-Correlation-Id": correlation_id},
        )


app.include_router(health.router)
app.include_router(skills.router, prefix="/skills")
app.include_router(agnt_agent.router, prefix="/agent")
