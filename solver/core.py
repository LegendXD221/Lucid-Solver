"""Turnstile solver — async orchestration layer.

All Playwright calls are async to be compatible with FastAPI's asyncio
event loop.  This is required for concurrent solve handling — the sync
Playwright API's greenlets conflict with asyncio under concurrent load.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from solver.browser import BrowserLauncher
from solver.constants import BROWSER_TYPES, DEFAULT_UA, WIDGET_TYPES
from solver.html_builder import build_config, render_html
from solver.models import SolveResult
from solver.pool import BrowserPool
from solver.token_poller import (
    extract_useragent,
    get_cf_clearance,
    is_managed_challenge,
    wait_for_token,
)
from solver.utils import env_bool

logger = logging.getLogger("solver.core")


class TurnstileSolver:
    WIDGET_TYPES = WIDGET_TYPES
    BROWSER_TYPES = BROWSER_TYPES

    def __init__(
        self,
        headless: bool = True,
        max_retries: int = 2,
        timeout: int = 15,
        useragent: Optional[str] = None,
        browser_type: str = "chrome",
        browser_path: Optional[str] = None,
    ) -> None:
        self.headless = env_bool("HEADLESS", headless)
        self.max_retries = int(os.getenv("MAX_RETRIES", max_retries))
        self.timeout = int(os.getenv("SOLVE_TIMEOUT", timeout))
        self.useragent = useragent or os.getenv("USER_AGENT") or DEFAULT_UA
        self.browser_type = os.getenv("BROWSER_TYPE", browser_type) or "chrome"
        self.browser_path = (
            browser_path or os.getenv("CHROMIUM_PATH") or os.getenv("BROWSER_PATH")
        )

        pool_size = int(os.getenv("POOL_SIZE", "2"))
        queue_timeout = int(os.getenv("QUEUE_TIMEOUT", "60"))
        self._pool = BrowserPool(
            size=pool_size, queue_timeout=queue_timeout,
            headless=self.headless, useragent=self.useragent,
            browser_type=self.browser_type, browser_path=self.browser_path,
        )
        self._launcher = self._pool._launchers[0] if pool_size > 0 else BrowserLauncher(
            headless=self.headless, useragent=self.useragent,
            browser_type=self.browser_type, browser_path=self.browser_path,
        )

        logger.info(
            "solver_initialized headless=%s retries=%d timeout=%s browser=%s pool=%d",
            self.headless, self.max_retries, self.timeout, self.browser_type, pool_size,
        )

    async def solve(
        self, url: str, sitekey: str, proxy: Optional[str] = None,
        widget_type: str = "managed", action: Optional[str] = None,
        cdata: Optional[str] = None, theme: str = "auto", size: str = "normal",
        language: str = "auto", appearance: Optional[str] = None,
        execution: Optional[str] = None,
    ) -> dict:
        if widget_type not in self.WIDGET_TYPES:
            raise ValueError(f"widget_type must be one of {self.WIDGET_TYPES}, got {widget_type!r}")
        if not sitekey:
            raise ValueError("sitekey is required")
        if not url:
            raise ValueError("url is required")

        started = time.monotonic()
        last_error: Optional[str] = None
        last_ua: Optional[str] = None
        last_proxy = proxy

        for attempt in range(1, self.max_retries + 1):
            attempt_started = time.monotonic()
            logger.info("solve_attempt start attempt=%d/%d url=%s widget=%s", attempt, self.max_retries, url[:30], widget_type)
            try:
                token, ua = await self._solve_once(
                    url=url, sitekey=sitekey, proxy=last_proxy,
                    widget_type=widget_type, action=action, cdata=cdata,
                    theme=theme, size=size, language=language,
                    appearance=appearance, execution=execution,
                )
                elapsed_ms = int((time.monotonic() - started) * 1000)
                result = SolveResult(token=token, elapsed_ms=elapsed_ms, useragent=ua,
                    status="success", attempts=attempt, proxy_used=last_proxy, widget_type=widget_type)
                logger.info("solve_success attempt=%d elapsed_ms=%d", attempt, elapsed_ms)
                return result.to_dict()
            except Exception as exc:
                attempt_ms = int((time.monotonic() - attempt_started) * 1000)
                last_error = f"{type(exc).__name__}: {exc}"
                last_ua = getattr(exc, "useragent", None) or last_ua
                logger.warning("solve_attempt_failed attempt=%d attempt_ms=%d error=%s", attempt, attempt_ms, last_error[:100])
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2.0 * attempt, 5.0))
                continue

        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.error("solve_failed url=%s elapsed_ms=%d error=%s", url[:30], elapsed_ms, last_error)
        return SolveResult(token=None, elapsed_ms=elapsed_ms, useragent=last_ua,
            status="failed", attempts=self.max_retries, error=last_error,
            proxy_used=last_proxy, widget_type=widget_type).to_dict()

    async def challenge(self, url: str, proxy: Optional[str] = None, timeout: Optional[int] = None) -> dict:
        t = timeout or max(self.timeout, 30)
        started = time.monotonic()
        pool_idx, launcher = await self._pool.acquire()
        try:
            page = await launcher.new_page(proxy=proxy)
            try:
                await page.goto(url, timeout=t * 1000)
                deadline = time.monotonic() + t
                clearance = None
                while time.monotonic() < deadline:
                    clearance = await get_cf_clearance(page)
                    if clearance:
                        break
                    if await is_managed_challenge(page):
                        from solver.token_poller import _try_click_widget
                        await _try_click_widget(page)
                    await asyncio.sleep(1.0)
                ua = await extract_useragent(page)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                status = "success" if clearance else "failed"
                return {"cf_clearance": clearance, "url": page.url, "useragent": ua, "status": status, "elapsed_ms": elapsed_ms}
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
        finally:
            self._pool.release(pool_idx)

    @property
    def pool_status(self) -> dict:
        return {"size": self._pool.size, "available": self._pool.available_count, "busy": self._pool.busy_count}

    async def _solve_once(
        self, url: str, sitekey: str, proxy: Optional[str],
        widget_type: str, action: Optional[str], cdata: Optional[str],
        theme: str, size: str, language: str,
        appearance: Optional[str], execution: Optional[str],
    ) -> tuple[str, str]:
        config = build_config(sitekey=sitekey, widget_type=widget_type, action=action,
            cdata=cdata, theme=theme, size=size, language=language, appearance=appearance, execution=execution)
        html = render_html(config)

        pool_idx, launcher = await self._pool.acquire()
        t0 = time.monotonic()
        try:
            page = await launcher.new_page(proxy=proxy)
            t_page = time.monotonic()
            url_with_slash = url if url.endswith("/") else url + "/"
            # Async route handler — sync lambdas don't work properly in async
            # Playwright (route.fulfill() coroutine never gets awaited).
            async def _route_handler(route):
                await route.fulfill(
                    body=html, status=200, content_type="text/html; charset=utf-8")
            await page.route(url_with_slash, _route_handler)
            await page.goto(url_with_slash, timeout=self.timeout * 1000)
            t_goto = time.monotonic()

            token = await wait_for_token(page, timeout=self.timeout, widget_type=widget_type)
            t_token = time.monotonic()
            ua = await extract_useragent(page)
            logger.info("solve_timing pool=%d page=%dms goto=%dms token=%dms total=%dms",
                pool_idx, int((t_page-t0)*1000), int((t_goto-t_page)*1000),
                int((t_token-t_goto)*1000), int((t_token-t0)*1000))
            if not token:
                raise RuntimeError("token_empty_after_wait")
            return token, ua
        finally:
            try:
                await page.unroute(url_with_slash)
            except Exception:
                pass
            try:
                ctx = getattr(page, "_stealth_context", None)
                if ctx:
                    await ctx.close()
                else:
                    await page.close()
            except Exception:
                pass
            self._pool.release(pool_idx)


_default_solver: Optional[TurnstileSolver] = None

def get_solver() -> TurnstileSolver:
    global _default_solver
    if _default_solver is None:
        _default_solver = TurnstileSolver()
    return _default_solver
