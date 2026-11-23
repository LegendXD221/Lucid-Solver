"""Turnstile solver core.

Implements :class:`TurnstileSolver`, a DrissionPage based solver that renders
the Cloudflare Turnstile widget on a generated local HTML page, waits for the
callback to fire, and returns the resulting ``cf-turnstile-response`` token.

The approach follows the reference implementation at
``github.com/Theyka/Turnstile-Solver``:

1. Build an HTML page embedding the Turnstile JS bundle and the supplied
   ``sitekey``.
2. Spin up a headless Chromium instance through DrissionPage, optionally
   routing traffic through a proxy.
3. Poll ``window.__tsToken`` until the widget calls back with a token, or
   until the per-attempt timeout elapses.
4. Retry up to ``max_retries`` times before giving up.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage._units.listener import Listener  # noqa: F401  (type only)

logger = logging.getLogger("solver.core")

# ---------------------------------------------------------------------------
# Widget templates
# ---------------------------------------------------------------------------

# Placeholders are replaced via str.replace() in _write_html() to avoid
# conflicts between JavaScript object braces and str.format() syntax.
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Turnstile Solver</title>
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=__tsLoad" async defer></script>
    <script>
        window.__tsToken = null;
        window.__tsError = null;
        window.__tsReady = false;
        window.__tsWidgetId = null;
        window.__tsWidgetType = "__WIDGET_TYPE__";

        function __tsLoad() {
            try {
                var cfg = {
                    sitekey: "__SITEKEY__",
                    action: "__ACTION__",
                    callback: function(token) { window.__tsToken = token; },
                    "error-callback": function(code) { window.__tsError = String(code || "error"); },
                    "expired-callback": function() { window.__tsError = "expired"; window.__tsToken = null; },
                    "timeout-callback": function() { window.__tsError = "timeout"; }
                };

                if (window.__tsWidgetType === "invisible") {
                    cfg.appearance = "execute";
                } else if (window.__tsWidgetType === "non-interactive") {
                    cfg.appearance = "always";
                } else {
                    cfg.appearance = "always";
                }

                window.__tsWidgetId = turnstile.render("#cf-turnstile", cfg);
                window.__tsReady = true;

                if (window.__tsWidgetType === "invisible") {
                    setTimeout(function() {
                        try { turnstile.execute(window.__tsWidgetId); }
                        catch (e) { window.__tsError = "execute:" + e.message; }
                    }, 400);
                }
            } catch (e) {
                window.__tsError = "load:" + e.message;
            }
        }
    </script>
</head>
<body>
    <div id="cf-turnstile" data-sitekey="__SITEKEY__"></div>
</body>
</html>"""

# Default user-agent that plays nicely with Cloudflare - overridden at runtime
# by Chromium's own UA unless ``useragent`` is explicitly supplied.
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SolveResult:
    token: Optional[str]
    elapsed_ms: int
    useragent: Optional[str]
    status: str  # "success" | "failed"
    attempts: int = 0
    error: Optional[str] = None
    proxy_used: Optional[str] = None
    widget_type: str = "managed"
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload = {
            "token": self.token,
            "elapsed_ms": self.elapsed_ms,
            "useragent": self.useragent,
            "status": self.status,
            "attempts": self.attempts,
            "widget_type": self.widget_type,
        }
        if self.error:
            payload["error"] = self.error
        if self.proxy_used:
            payload["proxy_used"] = self.proxy_used
        if self.extra:
            payload.update(self.extra)
        return payload


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

class TurnstileSolver:
    """Solve Cloudflare Turnstile challenges using DrissionPage.

    Parameters
    ----------
    headless:
        Run Chromium without a visible window (default ``True``).
    max_retries:
        Number of attempts before giving up (default ``3``).
    timeout:
        Per-attempt timeout in seconds (default ``30``).
    useragent:
        Force a specific user-agent string.  When ``None`` Chromium's default
        is used.
    browser_path:
        Path to a Chromium binary.  When ``None`` DrissionPage auto-detects.
    tmp_dir:
        Directory used to drop the generated HTML file.  Defaults to the
        system temp directory.
    """

    WIDGET_TYPES = ("managed", "non-interactive", "invisible")

    def __init__(
        self,
        headless: bool = True,
        max_retries: int = 3,
        timeout: int = 30,
        useragent: Optional[str] = None,
        browser_path: Optional[str] = None,
        tmp_dir: Optional[str | Path] = None,
    ) -> None:
        self.headless = self._env_bool("HEADLESS", headless)
        self.max_retries = int(os.getenv("MAX_RETRIES", max_retries))
        self.timeout = int(os.getenv("SOLVE_TIMEOUT", timeout))
        self.useragent = useragent or os.getenv("USER_AGENT")
        self.browser_path = browser_path or os.getenv("CHROMIUM_PATH")
        self.tmp_dir = Path(tmp_dir or os.getenv("TMP_DIR", tempfile.gettempdir()))
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "solver_initialized headless=%s retries=%d timeout=%s ua=%s browser=%s",
            self.headless, self.max_retries, self.timeout,
            bool(self.useragent), bool(self.browser_path),
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
    ) -> dict:
        """Attempt to solve a Turnstile challenge.

        Parameters
        ----------
        url:
            The target URL.  Used as a referrer hint for Cloudflare; the
            widget itself is always rendered on a generated local HTML page
            so we fully control the DOM.
        sitekey:
            The Turnstile sitekey (``0x4AAAA...``).
        proxy:
            Optional proxy URL (``http://``, ``https://``, ``socks5://``).
            Credentials may be embedded: ``http://user:pass@host:port``.
        widget_type:
            One of ``managed``, ``non-interactive``, ``invisible``.
        action:
            Optional Turnstile ``action`` string embedded in the token.

        Returns
        -------
        dict
            ``{token, elapsed_ms, useragent, status, attempts, widget_type}``
            plus ``error``/``proxy_used`` when relevant.
        """
        if widget_type not in self.WIDGET_TYPES:
            raise ValueError(
                f"widget_type must be one of {self.WIDGET_TYPES}, got {widget_type!r}"
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
                    action=action or "",
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
            except Exception as exc:  # noqa: BLE001 - we want to capture all
                attempt_ms = int((time.monotonic() - attempt_started) * 1000)
                last_error = f"{type(exc).__name__}: {exc}"
                last_ua = getattr(exc, "useragent", None) or last_ua
                logger.warning(
                    "solve_attempt_failed attempt=%d attempt_ms=%d error=%s",
                    attempt, attempt_ms, last_error,
                )
                # Brief backoff before retrying, unless this was the last try.
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
    # Internals
    # ------------------------------------------------------------------ #
    def _solve_once(
        self,
        url: str,
        sitekey: str,
        proxy: Optional[str],
        widget_type: str,
        action: str,
    ) -> tuple[str, str]:
        html_path = self._write_html(sitekey, widget_type, action)
        co = self._build_options(proxy=proxy, referrer=url)

        tab = None
        browser = None
        try:
            browser = Chromium(co)
            tab = browser.latest_tab
            if tab is None:
                # DrissionPage <4.1 returns a ChromiumPage directly
                tab = browser
            tab.set.timeouts(base=self.timeout, page_load=self.timeout, script=self.timeout)
            tab.get(f"file://{html_path}")

            token = self._wait_for_token(tab, timeout=self.timeout)
            ua = self._extract_useragent(tab)
            if not token:
                raise RuntimeError("token_empty_after_wait")
            return token, ua
        finally:
            # Attach useragent to any exception for telemetry.
            try:
                if tab is not None:
                    self._extract_useragent(tab)  # cache for error path
            except Exception:  # noqa: BLE001
                pass
            try:
                if browser is not None:
                    browser.quit()
            except Exception:  # noqa: BLE001
                pass
            try:
                html_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

    def _build_options(self, proxy: Optional[str], referrer: str) -> ChromiumOptions:
        co = ChromiumOptions()
        if self.browser_path:
            co.set_browser_path(self.browser_path)
        if self.headless:
            co.headless()
        # Stability flags - required inside Docker / systemd containers.
        for flag in (
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-extensions",
            "--disable-sync",
            "--disable-translate",
            "--disable-default-apps",
            "--no-first-run",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--window-size=1280,800",
        ):
            co.set_argument(flag)
        if self.useragent:
            co.set_user_agent(self.useragent)
        if proxy:
            co.set_proxy(proxy)
        if referrer:
            # Spoof the referer so Cloudflare sees the registered domain.
            co.set_argument(f"--referer={referrer}")
        # Mute noisy stderr from Chromium when headless.
        co.set_argument("--log-level=3")
        return co

    def _write_html(self, sitekey: str, widget_type: str, action: str) -> Path:
        # Use str.replace() instead of str.format() so the JavaScript object
        # braces in the template are not mistaken for format placeholders.
        html = (
            HTML_TEMPLATE
            .replace("__SITEKEY__", sitekey)
            .replace("__WIDGET_TYPE__", widget_type)
            .replace("__ACTION__", action or "")
        )
        fd, path = tempfile.mkstemp(
            prefix="turnstile_", suffix=".html", dir=str(self.tmp_dir)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(html)
        return Path(path)

    def _wait_for_token(self, tab, timeout: int) -> Optional[str]:
        deadline = time.monotonic() + timeout
        poll_interval = 0.4
        while time.monotonic() < deadline:
            try:
                token = tab.run_js("return window.__tsToken || null;")
                if token and isinstance(token, str) and len(token) > 20:
                    return token
                err = tab.run_js("return window.__tsError || null;")
                if err:
                    # If it is a hard error from Cloudflare, bail immediately.
                    raise RuntimeError(f"turnstile_error: {err}")
            except RuntimeError:
                raise
            except Exception:  # noqa: BLE001 - keep polling through JS hiccups
                logger.debug("poll_js_exception", exc_info=True)
            time.sleep(poll_interval)
        return None

    def _extract_useragent(self, tab) -> str:
        try:
            ua = tab.run_js("return navigator.userAgent;")
            if ua and isinstance(ua, str):
                return ua
        except Exception:  # noqa: BLE001
            pass
        try:
            return tab.user_agent  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return self.useragent or DEFAULT_UA

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Module-level singleton helpers
# ---------------------------------------------------------------------------

_default_solver: Optional[TurnstileSolver] = None


def get_solver() -> TurnstileSolver:
    """Return a lazily constructed process-wide :class:`TurnstileSolver`."""
    global _default_solver
    if _default_solver is None:
        _default_solver = TurnstileSolver()
    return _default_solver
