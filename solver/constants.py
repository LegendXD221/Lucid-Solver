"""Static constants and templates for the Turnstile solver.

This module holds the immutable data the rest of the package depends on:
default user-agent, widget-type → appearance/execution mapping, the HTML
template that gets served at the target URL, and the validated type tuples.
"""

from __future__ import annotations

from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Default user-agent.
#
# MUST NOT contain "HeadlessChrome" — that substring is an instant
# bot-detection signal for Cloudflare.  We default to a realistic Chrome
# UA; the version number doesn't need to match the browser binary exactly.
# Override via the ``USER_AGENT`` env var for a specific Chrome version.
# ---------------------------------------------------------------------------

DEFAULT_UA: str = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Widget type → default appearance / execution mapping.
#
# The widget type is fundamentally a server-side property of the sitekey
# (configured in the Cloudflare dashboard).  The client controls two
# related options that determine how the widget behaves on the page:
#
#   * ``appearance`` — when the widget is *visible*:
#       - ``always``           visible as soon as it renders
#       - ``execute``          visible only while a challenge is executing
#       - ``interaction-only`` visible only when user interaction is required
#
#   * ``execution`` — when the challenge *runs*:
#       - ``render``   runs automatically on render
#       - ``execute``  runs only when ``turnstile.execute(widgetId)`` is called
#
# For invisible sitekeys the client MUST use execution=execute and call
# ``turnstile.execute()``; otherwise no challenge runs and no token is issued.
# ---------------------------------------------------------------------------

WIDGET_TYPE_CONFIG: Dict[str, Dict[str, str]] = {
    "managed":         {"appearance": "always",  "execution": "render"},
    "non-interactive": {"appearance": "always",  "execution": "render"},
    "invisible":       {"appearance": "execute", "execution": "execute"},
}

# Validated option tuples — used by both the solver and the API layer for
# pydantic field validation.
WIDGET_TYPES: Tuple[str, ...] = ("managed", "non-interactive", "invisible")
BROWSER_TYPES: Tuple[str, ...] = ("chromium", "chrome", "msedge")
THEMES: Tuple[str, ...] = ("auto", "light", "dark")
SIZES: Tuple[str, ...] = ("normal", "flexible", "compact")
APPEARANCES: Tuple[str, ...] = ("always", "execute", "interaction-only")
EXECUTIONS: Tuple[str, ...] = ("render", "execute")

# ---------------------------------------------------------------------------
# HTML template.
#
# Configuration is injected as a JSON blob (the ``__CONFIG_JSON__``
# placeholder) so we never have to worry about brace-escaping — the
# JavaScript object literals in the template use ``{ }`` freely and
# ``str.format()`` would choke on them.
#
# The template uses the explicit ``turnstile.render()`` JavaScript API
# (via the ``?onload=__tsLoad`` callback) rather than the declarative
# ``<div class="cf-turnstile">`` auto-render, so we can pass per-widget-type
# ``appearance`` and ``execution`` options.
# ---------------------------------------------------------------------------

HTML_TEMPLATE: str = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Just a moment...</title>
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=__tsLoad" async defer></script>
    <script>
        window.__tsToken = null;
        window.__tsError = null;
        window.__tsWidgetId = null;
        window.__tsReady = false;
        window.__tsExecuted = false;
        window.__tsConfig = __CONFIG_JSON__;

        function __tsLoad() {
            try {
                // Merge caller config over the callback wiring.  Caller
                // provides sitekey/appearance/execution/etc.; we always
                // supply the callback hooks so Python can observe state.
                var cfg = Object.assign({
                    callback: function(token) { window.__tsToken = token; },
                    'error-callback': function(code) {
                        window.__tsError = 'error:' + String(code || 'unknown');
                    },
                    'expired-callback': function() {
                        window.__tsError = 'expired';
                        window.__tsToken = null;
                    },
                    'timeout-callback': function() {
                        window.__tsError = 'timeout';
                    },
                    'unsupported-callback': function() {
                        window.__tsError = 'unsupported';
                    }
                }, window.__tsConfig);

                window.__tsWidgetId = turnstile.render('#cf-turnstile', cfg);
                window.__tsReady = true;

                // Invisible / execute widgets need an explicit execute()
                // call to start the challenge.  Delay slightly so the widget
                // is fully mounted first.
                var needsExecute = (cfg.execution === 'execute')
                    || (cfg.appearance === 'execute');
                if (needsExecute) {
                    setTimeout(function() {
                        try {
                            turnstile.execute(window.__tsWidgetId);
                            window.__tsExecuted = true;
                        } catch (e) {
                            window.__tsError = 'execute:' + e.message;
                        }
                    }, 300);
                }
            } catch (e) {
                window.__tsError = 'load:' + e.message;
            }
        }
    </script>
</head>
<body>
    <div id="cf-turnstile"></div>
</body>
</html>"""
