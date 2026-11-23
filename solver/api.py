"""FastAPI application exposing the Turnstile solver as an HTTP service.

Endpoints
---------
``POST /solve``    Solve a Turnstile challenge.  Body: ``{url, sitekey, proxy?, widget_type?, action?}``.
``GET  /health``   Liveness probe with uptime and total solve count.
``GET  /stats``    Aggregate metrics: success rate, average latency, error counts.

Configuration is 100% environment driven - see ``.env.example``.  When
``API_KEY`` is set, every request must carry ``X-API-Key: <value>``; when
unset the service runs in open mode.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from solver.core import TurnstileSolver, get_solver
from solver.proxy import ProxyManager

# ---------------------------------------------------------------------------
# JSON logging to stdout
# ---------------------------------------------------------------------------

_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


class JsonFormatter(logging.Formatter):
    """Emit every log record as a single-line JSON object on stdout."""

    _SYS_ATTRS = (
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Pull in structured extras (anything the caller added via extra=...).
        for key, value in record.__dict__.items():
            if key not in self._SYS_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(_LOG_LEVEL)
    # Quieten noisy libraries but keep our own logs loud.
    for noisy in ("urllib3", "DrissionPage", "asyncio", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger("solver.api")


# ---------------------------------------------------------------------------
# Metrics store
# ---------------------------------------------------------------------------

class _Metrics:
    """In-process metrics counter, thread-safe.

    With multiple Uvicorn workers each process keeps its own counters; the
    ``/stats`` endpoint reports the view from the current worker.  For a
    shared view put the service behind a load balancer with Prometheus-style
    scraping or swap this out for Redis.
    """

    def __init__(self, history: int = 500) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._solves_total = 0
        self._solves_success = 0
        self._solves_failed = 0
        self._latencies_ms: Deque[int] = deque(maxlen=history)
        self._errors: Dict[str, int] = {}
        self._last_solve: Optional[Dict[str, Any]] = None

    def record(self, result: Dict[str, Any]) -> None:
        with self._lock:
            self._solves_total += 1
            elapsed = int(result.get("elapsed_ms", 0))
            self._latencies_ms.append(elapsed)
            status = result.get("status")
            if status == "success":
                self._solves_success += 1
            else:
                self._solves_failed += 1
                err = result.get("error") or "unknown"
                # Bucket the error by type prefix.
                bucket = err.split(":", 1)[0].strip()
                self._errors[bucket] = self._errors.get(bucket, 0) + 1
            self._last_solve = {
                "status": status,
                "elapsed_ms": elapsed,
                "attempts": result.get("attempts"),
                "ts": round(time.time(), 3),
            }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            total = self._solves_total
            success = self._solves_success
            failed = self._solves_failed
            latencies = list(self._latencies_ms)
            avg_ms = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
            success_rate = round((success / total) * 100, 2) if total else 0.0
            return {
                "uptime_s": round(time.time() - self._started_at, 2),
                "solves_total": total,
                "solves_success": success,
                "solves_failed": failed,
                "success_rate_pct": success_rate,
                "avg_ms": avg_ms,
                "errors": dict(self._errors),
                "last_solve": self._last_solve,
            }


metrics = _Metrics()
proxy_manager = ProxyManager()


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

_API_KEY = os.getenv("API_KEY", "").strip() or None


def _verify_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if _API_KEY is None:
        return  # Open mode - no auth required.
    if not x_api_key or x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SolveRequest(BaseModel):
    url: str = Field(..., description="Target URL registered with the sitekey.")
    sitekey: str = Field(..., description="Cloudflare Turnstile sitekey (0x...).")
    proxy: Optional[str] = Field(
        None, description="Override proxy for this request (http/https/socks5)."
    )
    widget_type: str = Field(
        "managed",
        description="Widget type: managed | non-interactive | invisible.",
    )
    action: Optional[str] = Field(None, description="Optional Turnstile action value.")
    use_pool_proxy: bool = Field(
        False, description="If true and no proxy is set, rotate one from PROXY_LIST.",
    )

    @field_validator("widget_type")
    @classmethod
    def _validate_widget(cls, v: str) -> str:
        v = (v or "managed").strip().lower()
        allowed = ("managed", "non-interactive", "invisible")
        if v not in allowed:
            raise ValueError(f"widget_type must be one of {allowed}")
        return v

    @field_validator("sitekey")
    @classmethod
    def _validate_sitekey(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("sitekey is required")
        return v

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("url is required")
        if not v.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return v


class SolveResponse(BaseModel):
    token: Optional[str]
    elapsed_ms: int
    useragent: Optional[str]
    status: str
    attempts: int
    widget_type: str
    proxy_used: Optional[str] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    uptime: float
    solves_total: int
    auth_enabled: bool


class StatsResponse(BaseModel):
    success_rate_pct: float
    avg_ms: float
    solves_total: int
    solves_success: int
    solves_failed: int
    errors: Dict[str, int]
    uptime_s: float
    last_solve: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="Lucid Turnstile Solver",
        version="1.0.0",
        description="Cloudflare Turnstile token solver API (DrissionPage based).",
        docs_url="/docs",
        redoc_url=None,
    )

    @app.middleware("http")
    async def _access_log(request: Request, call_next):
        started = time.monotonic()
        response: JSONResponse = await call_next(request)
        dur_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "dur_ms": dur_ms,
                "client": request.client.host if request.client else None,
            },
        )
        return response

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        snap = metrics.snapshot()
        return HealthResponse(
            status="ok",
            uptime=round(snap["uptime_s"], 2),
            solves_total=snap["solves_total"],
            auth_enabled=_API_KEY is not None,
        )

    @app.get("/stats", response_model=StatsResponse, tags=["meta"])
    async def stats(_: None = Depends(_verify_api_key)) -> StatsResponse:
        snap = metrics.snapshot()
        return StatsResponse(
            success_rate_pct=snap["success_rate_pct"],
            avg_ms=snap["avg_ms"],
            solves_total=snap["solves_total"],
            solves_success=snap["solves_success"],
            solves_failed=snap["solves_failed"],
            errors=snap["errors"],
            uptime_s=snap["uptime_s"],
            last_solve=snap["last_solve"],
        )

    @app.post(
        "/solve",
        response_model=SolveResponse,
        tags=["solver"],
        dependencies=[Depends(_verify_api_key)],
    )
    async def solve(req: SolveRequest) -> SolveResponse:
        # Proxy resolution priority: explicit > pool rotation > none.
        proxy = req.proxy
        if not proxy and req.use_pool_proxy:
            proxy = proxy_manager.rotate()
            if proxy is None:
                logger.warning("pool_proxy_requested_but_empty")

        logger.info(
            "solve_request",
            extra={
                "url": req.url,
                "sitekey": req.sitekey[:10] + "...",
                "widget_type": req.widget_type,
                "proxy": bool(proxy),
            },
        )

        solver = get_solver()
        result = solver.solve(
            url=req.url,
            sitekey=req.sitekey,
            proxy=proxy,
            widget_type=req.widget_type,
            action=req.action,
        )
        metrics.record(result)

        status = result.get("status", "failed")
        if status != "success":
            # 502 lets upstream callers retry cleanly without parsing bodies.
            return JSONResponse(
                status_code=502,
                content=SolveResponse(**result).model_dump(),
            )
        return SolveResponse(**result)

    @app.get("/", tags=["meta"])
    async def root() -> Dict[str, str]:
        return {
            "service": "lucid-turnstile-solver",
            "version": "1.0.0",
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()

# Log a startup banner so the very first JSON line explains the running config.
logger.info(
    "api_started",
    extra={
        "auth_enabled": _API_KEY is not None,
        "proxy_pool_size": len(proxy_manager),
        "log_level": _LOG_LEVEL,
        "headless": os.getenv("HEADLESS", "true").lower() in ("1", "true", "yes", "on"),
        "max_retries": int(os.getenv("MAX_RETRIES", "3")),
        "solve_timeout": int(os.getenv("SOLVE_TIMEOUT", "30")),
    },
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "solver.api:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        workers=int(os.getenv("WORKERS", "4")),
        log_config=None,  # we use our own JSON formatter
    )
