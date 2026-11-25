"""Browser lifecycle management for the Turnstile solver.

Wraps patchright's Chromium launch with stealth configuration:

* ``channel="chrome"`` — system Google Chrome binary (real fingerprint).
* ``--user-agent=<realistic UA>`` — strips ``HeadlessChrome``.
* **Stealth launch args** from ``solver.stealth`` — disables automation
  signals at the Blink/IPC level.
* **Stealth JS injection** via ``context.add_init_script()`` — patches
  ``navigator.webdriver``, plugins, languages, WebGL, chrome.runtime,
  connection, hardwareConcurrency, deviceMemory, canvas, shadow DOM,
  offsetHeight, and Function.toString BEFORE any page loads.

The stealth JS is the single biggest speed win: without it, Turnstile
detects automation and silently retries/rejects, adding 5-10s of wasted
time.  With it, the widget solves on the first attempt.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from patchright.sync_api import Browser, BrowserContext

from solver.constants import BROWSER_TYPES, DEFAULT_UA
from solver.stealth import get_launch_args, get_stealth_js

logger = logging.getLogger("solver.browser")


class BrowserLauncher:
    """Configures and launches a patchright Chromium browser instance.

    The launcher is created once per solver.  Each ``launch()`` call
    starts a fresh browser process; ``new_context()`` creates a context
    with stealth JS pre-injected.
    """

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
            logger.warning(
                "unsupported browser_type=%s, falling back to chromium",
                self.browser_type,
            )
            self.browser_type = "chromium"

    def launch(self, playwright, proxy: Optional[str] = None) -> Browser:
        """Launch a browser with stealth args."""
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

        logger.info(
            "browser_launch headless=%s type=%s proxy=%s stealth_args=%d",
            self.headless, self.browser_type, bool(proxy), len(launch_args),
        )
        return playwright.chromium.launch(**launch_opts)

    def new_context(self, browser: Browser) -> BrowserContext:
        """Create a browser context with stealth JS pre-injected.

        The stealth JS runs via ``add_init_script()`` which executes before
        any page's own JavaScript, so Cloudflare's challenge script never
        sees automation fingerprints.
        """
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
