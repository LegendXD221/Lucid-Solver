"""Browser lifecycle management for the Turnstile solver.

Wraps patchright's Chromium launch with the configuration that defeats
Cloudflare's bot detection:

* ``channel="chrome"`` — use the system Google Chrome binary (real
  fingerprint) rather than Playwright's bundled Chromium.
* ``--user-agent=<realistic UA>`` — strip ``HeadlessChrome`` from the UA
  string, which is an instant bot-detection signal.
* Minimal args — only ``--no-sandbox`` (required for root) and
  ``--user-agent``.  Extra anti-detection flags like
  ``--disable-blink-features=AutomationControlled`` are counterproductive:
  they themselves become fingerprints.  Patchright handles
  ``navigator.webdriver`` and CDP stealth internally.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from patchright.sync_api import Browser

from solver.constants import BROWSER_TYPES, DEFAULT_UA

logger = logging.getLogger("solver.browser")


class BrowserLauncher:
    """Configures and launches a patchright Chromium browser instance.

    Created once per solver and reused across attempts.  The actual browser
    process is created fresh per attempt (inside a ``sync_playwright()``
    context manager) to avoid state leakage between solves.
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
        """Launch a browser instance.

        Parameters
        ----------
        playwright:
            A running ``sync_playwright()`` context manager.
        proxy:
            Optional proxy URL (``http://``, ``https://``, ``socks5://``).
            Credentials: ``http://user:pass@host:port``.
        """
        # Minimal args — patchright handles anti-detection internally.
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
            launch_opts["proxy"] = self.parse_proxy(proxy)

        logger.info(
            "browser_launch headless=%s type=%s proxy=%s",
            self.headless, self.browser_type, bool(proxy),
        )
        return playwright.chromium.launch(**launch_opts)

    @staticmethod
    def parse_proxy(proxy_url: str) -> dict:
        """Convert a proxy URL string into patchright's proxy dict.

        Accepts ``http://``, ``https://``, ``socks5://``, ``socks5h://``
        with optional credentials: ``scheme://user:pass@host:port``.
        Bare ``host:port`` is treated as HTTP.
        """
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
