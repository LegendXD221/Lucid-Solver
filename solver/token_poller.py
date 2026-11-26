"""Sync token polling — fast 150ms poll, single upfront click."""
from __future__ import annotations
import logging, random, time
from typing import Optional
from patchright.sync_api import Page
from solver.constants import DEFAULT_UA

logger = logging.getLogger("solver.token")
_POLL_S = 0.15
_MIN_TOK = 20


def wait_for_token(page: Page, timeout: int, widget_type: str) -> Optional[str]:
    deadline = time.monotonic() + timeout
    last_click = 0.0
    # Give the widget time to render.
    time.sleep(1.0)
    while time.monotonic() < deadline:
        token = _eval_token(page)
        if token: return token
        token = _read_hidden(page)
        if token: return token
        _check_err(page)
        if widget_type != "invisible" and (time.monotonic() - last_click) > 3.0:
            _click_widget(page)
            last_click = time.monotonic()
        time.sleep(_POLL_S)
    return None


def extract_useragent(page: Page) -> str:
    try:
        ua = page.evaluate("() => navigator.userAgent")
        if ua and isinstance(ua, str): return ua
    except: pass
    return DEFAULT_UA


def _eval_token(page: Page) -> Optional[str]:
    try:
        t = page.evaluate("() => window.__tsToken")
        if t and isinstance(t, str) and len(t) > _MIN_TOK: return t
    except: pass
    return None


def _read_hidden(page: Page) -> Optional[str]:
    try:
        v = page.input_value("[name=cf-turnstile-response]", timeout=200)
        if v and len(v) > _MIN_TOK: return v
    except: pass
    return None


def _check_err(page: Page):
    try:
        e = page.evaluate("() => window.__tsError")
        if e: raise RuntimeError(f"turnstile_error: {e}")
    except RuntimeError: raise
    except: pass


def _click_widget(page: Page):
    for sel in ('iframe[src*="challenges.cloudflare.com"]', '.cf-turnstile', '#cf-turnstile'):
        try:
            loc = page.locator(sel).first
            bd = loc.bounding_box(timeout=2000)
            if not bd or bd["width"] <= 0: continue
            x = bd["x"] + 30 + (random.random()-0.5)*6
            y = bd["y"] + bd["height"]/2 + (random.random()-0.5)*4
            page.mouse.click(x, y)
            return
        except: continue
