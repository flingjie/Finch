from datetime import datetime

from sqlalchemy import inspect

from finch.content.checkers.base import CheckResult
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard, Source
from finch.review.models import Feedback, ReviewAction, ReviewDecision, SkipReason
from finch.storage.database import Store
from finch.storage.repositories import (
    CriticReportRepository,
    DraftRepository,
    DraftVersionRepository,
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


def test_get_position_review_latest(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = ReviewRepository(store)
    repo.save_review(ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.APPROVE,
                                    decided_at=datetime(2026, 1, 1)))
    repo.save_review(ReviewDecision(id="confirm_d1", draft_id="d1",
                                    action=ReviewAction.CONFIRM_POSITION, voice_match=3,
                                    decided_at=datetime(2026, 1, 1)))
    assert repo.get_position_review("d1").voice_match == 3
    # 最新一次确认 merge 覆盖旧值；approve 决策不受影响
    repo.save_review(ReviewDecision(id="confirm_d1", draft_id="d1",
                                    action=ReviewAction.CONFIRM_POSITION, voice_match=5,
                                    decided_at=datetime(2026, 1, 2)))
    assert repo.get_position_review("d1").voice_match == 5
    assert repo.get_review("d1").action == ReviewAction.APPROVE
    # 无 confirm 时返回 None
    repo.save_review(ReviewDecision(id="rev_d2", draft_id="d2", action=ReviewAction.APPROVE,
                                    decided_at=datetime(2026, 1, 1)))
    assert repo.get_position_review("d2") is None


def test_feedback_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = FeedbackRepository(store)
    repo.save_feedback(Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                                recorded_at=datetime(2026, 1, 1)))
    assert repo.get_feedback("d1").published_url == "https://x.com/u/status/1"


def test_latest_revised_body_reads_history_not_final_decision(tmp_path):
    """F3: approve() 覆盖 rev_<id>（revised_body=None）后，修订文本仍需从历史读回。"""
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = ReviewRepository(store)
    repo.append_history(ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.REVISE,
                                       revised_body="v1", decided_at=datetime(2026, 1, 1)))
    repo.append_history(ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.REVISE,
                                       revised_body="v2", decided_at=datetime(2026, 1, 2)))
    # 最终决策被 approve 覆盖，revised_body=None
    repo.save_review(ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.APPROVE,
                                    decided_at=datetime(2026, 1, 3)))
    assert repo.latest_revised_body("d1") == "v2"
    assert repo.latest_revised_body("missing") is None


def test_contentjob_record_table_registered(tmp_path):
    """Test that ContentJobRecord is registered for table creation."""
    store = Store(tmp_path / "db.sqlite")
    store.init()

    # Check that contentjobrecord table exists via SQLAlchemy inspect
    inspector = inspect(store.engine)
    assert inspector.has_table("contentjobrecord")


def test_draft_version_roundtrip_ordering_and_idempotent_merge(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = DraftVersionRepository(store)
    repo.upsert_version("d1", 0, _draft().model_copy(update={"body": "v0"}))
    repo.upsert_version("d1", 1, _draft().model_copy(update={"body": "v1"}))
    assert [v.body for v in repo.list_versions("d1")] == ["v0", "v1"]
    # 幂等 merge：round 0 被覆盖而非重复
    repo.upsert_version("d1", 0, _draft().model_copy(update={"body": "v0-again"}))
    versions = repo.list_versions("d1")
    assert len(versions) == 2
    assert [v.body for v in versions] == ["v0-again", "v1"]


def test_critic_report_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = CriticReportRepository(store)
    repo.upsert_report(
        "d1",
        0,
        [
            CheckResult(
                checker="specificity",
                passed=False,
                severity="high",
                locations=["s[0]"],
                issues=["vague"],
                rewrite_instructions=["be specific"],
            )
        ],
        "rewrite",
    )
    repo.upsert_report(
        "d1", 1, [CheckResult(checker="specificity", passed=True, severity="low")], "pass"
    )
    reports = repo.list_reports("d1")
    assert [r["outcome"] for r in reports] == ["rewrite", "pass"]
    assert reports[0]["checks"][0]["checker"] == "specificity"
    assert reports[0]["checks"][0]["passed"] is False
