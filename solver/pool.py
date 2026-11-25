"""Async browser pool for concurrent solve handling.

Uses ``asyncio.Queue`` (not ``queue.Queue``) so that ``acquire()`` yields
to the event loop while waiting for an available browser.  This prevents
the deadlock where a queued request blocks the event loop and prevents
in-progress solves from completing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from solver.browser import BrowserLauncher

logger = logging.getLogger("solver.pool")


class BrowserPool:
    def __init__(
        self,
        size: int = 2,
        queue_timeout: int = 60,
        **launcher_kwargs,
    ) -> None:
        self._size = size
        self._queue_timeout = queue_timeout
        self._available: asyncio.Queue[int] = asyncio.Queue()
        self._launchers: list[BrowserLauncher] = []
        self._initialized = False

        for i in range(size):
            self._launchers.append(BrowserLauncher(**launcher_kwargs))
            self._available.put_nowait(i)

        logger.info("browser_pool_created size=%d queue_timeout=%ds", size, queue_timeout)

    async def acquire(self) -> tuple[int, BrowserLauncher]:
        """Acquire an available browser. Yields to event loop while waiting."""
        try:
            idx = await asyncio.wait_for(
                self._available.get(), timeout=self._queue_timeout
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"no browser available within {self._queue_timeout}s "
                f"(pool_size={self._size})"
            )
        launcher = self._launchers[idx]
        logger.debug("pool_acquire idx=%d", idx)
        return idx, launcher

    def release(self, idx: int) -> None:
        """Return a browser to the pool (non-blocking)."""
        self._available.put_nowait(idx)
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

    async def stop(self) -> None:
        for launcher in self._launchers:
            try:
                await launcher.stop()
            except Exception:
                pass
        logger.info("browser_pool_stopped")

    def __repr__(self) -> str:
        return f"<BrowserPool size={self._size} available={self.available_count} busy={self.busy_count}>"
