from datetime import datetime

import pytest
from pydantic import ValidationError

from finch.review.models import (
    Feedback,
    OutcomeAssessment,
    ReviewAction,
    ReviewDecision,
    SkipReason,
)


def test_enums():
    assert ReviewAction.APPROVE.value == "approve"
    assert SkipReason.EVIDENCE_INSUFFICIENT.value == "evidence_insufficient"


def test_confirm_position_action_distinct_from_approve():
    assert ReviewAction.CONFIRM_POSITION.value == "confirm_position"
    assert ReviewAction.CONFIRM_POSITION != ReviewAction.APPROVE


def test_new_skip_reasons():
    assert SkipReason.NO_CLEAR_POSITION.value == "no_clear_position"
    assert SkipReason.GENERIC_VOICE.value == "generic_voice"
    assert SkipReason.JOB_NOT_USEFUL.value == "job_not_useful"
    assert SkipReason.FACT_ERROR.value == "fact_error"


def test_review_decision_new_fields_default():
    d = ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.APPROVE,
                       decided_at=datetime(2026, 1, 1))
    assert d.position_correct is None
    assert d.voice_match is None
    assert d.job_clear is None


def test_review_decision_confirm_position_carries_fields():
    d = ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.CONFIRM_POSITION,
                       position_correct=True, voice_match=4, job_clear=True,
                       decided_at=datetime(2026, 1, 1))
    assert d.action == ReviewAction.CONFIRM_POSITION
    assert d.position_correct is True
    assert d.voice_match == 4
    assert d.job_clear is True


def test_voice_match_bounded_0_to_5():
    with pytest.raises(ValidationError):
        ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.CONFIRM_POSITION,
                       voice_match=6, decided_at=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.CONFIRM_POSITION,
                       voice_match=-1, decided_at=datetime(2026, 1, 1))
    d = ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.CONFIRM_POSITION,
                       voice_match=5, decided_at=datetime(2026, 1, 1))
    assert d.voice_match == 5


def test_review_decision_shape():
    d = ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.SKIP,
                       reason=SkipReason.NOT_NOW.value,
                       decided_at=datetime(2026, 1, 1))
    assert d.action == ReviewAction.SKIP and d.reason == "not_now"


def test_feedback_shape():
    f = Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                 interaction_metrics={"likes": 3}, recorded_at=datetime(2026, 1, 1))
    assert f.interaction_metrics["likes"] == 3


def test_feedback_outcome_default_none():
    f = Feedback(draft_id="d1", recorded_at=datetime(2026, 1, 1))
    assert f.outcome is None


def test_outcome_assessment_roundtrip():
    o = OutcomeAssessment(job_completed="partly", reader_understood=True,
                          desired_action_count=2, useful_reply_count=1,
                          github_clicks=3, notes="ok")
    assert o.job_completed == "partly"
    assert o.desired_action_count == 2
    assert o.github_clicks == 3
    # JSON 序列化往返
    loaded = OutcomeAssessment.model_validate_json(o.model_dump_json())
    assert loaded == o


def test_outcome_assessment_defaults():
    o = OutcomeAssessment(job_completed="unknown")
    assert o.reader_understood is None
    assert o.desired_action_count is None
    assert o.useful_reply_count is None
    assert o.github_clicks is None
    assert o.notes is None


def test_outcome_assessment_job_completed_enum():
    with pytest.raises(ValidationError):
        OutcomeAssessment(job_completed="maybe")


def test_feedback_outcome_persistence_roundtrip():
    o = OutcomeAssessment(job_completed="yes", useful_reply_count=4)
    f = Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                 recorded_at=datetime(2026, 1, 1), outcome=o)
    # FeedbackRecord.payload_json 存整份 Feedback，outcome 随 JSON 持久化往返
    loaded = Feedback.model_validate_json(f.model_dump_json())
    assert loaded.outcome is not None
    assert loaded.outcome.job_completed == "yes"
    assert loaded.outcome.useful_reply_count == 4
