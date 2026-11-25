"""Turnstile solver — orchestration layer.

This module ties together the focused sub-modules into the public
:class:`TurnstileSolver` class:

* :mod:`solver.constants`  — static config, HTML template, type tuples
* :mod:`solver.models`     — :class:`SolveResult` dataclass
* :mod:`solver.browser`    — :class:`BrowserLauncher` (patchright launch)
* :mod:`solver.html_builder` — :func:`build_config` + :func:`render_html`
* :mod:`solver.token_poller` — :func:`wait_for_token` + :func:`extract_useragent`

The solver orchestrates a single solve flow:

1. Build the JSON config for the requested widget type.
2. Inject it into the HTML template.
3. Launch a patchright browser (system Chrome for best stealth).
4. Intercept navigation to the target URL and serve the HTML.
5. Poll for the token (callback variable + hidden input).
6. Retry up to ``max_retries`` times on failure.

Based on the reference implementation at
``github.com/Theyka/Turnstile-Solver``, updated to support every Cloudflare
Turnstile widget type and all client-side configuration options.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from solver.browser import BrowserLauncher
from solver.constants import BROWSER_TYPES, DEFAULT_UA, WIDGET_TYPES
from solver.html_builder import build_config, render_html
from solver.models import SolveResult
from solver.pool import BrowserPool
from solver.token_poller import extract_useragent, get_cf_clearance, is_managed_challenge, wait_for_token
from solver.utils import env_bool

logger = logging.getLogger("solver.core")


class TurnstileSolver:
    """Solve Cloudflare Turnstile challenges using patchright.

    Parameters
    ----------
    headless:
        Run the browser without a visible window (default ``True``).
    max_retries:
        Number of attempts before giving up (default ``3``).
    timeout:
        Per-attempt timeout in seconds (default ``30``).
    useragent:
        Override the browser user-agent.  Defaults to a realistic Chrome UA
        (see :data:`solver.constants.DEFAULT_UA`).
    browser_type:
        ``chrome`` (system Google Chrome, best stealth — default),
        ``chromium`` (patchright's patched build), or ``msedge``.
    browser_path:
        Explicit path to a browser binary.  Overrides ``browser_type``.
    """

    # Re-exported as class attributes for backward compatibility with
    # code that references ``TurnstileSolver.WIDGET_TYPES``.
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
        # Reduced from 30s to 15s per attempt — stealth JS + cursor clicking
        # makes the widget solve on the first try, so we don't need long
        # timeouts.  2 retries × 15s = 30s worst case, but typical is 5-10s.
        self.timeout = int(os.getenv("SOLVE_TIMEOUT", timeout))
        self.useragent = useragent or os.getenv("USER_AGENT") or DEFAULT_UA
        self.browser_type = os.getenv("BROWSER_TYPE", browser_type) or "chrome"
        self.browser_path = (
            browser_path
            or os.getenv("CHROMIUM_PATH")
            or os.getenv("BROWSER_PATH")
        )

        # Browser pool for concurrent solves (default 2 browsers per worker).
        pool_size = int(os.getenv("POOL_SIZE", "2"))
        queue_timeout = int(os.getenv("QUEUE_TIMEOUT", "60"))
        self._pool = BrowserPool(
            size=pool_size,
            queue_timeout=queue_timeout,
            headless=self.headless,
            useragent=self.useragent,
            browser_type=self.browser_type,
            browser_path=self.browser_path,
        )
        # Keep the single launcher for backward compat / tests.
        self._launcher = self._pool._launchers[0] if pool_size > 0 else BrowserLauncher(
            headless=self.headless, useragent=self.useragent,
            browser_type=self.browser_type, browser_path=self.browser_path,
        )

        logger.info(
            "solver_initialized headless=%s retries=%d timeout=%s browser=%s pool=%d ua=%s",
            self.headless, self.max_retries, self.timeout,
            self.browser_type, pool_size, bool(self.useragent),
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def solve(
        self,
        url: str,
        sitekey: str,
        proxy: Optional[str] = None,
        widget_type: str = "managed",
        action: Optional[str] = None,
        cdata: Optional[str] = None,
        theme: str = "auto",
        size: str = "normal",
        language: str = "auto",
        appearance: Optional[str] = None,
        execution: Optional[str] = None,
    ) -> dict:
        """Attempt to solve a Turnstile challenge.

        See :meth:`solve`'s docstring parameters in the class docstring
        above, or the README, for full option descriptions.

        Returns
        -------
        dict
            ``{token, elapsed_ms, useragent, status, attempts, widget_type}``
            plus ``error`` / ``proxy_used`` when relevant.
        """
        if widget_type not in self.WIDGET_TYPES:
            raise ValueError(
                f"widget_type must be one of {self.WIDGET_TYPES}, "
                f"got {widget_type!r}"
            )
        if not sitekey:
            raise ValueError("sitekey is required")
        if not url:
            raise ValueError("url is required")

        started = time.monotonic()
        last_error: Optional[str] = None
        last_ua: Optional[str] = None
        last_proxy: Optional[str] = proxy

        for attempt in range(1, self.max_retries + 1):
            attempt_started = time.monotonic()
            logger.info(
                "solve_attempt start attempt=%d/%d url=%s sitekey=%s proxy=%s widget=%s",
                attempt, self.max_retries, url, sitekey[:10] + "...",
                bool(last_proxy), widget_type,
            )
            try:
                token, ua = self._solve_once(
                    url=url,
                    sitekey=sitekey,
                    proxy=last_proxy,
                    widget_type=widget_type,
                    action=action,
                    cdata=cdata,
                    theme=theme,
                    size=size,
                    language=language,
                    appearance=appearance,
                    execution=execution,
                )
                elapsed_ms = int((time.monotonic() - started) * 1000)
                result = SolveResult(
                    token=token,
                    elapsed_ms=elapsed_ms,
                    useragent=ua,
                    status="success",
                    attempts=attempt,
                    proxy_used=last_proxy,
                    widget_type=widget_type,
                )
                logger.info(
                    "solve_success attempt=%d elapsed_ms=%d token_len=%d",
                    attempt, elapsed_ms, len(token or ""),
                )
                return result.to_dict()
            except Exception as exc:  # noqa: BLE001 - capture all for retries
                attempt_ms = int((time.monotonic() - attempt_started) * 1000)
                last_error = f"{type(exc).__name__}: {exc}"
                last_ua = getattr(exc, "useragent", None) or last_ua
                logger.warning(
                    "solve_attempt_failed attempt=%d attempt_ms=%d error=%s",
                    attempt, attempt_ms, last_error,
                )
                if attempt < self.max_retries:
                    backoff = min(2.0 * attempt, 5.0)
                    time.sleep(backoff)
                continue

        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "solve_failed url=%s sitekey=%s elapsed_ms=%d error=%s",
            url, sitekey[:10] + "...", elapsed_ms, last_error,
        )
        return SolveResult(
            token=None,
            elapsed_ms=elapsed_ms,
            useragent=last_ua,
            status="failed",
            attempts=self.max_retries,
            error=last_error,
            proxy_used=last_proxy,
            widget_type=widget_type,
        ).to_dict()

    # ------------------------------------------------------------------ #
    # Managed challenge bypass
    # ------------------------------------------------------------------ #
    def challenge(
        self,
        url: str,
        proxy: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> dict:
        """Navigate to a Cloudflare managed challenge page and wait for it
        to auto-resolve, returning the ``cf_clearance`` cookie.

        Unlike ``solve()`` which renders a Turnstile widget via route
        interception, ``challenge()`` navigates to the REAL URL and waits
        for Cloudflare's "Just a moment..." interstitial to clear.

        Returns
        -------
        dict
            ``{cf_clearance, url, useragent, status, elapsed_ms}``
        """
        t = timeout or max(self.timeout, 30)
        started = time.monotonic()
        pool_idx, launcher = self._pool.acquire()
        try:
            page = launcher.new_page(proxy=proxy)
            try:
                page.goto(url, timeout=t * 1000)
                # Wait for the managed challenge to resolve.
                deadline = time.monotonic() + t
                clearance = None
                while time.monotonic() < deadline:
                    clearance = get_cf_clearance(page)
                    if clearance:
                        break
                    # Check if there's a Turnstile widget to click.
                    if is_managed_challenge(page):
                        from solver.token_poller import _try_click_widget
                        _try_click_widget(page)
                    time.sleep(1.0)
                ua = extract_useragent(page)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if clearance:
                    logger.info("challenge_success url=%s elapsed_ms=%d", url[:50], elapsed_ms)
                    return {
                        "cf_clearance": clearance,
                        "url": page.url,
                        "useragent": ua,
                        "status": "success",
                        "elapsed_ms": elapsed_ms,
                    }
                logger.warning("challenge_timeout url=%s elapsed_ms=%d", url[:50], elapsed_ms)
                return {
                    "cf_clearance": None,
                    "url": page.url,
                    "useragent": ua,
                    "status": "failed",
                    "error": "challenge_timeout",
                    "elapsed_ms": elapsed_ms,
                }
            finally:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self._pool.release(pool_idx)

    @property
    def pool_status(self) -> dict:
        """Return the current browser pool status."""
        return {
            "size": self._pool.size,
            "available": self._pool.available_count,
            "busy": self._pool.busy_count,
        }

    # ------------------------------------------------------------------ #
    # Single-attempt internals
    # ------------------------------------------------------------------ #
    def _solve_once(
        self,
        url: str,
        sitekey: str,
        proxy: Optional[str],
        widget_type: str,
        action: Optional[str],
        cdata: Optional[str],
        theme: str,
        size: str,
        language: str,
        appearance: Optional[str],
        execution: Optional[str],
    ) -> tuple[str, str]:
        """Run one solve attempt. Raises on failure; returns (token, ua)."""
        config = build_config(
            sitekey=sitekey,
            widget_type=widget_type,
            action=action,
            cdata=cdata,
            theme=theme,
            size=size,
            language=language,
            appearance=appearance,
            execution=execution,
        )
        html = render_html(config)

        # Acquire a browser from the pool (blocks if all are busy).
        pool_idx, launcher = self._pool.acquire()
        t0 = time.monotonic()
        try:
            page = launcher.new_page(proxy=proxy)
            t_page = time.monotonic()
            url_with_slash = url if url.endswith("/") else url + "/"
            page.route(
                url_with_slash,
                lambda route: route.fulfill(
                    body=html,
                    status=200,
                    content_type="text/html; charset=utf-8",
                ),
            )
            page.goto(url_with_slash, timeout=self.timeout * 1000)
            t_goto = time.monotonic()

            token = wait_for_token(
                page, timeout=self.timeout, widget_type=widget_type,
            )
            t_token = time.monotonic()
            ua = extract_useragent(page)
            logger.info(
                "solve_timing pool=%d page_create=%dms goto=%dms token_wait=%dms total=%dms",
                pool_idx,
                int((t_page - t0) * 1000),
                int((t_goto - t_page) * 1000),
                int((t_token - t_goto) * 1000),
                int((t_token - t0) * 1000),
            )
            if not token:
                raise RuntimeError("token_empty_after_wait")
            return token, ua
        finally:
            try:
                page.unroute(url_with_slash)
            except Exception:  # noqa: BLE001
                pass
            try:
                ctx = getattr(page, "_stealth_context", None)
                if ctx:
                    ctx.close()
                else:
                    page.close()
            except Exception:  # noqa: BLE001
                pass
            # Return the browser to the pool.
            self._pool.release(pool_idx)


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------

_default_solver: Optional[TurnstileSolver] = None


def get_solver() -> TurnstileSolver:
    """Return a lazily-constructed process-wide :class:`TurnstileSolver`."""
    global _default_solver
    if _default_solver is None:
        _default_solver = TurnstileSolver()
    return _default_solver
