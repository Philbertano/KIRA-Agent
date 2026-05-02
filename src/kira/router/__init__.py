"""Model-Router: wählt Tier (haiku/sonnet/opus) je nach Aufgabe."""

from kira.router.policy import RoutingDecision, TaskType
from kira.router.rule_based import route

__all__ = ["RoutingDecision", "TaskType", "route"]
