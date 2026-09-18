"""NovaMed AI Business Copilot public package.

The package deliberately keeps its local execution path dependency-free.  The
``openai`` package is imported lazily only when ``mode="openai"`` is selected.
"""

from .agent import BusinessCopilot
from .router import IntentRouter, RouteDecision, route_query

__all__ = ["BusinessCopilot", "IntentRouter", "RouteDecision", "route_query"]
__version__ = "1.0.0"
