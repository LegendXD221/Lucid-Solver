"""Gemini-powered hCaptcha solver using hcaptcha-challenger.

When the hCaptcha checkbox click triggers a visual image grid challenge,
this module uses Google Gemini's vision model (via hcaptcha-challenger's
AgentV) to classify the images and click the correct cells.

Flow:
1. Navigate to the target URL (route interception serves HTML with hCaptcha widget)
2. AgentV clicks the hCaptcha checkbox
3. If a visual challenge appears → Gemini classifies the grid images → clicks correct cells
4. Token extracted from the hCaptcha response

Requires:
  - hcaptcha-challenger (pip install hcaptcha-challenger)
  - google-genai (pip install google-genai)
  - GEMINI_API_KEY env var
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("solver.hcaptcha_ai")

# hCaptcha demo HTML with explicit render
_DEMO_HTML = (
    '<!DOCTYPE html><html><head>'
    '<script src="https://js.hcaptcha.com/1/api.js" async defer></script>'
    '</head><body>'
    '<div class="h-captcha" data-sitekey="__SITEKEY__"></div>'
    '</body></html>'
)


async def _solve_hcaptcha_async(
    url: str,
    sitekey: str,
    proxy: Optional[str] = None,
    timeout: int = 60,
) -> dict:
    """Async hCaptcha solve using AgentV + Gemini.

    Returns a dict with {token, status, elapsed_ms, useragent, captcha_type}.
    """
    started = time.monotonic()

    try:
        from playwright.async_api import async_playwright
        from hcaptcha_challenger import AgentV, AgentConfig, CaptchaResponse
    except ImportError as e:
        return {
            "token": None,
            "status": "failed",
            "error": f"missing_dependency: {e}. Install: pip install hcaptcha-challenger google-genai",
            "elapsed_ms": 0,
            "captcha_type": "hcaptcha",
        }

    # Check Gemini API key
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        return {
            "token": None,
            "status": "failed",
            "error": "GEMINI_API_KEY not set. Set it in .env or environment.",
            "elapsed_ms": 0,
            "captcha_type": "hcaptcha",
        }

    ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
    html = _DEMO_HTML.replace("__SITEKEY__", sitekey)

    async with async_playwright() as p:
        launch_args = ["--no-sandbox", f"--user-agent={ua}"]
        launch_opts = {"headless": True, "args": launch_args, "channel": "chrome"}

        if proxy:
            if proxy == "auto":
                proxy = "socks5://127.0.0.1:10800"
            from urllib.parse import urlparse
            pu = urlparse(proxy if "://" in proxy else f"http://{proxy}")
            launch_opts["proxy"] = {
                "server": f"{pu.scheme}://{pu.hostname}:{pu.port}",
            }
            if pu.username:
                launch_opts["proxy"]["username"] = pu.username
            if pu.password:
                launch_opts["proxy"]["password"] = pu.password

        browser = await p.chromium.launch(**launch_opts)
        try:
            page = await browser.new_page()

            # Route interception — serve HTML at the target URL.
            url_with_slash = url if url.endswith("/") else url + "/"
            await page.route(url_with_slash, lambda route: route.fulfill(
                body=html, status=200, content_type="text/html; charset=utf-8",
            ))
            await page.goto(url_with_slash, timeout=15000)
            logger.info("hcaptcha_ai page_loaded url=%s", url[:40])

            # Initialize AgentV — this sets up the Gemini-powered challenge solver.
            agent_config = AgentConfig(DISABLE_BEZIER_TRAJECTORY=True)
            agent = AgentV(page=page, agent_config=agent_config)

            # Click the hCaptcha checkbox.
            await agent.robotic_arm.click_checkbox()
            logger.info("hcaptcha_ai checkbox_clicked")

            # Wait for the visual challenge to appear (if any).
            try:
                await asyncio.wait_for(agent.wait_for_challenge(), timeout=15)
                logger.info("hcaptcha_ai challenge_detected — Gemini solving...")
            except asyncio.TimeoutError:
                # No visual challenge — the checkbox might have passed directly.
                logger.info("hcaptcha_ai no_challenge — checking for token")

            # Check if we got a token.
            if agent.cr_list:
                cr: CaptchaResponse = agent.cr_list[-1]
                token = cr.generated_pass_UUID
                if token and len(token) > 20:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    logger.info("hcaptcha_ai success elapsed_ms=%d", elapsed_ms)
                    return {
                        "token": token,
                        "status": "success",
                        "elapsed_ms": elapsed_ms,
                        "useragent": ua,
                        "attempts": 1,
                        "captcha_type": "hcaptcha",
                        "proxy_used": proxy,
                    }

            # If no token yet, wait a bit more — the challenge might still be solving.
            for i in range(30):
                await asyncio.sleep(1)
                if agent.cr_list:
                    cr = agent.cr_list[-1]
                    token = cr.generated_pass_UUID
                    if token and len(token) > 20:
                        elapsed_ms = int((time.monotonic() - started) * 1000)
                        logger.info("hcaptcha_ai success_after_wait elapsed_ms=%d", elapsed_ms)
                        return {
                            "token": token,
                            "status": "success",
                            "elapsed_ms": elapsed_ms,
                            "useragent": ua,
                            "attempts": 1,
                            "captcha_type": "hcaptcha",
                            "proxy_used": proxy,
                        }

            elapsed_ms = int((time.monotonic() - started) * 1000)
            return {
                "token": None,
                "status": "failed",
                "error": "hcaptcha_no_token_after_solving",
                "elapsed_ms": elapsed_ms,
                "captcha_type": "hcaptcha",
            }

        finally:
            try:
                await browser.close()
            except Exception:
                pass


def solve_hcaptcha_with_ai(
    url: str,
    sitekey: str,
    proxy: Optional[str] = None,
    timeout: int = 60,
) -> dict:
    """Sync wrapper for the async hCaptcha solver.

    Runs the async solver in a fresh event loop via ``asyncio.run()``.
    This is safe to call from a sync FastAPI endpoint because it creates
    its own event loop (doesn't conflict with FastAPI's asyncio loop).
    """
    try:
        return asyncio.run(_solve_hcaptcha_async(url, sitekey, proxy, timeout))
    except Exception as e:
        return {
            "token": None,
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "elapsed_ms": 0,
            "captcha_type": "hcaptcha",
        }
