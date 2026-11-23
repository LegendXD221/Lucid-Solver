"""Proxy pool management for the Turnstile solver.

Loads a list of proxies from the ``PROXY_LIST`` environment variable (comma
separated) or, falling back, from a ``proxies.txt`` file sitting next to the
project root.  Rotates through them round-robin on every call to
:meth:`ProxyManager.rotate`.

Supported schemes: ``http``, ``https``, ``socks5`` and ``socks5h``.  Proxies
may carry credentials in the ``scheme://user:pass@host:port`` form.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger("solver.proxy")

_VALID_SCHEMES = ("http://", "https://", "socks5://", "socks5h://")


def _normalize(proxy: str) -> str:
    proxy = proxy.strip()
    if not proxy:
        return ""
    # Allow bare ``host:port`` entries to be treated as HTTP proxies.
    if not any(proxy.startswith(s) for s in _VALID_SCHEMES):
        proxy = f"http://{proxy}"
    return proxy


def _load_from_env() -> List[str]:
    raw = os.getenv("PROXY_LIST", "").strip()
    if not raw:
        return []
    proxies = [_normalize(p) for p in raw.split(",") if p.strip()]
    return [p for p in proxies if p]


def _load_from_file(path: Path) -> List[str]:
    if not path.is_file():
        return []
    proxies: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        normalized = _normalize(line)
        if normalized:
            proxies.append(normalized)
    return proxies


class ProxyManager:
    """Round-robin proxy rotator backed by an in-memory pool.

    The pool is loaded lazily on first access and can be refreshed at any time
    via :meth:`reload`.  The class is thread-safe so it can be shared across
    FastAPI worker threads.
    """

    def __init__(self, proxies: Optional[Iterable[str]] = None,
                 proxies_file: Optional[str | Path] = None) -> None:
        self._lock = threading.Lock()
        self._index = 0
        self._proxies: List[str] = []
        self._proxies_file = Path(proxies_file) if proxies_file else Path("proxies.txt")
        if proxies is not None:
            self._proxies = [_normalize(p) for p in proxies if p and p.strip()]
        else:
            self.reload()
        logger.info("proxy_manager_initialized count=%d file=%s",
                    len(self._proxies), str(self._proxies_file))

    # ------------------------------------------------------------------ #
    # Loading / mutation
    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        """Re-read the pool from env/file without losing the current index."""
        with self._lock:
            new_pool = _load_from_env()
            if not new_pool:
                new_pool = _load_from_file(self._proxies_file)
            self._proxies = new_pool
            if self._index >= len(self._proxies):
                self._index = 0
        logger.info("proxy_pool_reloaded count=%d", len(self._proxies))

    def add(self, proxy: str) -> None:
        normalized = _normalize(proxy)
        if not normalized:
            return
        with self._lock:
            if normalized not in self._proxies:
                self._proxies.append(normalized)

    def remove(self, proxy: str) -> None:
        normalized = _normalize(proxy)
        with self._lock:
            if normalized in self._proxies:
                self._proxies.remove(normalized)
                if self._index >= len(self._proxies):
                    self._index = 0

    # ------------------------------------------------------------------ #
    # Access
    # ------------------------------------------------------------------ #
    def rotate(self) -> Optional[str]:
        """Return the next proxy in round-robin order, or ``None`` if empty."""
        with self._lock:
            if not self._proxies:
                return None
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index = (self._index + 1) % len(self._proxies)
            return proxy

    def random(self) -> Optional[str]:
        """Return a uniformly random proxy from the pool."""
        import random as _random
        with self._lock:
            if not self._proxies:
                return None
            return _random.choice(self._proxies)

    def all(self) -> List[str]:
        with self._lock:
            return list(self._proxies)

    def __len__(self) -> int:
        with self._lock:
            return len(self._proxies)

    def __contains__(self, proxy: str) -> bool:
        return _normalize(proxy) in self.all()

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<ProxyManager count={len(self)} idx={self._index}>"
