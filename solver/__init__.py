"""Lucid Turnstile Solver package.

A Cloudflare Turnstile token solver built on top of DrissionPage and exposed
through a FastAPI service.
"""

from solver.core import TurnstileSolver
from solver.proxy import ProxyManager

__all__ = ["TurnstileSolver", "ProxyManager"]
__version__ = "1.0.0"
