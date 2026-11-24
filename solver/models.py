"""Data models for solver results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SolveResult:
    """Outcome of a single ``solve()`` call.

    Fields
    ------
    token:
        The ``cf-turnstile-response`` token, or ``None`` on failure.
    elapsed_ms:
        Wall-clock time from the first attempt to the return, in ms.
    useragent:
        The user-agent the browser reported (``navigator.userAgent``).
    status:
        ``"success"`` or ``"failed"``.
    attempts:
        Number of attempts made (1-based; equals ``max_retries`` on failure).
    error:
        Last error message, only present when ``status == "failed"``.
    proxy_used:
        Proxy URL that was used for the request, if any.
    widget_type:
        The widget type passed to ``solve()``.
    extra:
        Additional fields merged into :meth:`to_dict` output.
    """

    token: Optional[str]
    elapsed_ms: int
    useragent: Optional[str]
    status: str  # "success" | "failed"
    attempts: int = 0
    error: Optional[str] = None
    proxy_used: Optional[str] = None
    widget_type: str = "managed"
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise to a plain dict suitable for JSON responses."""
        payload: Dict[str, Any] = {
            "token": self.token,
            "elapsed_ms": self.elapsed_ms,
            "useragent": self.useragent,
            "status": self.status,
            "attempts": self.attempts,
            "widget_type": self.widget_type,
        }
        if self.error:
            payload["error"] = self.error
        if self.proxy_used:
            payload["proxy_used"] = self.proxy_used
        if self.extra:
            payload.update(self.extra)
        return payload
