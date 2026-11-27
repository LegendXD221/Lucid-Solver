"""Gemini-powered hCaptcha solver using raw REST API calls.

Instead of the google-genai SDK (which has heavy deps that don't install
on Python 3.14), this module calls the Gemini REST API directly via httpx.
The only dependency is httpx (already installed for FastAPI).

Flow:
1. Load the hCaptcha widget (route interception)
2. Click the checkbox
3. If a visual grid challenge appears:
   a. Take a screenshot of the challenge area
   b. Send to Gemini API with a prompt: "Which grid cells contain [target]?"
   c. Parse the response for cell numbers (0-8)
   d. Click the matching cells
   e. Submit and wait for the token
4. Return the h-captcha-response token

Requires: GEMINI_API_KEY env var (free tier works)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import time
from typing import List, Optional

import httpx

from patchright.sync_api import sync_playwright

logger = logging.getLogger("solver.hcaptcha_ai")

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

_DEMO_HTML = (
    '<!DOCTYPE html><html><head>'
    '<script src="https://js.hcaptcha.com/1/api.js" async defer></script>'
    '</head><body>'
    '<div class="h-captcha" data-sitekey="__SITEKEY__"></div>'
    '</body></html>'
)


def _call_gemini(api_key: str, image_b64: str, prompt: str) -> str:
    """Call the Gemini REST API with an image and prompt. Returns text response."""
    url = f"{GEMINI_API_URL}?key={api_key}"
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/png", "data": image_b64}},
            ]
        }],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 100},
    }
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            # Extract text from response
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                for p in parts:
                    if "text" in p:
                        return p["text"]
        return ""
    except Exception as e:
        logger.warning("gemini_api_error: %s", str(e)[:100])
        return ""


def _parse_grid_cells(text: str) -> List[int]:
    """Parse Gemini's response for grid cell numbers (0-8)."""
    import re
    # Look for numbers 0-8 in the response.
    numbers = re.findall(r'\b([0-8])\b', text)
    # Deduplicate and return.
    seen = set()
    cells = []
    for n in numbers:
        n = int(n)
        if n not in seen:
            seen.add(n)
            cells.append(n)
    return cells


def solve_hcaptcha_with_ai(
    url: str,
    sitekey: str,
    proxy: Optional[str] = None,
    timeout: int = 60,
) -> dict:
    """Solve an hCaptcha challenge using Gemini for visual grid classification.

    Returns a dict with {token, status, elapsed_ms, useragent, captcha_type}.
    """
    started = time.monotonic()
    api_key = os.getenv("GEMINI_API_KEY", "")

    if not api_key:
        return {
            "token": None, "status": "failed",
            "error": "GEMINI_API_KEY not set",
            "elapsed_ms": 0, "captcha_type": "hcaptcha",
        }

    ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
    html = _DEMO_HTML.replace("__SITEKEY__", sitekey)
    url_with_slash = url if url.endswith("/") else url + "/"

    with sync_playwright() as p:
        launch_args = ["--no-sandbox", f"--user-agent={ua}"]
        opts = {"headless": True, "args": launch_args, "channel": "chrome"}
        if proxy:
            if proxy == "auto":
                proxy = "socks5://127.0.0.1:10800"
            from solver.browser import BrowserLauncher
            opts["proxy"] = BrowserLauncher.parse_proxy(proxy)

        browser = p.chromium.launch(**opts)
        try:
            page = browser.new_page()
            page.route(url_with_slash, lambda r: r.fulfill(
                body=html, status=200, content_type="text/html; charset=utf-8"))
            page.goto(url_with_slash, timeout=15000)
            logger.info("hcaptcha_ai page_loaded")

            # Wait for widget to render
            time.sleep(2)

            # Click the hCaptcha checkbox
            clicked = False
            for sel in ('iframe[src*="hcaptcha.com"]', 'iframe[title*="hCaptcha"]', '.h-captcha'):
                try:
                    loc = page.locator(sel).first
                    bd = loc.bounding_box(timeout=3000)
                    if bd and bd["width"] > 10:
                        page.mouse.click(bd["x"]+30, bd["y"]+bd["height"]/2)
                        clicked = True
                        logger.info("hcaptcha_ai checkbox_clicked")
                        break
                except:
                    continue

            if not clicked:
                return {"token": None, "status": "failed",
                    "error": "hcaptcha_widget_not_found",
                    "elapsed_ms": int((time.monotonic()-started)*1000),
                    "captcha_type": "hcaptcha"}

            # Wait for either token (checkbox passed) or challenge grid
            time.sleep(3)

            # Check if token already appeared (no visual challenge)
            for sel in ("[name='h-captcha-response']", "[name='g-recaptcha-response']"):
                try:
                    val = page.input_value(sel, timeout=500)
                    if val and len(val) > 20:
                        elapsed_ms = int((time.monotonic()-started)*1000)
                        logger.info("hcaptcha_ai token_without_challenge %dms", elapsed_ms)
                        return {"token": val, "status": "success",
                            "elapsed_ms": elapsed_ms, "useragent": ua,
                            "captcha_type": "hcaptcha", "attempts": 1}
                except:
                    pass

            # Visual challenge appeared — use Gemini to solve it
            logger.info("hcaptcha_ai visual_challenge_detected — using Gemini")

            for attempt in range(3):
                # Take screenshot of the challenge
                try:
                    screenshot = page.screenshot()
                    img_b64 = base64.b64encode(screenshot).decode()
                except:
                    break

                # Ask Gemini which cells to click
                prompt = (
                    "This is an hCaptcha challenge screenshot. "
                    "The challenge asks to select images matching a specific description. "
                    "Look at the grid of images (usually 3x3 or 4x4). "
                    "Return ONLY the numbers of the cells that match, "
                    "numbered left-to-right, top-to-bottom starting from 0. "
                    "Format: just numbers separated by commas, e.g. 0,2,5. "
                    "If you can't determine the answer, return 'none'."
                )

                gemini_response = _call_gemini(api_key, img_b64, prompt)
                logger.info("hcaptcha_ai gemini_response: %s", gemini_response[:80])

                cells = _parse_grid_cells(gemini_response)
                if not cells:
                    logger.warning("hcaptcha_ai no_cells_from_gemini attempt=%d", attempt+1)
                    # Try refreshing the challenge
                    try:
                        page.locator(".refresh.button").click(timeout=2000)
                        time.sleep(2)
                    except:
                        pass
                    continue

                # Click the cells in the challenge grid
                # The grid cells are inside the hCaptcha iframe
                try:
                    challenge_frame = page.frame_locator('iframe[src*="hcaptcha.com"]')
                    grid_cells = challenge_frame.locator(".task-image")
                    count = grid_cells.count()
                    logger.info("hcaptcha_ai grid_cells=%d matching=%s", count, cells)

                    for cell_idx in cells:
                        if cell_idx < count:
                            try:
                                box = grid_cells.nth(cell_idx).bounding_box(timeout=1000)
                                if box:
                                    page.mouse.click(
                                        box["x"] + box["width"]/2,
                                        box["y"] + box["height"]/2,
                                    )
                                    time.sleep(0.3)
                            except:
                                pass

                    # Click submit/verify button
                    time.sleep(1)
                    try:
                        challenge_frame.locator(".button-submit").click(timeout=2000)
                    except:
                        try:
                            challenge_frame.locator("button:has-text('Submit')").click(timeout=2000)
                        except:
                            pass

                    # Wait for token or next challenge
                    time.sleep(3)
                    for sel in ("[name='h-captcha-response']", "[name='g-recaptcha-response']"):
                        try:
                            val = page.input_value(sel, timeout=500)
                            if val and len(val) > 20:
                                elapsed_ms = int((time.monotonic()-started)*1000)
                                logger.info("hcaptcha_ai success_via_gemini %dms", elapsed_ms)
                                return {"token": val, "status": "success",
                                    "elapsed_ms": elapsed_ms, "useragent": ua,
                                    "captcha_type": "hcaptcha", "attempts": attempt+1}
                        except:
                            pass
                except Exception as e:
                    logger.warning("hcaptcha_ai grid_click_error: %s", str(e)[:60])

            elapsed_ms = int((time.monotonic()-started)*1000)
            return {"token": None, "status": "failed",
                "error": "hcaptcha_gemini_solve_failed",
                "elapsed_ms": elapsed_ms, "captcha_type": "hcaptcha"}

        finally:
            try:
                browser.close()
            except:
                pass
