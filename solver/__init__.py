"""Lucid Turnstile Solver package.

A Cloudflare Turnstile token solver built on patchright (patched Playwright)
and exposed through a FastAPI service.

Module layout
-------------
* :mod:`solver.constants`    — static config, HTML template, type tuples
* :mod:`solver.models`       — :class:`SolveResult` dataclass
* :mod:`solver.utils`        — small helpers (env_bool)
* :mod:`solver.browser`      — :class:`BrowserLauncher` (patchright launch)
* :mod:`solver.html_builder` — :func:`build_config` + :func:`render_html`
* :mod:`solver.token_poller` — :func:`wait_for_token` + :func:`extract_useragent`
* :mod:`solver.core`         — :class:`TurnstileSolver` orchestrator
* :mod:`solver.proxy`        — :class:`ProxyManager` (round-robin pool)
* :mod:`solver.api`          — FastAPI app + metrics + JSON logging
"""

from solver.core import TurnstileSolver, get_solver
from solver.models import SolveResult
from solver.proxy import ProxyManager

__all__ = [
    "TurnstileSolver",
    "SolveResult",
    "ProxyManager",
    "get_solver",
]
__version__ = "2.1.0"
