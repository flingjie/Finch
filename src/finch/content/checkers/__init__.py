"""Critic Suite 检查器：可解释的逐项检查（Task 4/5/6）。"""

from finch.content.checkers.aggregate import AggregateOutcome, aggregate_checks
from finch.content.checkers.base import CheckContext, Checker, CheckResult
from finch.content.checkers.decision import DecisionChecker
from finch.content.checkers.evidence import EvidenceChecker
from finch.content.checkers.method_card import MethodCardChecker
from finch.content.checkers.portability import PortabilityChecker
from finch.content.checkers.safety import SafetyChecker
from finch.content.checkers.specificity import SpecificityChecker
from finch.content.checkers.structure import StructureChecker
from finch.content.checkers.suites import checker_suite_for, content_type_for
from finch.content.checkers.voice import VoiceChecker

__all__ = [
    "CheckResult",
    "CheckContext",
    "Checker",
    "EvidenceChecker",
    "DecisionChecker",
    "SpecificityChecker",
    "PortabilityChecker",
    "VoiceChecker",
    "StructureChecker",
    "SafetyChecker",
    "MethodCardChecker",
    "checker_suite_for",
    "content_type_for",
    "AggregateOutcome",
    "aggregate_checks",
]
