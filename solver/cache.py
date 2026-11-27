"""Token cache with TTL — repeated solves return instantly.

Cloudflare Turnstile tokens are valid for ~300 seconds (5 minutes).
If the same sitekey + url combination is requested within that window,
we return the cached token instead of launching a browser.

Cache key: ``sitekey:url:widget_type`` → ``{token, useragent, timestamp}``

This gives a 10x improvement for repeated solves: 0ms instead of 8.5s.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

# Turnstile tokens are valid for ~300 seconds. We cache for 280s to
# leave a safety margin.
DEFAULT_TTL = 280


@dataclass
class CacheEntry:
    token: str
    useragent: str
    timestamp: float
    elapsed_ms: int


class TokenCache:
    """Thread-safe token cache with TTL expiry."""

    def __init__(self, ttl: int = DEFAULT_TTL) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._cache: Dict[str, CacheEntry] = {}
        self._hits = 0
        self._misses = 0

    def _key(self, sitekey: str, url: str, widget_type: str) -> str:
        return f"{sitekey}:{url}:{widget_type}"

    def get(self, sitekey: str, url: str, widget_type: str) -> Optional[CacheEntry]:
        """Return a cached entry if it exists and hasn't expired."""
        key = self._key(sitekey, url, widget_type)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.time() - entry.timestamp > self._ttl:
                del self._cache[key]
                self._misses += 1
                return None
            self._hits += 1
            return entry

    def put(self, sitekey: str, url: str, widget_type: str,
            token: str, useragent: str, elapsed_ms: int) -> None:
        """Store a solved token in the cache."""
        key = self._key(sitekey, url, widget_type)
        with self._lock:
            self._cache[key] = CacheEntry(
                token=token,
                useragent=useragent,
                timestamp=time.time(),
                elapsed_ms=elapsed_ms,
            )

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total * 100, 1) if total else 0.0,
                "ttl_seconds": self._ttl,
            }
