# tests/unit/test_evidence_models.py
import pytest
from pydantic import ValidationError

from finch.evidence.models import (
    Claim,
    ClaimConfidence,
    EngineeringEvent,
    EvidenceCard,
    JudgeScores,
    MatchResult,
    RankedCandidate,
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


def test_judge_scores_bounds():
    JudgeScores(relevance=0, evidence_strength=1, incremental_value=0.5, discussability=0.5)
    with pytest.raises(ValidationError):
        JudgeScores(relevance=1.1, evidence_strength=0, incremental_value=0, discussability=0)


def test_match_result_shape():
    m = MatchResult(
        candidate_id="t1",
        card_ids=["ev_1"],
        scores=JudgeScores(
            relevance=0.8, evidence_strength=0.9,
            incremental_value=0.7, discussability=0.6,
        ),
        timing=0.3, relationship_value=0.5, score=0.77,
    )
    assert m.card_ids == ["ev_1"]


def test_ranked_candidate_shape():
    r = RankedCandidate(candidate_id="t1", card_ids=["ev_1", "ev_2"], recall_score=0.4)
    assert r.recall_score == 0.4
