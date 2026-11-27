"""hCaptcha solver module — solves hCaptcha widgets via route interception.

hCaptcha is a privacy-focused CAPTCHA service similar to reCAPTCHA.
The solver uses the same route-interception technique as the Turnstile
solver: serves a custom HTML page at the target URL with the hCaptcha
widget embedded, clicks the checkbox, and extracts the token.

hCaptcha vs Turnstile differences:
- Script: js.hcaptcha.com/1/api.js (not challenges.cloudflare.com)
- Widget div: class="h-captcha" (not "cf-turnstile")
- Token input: name="h-captcha-response" (not "cf-turnstile-response")
- Iframe src: hcaptcha.com (not challenges.cloudflare.com)
- Sitekey format: UUID (not 0x4AAAA...)

Test sitekeys (from hCaptcha docs):
- 10000000-ffff-ffff-ffff-000000000001 — always passes
- 20000000-ffff-ffff-ffff-000000000001 — always fails
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from patchright.sync_api import Page

from solver.constants import DEFAULT_UA

logger = logging.getLogger("solver.hcaptcha")

_POLL_S = 0.2
_MIN_TOK = 20

# hCaptcha HTML template — uses explicit render with callback.
HCAPTCHA_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Verify...</title>
    <script src="https://js.hcaptcha.com/1/api.js?onload=__hcLoad&render=explicit" async defer></script>
    <script>
        window.__hcToken = null;
        window.__hcError = null;
        window.__hcWidgetId = null;

        function __hcLoad() {
            try {
                hcaptcha.render('#h-captcha', {
                    sitekey: "__SITEKEY__",
                    callback: function(token) { window.__hcToken = token; },
                    'error-callback': function(err) { window.__hcError = 'error:' + (err || 'unknown'); },
                    'expired-callback': function() { window.__hcError = 'expired'; window.__hcToken = null; },
                    'close-callback': function() { window.__hcError = 'closed'; },
                    'chalexpired-callback': function() { window.__hcError = 'chalexpired'; },
                    theme: "__THEME__",
                    size: "__SIZE__"
                });
                window.__hcWidgetId = true;
            } catch(e) {
                window.__hcError = 'load:' + e.message;
            }
        }
    </script>
</head>
<body>
    <div id="h-captcha"></div>
</body>
</html>"""


def render_hcaptcha_html(sitekey: str, theme: str = "light", size: str = "normal") -> str:
    """Build the HTML page with the hCaptcha widget."""
    return (
        HCAPTCHA_HTML
        .replace("__SITEKEY__", sitekey)
        .replace("__THEME__", theme)
        .replace("__SIZE__", size)
    )


def wait_for_hcaptcha_token(page: Page, timeout: int) -> Optional[str]:
    """Poll for an hCaptcha token.

    Clicks the hCaptcha checkbox, then polls for the token via both
    the callback variable and the hidden input.
    """
    deadline = time.monotonic() + timeout

    # Give the widget time to render.
    time.sleep(1.5)

    last_click = 0.0
    while time.monotonic() < deadline:
        # Method 1: callback variable.
        token = _eval_hc_token(page)
        if token:
            return token

        # Method 2: hidden input.
        token = _read_hc_hidden(page)
        if token:
            return token

        # Check for errors.
        _check_hc_error(page)

        # Click the hCaptcha checkbox every 4 seconds.
        if (time.monotonic() - last_click) > 4.0:
            _click_hcaptcha(page)
            last_click = time.monotonic()

        time.sleep(_POLL_S)

    return None


def extract_hcaptcha_useragent(page: Page) -> str:
    try:
        ua = page.evaluate("() => navigator.userAgent")
        if ua and isinstance(ua, str):
            return ua
    except Exception:
        pass
    return DEFAULT_UA


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _eval_hc_token(page: Page) -> Optional[str]:
    try:
        token = page.evaluate("() => window.__hcToken")
        if token and isinstance(token, str) and len(token) > _MIN_TOK:
            return token
    except Exception:
        pass
    return None


def _read_hc_hidden(page: Page) -> Optional[str]:
    for sel in ("[name='h-captcha-response']", "[name='g-recaptcha-response']"):
        try:
            val = page.input_value(sel, timeout=300)
            if val and len(val) > _MIN_TOK:
                return val
        except Exception:
            pass
    return None


def _check_hc_error(page: Page) -> None:
    try:
        err = page.evaluate("() => window.__hcError")
        if err:
            raise RuntimeError(f"hcaptcha_error: {err}")
    except RuntimeError:
        raise
    except Exception:
        pass


def _click_hcaptcha(page: Page) -> None:
    """Click the hCaptcha checkbox."""
    import random
    for sel in (
        'iframe[src*="hcaptcha.com"]',
        'iframe[src*="assets.hcaptcha.com"]',
        'iframe[title*="hCaptcha"]',
        '.h-captcha',
        '#h-captcha',
    ):
        try:
            loc = page.locator(sel).first
            bd = loc.bounding_box(timeout=2000)
            if not bd or bd["width"] <= 0 or bd["height"] <= 0:
                continue
            # hCaptcha checkbox is at ~30px from the left edge.
            x = bd["x"] + 30 + (random.random() - 0.5) * 6
            y = bd["y"] + bd["height"] / 2 + (random.random() - 0.5) * 4
            page.mouse.click(x, y)
            return
        except Exception:
            continue
