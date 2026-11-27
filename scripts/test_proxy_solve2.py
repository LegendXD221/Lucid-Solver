import time, random
from patchright.sync_api import sync_playwright

URL = "https://www.crunchbase.com/login/"
SK = "0x4AAAAAAAyJK2FfyvayqHnv"
HTML = (
    '<!DOCTYPE html><html><head>'
    '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async></script>'
    '</head><body>'
    '<div class="cf-turnstile" data-sitekey="' + SK + '"></div>'
    '</body></html>'
)
# Local SOCKS5 forwarder (no auth — handles upstream auth internally)
PROXY = {"server": "socks5://127.0.0.1:10800"}

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, channel="chrome", args=[
        "--no-sandbox",
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36",
    ])
    ctx = b.new_context(proxy=PROXY)
    page = ctx.new_page()
    page.route(URL, lambda r: r.fulfill(body=HTML, status=200, content_type="text/html"))
    print("navigating with local SOCKS5 proxy...", flush=True)
    page.goto(URL, timeout=20000)
    print("page loaded", flush=True)
    time.sleep(3)
    try:
        loc = page.locator('iframe[src*="challenges.cloudflare"]').first
        bd = loc.bounding_box(timeout=3000)
        if bd and bd["width"] > 10:
            print(f"widget size: {bd['width']:.0f}x{bd['height']:.0f} (FULL!)", flush=True)
            page.mouse.click(bd["x"]+30+(random.random()-0.5)*6, bd["y"]+bd["height"]/2)
            print("clicked", flush=True)
        elif bd:
            print(f"widget too small: {bd['width']:.0f}x{bd['height']:.0f}", flush=True)
        else:
            print("no widget found", flush=True)
    except Exception as e:
        print(f"widget error: {str(e)[:60]}", flush=True)
    for i in range(20):
        time.sleep(1)
        try:
            v = page.input_value("[name=cf-turnstile-response]", timeout=500)
            if v and len(v) > 20:
                print(f"TOKEN after {i+4}s: {v[:60]}...", flush=True)
                print(f"token length: {len(v)}", flush=True)
                break
        except:
            pass
    else:
        print("NO TOKEN after 23s", flush=True)
    b.close()
