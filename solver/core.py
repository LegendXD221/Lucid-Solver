"""Sync Turnstile solver — persistent browser, stealth JS, 8.5s target.

Uses a threading.Lock to serialize solves within each uvicorn worker.
This prevents the "Sync API inside asyncio loop" greenlet conflict that
occurs when multiple concurrent requests hit the same worker.

Concurrency is achieved via multiple uvicorn workers (``--workers N``),
each with its own browser instance and lock.  With WORKERS=2, two solves
can run in parallel (one per worker process).
"""
from __future__ import annotations
import logging, os, time, threading
from typing import Optional
from solver.browser import BrowserLauncher
from solver.cache import TokenCache
from solver.constants import BROWSER_TYPES, DEFAULT_UA, WIDGET_TYPES
from solver.hcaptcha import (
    extract_hcaptcha_useragent,
    render_hcaptcha_html,
    wait_for_hcaptcha_token,
)
from solver.hcaptcha_ai import solve_hcaptcha_with_ai
from solver.html_builder import build_config, render_html
from solver.models import SolveResult
from solver.token_poller import extract_useragent, wait_for_token
from solver.utils import env_bool

logger = logging.getLogger("solver.core")


class TurnstileSolver:
    WIDGET_TYPES = WIDGET_TYPES
    BROWSER_TYPES = BROWSER_TYPES

    def __init__(self, headless=True, max_retries=2, timeout=20, useragent=None,
                 browser_type="chrome", browser_path=None):
        self.headless = env_bool("HEADLESS", headless)
        self.max_retries = int(os.getenv("MAX_RETRIES", max_retries))
        self.timeout = int(os.getenv("SOLVE_TIMEOUT", timeout))
        self.useragent = useragent or os.getenv("USER_AGENT") or DEFAULT_UA
        self.browser_type = os.getenv("BROWSER_TYPE", browser_type) or "chrome"
        self.browser_path = browser_path or os.getenv("CHROMIUM_PATH") or os.getenv("BROWSER_PATH")
        self._launcher = BrowserLauncher(
            headless=self.headless, useragent=self.useragent,
            browser_type=self.browser_type, browser_path=self.browser_path)
        self._lock = threading.Lock()
        self._cache = TokenCache()
        logger.info("solver_initialized retries=%d timeout=%d browser=%s",
                     self.max_retries, self.timeout, self.browser_type)

    def solve(self, url, sitekey, proxy=None, widget_type="managed", action=None,
              cdata=None, theme="auto", size="normal", language="auto",
              appearance=None, execution=None) -> dict:
        if widget_type not in self.WIDGET_TYPES:
            raise ValueError(f"widget_type must be one of {self.WIDGET_TYPES}")
        if not sitekey: raise ValueError("sitekey is required")
        if not url: raise ValueError("url is required")
        # Check token cache first — repeated solves return instantly.
        cached = self._cache.get(sitekey, url, widget_type)
        if cached:
            logger.info("cache_hit sitekey=%s url=%s", sitekey[:10]+"...", url[:30])
            return {
                "token": cached.token,
                "elapsed_ms": 0,
                "useragent": cached.useragent,
                "status": "success",
                "attempts": 0,
                "widget_type": widget_type,
                "cached": True,
            }
        # Lock prevents concurrent sync Playwright calls in the same worker.
        with self._lock:
            result = self._solve_locked(url, sitekey, proxy, widget_type, action,
                cdata, theme, size, language, appearance, execution)
        # Cache the result if successful.
        if result.get("status") == "success" and result.get("token"):
            self._cache.put(sitekey, url, widget_type,
                result["token"], result.get("useragent", ""), result["elapsed_ms"])
        return result

    def _solve_locked(self, url, sitekey, proxy, widget_type, action, cdata,
                      theme, size, language, appearance, execution) -> dict:
        started = time.monotonic()
        last_error = None
        last_ua = None
        for attempt in range(1, self.max_retries + 1):
            t0 = time.monotonic()
            logger.info("solve_attempt %d/%d url=%s widget=%s", attempt, self.max_retries, url[:30], widget_type)
            try:
                token, ua = self._solve_once(url, sitekey, proxy, widget_type,
                    action, cdata, theme, size, language, appearance, execution)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.info("solve_success attempt=%d elapsed_ms=%d", attempt, elapsed_ms)
                return SolveResult(token=token, elapsed_ms=elapsed_ms, useragent=ua,
                    status="success", attempts=attempt, proxy_used=proxy,
                    widget_type=widget_type).to_dict()
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_ua = getattr(exc, "useragent", None) or last_ua
                ms = int((time.monotonic() - t0) * 1000)
                logger.warning("solve_attempt_failed %d %dms error=%s", attempt, ms, last_error[:80])
                if attempt < self.max_retries:
                    time.sleep(min(2.0 * attempt, 5.0))
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.error("solve_failed elapsed_ms=%d error=%s", elapsed_ms, last_error)
        return SolveResult(token=None, elapsed_ms=elapsed_ms, useragent=last_ua,
            status="failed", attempts=self.max_retries, error=last_error,
            proxy_used=proxy, widget_type=widget_type).to_dict()

    def solve_batch(self, tasks: list) -> list:
        """Solve multiple Turnstile challenges. Checks cache first for each.

        ``tasks`` is a list of dicts with url, sitekey, widget_type, etc.
        Returns a list of results in the same order.
        """
        results = []
        for task in tasks:
            try:
                r = self.solve(
                    url=task.get("url", ""),
                    sitekey=task.get("sitekey", ""),
                    proxy=task.get("proxy"),
                    widget_type=task.get("widget_type", "managed"),
                    action=task.get("action"),
                    cdata=task.get("cdata"),
                )
                results.append(r)
            except Exception as e:
                results.append({"status": "failed", "error": str(e), "elapsed_ms": 0})
        return results

    @property
    def cache_stats(self) -> dict:
        return self._cache.stats()

    @property
    def browser_alive(self) -> bool:
        try:
            return self._launcher._browser is not None and self._launcher._browser.is_connected()
        except:
            return False

    def prewarm(self) -> None:
        """Start the browser immediately so the first solve is fast."""
        try:
            self._launcher._ensure_browser()
            logger.info("browser_prewarmed")
        except Exception as e:
            logger.warning("prewarm_failed: %s", str(e)[:80])

    # ------------------------------------------------------------------ #
    # hCaptcha solver
    # ------------------------------------------------------------------ #
    def hsolve(self, url, sitekey, proxy=None, theme="light", size="normal") -> dict:
        """Solve an hCaptcha challenge.

        Uses the same route-interception technique as ``solve()`` but
        loads the hCaptcha widget instead of Turnstile.  Returns the
        ``h-captcha-response`` token.

        Parameters
        ----------
        url: Target URL registered with the hCaptcha sitekey.
        sitekey: hCaptcha sitekey (UUID format, e.g. ``10000000-ffff-...``).
        proxy: Optional proxy (``"auto"`` for SOCKS5 forwarder).
        theme: ``light`` or ``dark``.
        size: ``normal``, ``compact``, or ``invisible``.
        """
        if not sitekey:
            raise ValueError("sitekey is required")
        if not url:
            raise ValueError("url is required")

        # Check cache.
        cached = self._cache.get(sitekey, url, "hcaptcha")
        if cached:
            logger.info("hcaptcha_cache_hit sitekey=%s", sitekey[:12])
            return {
                "token": cached.token,
                "elapsed_ms": 0,
                "useragent": cached.useragent,
                "status": "success",
                "attempts": 0,
                "captcha_type": "hcaptcha",
                "cached": True,
            }

        # If Gemini API key is set, use the AI-powered solver (handles visual challenges).
        if os.getenv("GEMINI_API_KEY"):
            logger.info("hsolve_ai sitekey=%s url=%s", sitekey[:12], url[:30])
            result = solve_hcaptcha_with_ai(url, sitekey, proxy=proxy, timeout=60)
            if result.get("status") == "success" and result.get("token"):
                self._cache.put(sitekey, url, "hcaptcha",
                    result["token"], result.get("useragent", ""), result["elapsed_ms"])
            return result

        # Fallback: simple checkbox solver (no visual challenge handling).
        logger.info("hsolve_simple sitekey=%s (no GEMINI_API_KEY)", sitekey[:12])

        started = time.monotonic()
        last_error = None
        last_ua = None

        for attempt in range(1, self.max_retries + 1):
            logger.info("hsolve_attempt %d/%d url=%s", attempt, self.max_retries, url[:30])
            try:
                token, ua = self._hsolve_once(url, sitekey, proxy, theme, size)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.info("hsolve_success attempt=%d elapsed_ms=%d", attempt, elapsed_ms)
                result = {
                    "token": token,
                    "elapsed_ms": elapsed_ms,
                    "useragent": ua,
                    "status": "success",
                    "attempts": attempt,
                    "captcha_type": "hcaptcha",
                    "proxy_used": proxy,
                }
                self._cache.put(sitekey, url, "hcaptcha", token, ua, elapsed_ms)
                return result
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_ua = getattr(exc, "useragent", None) or last_ua
                logger.warning("hsolve_attempt_failed %d error=%s", attempt, last_error[:80])
                if attempt < self.max_retries:
                    time.sleep(min(2.0 * attempt, 5.0))

        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "token": None,
            "elapsed_ms": elapsed_ms,
            "useragent": last_ua,
            "status": "failed",
            "attempts": self.max_retries,
            "captcha_type": "hcaptcha",
            "error": last_error,
            "proxy_used": proxy,
        }

    def _hsolve_once(self, url, sitekey, proxy, theme, size) -> tuple:
        """Single hCaptcha solve attempt."""
        html = render_hcaptcha_html(sitekey, theme=theme, size=size)
        t0 = time.monotonic()
        page = self._launcher.new_page(proxy=proxy)
        try:
            url_with_slash = url if url.endswith("/") else url + "/"
            page.route(url_with_slash, lambda route: route.fulfill(
                body=html, status=200, content_type="text/html; charset=utf-8"))
            page.goto(url_with_slash, timeout=self.timeout * 1000)
            t_goto = time.monotonic()
            token = wait_for_hcaptcha_token(page, timeout=self.timeout)
            t_token = time.monotonic()
            ua = extract_hcaptcha_useragent(page)
            logger.info("hsolve_timing page=%dms goto=%dms token=%dms total=%dms",
                int((t0 - t0) * 1000), int((t_goto - t0) * 1000),
                int((t_token - t_goto) * 1000), int((t_token - t0) * 1000))
            if not token:
                raise RuntimeError("hcaptcha_token_empty_after_wait")
            return token, ua
        finally:
            try:
                page.unroute(url_with_slash)
            except:
                pass
            try:
                ctx = getattr(page, "_stealth_context", None)
                if ctx:
                    ctx.close()
                else:
                    page.close()
            except:
                pass

    def challenge(self, url, proxy=None, timeout=30):
        """Bypass a Cloudflare managed challenge page.

        Navigates to the real URL, waits for the "Just a moment..."
        interstitial to auto-resolve, returns cookies + final URL + title.
        """
        started = time.monotonic()
        with self._lock:
            page = self._launcher.new_page(proxy=proxy)
            try:
                try:
                    page.goto(url, timeout=10000, wait_until="commit")
                except Exception:
                    pass
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    time.sleep(1)
                    cookies = page.context.cookies()
                    has_clearance = any(c.get("name")=="cf_clearance" and len(c.get("value",""))>20 for c in cookies)
                    try:
                        title = page.title()
                        is_challenge = any(x in title.lower() for x in ("just a moment","checking your browser","attention required"))
                    except:
                        title = ""
                        is_challenge = True
                    if has_clearance or (title and not is_challenge):
                        elapsed_ms = int((time.monotonic()-started)*1000)
                        all_cookies = [{"name":c["name"],"value":c["value"],"domain":c.get("domain",""),"path":c.get("path","/")} for c in cookies]
                        cf_clearance = next((c["value"] for c in cookies if c.get("name")=="cf_clearance"), "")
                        try: final_url = page.url
                        except: final_url = url
                        logger.info("challenge_success elapsed_ms=%d title=%s", elapsed_ms, title[:30])
                        return {"status":"success","url":final_url,"title":title,"cookies":all_cookies,"cf_clearance":cf_clearance,"elapsed_ms":elapsed_ms}
                elapsed_ms = int((time.monotonic()-started)*1000)
                return {"status":"failed","error":"challenge_timeout","url":url,"elapsed_ms":elapsed_ms}
            finally:
                try: page.close()
                except: pass

    def _solve_once(self, url, sitekey, proxy, widget_type, action, cdata,
                    theme, size, language, appearance, execution) -> tuple:
        config = build_config(sitekey=sitekey, widget_type=widget_type, action=action,
            cdata=cdata, theme=theme, size=size, language=language,
            appearance=appearance, execution=execution)
        html = render_html(config)
        t0 = time.monotonic()
        page = self._launcher.new_page(proxy=proxy)
        t_page = time.monotonic()
        try:
            url_with_slash = url if url.endswith("/") else url + "/"
            page.route(url_with_slash, lambda route: route.fulfill(
                body=html, status=200, content_type="text/html; charset=utf-8"))
            page.goto(url_with_slash, timeout=self.timeout * 1000)
            t_goto = time.monotonic()
            token = wait_for_token(page, timeout=self.timeout, widget_type=widget_type)
            t_token = time.monotonic()
            ua = extract_useragent(page)
            logger.info("solve_timing page=%dms goto=%dms token=%dms total=%dms",
                int((t_page-t0)*1000), int((t_goto-t_page)*1000),
                int((t_token-t_goto)*1000), int((t_token-t0)*1000))
            if not token:
                raise RuntimeError("token_empty_after_wait")
            return token, ua
        finally:
            try: page.unroute(url_with_slash)
            except: pass
            try:
                ctx = getattr(page, "_stealth_context", None)
                if ctx: ctx.close()
                else: page.close()
            except: pass


_default_solver = None

def get_solver():
    global _default_solver
    if _default_solver is None:
        _default_solver = TurnstileSolver()
    return _default_solver
