"""Sync browser lifecycle with persistent browser reuse + auto-restart.

Keeps a single browser process alive across solves.  Auto-restarts on crash.
Injects self-executing stealth JS before every page load.
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
    def __init__(self, headless=True, useragent=None, browser_type="chrome", browser_path=None):
        self.headless = headless
        self.useragent = useragent or DEFAULT_UA
        self.browser_type = browser_type
        self.browser_path = browser_path
        if self.browser_type not in BROWSER_TYPES:
            self.browser_type = "chromium"
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    def _ensure_browser(self) -> Browser:
        if self._browser is not None:
            try:
                if not self._browser.is_connected():
                    raise RuntimeError("disconnected")
                return self._browser
            except Exception:
                logger.warning("browser_crashed, restarting")
                self._browser = None
                if self._playwright:
                    try: self._playwright.stop()
                    except: pass
                    self._playwright = None
        from patchright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._do_launch(self._playwright)
        logger.info("browser_started type=%s", self.browser_type)
        return self._browser

    def _do_launch(self, pw: Playwright, proxy: Optional[str] = None) -> Browser:
        args = get_launch_args()
        if self.useragent:
            args.append(f"--user-agent={self.useragent}")
        opts: Dict[str, Any] = {"headless": self.headless, "args": args}
        if self.browser_path:
            opts["executable_path"] = self.browser_path
        elif self.browser_type == "chrome":
            opts["channel"] = "chrome"
        elif self.browser_type == "msedge":
            opts["channel"] = "msedge"
        if proxy:
            opts["proxy"] = self.parse_proxy(proxy)
        return pw.chromium.launch(**opts)

    def new_page(self, proxy: Optional[str] = None) -> Page:
        """Create a page with stealth JS. Auto-restarts on crash."""
        sjs = get_stealth_js()
        last_err = None
        for attempt in range(2):
            browser = self._ensure_browser()
            try:
                page = browser.new_page()
                page.add_init_script(sjs)
                return page
            except Exception as e:
                last_err = e
                logger.warning("new_page_failed attempt=%d error=%s", attempt+1, str(e)[:100])
                self._browser = None
                if self._playwright:
                    try: self._playwright.stop()
                    except: pass
                    self._playwright = None
        raise RuntimeError(f"new_page failed: {last_err}")

    def stop(self):
        if self._browser:
            try: self._browser.close()
            except: pass
            self._browser = None
        if self._playwright:
            try: self._playwright.stop()
            except: pass
            self._playwright = None

    @staticmethod
    def parse_proxy(proxy_url: str) -> dict:
        if not any(proxy_url.startswith(s) for s in ("http://","https://","socks5://","socks5h://")):
            proxy_url = f"http://{proxy_url}"
        p = urlparse(proxy_url)
        if not p.hostname or not p.port:
            raise ValueError(f"invalid proxy: {proxy_url!r}")
        r: Dict[str,str] = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
        if p.username: r["username"] = unquote(p.username)
        if p.password: r["password"] = unquote(p.password)
        return r
