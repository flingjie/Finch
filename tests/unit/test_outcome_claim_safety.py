"""Tests for deterministic outcome-claim safety (Value Discovery v2)."""

from finch.content.checkers.base import CheckContext
from finch.content.checkers.safety import SafetyChecker, scan_unsupported_outcome_claims
from finch.content.models import Draft, DraftKind


def _draft(body: str) -> Draft:
    return Draft(id="d1", kind=DraftKind.ORIGINAL, body=body)


def test_unsourced_savings_percent_hard_fails():
    assert scan_unsupported_outcome_claims("我们帮用户节省 40% 时间")
    result = SafetyChecker().check(CheckContext(draft=_draft("我们帮用户节省 40% 时间"), cards=[]))
    assert result.passed is False
    assert result.severity == "hard_fail"
    assert any("unsupported_metric" in i for i in result.issues)


def test_payment_validated_claim_hard_fails():
    result = SafetyChecker().check(
        CheckContext(draft=_draft("用户愿意付费已验证"), cards=[])
    )
    assert result.passed is False


def test_qualified_claim_passes_deterministic_scan():
    body = "据记录在这次试用中节省 40%（observation_note），仅限该版本。"
    assert scan_unsupported_outcome_claims(body) == []
    result = SafetyChecker().check(CheckContext(draft=_draft(body), cards=[]))
    assert result.passed is True
