from datetime import datetime

from finch.review.models import Feedback, ReviewAction, ReviewDecision, SkipReason


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


def test_review_decision_shape():
    d = ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.SKIP,
                       reason=SkipReason.NOT_NOW.value,
                       decided_at=datetime(2026, 1, 1))
    assert d.action == ReviewAction.SKIP and d.reason == "not_now"


def test_feedback_shape():
    f = Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                 interaction_metrics={"likes": 3}, recorded_at=datetime(2026, 1, 1))
    assert f.interaction_metrics["likes"] == 3
