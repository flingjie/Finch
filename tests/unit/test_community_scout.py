from datetime import UTC, datetime, timedelta

from finch.communities.models import CommunityCandidate, CommunityFeedback, CommunityResult
from finch.communities.scout import candidate_identity, derive_feedback_facts


def _fb(result: CommunityResult, *, days_ago: int = 0, reason: str = "", note: str = ""):
    return CommunityFeedback(
        community_id="comm_x",
        result=result,
        reason_kind=reason,
        note=note,
        at=datetime(2026, 9, 29, tzinfo=UTC) - timedelta(days=days_ago),
    )


def test_ignored_within_window_is_excluded():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts({"k": _fb(CommunityResult.IGNORED, days_ago=7)}, now=now)
    assert "k" in facts.excluded
    assert facts.excluded["k"] == "ignored 1w ago"


def test_ignored_beyond_window_not_excluded():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts(
        {"k": _fb(CommunityResult.IGNORED, days_ago=35)}, now=now
    )
    assert "k" not in facts.excluded


def test_no_time_never_excluded():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts(
        {"k": _fb(CommunityResult.SAVED, days_ago=1, reason="no_time")}, now=now
    )
    assert "k" not in facts.excluded


def test_engaged_results_enter_continue_framing():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts({"k": _fb(CommunityResult.INTERACTED, days_ago=2)}, now=now)
    assert facts.continue_framing == ["k"]


def test_summaries_carry_reason_and_note():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts(
        {"k": _fb(CommunityResult.SAVED, days_ago=3, reason="deep_but_later", note="先观察")},
        now=now,
    )
    assert "deep_but_later" in facts.summaries["k"]
    assert "先观察" in facts.summaries["k"]


def test_candidate_identity_prefers_canonical_url():
    assert (
        candidate_identity(CommunityCandidate(name="Temporal", canonical_url="https://t.io"))
        == "https://t.io"
    )


def test_candidate_identity_falls_back_to_name_hash():
    c = CommunityCandidate(name="Temporal")
    assert candidate_identity(c) == candidate_identity(CommunityCandidate(name="Temporal"))
