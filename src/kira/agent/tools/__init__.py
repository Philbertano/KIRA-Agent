"""Tools, die der Agent verwenden kann.

Jedes Tool exportiert:
- TOOL_SPEC: Anthropic Tool-Spec (JSONSchema)
- run(input: dict) -> str: Ausführung
"""

from kira.agent.tools import frist, norm_list, norm_lookup, norm_search, urteil_fetch
from kira.agent.tools._registry import REGISTRY, Tool

__all__ = [
    "REGISTRY",
    "Tool",
    "frist",
    "norm_list",
    "norm_lookup",
    "norm_search",
    "urteil_fetch",
]
