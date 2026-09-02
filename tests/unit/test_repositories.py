from datetime import datetime

from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard, Source
from finch.review.models import Feedback, ReviewAction, ReviewDecision, SkipReason
from finch.storage.database import Store
from finch.storage.repositories import (
    DraftRepository,
    EvidenceRepository,
    FeedbackRepository,
    ReviewRepository,
)


def test_upsert_card_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = EvidenceRepository(store)
    card = EvidenceCard(
        id="ev_1", event_id="evt", claim="x",
        sources=[Source(type="commit", url="https://github.com/a/b/commit/1")],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=["t"],
    )
    repo.upsert_card(card)
    got = repo.get_card("ev_1")
    assert got is not None
    assert got.claim == "x"
    card2 = card.model_copy(update={"claim": "y"})
    repo.upsert_card(card2)
    assert repo.get_card("ev_1").claim == "y"
    assert [c.id for c in repo.list_cards()] == ["ev_1"]


def _draft():
    return Draft(id="d1", kind=DraftKind.REPLY, candidate_id="t1", body="hi",
                 claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                  confidence=ClaimConfidence.VERIFIED)])


def test_draft_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = DraftRepository(store)
    repo.upsert_draft(_draft())
    assert repo.get_draft("d1") is not None
    assert repo.list_drafts()[0].id == "d1"


def test_review_save_idempotent(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = ReviewRepository(store)
    d1 = ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.APPROVE,
                        decided_at=datetime(2026, 1, 1))
    repo.save_review(d1)
    repo.save_review(d1.model_copy(update={"action": ReviewAction.SKIP, "reason": "not_now"}))
    got = repo.get_review("d1")
    assert got is not None and got.action == ReviewAction.SKIP  # 覆盖同一条，不重复
    assert got.reason == SkipReason.NOT_NOW.value


def test_feedback_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = FeedbackRepository(store)
    repo.save_feedback(Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                                recorded_at=datetime(2026, 1, 1)))
    assert repo.get_feedback("d1").published_url == "https://x.com/u/status/1"
