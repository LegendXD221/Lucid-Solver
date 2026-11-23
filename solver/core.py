"""Turnstile solver core - patchright-based.

Based on the reference implementation at github.com/Theyka/Turnstile-Solver,
updated to support **every** Cloudflare Turnstile widget type and all
client-side configuration options.

Key techniques
--------------
1. **Route interception** — instead of loading a ``file://`` page, the solver
   intercepts requests to the *target URL* and fulfills them with a custom
   HTML page.  Cloudflare sees the correct ``Origin`` / ``Referer``, which is
   critical for sitekeys with domain verification enabled.

2. **Explicit ``turnstile.render()``** — the HTML uses the JavaScript API
   rather than the declarative ``<div class="cf-turnstile">`` so we can pass
   ``appearance`` and ``execution`` per widget type:
     * ``managed``         → appearance=always,    execution=render
     * ``non-interactive`` → appearance=always,    execution=render
     * ``invisible``       → appearance=execute,   execution=execute (+ turnstile.execute())

3. **patchright** — a patched fork of Playwright that strips automation
   fingerprints (``navigator.webdriver``, CDP artifacts, etc.) so Cloudflare's
   bot detection has a harder time distinguishing the browser from a real user.

4. **Dual token extraction** — polls both the ``callback``-populated
   ``window.__tsToken`` variable and the ``[name=cf-turnstile-response]``
   hidden input that Turnstile creates.  Either signal is sufficient.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlparse, unquote

from patchright.sync_api import sync_playwright, Browser, Page

logger = logging.getLogger("solver.core")

# ---------------------------------------------------------------------------
# Default user-agent - a realistic desktop Chrome UA.  Patchright already
# strips ``HeadlessChrome`` from the UA, but setting an explicit one avoids
# any residual fingerprint.
# ---------------------------------------------------------------------------

# Default user-agent.  MUST NOT contain "HeadlessChrome" — that substring
# is an instant bot-detection signal for Cloudflare.  We default to a
# realistic Chrome UA; the version doesn't need to match the binary exactly.
# Override via the USER_AGENT env var for a specific Chrome version.
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Widget type → default appearance / execution mapping.
#
# The widget type is fundamentally a server-side property of the sitekey
# (configured in the Cloudflare dashboard).  The client controls two related
# options that determine how the widget behaves on the page:
#
#   * ``appearance`` — when the widget is *visible*:
#       - ``always``           visible as soon as it renders
#       - ``execute``          visible only while a challenge is executing
#       - ``interaction-only`` visible only when user interaction is required
#
#   * ``execution`` — when the challenge *runs*:
#       - ``render``   runs automatically on render
#       - ``execute``  runs only when ``turnstile.execute(widgetId)`` is called
#
# For invisible sitekeys the client MUST use execution=execute and call
# ``turnstile.execute()``; otherwise no challenge runs and no token is issued.
# ---------------------------------------------------------------------------

WIDGET_TYPE_CONFIG: Dict[str, Dict[str, str]] = {
    "managed":         {"appearance": "always",  "execution": "render"},
    "non-interactive": {"appearance": "always",  "execution": "render"},
    "invisible":       {"appearance": "execute", "execution": "execute"},
}

# ---------------------------------------------------------------------------
# HTML template.  Configuration is injected as a JSON blob so we never have
# to worry about brace-escaping (the JS object literals use { } freely).
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Just a moment...</title>
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=__tsLoad" async defer></script>
    <script>
        window.__tsToken = null;
        window.__tsError = null;
        window.__tsWidgetId = null;
        window.__tsReady = false;
        window.__tsExecuted = false;
        window.__tsConfig = __CONFIG_JSON__;

        function __tsLoad() {
            try {
                // Merge caller config over the callback wiring.  Caller
                // provides sitekey/appearance/execution/etc.; we always
                // supply the callback hooks so Python can observe state.
                var cfg = Object.assign({
                    callback: function(token) { window.__tsToken = token; },
                    'error-callback': function(code) {
                        window.__tsError = 'error:' + String(code || 'unknown');
                    },
                    'expired-callback': function() {
                        window.__tsError = 'expired';
                        window.__tsToken = null;
                    },
                    'timeout-callback': function() {
                        window.__tsError = 'timeout';
                    },
                    'unsupported-callback': function() {
                        window.__tsError = 'unsupported';
                    }
                }, window.__tsConfig);

                window.__tsWidgetId = turnstile.render('#cf-turnstile', cfg);
                window.__tsReady = true;

                // Invisible / execute widgets need an explicit execute()
                // call to start the challenge.  Delay slightly so the widget
                // is fully mounted first.
                var needsExecute = (cfg.execution === 'execute')
                    || (cfg.appearance === 'execute');
                if (needsExecute) {
                    setTimeout(function() {
                        try {
                            turnstile.execute(window.__tsWidgetId);
                            window.__tsExecuted = true;
                        } catch (e) {
                            window.__tsError = 'execute:' + e.message;
                        }
                    }, 300);
                }
            } catch (e) {
                window.__tsError = 'load:' + e.message;
            }
        }
    </script>
</head>
<body>
    <div id="cf-turnstile"></div>
</body>
</html>"""


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
        payload: Dict[str, Any] = {
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
        Override the browser user-agent.  Defaults to a realistic Chrome UA.
    browser_type:
        One of ``chromium`` (patchright's patched build, best anti-detection),
        ``chrome`` (system Google Chrome), ``msedge`` (system Edge).
        Default ``chromium``.
    browser_path:
        Explicit path to a browser binary.  Overrides ``browser_type``.
    """

    WIDGET_TYPES = ("managed", "non-interactive", "invisible")
    BROWSER_TYPES = ("chromium", "chrome", "msedge")

    def __init__(
        self,
        headless: bool = True,
        max_retries: int = 3,
        timeout: int = 30,
        useragent: Optional[str] = None,
        browser_type: str = "chrome",
        browser_path: Optional[str] = None,
    ) -> None:
        self.headless = self._env_bool("HEADLESS", headless)
        self.max_retries = int(os.getenv("MAX_RETRIES", max_retries))
        self.timeout = int(os.getenv("SOLVE_TIMEOUT", timeout))
        self.useragent = useragent or os.getenv("USER_AGENT") or DEFAULT_UA
        # Default to "chrome" (system Google Chrome via channel="chrome")
        # — this gives a real Chrome binary which Cloudflare's bot detection
        # has a harder time fingerprinting than Playwright's bundled Chromium.
        # Fall back to "chromium" (patchright's patched build) if system
        # Chrome is not available.
        self.browser_type = os.getenv("BROWSER_TYPE", browser_type) or "chrome"
        self.browser_path = browser_path or os.getenv("CHROMIUM_PATH") or os.getenv("BROWSER_PATH")

        if self.browser_type not in self.BROWSER_TYPES:
            logger.warning("unsupported browser_type=%s, falling back to chromium", self.browser_type)
            self.browser_type = "chromium"

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

        Parameters
        ----------
        url:
            Target URL registered with the sitekey.  The solver intercepts
            navigation to this URL and serves a custom HTML page, so
            Cloudflare sees the correct origin.
        sitekey:
            The Turnstile sitekey (``0x4AAAA...``).
        proxy:
            Optional proxy URL (``http://``, ``https://``, ``socks5://``).
            Credentials: ``http://user:pass@host:port``.
        widget_type:
            One of ``managed``, ``non-interactive``, ``invisible``.
            Determines default ``appearance``/``execution``; overridable.
        action:
            Optional Turnstile ``action`` embedded in the token.
        cdata:
            Optional custom data Cloudflare echoes back in the siteverify
            response.
        theme:
            ``auto``, ``light``, ``dark``.
        size:
            ``normal``, ``flexible``, ``compact``.
        language:
            ``auto`` or an ISO 639-1 code (``en``, ``fr``, ...).
        appearance:
            Override the widget-type default: ``always``, ``execute``,
            ``interaction-only``.
        execution:
            Override the widget-type default: ``render``, ``execute``.

        Returns
        -------
        dict
            ``{token, elapsed_ms, useragent, status, attempts, widget_type}``
            plus ``error`` / ``proxy_used`` when relevant.
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
            except Exception as exc:  # noqa: BLE001 - capture everything for retries
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
        config = self._build_config(
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
        html = HTML_TEMPLATE.replace("__CONFIG_JSON__", json.dumps(config))

        with sync_playwright() as p:
            browser = self._launch_browser(p, proxy)
            try:
                page = browser.new_page()
                # Route interception: serve our HTML when the browser navigates
                # to the target URL.  Cloudflare sees the correct origin.
                url_with_slash = url if url.endswith("/") else url + "/"
                page.route(url_with_slash, lambda route: route.fulfill(
                    body=html,
                    status=200,
                    content_type="text/html; charset=utf-8",
                ))
                page.goto(url_with_slash, timeout=self.timeout * 1000)

                token = self._wait_for_token(page, timeout=self.timeout,
                                             widget_type=widget_type)
                ua = self._extract_useragent(page)
                if not token:
                    raise RuntimeError("token_empty_after_wait")
                return token, ua
            finally:
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass

    def _build_config(
        self,
        sitekey: str,
        widget_type: str,
        action: Optional[str],
        cdata: Optional[str],
        theme: str,
        size: str,
        language: str,
        appearance: Optional[str],
        execution: Optional[str],
    ) -> dict:
        """Build the JSON config object passed to ``turnstile.render()``."""
        # Start from the widget-type defaults, then apply explicit overrides.
        defaults = WIDGET_TYPE_CONFIG.get(widget_type, WIDGET_TYPE_CONFIG["managed"])
        cfg: Dict[str, Any] = {
            "sitekey": sitekey,
            "theme": theme or "auto",
            "size": size or "normal",
            "appearance": appearance or defaults["appearance"],
            "execution": execution or defaults["execution"],
        }
        if action:
            cfg["action"] = action
        if cdata:
            cfg["cdata"] = cdata
        if language and language != "auto":
            cfg["language"] = language
        # Sensible retry defaults so the widget retries on its own.
        cfg.setdefault("retry", "auto")
        cfg.setdefault("retry-interval", 2000)
        cfg.setdefault("refresh-expired", "auto")
        return cfg

    def _launch_browser(self, playwright, proxy: Optional[str]) -> Browser:
        # Minimal args — patchright handles anti-detection internally.
        # Extra flags like --disable-blink-features=AutomationControlled are
        # counterproductive: they themselves become fingerprints that
        # Cloudflare detects.  Only --no-sandbox (required for root) and
        # --user-agent (to strip "HeadlessChrome") are needed.
        launch_args = ["--no-sandbox"]
        if self.useragent:
            launch_args.append(f"--user-agent={self.useragent}")

        launch_opts: Dict[str, Any] = {
            "headless": self.headless,
            "args": launch_args,
        }
        if self.browser_path:
            launch_opts["executable_path"] = self.browser_path
        elif self.browser_type == "chrome":
            # System Google Chrome — real binary, best stealth.
            launch_opts["channel"] = "chrome"
        elif self.browser_type == "msedge":
            launch_opts["channel"] = "msedge"
        # browser_type == "chromium" → patchright's patched build (fallback)

        if proxy:
            launch_opts["proxy"] = self._parse_proxy(proxy)

        return playwright.chromium.launch(**launch_opts)

    @staticmethod
    def _parse_proxy(proxy_url: str) -> dict:
        """Convert a proxy URL string into patchright's proxy dict."""
        parsed = urlparse(proxy_url)
        if not parsed.hostname or not parsed.port:
            raise ValueError(f"invalid proxy url: {proxy_url!r}")
        server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        result: Dict[str, str] = {"server": server}
        if parsed.username:
            result["username"] = unquote(parsed.username)
        if parsed.password:
            result["password"] = unquote(parsed.password)
        return result

    def _wait_for_token(self, page: Page, timeout: int,
                        widget_type: str) -> Optional[str]:
        """Poll for the token via both the JS callback and the hidden input."""
        deadline = time.monotonic() + timeout
        poll_interval = 0.4
        last_click = 0.0
        click_cooldown = 2.0  # don't hammer the widget

        while time.monotonic() < deadline:
            # Method 1: callback-populated JS variable (most reliable).
            try:
                token = page.evaluate("() => window.__tsToken")
                if token and isinstance(token, str) and len(token) > 20:
                    return token
            except Exception:  # noqa: BLE001
                pass

            # Method 2: hidden input that Turnstile populates (fallback).
            try:
                val = page.input_value("[name=cf-turnstile-response]", timeout=500)
                if val and len(val) > 20:
                    return val
            except Exception:  # noqa: BLE001
                pass

            # Check for hard errors from Cloudflare.
            try:
                err = page.evaluate("() => window.__tsError")
                if err:
                    raise RuntimeError(f"turnstile_error: {err}")
            except RuntimeError:
                raise
            except Exception:  # noqa: BLE001
                pass

            # For visible widgets (managed / non-interactive) a click can
            # nudge the challenge forward.  Skip for invisible widgets -
            # there is nothing to click and execute() already ran in JS.
            if widget_type != "invisible" and (time.monotonic() - last_click) > click_cooldown:
                try:
                    page.click("div.cf-turnstile, #cf-turnstile", timeout=1500)
                    last_click = time.monotonic()
                except Exception:  # noqa: BLE001
                    pass

            time.sleep(poll_interval)

        return None

    @staticmethod
    def _extract_useragent(page: Page) -> str:
        try:
            ua = page.evaluate("() => navigator.userAgent")
            if ua and isinstance(ua, str):
                return ua
        except Exception:  # noqa: BLE001
            pass
        return DEFAULT_UA

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
    """Return a lazily-constructed process-wide :class:`TurnstileSolver`."""
    global _default_solver
    if _default_solver is None:
        _default_solver = TurnstileSolver()
    return _default_solver
