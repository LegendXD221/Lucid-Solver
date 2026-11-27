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
    # "commit" returns as soon as the navigation request is sent — doesn't
    # wait for the page to load. The challenge runs in the background.
    try:
        page.goto(URL, timeout=10000, wait_until="commit")
        print("navigation started", flush=True)
    except Exception as e:
        print(f"goto (expected): {str(e)[:60]}", flush=True)

    # Poll for cf_clearance cookie — the challenge auto-resolves in background
    for i in range(40):
        time.sleep(1)
        try:
            cookies = page.context.cookies()
            for c in cookies:
                if c.get("name") == "cf_clearance" and len(c.get("value", "")) > 20:
                    print(f"\nCF_CLEARANCE found after {i+1}s!", flush=True)
                    print(f"cookie value: {c['value'][:60]}...", flush=True)
                    try:
                        print(f"title: {page.title()[:60]}", flush=True)
                        print(f"url: {page.url[:100]}", flush=True)
                        body = page.evaluate("() => document.body?.innerText?.substring(0, 200) || ''")
                        print(f"body: {body[:150]}", flush=True)
                    except:
                        pass
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

    print(f"\nNO cf_clearance after 40s", flush=True)
    b.close()
