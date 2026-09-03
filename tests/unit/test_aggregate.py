"""Tests for the deterministic checker aggregator (C7)."""

from finch.content.checkers.aggregate import AggregateOutcome, aggregate_checks
from finch.content.checkers.base import CheckResult


def _pass(name: str = "x") -> CheckResult:
    return CheckResult(checker=name, passed=True, severity="low")


def _fail(
    name: str = "x",
    severity: str = "medium",
    requires_human_input: bool = False,
) -> CheckResult:
    return CheckResult(
        checker=name,
        passed=False,
        severity=severity,  # type: ignore[arg-type]
        locations=["sentence[0]"],
        issues=["issue"],
        rewrite_instructions=["instruction"],
        requires_human_input=requires_human_input,
    )


def test_all_pass():
    assert aggregate_checks([_pass("a"), _pass("b")]) == "pass"


def test_any_failed_becomes_rewrite():
    assert aggregate_checks([_pass("a"), _fail("b")]) == "rewrite"


def test_hard_fail_becomes_reject():
    assert aggregate_checks([_pass("a"), _fail("b", severity="hard_fail")]) == "reject"


def test_hard_fail_not_masked_by_passes():
    # hard_fail cannot be overridden by an average score or other passes
    assert aggregate_checks([_pass("a"), _fail("b", severity="hard_fail")]) == "reject"


def test_high_requires_human_input_becomes_needs_input():
    assert (
        aggregate_checks([_pass("a"), _fail("b", severity="high", requires_human_input=True)])
        == "needs_input"
    )


def test_reject_precedes_needs_input():
    # hard_fail outranks needs_input in the priority order
    assert (
        aggregate_checks(
            [
                _fail("a", severity="hard_fail"),
                _fail("b", severity="high", requires_human_input=True),
            ]
        )
        == "reject"
    )


def test_outcome_enum_values_stable():
    assert AggregateOutcome.PASS == "pass"
    assert AggregateOutcome.REWRITE == "rewrite"
    assert AggregateOutcome.REJECT == "reject"
    assert AggregateOutcome.NEEDS_INPUT == "needs_input"
