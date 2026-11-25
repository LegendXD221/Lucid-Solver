"""Token polling and widget interaction for the Turnstile solver.

Once the browser has loaded the HTML page (with the Turnstile widget
rendered), these functions:

1. **Detect** the Turnstile widget — by selector AND by box-size matching
   (260-340px wide, 35-90px tall is the canonical Turnstile iframe size).
2. **Click** the widget checkbox using the human-like ``Cursor`` (Bezier
   curves, overshoot, tremor, micro-correction) via CDP
   ``Input.dispatchMouseEvent``.
3. **Poll** for the token via both the ``callback`` variable and the
   hidden ``[name=cf-turnstile-response]`` input — at 150ms intervals
   for speed.
4. **Detect Cloudflare errors** via ``window.__tsError`` and abort
   immediately.

Also includes **managed challenge detection** — checks if the page is a
Cloudflare "Just a moment" interstitial and waits for auto-resolution.
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

# Fast polling for speed (was 400ms, now 150ms).
_POLL_INTERVAL_S = 0.15
_CLICK_COOLDOWN_S = 1.5
_MIN_TOKEN_LEN = 20

# Turnstile widget box size (from latcf — the canonical iframe dimensions).
_WIDGET_MIN_W = 260
_WIDGET_MAX_W = 340
_WIDGET_MIN_H = 35
_WIDGET_MAX_H = 90

# Selectors for finding the Turnstile widget, in priority order.
_WIDGET_SELECTORS = [
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="cdn-cgi/challenge-platform"]',
    ".cf-turnstile",
    "#cf-turnstile",
    '[data-sitekey]',
    '[role="checkbox"]',
]

# Selectors for the hidden token input.
_TOKEN_SELECTORS = [
    "[name=cf-turnstile-response]",
    "[name=turnstile-response]",
    "[name=turnstile-token]",
    "[data-cf-turnstile-response]",
    "[data-turnstile-response]",
    "[data-turnstile-token]",
]


def is_turnstile_box(box: Box) -> bool:
    """Check if a bounding box matches the Turnstile widget dimensions."""
    return (_WIDGET_MIN_W <= box.width <= _WIDGET_MAX_W and
            _WIDGET_MIN_H <= box.height <= _WIDGET_MAX_H)


def click_point(box: Box) -> Point:
    """Compute the click point — the checkbox is at x≈30 from the left."""
    x_base = 30 if box.width > 80 else box.width / 2
    x_noise = (random.random() - 0.5) * min(8, box.width * 0.06)
    y_noise = (random.random() - 0.5) * min(6, box.height * 0.08)
    return Point(box.x + x_base + x_noise, box.y + box.height / 2 + y_noise)


def wait_for_token(
    page: Page,
    timeout: int,
    widget_type: str,
) -> Optional[str]:
    """Poll for a Turnstile token with human-like widget clicking.

    Parameters
    ----------
    page:
        The patchright Page with the Turnstile widget loaded.
    timeout:
        Maximum seconds to wait.
    widget_type:
        ``managed``, ``non-interactive``, or ``invisible``.  For visible
        types the widget is clicked with a human-like cursor; for invisible
        types clicking is skipped (``turnstile.execute()`` ran in JS).
    """
    deadline = time.monotonic() + timeout
    cursor: Optional[Cursor] = None
    last_click = 0.0
    clicked = False

    while time.monotonic() < deadline:
        # Method 1: callback-populated JS variable (fastest signal).
        token = _eval_token(page)
        if token:
            return token

        # Method 2: hidden input fallback.
        token = _read_hidden_input(page)
        if token:
            return token

        # Check for hard errors from Cloudflare.
        _check_error(page)

        # For visible widgets, click the checkbox with human-like cursor.
        if widget_type != "invisible" and (time.monotonic() - last_click) > _CLICK_COOLDOWN_S:
            if cursor is None:
                try:
                    cursor = Cursor(page)
                except Exception:
                    cursor = None
            if cursor:
                if _click_widget(page, cursor):
                    clicked = True
                    last_click = time.monotonic()
            else:
                # Fallback: direct page.click if cursor init failed.
                _fallback_click(page)
                last_click = time.monotonic()

        time.sleep(_POLL_INTERVAL_S)

    logger.warning("token_timeout widget_type=%s clicked=%s", widget_type, clicked)
    return None


def extract_useragent(page: Page) -> str:
    """Read ``navigator.userAgent`` from the page."""
    try:
        ua = page.evaluate("() => navigator.userAgent")
        if ua and isinstance(ua, str):
            return ua
    except Exception:
        pass
    return DEFAULT_UA


def is_managed_challenge(page: Page) -> bool:
    """Detect if the page is a Cloudflare managed challenge interstitial."""
    try:
        return page.evaluate("""() => {
            const t = (document.title || '') + '\\n' +
                      (document.body?.innerText?.slice(0, 5000) || '') + '\\n' +
                      location.href;
            const hasChallenge = /just a moment|security verification|checking your browser|verify you are human|attention required/i.test(t);
            const hasCf = /cloudflare|verify you are not a bot|malicious bots|ray id|__cf_chl_rt_tk|cf-challenge|challenge-platform/i.test(t);
            if (hasChallenge && hasCf) return true;
            if (hasChallenge && document.querySelector('script[src*="challenges.cloudflare.com"]')) return true;
            if (hasChallenge && document.querySelector('#challenge-running, #challenge-stage, .challenge-running')) return true;
            return false;
        }""")
    except Exception:
        return False


def get_cf_clearance(page: Page) -> Optional[str]:
    """Extract the ``cf_clearance`` cookie if present."""
    try:
        cookies = page.context.cookies()
        for c in cookies:
            if c.get("name") == "cf_clearance" and len(c.get("value", "")) > 20:
                return c["value"]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _eval_token(page: Page) -> Optional[str]:
    """Read ``window.__tsToken`` (populated by the success callback)."""
    try:
        token = page.evaluate("() => window.__tsToken")
        if token and isinstance(token, str) and len(token) > _MIN_TOKEN_LEN:
            return token
    except Exception:
        pass
    return None


def _read_hidden_input(page: Page) -> Optional[str]:
    """Read any of the hidden token input fields."""
    for sel in _TOKEN_SELECTORS:
        try:
            val = page.input_value(sel, timeout=300)
            if val and len(val) > _MIN_TOKEN_LEN:
                return val
        except Exception:
            pass
    return None


def _check_error(page: Page) -> None:
    """Raise RuntimeError if ``window.__tsError`` is populated."""
    try:
        err = page.evaluate("() => window.__tsError")
        if err:
            raise RuntimeError(f"turnstile_error: {err}")
    except RuntimeError:
        raise
    except Exception:
        pass


def _click_widget(page: Page, cursor: Cursor) -> bool:
    """Find and click the Turnstile widget checkbox with a human-like cursor."""
    for sel in _WIDGET_SELECTORS:
        try:
            loc = page.locator(sel)
            count = loc.count()
            for i in range(min(count, 5)):
                try:
                    bd = loc.nth(i).bounding_box(timeout=500)
                    if not bd or bd["width"] <= 0 or bd["height"] <= 0:
                        continue
                    box = Box(bd["x"], bd["y"], bd["width"], bd["height"])
                    # Accept Turnstile-sized boxes OR any visible element
                    # with a cf-turnstile class (it might be a different size).
                    if is_turnstile_box(box) or "turnstile" in sel:
                        pt = click_point(box)
                        # 60% chance: move nearby first (human-like approach)
                        if random.random() < 0.6:
                            wx = pt.x + (random.random() - 0.5) * 120
                            wy = pt.y + (random.random() - 0.5) * 80
                            try:
                                cursor.move_to(Point(wx, wy), speed=0.7 + random.random() * 0.6)
                                page.wait_for_timeout(int(80 + random.random() * 200))
                            except Exception:
                                pass
                        # Hesitate briefly, then click
                        page.wait_for_timeout(int(30 + random.random() * 120))
                        cursor.click(
                            pt,
                            speed=1.0,
                            hesitate=int(20 + random.random() * 60),
                            overshoot_threshold=420,
                        )
                        page.wait_for_timeout(int(100 + random.random() * 150))
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _fallback_click(page: Page) -> None:
    """Direct ``page.click()`` fallback if the cursor CDP session fails."""
    for sel in _WIDGET_SELECTORS:
        try:
            page.click(sel, timeout=1500)
            return
        except Exception:
            continue
