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

from patchright.sync_api import sync_playwright

from solver.browser import BrowserLauncher
from solver.constants import BROWSER_TYPES, DEFAULT_UA, WIDGET_TYPES
from solver.html_builder import build_config, render_html
from solver.models import SolveResult
from solver.token_poller import extract_useragent, wait_for_token
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
        max_retries: int = 3,
        timeout: int = 30,
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
            browser_path
            or os.getenv("CHROMIUM_PATH")
            or os.getenv("BROWSER_PATH")
        )

        # The BrowserLauncher encapsulates all launch/proxy logic.
        self._launcher = BrowserLauncher(
            headless=self.headless,
            useragent=self.useragent,
            browser_type=self.browser_type,
            browser_path=self.browser_path,
        )

        logger.info(
            "solver_initialized headless=%s retries=%d timeout=%s browser=%s ua=%s",
            self.headless, self.max_retries, self.timeout,
            self.browser_type, bool(self.useragent),
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
        # Build the Turnstile widget config + inject into HTML.
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

        with sync_playwright() as p:
            browser = self._launcher.launch(playwright, proxy=proxy)
            try:
                page = browser.new_page()
                # Route interception: serve our HTML when the browser
                # navigates to the target URL.  Cloudflare sees the
                # correct origin.
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

                token = wait_for_token(
                    page, timeout=self.timeout, widget_type=widget_type,
                )
                ua = extract_useragent(page)
                if not token:
                    raise RuntimeError("token_empty_after_wait")
                return token, ua
            finally:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass


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
