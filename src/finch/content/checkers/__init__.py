"""Critic Suite 检查器：可解释的逐项检查（Task 4/5）。"""

from finch.content.checkers.aggregate import AggregateOutcome, aggregate_checks
from finch.content.checkers.base import CheckContext, Checker, CheckResult
from finch.content.checkers.decision import DecisionChecker
from finch.content.checkers.evidence import EvidenceChecker
from finch.content.checkers.portability import PortabilityChecker
from finch.content.checkers.specificity import SpecificityChecker

__all__ = [
    "CheckResult",
    "CheckContext",
    "Checker",
    "EvidenceChecker",
    "DecisionChecker",
    "SpecificityChecker",
    "PortabilityChecker",
    "AggregateOutcome",
    "aggregate_checks",
]
