"""Token polling and widget interaction for the Turnstile solver.

Optimized for speed: the fast path polls ``window.__tsToken`` via a single
JS evaluate (sub-millisecond).  Widget detection and cursor clicking happen
once at the start, not on every poll cycle.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Optional

from patchright.sync_api import Page

from solver.constants import DEFAULT_UA
from solver.cursor import Box, Cursor, Point

logger = logging.getLogger("solver.token")

_POLL_INTERVAL_S = 0.15
_MIN_TOKEN_LEN = 20


def wait_for_token(
    page: Page,
    timeout: int,
    widget_type: str,
) -> Optional[str]:
    """Poll for a Turnstile token with a single upfront widget click."""
    deadline = time.monotonic() + timeout

    # Phase 1: find and click the widget ONCE (not every poll cycle).
    if widget_type != "invisible":
        _try_click_widget(page)

    # Phase 2: fast poll — just check the token, no bounding boxes.
    while time.monotonic() < deadline:
        # Fastest signal: JS callback variable.
        token = _eval_token(page)
        if token:
            return token
        # Fallback: hidden input.
        token = _read_hidden_input(page)
        if token:
            return token
        # Check for hard errors.
        _check_error(page)
        time.sleep(_POLL_INTERVAL_S)

    return None


def extract_useragent(page: Page) -> str:
    try:
        ua = page.evaluate("() => navigator.userAgent")
        if ua and isinstance(ua, str):
            return ua
    except Exception:
        pass
    return DEFAULT_UA


def is_managed_challenge(page: Page) -> bool:
    try:
        return page.evaluate("""() => {
            const t = (document.title||'') + '\\n' + (document.body?.innerText?.slice(0,2000)||'') + '\\n' + location.href;
            return /just a moment|checking your browser|verify you are human/i.test(t)
                && /cloudflare|cf-challenge|challenge-platform/i.test(t);
        }""")
    except Exception:
        return False


def get_cf_clearance(page: Page) -> Optional[str]:
    try:
        for c in page.context.cookies():
            if c.get("name") == "cf_clearance" and len(c.get("value", "")) > 20:
                return c["value"]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Internal — kept lightweight for speed
# ---------------------------------------------------------------------------

def _eval_token(page: Page) -> Optional[str]:
    try:
        token = page.evaluate("() => window.__tsToken")
        if token and isinstance(token, str) and len(token) > _MIN_TOKEN_LEN:
            return token
    except Exception:
        pass
    return None


def _read_hidden_input(page: Page) -> Optional[str]:
    try:
        val = page.input_value("[name=cf-turnstile-response]", timeout=200)
        if val and len(val) > _MIN_TOKEN_LEN:
            return val
    except Exception:
        pass
    return None


def _check_error(page: Page) -> None:
    try:
        err = page.evaluate("() => window.__tsError")
        if err:
            raise RuntimeError(f"turnstile_error: {err}")
    except RuntimeError:
        raise
    except Exception:
        pass


def _try_click_widget(page: Page) -> None:
    """Find the Turnstile iframe and click its checkbox once.

    Uses the human-like Cursor if CDP is available; falls back to
    ``page.click()`` otherwise.  Called ONCE at the start of polling,
    not on every cycle.
    """
    # Find the Turnstile iframe by its src URL (fast, single selector).
    for sel in (
        'iframe[src*="challenges.cloudflare.com"]',
        'iframe[src*="cdn-cgi/challenge-platform"]',
        ".cf-turnstile",
        "#cf-turnstile",
    ):
        try:
            loc = page.locator(sel).first
            bd = loc.bounding_box(timeout=2000)
            if not bd or bd["width"] <= 0 or bd["height"] <= 0:
                continue
            # Compute click point (checkbox is at x≈30 from left).
            x = bd["x"] + 30 + (random.random() - 0.5) * 8
            y = bd["y"] + bd["height"] / 2 + (random.random() - 0.5) * 6
            # Try cursor first, fall back to page.click.
            try:
                cursor = Cursor(page)
                # Brief hesitation, then click.
                cursor.click(Point(x, y), speed=1.5, hesitate=30, overshoot_threshold=420)
            except Exception:
                page.click(sel, timeout=2000, position={"x": 30, "y": bd["height"]/2})
            return
        except Exception:
            continue
