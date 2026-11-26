"""Async token polling and widget interaction.

All Playwright calls are async (``await page.evaluate()``, etc.) to be
compatible with FastAPI's asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

from patchright.async_api import Page

from solver.constants import DEFAULT_UA

logger = logging.getLogger("solver.token")

_POLL_INTERVAL_S = 0.15
_MIN_TOKEN_LEN = 20


async def wait_for_token(page: Page, timeout: int, widget_type: str) -> Optional[str]:
    """Poll for a Turnstile token with retry clicks."""
    deadline = asyncio.get_event_loop().time() + timeout
    last_click = 0.0
    click_cooldown = 3.0  # retry click every 3s if no token

    # Give the widget time to render before first click.
    await asyncio.sleep(1.0)

    while asyncio.get_event_loop().time() < deadline:
        token = await _eval_token(page)
        if token:
            return token
        token = await _read_hidden_input(page)
        if token:
            return token
        await _check_error(page)

        # Click the widget periodically (not just once).
        if widget_type != "invisible" and (asyncio.get_event_loop().time() - last_click) > click_cooldown:
            await _try_click_widget(page)
            last_click = asyncio.get_event_loop().time()

        await asyncio.sleep(_POLL_INTERVAL_S)

    return None


async def extract_useragent(page: Page) -> str:
    try:
        ua = await page.evaluate("() => navigator.userAgent")
        if ua and isinstance(ua, str):
            return ua
    except Exception:
        pass
    return DEFAULT_UA


async def is_managed_challenge(page: Page) -> bool:
    try:
        return await page.evaluate("""() => {
            const t = (document.title||'') + '\\n' + (document.body?.innerText?.slice(0,2000)||'') + '\\n' + location.href;
            return /just a moment|checking your browser|verify you are human/i.test(t)
                && /cloudflare|cf-challenge|challenge-platform/i.test(t);
        }""")
    except Exception:
        return False


async def get_cf_clearance(page: Page) -> Optional[str]:
    try:
        for c in await page.context.cookies():
            if c.get("name") == "cf_clearance" and len(c.get("value", "")) > 20:
                return c["value"]
    except Exception:
        pass
    return None


async def _eval_token(page: Page) -> Optional[str]:
    try:
        token = await page.evaluate("() => window.__tsToken")
        if token and isinstance(token, str) and len(token) > _MIN_TOKEN_LEN:
            return token
    except Exception:
        pass
    return None


async def _read_hidden_input(page: Page) -> Optional[str]:
    try:
        val = await page.input_value("[name=cf-turnstile-response]", timeout=200)
        if val and len(val) > _MIN_TOKEN_LEN:
            return val
    except Exception:
        pass
    return None


async def _check_error(page: Page) -> None:
    try:
        err = await page.evaluate("() => window.__tsError")
        if err:
            raise RuntimeError(f"turnstile_error: {err}")
    except RuntimeError:
        raise
    except Exception:
        pass


async def _try_click_widget(page: Page) -> None:
    """Find the Turnstile iframe and click its checkbox once."""
    for sel in (
        'iframe[src*="challenges.cloudflare.com"]',
        'iframe[src*="cdn-cgi/challenge-platform"]',
        ".cf-turnstile",
        "#cf-turnstile",
    ):
        try:
            loc = page.locator(sel).first
            bd = await loc.bounding_box(timeout=2000)
            if not bd or bd["width"] <= 0 or bd["height"] <= 0:
                continue
            x = bd["x"] + 30 + (random.random() - 0.5) * 6
            y = bd["y"] + bd["height"] / 2 + (random.random() - 0.5) * 4
            await page.mouse.click(x, y)
            return
        except Exception:
            continue
