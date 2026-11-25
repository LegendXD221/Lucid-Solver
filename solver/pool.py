"""Browser pool for concurrent solve handling.

Manages N persistent BrowserLauncher instances, allowing multiple solves
to run in parallel.  When all browsers are busy, requests wait in a
thread-safe queue (with timeout) instead of failing.

Architecture
------------
::

    BrowserPool (size=N)
    ├── BrowserLauncher[0]  ─── page ─── solve ─── close page ─── available
    ├── BrowserLauncher[1]  ─── page ─── solve ─── close page ─── available
    └── ...
    
    Request → acquire(timeout) → [wait if all busy] → launcher → solve → release

Thread safety: ``queue.Queue`` is used for the available pool, making
``acquire()`` and ``release()`` safe across FastAPI threadpool threads.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

from solver.browser import BrowserLauncher

logger = logging.getLogger("solver.pool")


class BrowserPool:
    """Pool of persistent browser launchers for concurrent solves.

    Parameters
    ----------
    size:
        Number of browser instances in the pool (default 2).
    queue_timeout:
        Seconds to wait for an available browser before giving up (default 60).
    **launcher_kwargs:
        Passed to each ``BrowserLauncher`` instance.
    """

    def __init__(
        self,
        size: int = 2,
        queue_timeout: int = 60,
        **launcher_kwargs,
    ) -> None:
        self._size = size
        self._queue_timeout = queue_timeout
        self._available: queue.Queue[int] = queue.Queue(maxsize=size)
        self._launchers: list[BrowserLauncher] = []
        self._lock = threading.Lock()
        self._initialized = False

        for i in range(size):
            self._launchers.append(BrowserLauncher(**launcher_kwargs))
            self._available.put(i)

        logger.info(
            "browser_pool_created size=%d queue_timeout=%ds",
            size, queue_timeout,
        )

    def _ensure_initialized(self) -> None:
        """Lazily start browsers on first use (not at construction time).

        This avoids launching N browser processes when the server starts
        if no solves are requested yet.
        """
        with self._lock:
            if not self._initialized:
                # Browsers start lazily on first new_page() call via
                # BrowserLauncher._ensure_browser().  No explicit start needed.
                self._initialized = True
                logger.info("browser_pool_initialized (lazy)")

    def acquire(self) -> tuple[int, BrowserLauncher]:
        """Acquire an available browser launcher from the pool.

        Blocks until a launcher is available or the queue timeout expires.

        Returns
        -------
        (index, launcher)
            The index and the BrowserLauncher instance.

        Raises
        ------
        TimeoutError
            If no browser becomes available within ``queue_timeout`` seconds.
        """
        self._ensure_initialized()
        try:
            idx = self._available.get(timeout=self._queue_timeout)
        except queue.Empty:
            raise TimeoutError(
                f"no browser available within {self._queue_timeout}s "
                f"(pool_size={self._size})"
            )
        launcher = self._launchers[idx]
        logger.debug("pool_acquire idx=%d", idx)
        return idx, launcher

    def release(self, idx: int) -> None:
        """Return a browser launcher to the pool."""
        self._available.put(idx)
        logger.debug("pool_release idx=%d", idx)

    @property
    def size(self) -> int:
        return self._size

    @property
    def available_count(self) -> int:
        return self._available.qsize()

    @property
    def busy_count(self) -> int:
        return self._size - self._available.qsize()

    def stop(self) -> None:
        """Stop all browsers in the pool."""
        for i, launcher in enumerate(self._launchers):
            try:
                launcher.stop()
            except Exception:
                pass
        logger.info("browser_pool_stopped")

    def __repr__(self) -> str:
        return (
            f"<BrowserPool size={self._size} "
            f"available={self.available_count} busy={self.busy_count}>"
        )
