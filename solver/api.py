"""FastAPI application exposing the Turnstile solver as an HTTP service.

Endpoints
---------
``POST /solve``    Solve a Turnstile challenge.  Body supports url, sitekey,
                   proxy, widget_type, action, cdata, theme, size, language,
                   appearance, execution.
``GET  /health``   Liveness probe with uptime and total solve count.
``GET  /stats``    Aggregate metrics: success rate, average latency, errors.

The ``/solve`` handler is a **sync** function (``def``, not ``async def``).
FastAPI automatically runs sync endpoints in a threadpool, which is the
correct concurrency model for sync patchright/Playwright calls.

Configuration is 100% environment driven - see ``.env.example``.  When
``API_KEY`` is set, every request must carry ``X-API-Key: <value>``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from solver import __version__ as SOLVER_VERSION
from solver.core import TurnstileSolver, get_solver
from solver.proxy import ProxyManager
from solver.web import HOMEPAGE_HTML

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
    for noisy in ("urllib3", "patchright", "playwright", "asyncio", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_configure_logging()
logger = logging.getLogger("solver.api")


# ---------------------------------------------------------------------------
# Metrics store
# ---------------------------------------------------------------------------

class _Metrics:
    """In-process metrics counter, thread-safe."""

    def __init__(self, history: int = 500) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._solves_total = 0
        self._solves_success = 0
        self._solves_failed = 0
        self._latencies_ms: Deque[int] = deque(maxlen=history)
        self._errors: Dict[str, int] = {}
        self._widget_types: Dict[str, int] = {}
        self._last_solve: Optional[Dict[str, Any]] = None

    def record(self, result: Dict[str, Any]) -> None:
        with self._lock:
            self._solves_total += 1
            elapsed = int(result.get("elapsed_ms", 0))
            self._latencies_ms.append(elapsed)
            status = result.get("status")
            wt = result.get("widget_type", "managed")
            self._widget_types[wt] = self._widget_types.get(wt, 0) + 1
            if status == "success":
                self._solves_success += 1
            else:
                self._solves_failed += 1
                err = result.get("error") or "unknown"
                bucket = err.split(":", 1)[0].strip()
                self._errors[bucket] = self._errors.get(bucket, 0) + 1
            self._last_solve = {
                "status": status,
                "elapsed_ms": elapsed,
                "attempts": result.get("attempts"),
                "widget_type": wt,
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
                "widget_types": dict(self._widget_types),
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
        return
    if not x_api_key or x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

_WIDGET_TYPES = ("managed", "non-interactive", "invisible")
_THEMES = ("auto", "light", "dark")
_SIZES = ("normal", "flexible", "compact")
_APPEARANCES = ("always", "execute", "interaction-only")
_EXECUTIONS = ("render", "execute")


class SolveRequest(BaseModel):
    url: str = Field(..., description="Target URL registered with the sitekey.")
    sitekey: str = Field(..., description="Cloudflare Turnstile sitekey (0x...).")
    proxy: Optional[str] = Field(None, description="Per-request proxy override.")
    widget_type: str = Field("managed", description="managed | non-interactive | invisible")
    action: Optional[str] = Field(None, description="Turnstile action embedded in token.")
    cdata: Optional[str] = Field(None, description="Custom data echoed back by siteverify.")
    theme: str = Field("auto", description="auto | light | dark")
    size: str = Field("normal", description="normal | flexible | compact")
    language: str = Field("auto", description="auto or ISO 639-1 code.")
    appearance: Optional[str] = Field(None, description="Override: always | execute | interaction-only")
    execution: Optional[str] = Field(None, description="Override: render | execute")
    use_pool_proxy: bool = Field(False, description="Rotate a proxy from PROXY_LIST when proxy is unset.")

    @field_validator("widget_type")
    @classmethod
    def _v_widget(cls, v: str) -> str:
        v = (v or "managed").strip().lower()
        if v not in _WIDGET_TYPES:
            raise ValueError(f"widget_type must be one of {_WIDGET_TYPES}")
        return v

    @field_validator("theme")
    @classmethod
    def _v_theme(cls, v: str) -> str:
        v = (v or "auto").strip().lower()
        if v not in _THEMES:
            raise ValueError(f"theme must be one of {_THEMES}")
        return v

    @field_validator("size")
    @classmethod
    def _v_size(cls, v: str) -> str:
        v = (v or "normal").strip().lower()
        if v not in _SIZES:
            raise ValueError(f"size must be one of {_SIZES}")
        return v

    @field_validator("appearance")
    @classmethod
    def _v_appearance(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in _APPEARANCES:
            raise ValueError(f"appearance must be one of {_APPEARANCES}")
        return v

    @field_validator("execution")
    @classmethod
    def _v_execution(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in _EXECUTIONS:
            raise ValueError(f"execution must be one of {_EXECUTIONS}")
        return v

    @field_validator("sitekey")
    @classmethod
    def _v_sitekey(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("sitekey is required")
        return v

    @field_validator("url")
    @classmethod
    def _v_url(cls, v: str) -> str:
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
    widget_types: Dict[str, int]
    uptime_s: float
    last_solve: Optional[Dict[str, Any]] = None


class ChallengeRequest(BaseModel):
    url: str = Field(..., description="Target URL behind a Cloudflare managed challenge.")
    proxy: Optional[str] = Field(None, description="Per-request proxy override.")
    timeout: Optional[int] = Field(30, description="Challenge timeout in seconds.")


class BatchTask(BaseModel):
    url: str
    sitekey: str
    widget_type: str = "managed"
    proxy: Optional[str] = None
    action: Optional[str] = None
    cdata: Optional[str] = None


class BatchSolveRequest(BaseModel):
    tasks: list = Field(..., description="List of {url, sitekey, widget_type} objects.")


class HSolveRequest(BaseModel):
    url: str = Field(..., description="Target URL registered with the hCaptcha sitekey.")
    sitekey: str = Field(..., description="hCaptcha sitekey (UUID format).")
    proxy: Optional[str] = Field(None, description="Per-request proxy override ('auto' for SOCKS5).")
    theme: str = Field("light", description="Widget theme: light or dark.")
    size: str = Field("normal", description="Widget size: normal, compact, or invisible.")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="Lucid Turnstile Solver",
        version=SOLVER_VERSION,
        description="Cloudflare Turnstile token solver API (patchright-based, all widget types).",
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

    @app.get("/health", tags=["meta"])
    async def health():
        """Comprehensive health check — browser, cache, proxy status."""
        snap = metrics.snapshot()
        solver = get_solver()
        return {
            "status": "ok",
            "uptime": round(snap["uptime_s"], 2),
            "solves_total": snap["solves_total"],
            "auth_enabled": _API_KEY is not None,
            "browser_alive": solver.browser_alive,
            "cache": solver.cache_stats,
        }

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
            widget_types=snap["widget_types"],
            uptime_s=snap["uptime_s"],
            last_solve=snap["last_solve"],
        )

    @app.post(
        "/solve",
        response_model=SolveResponse,
        tags=["solver"],
        dependencies=[Depends(_verify_api_key)],
    )
    # NOTE: ``def`` (not ``async def``) — FastAPI runs sync endpoints in a
    # threadpool, which is the correct model for sync patchright calls.
    def solve(req: SolveRequest) -> SolveResponse:
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
                "theme": req.theme,
                "size": req.size,
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
            cdata=req.cdata,
            theme=req.theme,
            size=req.size,
            language=req.language,
            appearance=req.appearance,
            execution=req.execution,
        )
        metrics.record(result)

        status = result.get("status", "failed")
        if status != "success":
            return JSONResponse(
                status_code=502,
                content=SolveResponse(**result).model_dump(),
            )
        return SolveResponse(**result)

    @app.post("/challenge", tags=["solver"], dependencies=[Depends(_verify_api_key)])
    def challenge(req: 'ChallengeRequest') -> JSONResponse:
        """Bypass a Cloudflare managed challenge page.

        Navigates to the real URL, waits for the "Just a moment..."
        interstitial to auto-resolve, and returns cookies + final URL + title.

        Body: ``{"url": "https://example.com", "proxy": null, "timeout": 30}``
        """
        solver = get_solver()
        result = solver.challenge(url=req.url, proxy=req.proxy, timeout=req.timeout or 30)
        code = 200 if result.get("status") == "success" else 502
        return JSONResponse(status_code=code, content=result)

    @app.post("/hsolve", tags=["solver"], dependencies=[Depends(_verify_api_key)])
    def hsolve(req: 'HSolveRequest') -> JSONResponse:
        """Solve an hCaptcha challenge.

        Body: ``{"url": "https://example.com", "sitekey": "10000000-ffff-ffff-ffff-000000000001"}``

        Returns: ``{"token": "...", "elapsed_ms": 4200, "status": "success", "captcha_type": "hcaptcha"}``
        """
        solver = get_solver()
        result = solver.hsolve(
            url=req.url, sitekey=req.sitekey,
            proxy=req.proxy, theme=req.theme, size=req.size,
        )
        if result.get("status") == "success":
            return JSONResponse(status_code=200, content=result)
        return JSONResponse(status_code=502, content=result)

    @app.post("/solve/batch", tags=["solver"], dependencies=[Depends(_verify_api_key)])
    def solve_batch(req: 'BatchSolveRequest') -> JSONResponse:
        """Solve multiple Turnstile challenges at once.

        Body: ``{"tasks": [{"url":"...","sitekey":"0x4AAA..."}, ...]}``
        Returns a list of results in the same order. Cache hits return instantly.
        """
        solver = get_solver()
        results = solver.solve_batch(req.tasks)
        return JSONResponse(status_code=200, content={"results": results})

    @app.post("/cache/clear", tags=["meta"], dependencies=[Depends(_verify_api_key)])
    def clear_cache() -> dict:
        """Clear the token cache."""
        solver = get_solver()
        solver._cache.clear()
        return {"status": "cleared", "cache": solver.cache_stats}

    @app.post("/prewarm", tags=["meta"], dependencies=[Depends(_verify_api_key)])
    def prewarm() -> dict:
        """Start the browser immediately so the next solve is fast."""
        solver = get_solver()
        solver.prewarm()
        return {"status": "prewarmed", "browser_alive": solver.browser_alive}

    @app.get("/", tags=["meta"], response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        return HTMLResponse(content=HOMEPAGE_HTML)

    return app


app = create_app()

logger.info(
    "api_started",
    extra={
        "auth_enabled": _API_KEY is not None,
        "proxy_pool_size": len(proxy_manager),
        "log_level": _LOG_LEVEL,
        "headless": os.getenv("HEADLESS", "true").lower() in ("1", "true", "yes", "on"),
        "max_retries": int(os.getenv("MAX_RETRIES", "3")),
        "solve_timeout": int(os.getenv("SOLVE_TIMEOUT", "30")),
        "browser_type": os.getenv("BROWSER_TYPE", "chromium"),
    },
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "solver.api:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        workers=int(os.getenv("WORKERS", "4")),
        log_config=None,
    )
