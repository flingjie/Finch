from datetime import datetime

from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence
from finch.review.models import Feedback, ReviewAction, ReviewDecision
from finch.review.weekly import WeeklyReport, render_weekly, weekly_analysis
from finch.storage.database import Store
from finch.storage.repositories import DraftRepository, FeedbackRepository, ReviewRepository


def _draft(id: str) -> Draft:
    return Draft(id=id, kind=DraftKind.REPLY, candidate_id="t1", body="hi",
                 claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                  confidence=ClaimConfidence.VERIFIED)])


def _decision(draft_id: str, action: ReviewAction, reason: str | None = None) -> ReviewDecision:
    return ReviewDecision(id=f"rev_{draft_id}", draft_id=draft_id, action=action,
                          reason=reason, decided_at=datetime(2026, 1, 1))


def test_weekly_analysis_counts(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    drafts = DraftRepository(store)
    reviews = ReviewRepository(store)
    feedbacks = FeedbackRepository(store)

    drafts.upsert_draft(_draft("d1"))
    drafts.upsert_draft(_draft("d2"))
    drafts.upsert_draft(_draft("d3"))
    reviews.save_review(_decision("d1", ReviewAction.APPROVE))
    reviews.save_review(_decision("d2", ReviewAction.SKIP, "not_now"))
    reviews.save_review(_decision("d3", ReviewAction.SKIP, "evidence_insufficient"))
    feedbacks.save_feedback(Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                                     recorded_at=datetime(2026, 1, 2)))

    report = weekly_analysis(drafts, reviews, feedbacks)
    assert report.total_drafts == 3
    assert report.approved == 1
    assert report.skipped == 2
    assert report.approval_rate == 1 / 3
    assert report.skip_reasons == {"not_now": 1, "evidence_insufficient": 1}
    assert report.published_draft_ids == ["d1"]
    assert report.published_candidate_ids == ["t1"]


def test_weekly_analysis_empty(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    report = weekly_analysis(DraftRepository(store), ReviewRepository(store),
                             FeedbackRepository(store))
    assert report.total_drafts == 0
    assert report.approval_rate == 0.0
    assert report.skip_reasons == {}


def test_render_weekly(tmp_path):
    r = WeeklyReport(total_drafts=2, approved=1, skipped=1, approval_rate=0.5,
                     skip_reasons={"not_now": 1}, published_draft_ids=["d1"],
                     published_candidate_ids=["t1"])
    out = render_weekly(r)
    assert "批准率: 50%" in out
    assert "not_now: 1" in out
    assert "产生对话的候选: t1" in out
