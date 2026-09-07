# tests/unit/test_evidence_models.py
import pytest
from pydantic import ValidationError

from finch.evidence.models import (
    Claim,
    ClaimConfidence,
    EngineeringEvent,
    EvidenceCard,
    Source,
    sanitize_model_confidence,
)


def test_confidence_assertable_rules():
    assert ClaimConfidence.VERIFIED.assertable
    assert ClaimConfidence.SUPPORTED.assertable
    assert ClaimConfidence.USER_CONFIRMED.assertable
    assert ClaimConfidence.INFERRED.assertable is False
    assert ClaimConfidence.UNKNOWN.assertable is False


def test_sanitize_model_confidence_downgrades_user_confirmed():
    assert sanitize_model_confidence(ClaimConfidence.USER_CONFIRMED) is ClaimConfidence.SUPPORTED


def test_sanitize_model_confidence_passthrough():
    for conf in (
        ClaimConfidence.VERIFIED,
        ClaimConfidence.SUPPORTED,
        ClaimConfidence.INFERRED,
        ClaimConfidence.UNKNOWN,
    ):
        assert sanitize_model_confidence(conf) is conf


def test_engineering_event_shape_matches_spec_7_1():
    e = EngineeringEvent(
        id="evt_1", repository="flingjie/FDE-Gym", commits=["abc123"],
        problem=Claim(statement="false positive", confidence=ClaimConfidence.VERIFIED),
        decision=Claim(statement="add checks", confidence=ClaimConfidence.INFERRED),
        result=Claim(statement="4 tests pass", confidence=ClaimConfidence.VERIFIED),
        missing_context=["real run or adversarial?"],
    )
    assert e.decision.confidence is ClaimConfidence.INFERRED


def test_engineering_event_normalizes_topics():
    e = EngineeringEvent(
        id="evt_1", repository="flingjie/FDE-Gym", commits=["abc123"],
        problem=Claim(statement="false positive", confidence=ClaimConfidence.VERIFIED),
        decision=Claim(statement="add checks", confidence=ClaimConfidence.INFERRED),
        result=Claim(statement="4 tests pass", confidence=ClaimConfidence.VERIFIED),
        topics=[" Agent Harness ", "durable execution", "agent harness", "EVALS"],
    )
    assert e.topics == ["agent harness", "durable execution", "evals"]


def test_evidence_card_sources():
    c = EvidenceCard(
        id="ev_1", event_id="evt_1", claim="final answer != correct execution",
        sources=[Source(type="commit", url="https://github.com/flingjie/FDE-Gym/commit/abc")],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=["agent-evals"],
    )
    assert c.publishable is True


def test_invalid_confidence_rejected():
    with pytest.raises(ValidationError):
        Claim(statement="x", confidence="NOT_A_LEVEL")
