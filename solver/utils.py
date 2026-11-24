"""Small utility helpers shared across solver modules."""

from __future__ import annotations


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable.

    Returns ``default`` when the variable is unset; otherwise interprets
    ``1``, ``true``, ``yes``, ``on`` (case-insensitive) as ``True`` and
    everything else as ``False``.
    """
    import os
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
