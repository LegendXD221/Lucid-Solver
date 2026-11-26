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

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, channel="chrome", args=[
        "--no-sandbox",
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36",
    ])
    page = b.new_page()
    page.route(URL, lambda r: r.fulfill(body=HTML, status=200, content_type="text/html"))
    page.goto(URL, timeout=20000)
    print("page loaded", flush=True)
    time.sleep(3)
    for sel in ['iframe[src*="challenges.cloudflare"]', '.cf-turnstile']:
        try:
            loc = page.locator(sel).first
            bd = loc.bounding_box(timeout=3000)
            if bd and bd["width"] > 0:
                x = bd["x"] + 30 + (random.random()-0.5)*6
                y = bd["y"] + bd["height"]/2 + (random.random()-0.5)*4
                print(f"clicking {sel} at {x:.0f},{y:.0f} box={bd['width']:.0f}x{bd['height']:.0f}", flush=True)
                page.mouse.click(x, y)
                break
            else:
                print(f"{sel}: no box", flush=True)
        except Exception as e:
            print(f"{sel}: {str(e)[:80]}", flush=True)
    for i in range(20):
        time.sleep(1)
        try:
            v = page.input_value("[name=cf-turnstile-response]", timeout=500)
            if v and len(v) > 20:
                print(f"TOKEN after {i+4}s: {v[:50]}...", flush=True)
                break
        except:
            pass
    else:
        print("NO TOKEN after 23s", flush=True)
    b.close()
