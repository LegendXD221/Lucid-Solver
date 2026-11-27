import time
from patchright.sync_api import sync_playwright

URL = "https://bicolink.com/DvfV"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, channel="chrome", args=[
        "--no-sandbox",
        f"--user-agent={UA}",
    ])
    page = b.new_page()
    print(f"navigating to {URL}...", flush=True)
    # Use domcontentloaded — much faster than "load" for challenge pages.
    page.goto(URL, timeout=45000, wait_until="domcontentloaded")
    print(f"DOM loaded. title: {page.title()[:60]}", flush=True)

    # Wait for cf_clearance cookie (challenge auto-resolves in background)
    for i in range(45):
        time.sleep(1)
        try:
            cookies = page.context.cookies()
            for c in cookies:
                if c.get("name") == "cf_clearance" and len(c.get("value", "")) > 20:
                    print(f"\nCF_CLEARANCE found after {i+1}s!", flush=True)
                    print(f"cookie: {c['value'][:60]}...", flush=True)
                    print(f"title: {page.title()[:60]}", flush=True)
                    print(f"url: {page.url[:100]}", flush=True)
                    body = page.evaluate("() => document.body?.innerText?.substring(0, 200) || ''")
                    print(f"body: {body[:150]}", flush=True)
                    b.close()
                    exit(0)
        except:
            pass
        if i % 5 == 0:
            try:
                t = page.title()[:40]
                print(f"[{i+1}s] title={t}", flush=True)
            except:
                print(f"[{i+1}s] waiting...", flush=True)

    print(f"\nNO cf_clearance after 45s", flush=True)
    try:
        print(f"final title: {page.title()}", flush=True)
        print(f"final url: {page.url[:100]}", flush=True)
    except:
        pass
    b.close()
