import time
from patchright.sync_api import sync_playwright

PROXY = {"server": "http://p.webshare.io:80", "username": "slwrayne-rotate", "password": "0aotsm8crk2h"}

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, channel="chrome", args=["--no-sandbox"])
    ctx = b.new_context(proxy=PROXY)

    # Test 1: Check IP through proxy
    page = ctx.new_page()
    print("checking IP through proxy...", flush=True)
    try:
        page.goto("https://api.ipify.org?format=json", timeout=10000)
        ip = page.evaluate("() => document.body.innerText")
        print(f"proxy IP: {ip}", flush=True)
    except Exception as e:
        print(f"IP check failed: {str(e)[:60]}", flush=True)
    page.close()

    # Test 2: Load Turnstile dummy sitekey through proxy
    page2 = ctx.new_page()
    URL = "https://example.com/"
    SK = "1x00000000000000000000AA"
    HTML = (
        '<!DOCTYPE html><html><head>'
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async></script>'
        '</head><body>'
        '<div class="cf-turnstile" data-sitekey="' + SK + '"></div>'
        '</body></html>'
    )
    page2.route(URL, lambda r: r.fulfill(body=HTML, status=200, content_type="text/html"))
    print("\nloading dummy turnstile through proxy...", flush=True)
    try:
        page2.goto(URL, timeout=15000)
        print("page loaded", flush=True)
        time.sleep(3)
        # Check frames
        frames = [f.url[:80] for f in page2.frames]
        print(f"frames: {frames}", flush=True)
        # Check widget
        try:
            loc = page2.locator('iframe[src*="challenges.cloudflare"]').first
            bd = loc.bounding_box(timeout=3000)
            if bd:
                print(f"widget box: {bd['width']:.0f}x{bd['height']:.0f}", flush=True)
            else:
                print("no widget box", flush=True)
        except Exception as e:
            print(f"widget error: {str(e)[:60]}", flush=True)
        # Check token
        for i in range(10):
            time.sleep(1)
            try:
                v = page2.input_value("[name=cf-turnstile-response]", timeout=500)
                if v and len(v) > 5:
                    print(f"TOKEN: {v[:40]}", flush=True)
                    break
            except:
                pass
        else:
            print("no token after 13s", flush=True)
    except Exception as e:
        print(f"page load failed: {str(e)[:60]}", flush=True)
    page2.close()
    b.close()
