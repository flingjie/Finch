from datetime import datetime

from finch.review.models import Feedback, ReviewAction, ReviewDecision, SkipReason


def test_enums():
    assert ReviewAction.APPROVE.value == "approve"
    assert SkipReason.EVIDENCE_INSUFFICIENT.value == "evidence_insufficient"


def test_review_decision_shape():
    d = ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.SKIP,
                       reason=SkipReason.NOT_NOW.value,
                       decided_at=datetime(2026, 1, 1))
    assert d.action == ReviewAction.SKIP and d.reason == "not_now"


def test_feedback_shape():
    f = Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                 interaction_metrics={"likes": 3}, recorded_at=datetime(2026, 1, 1))
    assert f.interaction_metrics["likes"] == 3
