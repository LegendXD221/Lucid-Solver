"""Stealth JavaScript injection and optimized browser launch args.

Patches 15+ browser fingerprints that Cloudflare's bot detection checks:

* ``navigator.webdriver`` → undefined
* Fake plugins array (Chrome PDF Plugin, Viewer, Native Client)
* Fake languages (en-US primary)
* Permissions.query override (notifications → Notification.permission)
* WebGL vendor/renderer spoofing (Intel)
* ``window.chrome.runtime = {}`` (present in real Chrome, absent in automation)
* Network connection info (4g, rtt 50, downlink 10)
* ``hardwareConcurrency = 8``, ``deviceMemory = 8``
* Canvas getContext override (hides headless WebGL debug info)
* Shadow DOM attachShadow → always open mode (allows widget inspection)
* ``offsetHeight`` → never 0 (anti-headless detection)
* ``Function.prototype.toString`` → returns native code for patched functions

Adapted from github.com/LATstudio-WeFun/lat-cf-challenger (MIT license).
"""

from __future__ import annotations

from typing import List

# ---------------------------------------------------------------------------
# Stealth JavaScript — injected via ``context.add_init_script()`` BEFORE any
# page loads, so Cloudflare's challenge script never sees automation
# fingerprints.  This is the single biggest speed win: without stealth JS,
# the Turnstile widget detects automation and silently retries/rejects,
# adding 5-10 seconds of wasted time.
# ---------------------------------------------------------------------------

STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const plugins = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
            ];
            plugins.length = 3;
            return plugins;
        },
    });

    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });

    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );

    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.call(this, parameter);
    };

    if (window.chrome) {
        const original = window.chrome;
        const newChrome = {};
        for (const key of Object.keys(original)) {
            newChrome[key] = original[key];
        }
        newChrome.runtime = {};
        Object.defineProperty(window, 'chrome', { get: () => newChrome });
    } else {
        window.chrome = { runtime: {} };
    }

    Object.defineProperty(navigator, 'connection', {
        get: () => ({
            effectiveType: '4g',
            rtt: 50,
            downlink: 10,
            saveData: false,
        }),
    });

    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

    const originalGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, attributes) {
        if (type === 'webgl' || type === 'webgl2') {
            const ctx = originalGetContext.call(this, type, attributes);
            if (ctx) {
                const getExt = ctx.getExtension;
                ctx.getExtension = function(name) {
                    if (name === 'WEBGL_debug_renderer_info') {
                        const ext = getExt.call(this, name);
                        if (ext) {
                            Object.defineProperty(ext, 'UNMASKED_VENDOR_WEBGL', { value: 0x9245 });
                            Object.defineProperty(ext, 'UNMASKED_RENDERER_WEBGL', { value: 0x9246 });
                        }
                        return ext;
                    }
                    return getExt.call(this, name);
                };
            }
            return ctx;
        }
        return originalGetContext.call(this, type, attributes);
    };

    // NOTE: attachShadow and offsetHeight patches removed — they can
    // break Turnstile widget rendering (shadow DOM encapsulation and
    // visibility checks).  The patches above are sufficient for
    // anti-detection.

    const originalToString = Function.prototype.toString;
    Function.prototype.toString = function() {
        if (this === window.navigator.permissions.query) {
            return 'function query() { [native code] }';
        }
        return originalToString.call(this);
    };
}
"""

# ---------------------------------------------------------------------------
# Optimized launch args.
#
# We use --no-sandbox (required for root) + --user-agent (to strip
# "HeadlessChrome") as before, PLUS latcf's anti-detection args that
# prevent Chrome from behaving differently when automated:
#   --disable-blink-features=AutomationControlled  (hides navigator.webdriver
#     at the Blink level — belt-and-suspenders with the JS patch)
#   --disable-ipc-flooding-protection  (prevents Chrome from throttling
#     CDP input events, which is critical for the human-like cursor)
#   --disable-background-timer-throttling  (prevents timer throttling in
#     background tabs, which can delay Turnstile's challenge JS)
#   --disable-renderer-backgrounding  (prevents the renderer from being
#     deprioritized, which slows down the challenge computation)
# ---------------------------------------------------------------------------

LAUNCH_ARGS: List[str] = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-ipc-flooding-protection",
]


def get_stealth_js() -> str:
    """Return the stealth JS script for ``context.add_init_script()``."""
    return STEALTH_JS


def get_launch_args(extra: List[str] | None = None) -> List[str]:
    """Return the optimized launch args, optionally extended."""
    args = list(LAUNCH_ARGS)
    if extra:
        args.extend(extra)
    return args
