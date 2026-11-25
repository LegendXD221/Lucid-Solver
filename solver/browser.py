"""Browser lifecycle management with persistent browser reuse.

Keeps a single browser process alive across solves to avoid the ~1s
launch overhead per request.  Each solve creates a new page (and
optionally a new context for per-request proxy) in the existing browser.

Architecture
------------
::

    BrowserLauncher
    ├── start()        → launch persistent playwright + browser (once)
    ├── new_page()     → create a page in the persistent browser (per solve)
    │   ├── no proxy   → browser.new_page()  (fastest, ~50ms)
    │   └── with proxy → browser.new_context(proxy=...) + new_page  (~200ms)
    └── stop()         → close browser + stop playwright (on shutdown)

If the browser crashes, ``new_page()`` detects it and auto-restarts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from patchright.sync_api import Browser, Page, Playwright

from solver.constants import BROWSER_TYPES, DEFAULT_UA
from solver.stealth import get_launch_args, get_stealth_js

logger = logging.getLogger("solver.browser")


class BrowserLauncher:
    """Manages a persistent patchright browser with stealth configuration."""

    def __init__(
        self,
        headless: bool = True,
        useragent: Optional[str] = None,
        browser_type: str = "chrome",
        browser_path: Optional[str] = None,
    ) -> None:
        self.headless = headless
        self.useragent = useragent or DEFAULT_UA
        self.browser_type = browser_type
        self.browser_path = browser_path

        if self.browser_type not in BROWSER_TYPES:
            logger.warning("unsupported browser_type=%s, falling back to chromium", self.browser_type)
            self.browser_type = "chromium"

        # Persistent state — initialized lazily on first new_page() call.
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    # ------------------------------------------------------------------ #
    # Persistent browser lifecycle
    # ------------------------------------------------------------------ #
    def _ensure_browser(self) -> Browser:
        """Launch the persistent browser if not already running.

        If the browser has crashed (is_closed()), restart it.
        """
        if self._browser is not None:
            try:
                if not self._browser.is_connected():
                    raise RuntimeError("browser disconnected")
                return self._browser
            except Exception:
                logger.warning("browser_crashed, restarting")
                self._browser = None
                if self._playwright:
                    try:
                        self._playwright.stop()
                    except Exception:
                        pass
                    self._playwright = None

        # Start playwright + browser.
        from patchright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._do_launch(self._playwright)
        logger.info(
            "browser_started headless=%s type=%s",
            self.headless, self.browser_type,
        )
        return self._browser

    def _do_launch(self, playwright: Playwright, proxy: Optional[str] = None) -> Browser:
        """Launch a browser instance with stealth args."""
        launch_args = get_launch_args()
        if self.useragent:
            launch_args.append(f"--user-agent={self.useragent}")

        launch_opts: Dict[str, Any] = {
            "headless": self.headless,
            "args": launch_args,
        }
        if self.browser_path:
            launch_opts["executable_path"] = self.browser_path
        elif self.browser_type == "chrome":
            launch_opts["channel"] = "chrome"
        elif self.browser_type == "msedge":
            launch_opts["channel"] = "msedge"

        if proxy:
            launch_opts["proxy"] = self.parse_proxy(proxy)

        return playwright.chromium.launch(**launch_opts)

    def new_page(self, proxy: Optional[str] = None) -> Page:
        """Create a new page in the persistent browser.

        If the browser has crashed (TargetClosedError), automatically
        restarts it and retries once.
        """
        stealth_js = get_stealth_js()
        last_err: Optional[Exception] = None

        for attempt in range(2):  # Try, restart on failure, try again.
            browser = self._ensure_browser()
            try:
                if proxy:
                    context = browser.new_context(
                        user_agent=self.useragent,
                        proxy=self.parse_proxy(proxy),
                    )
                    page = context.new_page()
                    page.add_init_script(stealth_js)
                    page._stealth_context = context  # type: ignore[attr-defined]
                else:
                    page = browser.new_page()
                    page.add_init_script(stealth_js)
                return page
            except Exception as e:
                last_err = e
                logger.warning(
                    "new_page_failed attempt=%d error=%s",
                    attempt + 1, str(e)[:120],
                )
                # Force-restart the browser.
                self._browser = None
                if self._playwright:
                    try:
                        self._playwright.stop()
                    except Exception:
                        pass
                    self._playwright = None
                # Second attempt will call _ensure_browser() which starts fresh.

        raise RuntimeError(f"new_page failed after restart: {last_err}")

    def stop(self) -> None:
        """Close the persistent browser and stop playwright."""
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        logger.info("browser_stopped")

    # ------------------------------------------------------------------ #
    # Legacy method (still used by tests and fallback)
    # ------------------------------------------------------------------ #
    def launch(self, playwright: Playwright, proxy: Optional[str] = None) -> Browser:
        """Launch a one-shot browser (legacy, not for persistent use)."""
        return self._do_launch(playwright, proxy=proxy)

    def new_context(self, browser: Browser):
        """Create a stealth context (legacy)."""
        context = browser.new_context(user_agent=self.useragent)
        context.add_init_script(get_stealth_js())
        return context

    @staticmethod
    def parse_proxy(proxy_url: str) -> dict:
        """Convert a proxy URL string into patchright's proxy dict."""
        if not any(proxy_url.startswith(s) for s in
                   ("http://", "https://", "socks5://", "socks5h://")):
            proxy_url = f"http://{proxy_url}"
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
