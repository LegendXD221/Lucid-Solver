"""Async browser lifecycle management with persistent browser reuse.

Uses ``patchright.async_api`` (async Playwright) to be compatible with
FastAPI's asyncio event loop.  The sync API's greenlets conflict with
asyncio under concurrent load, causing "Sync API inside asyncio loop"
errors.

Keeps a single browser process alive across solves.  Each solve creates
a new page in the existing browser.  If the browser crashes, auto-restarts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from patchright.async_api import Browser, BrowserContext, Page, Playwright

from solver.constants import BROWSER_TYPES, DEFAULT_UA
from solver.stealth import get_launch_args, get_stealth_js

logger = logging.getLogger("solver.browser")


class BrowserLauncher:
    """Manages a persistent async patchright browser with stealth config."""

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

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    async def _ensure_browser(self) -> Browser:
        """Launch the persistent browser if not already running."""
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
                        await self._playwright.stop()
                    except Exception:
                        pass
                    self._playwright = None

        from patchright.async_api import async_playwright
        self._playwright = await async_playwright().start()
        self._browser = await self._do_launch(self._playwright)
        logger.info("browser_started headless=%s type=%s", self.headless, self.browser_type)
        return self._browser

    async def _do_launch(self, playwright: Playwright, proxy: Optional[str] = None) -> Browser:
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

        return await playwright.chromium.launch(**launch_opts)

    async def new_page(self, proxy: Optional[str] = None) -> Page:
        """Create a new page with stealth JS. Auto-restarts on crash."""
        stealth_js = get_stealth_js()
        last_err: Optional[Exception] = None

        for attempt in range(2):
            browser = await self._ensure_browser()
            try:
                if proxy:
                    context = await browser.new_context(
                        user_agent=self.useragent,
                        proxy=self.parse_proxy(proxy),
                    )
                    page = await context.new_page()
                    await page.add_init_script(stealth_js)
                    page._stealth_context = context  # type: ignore[attr-defined]
                else:
                    page = await browser.new_page()
                    await page.add_init_script(stealth_js)
                return page
            except Exception as e:
                last_err = e
                logger.warning("new_page_failed attempt=%d error=%s", attempt + 1, str(e)[:120])
                self._browser = None
                if self._playwright:
                    try:
                        await self._playwright.stop()
                    except Exception:
                        pass
                    self._playwright = None

        raise RuntimeError(f"new_page failed after restart: {last_err}")

    async def stop(self) -> None:
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        logger.info("browser_stopped")

    @staticmethod
    def parse_proxy(proxy_url: str) -> dict:
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
