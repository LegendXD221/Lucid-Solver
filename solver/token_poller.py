"""Token polling and extraction from the rendered Turnstile page.

Once the browser has loaded the HTML page (with the Turnstile widget
rendered), these functions poll the page until either a token appears or
the timeout elapses.

Two extraction methods are used in parallel:

1. **JavaScript callback variable** — ``window.__tsToken`` is populated by
   the ``callback`` option passed to ``turnstile.render()``.  This is the
   most reliable signal.

2. **Hidden input fallback** — ``[name=cf-turnstile-response]`` is the
   hidden ``<input>`` that Turnstile creates when it auto-renders via the
   declarative ``<div class="cf-turnstile">`` markup.  We check it as a
   fallback in case the callback wiring failed.

Hard errors from Cloudflare (``window.__tsError``) abort immediately.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from patchright.sync_api import Page

from solver.constants import DEFAULT_UA

logger = logging.getLogger("solver.token")

# Polling cadence.
_POLL_INTERVAL_S = 0.4
# Click cooldown so we don't hammer the widget div.
_CLICK_COOLDOWN_S = 2.0
# Minimum token length to accept (Turnstile tokens are ~400+ chars).
_MIN_TOKEN_LEN = 20


def wait_for_token(
    page: Page,
    timeout: int,
    widget_type: str,
) -> Optional[str]:
    """Poll the page for a Turnstile token.

    Parameters
    ----------
    page:
        The patchright ``Page`` with the Turnstile widget loaded.
    timeout:
        Maximum seconds to wait.
    widget_type:
        One of ``managed``, ``non-interactive``, ``invisible``.  For
        visible types the widget div is periodically clicked to nudge the
        challenge forward; for invisible types clicking is skipped
        (``turnstile.execute()`` already ran in JS).

    Returns
    -------
    str or None
        The token, or ``None`` if the timeout elapsed without one.

    Raises
    ------
    RuntimeError
        If ``window.__tsError`` is populated (Cloudflare-side error).
    """
    deadline = time.monotonic() + timeout
    last_click = 0.0

    while time.monotonic() < deadline:
        # Method 1: callback-populated JS variable (most reliable).
        token = _eval_token(page)
        if token:
            return token

        # Method 2: hidden input that Turnstile populates (fallback).
        token = _read_hidden_input(page)
        if token:
            return token

        # Check for hard errors from Cloudflare.
        _check_error(page)  # raises RuntimeError on error

        # For visible widgets a click can nudge the challenge forward.
        # Skip for invisible widgets — there is nothing to click and
        # ``execute()`` already ran in JS.
        if widget_type != "invisible" and \
                (time.monotonic() - last_click) > _CLICK_COOLDOWN_S:
            _click_widget(page)
            last_click = time.monotonic()

        time.sleep(_POLL_INTERVAL_S)

    return None


def extract_useragent(page: Page) -> str:
    """Read ``navigator.userAgent`` from the page, with a safe fallback."""
    try:
        ua = page.evaluate("() => navigator.userAgent")
        if ua and isinstance(ua, str):
            return ua
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_UA


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _eval_token(page: Page) -> Optional[str]:
    """Read ``window.__tsToken`` (populated by the success callback)."""
    try:
        token = page.evaluate("() => window.__tsToken")
        if token and isinstance(token, str) and len(token) > _MIN_TOKEN_LEN:
            return token
    except Exception:  # noqa: BLE001
        pass
    return None


def _read_hidden_input(page: Page) -> Optional[str]:
    """Read the ``[name=cf-turnstile-response]`` hidden input value."""
    try:
        val = page.input_value("[name=cf-turnstile-response]", timeout=500)
        if val and len(val) > _MIN_TOKEN_LEN:
            return val
    except Exception:  # noqa: BLE001
        pass
    return None


def _check_error(page: Page) -> None:
    """Raise ``RuntimeError`` if ``window.__tsError`` is populated."""
    try:
        err = page.evaluate("() => window.__tsError")
        if err:
            raise RuntimeError(f"turnstile_error: {err}")
    except RuntimeError:
        raise
    except Exception:  # noqa: BLE001
        pass


def _click_widget(page: Page) -> None:
    """Click the Turnstile widget div to nudge the challenge."""
    try:
        page.click("div.cf-turnstile, #cf-turnstile", timeout=1500)
    except Exception:  # noqa: BLE001
        pass
