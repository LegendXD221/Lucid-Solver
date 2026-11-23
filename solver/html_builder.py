"""HTML page generation for the Turnstile widget.

Builds the JSON config object passed to ``turnstile.render()`` and injects
it into the :data:`HTML_TEMPLATE` from :mod:`solver.constants`.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from solver.constants import HTML_TEMPLATE, WIDGET_TYPE_CONFIG


def build_config(
    sitekey: str,
    widget_type: str,
    action: Optional[str] = None,
    cdata: Optional[str] = None,
    theme: str = "auto",
    size: str = "normal",
    language: str = "auto",
    appearance: Optional[str] = None,
    execution: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the JSON config object passed to ``turnstile.render()``.

    Starts from the widget-type defaults (see
    :data:`solver.constants.WIDGET_TYPE_CONFIG`), then applies explicit
    per-call overrides.  Sensible ``retry`` / ``refresh-expired`` defaults
    are set so the widget retries on its own without needing Python-side
    intervention.
    """
    defaults = WIDGET_TYPE_CONFIG.get(
        widget_type, WIDGET_TYPE_CONFIG["managed"]
    )
    cfg: Dict[str, Any] = {
        "sitekey": sitekey,
        "theme": theme or "auto",
        "size": size or "normal",
        "appearance": appearance or defaults["appearance"],
        "execution": execution or defaults["execution"],
    }
    if action:
        cfg["action"] = action
    if cdata:
        cfg["cdata"] = cdata
    if language and language != "auto":
        cfg["language"] = language
    # Sensible retry defaults so the widget retries on its own.
    cfg.setdefault("retry", "auto")
    cfg.setdefault("retry-interval", 2000)
    cfg.setdefault("refresh-expired", "auto")
    return cfg


def render_html(config: Dict[str, Any]) -> str:
    """Inject the config JSON into the HTML template.

    Uses ``str.replace()`` on the ``__CONFIG_JSON__`` placeholder so the
    JavaScript braces in the template are not mistaken for
    ``str.format()`` field markers.
    """
    return HTML_TEMPLATE.replace("__CONFIG_JSON__", json.dumps(config))
