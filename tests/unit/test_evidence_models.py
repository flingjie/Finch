# tests/unit/test_evidence_models.py
import pytest
from pydantic import ValidationError

from finch.evidence.models import (
    ClaimConfidence,
    EngineeringEvent,
    EvidenceCard,
    Claim,
    Source,
)


def test_confidence_assertable_rules():
    assert ClaimConfidence.VERIFIED.assertable
    assert ClaimConfidence.INFERRED.assertable is False
    assert ClaimConfidence.UNKNOWN.assertable is False


def test_engineering_event_shape_matches_spec_7_1():
    e = EngineeringEvent(
        id="evt_1", repository="flingjie/FDE-Gym", commits=["abc123"],
        problem=Claim(statement="false positive", confidence=ClaimConfidence.VERIFIED),
        decision=Claim(statement="add checks", confidence=ClaimConfidence.INFERRED),
        result=Claim(statement="4 tests pass", confidence=ClaimConfidence.VERIFIED),
        missing_context=["real run or adversarial?"],
    )
    assert e.decision.confidence is ClaimConfidence.INFERRED


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
