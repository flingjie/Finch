from datetime import UTC, datetime

from sqlalchemy import inspect

from finch.content.checkers.base import CheckResult
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard, Source
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.learn.models import Feedback
from finch.storage.database import Store
from finch.storage.repositories import (
    CriticReportRepository,
    DecisionRecordRepository,
    DraftRepository,
    DraftVersionRepository,
    EvidenceRepository,
    FeedbackRepository,
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


def test_upsert_cards_batch_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = EvidenceRepository(store)
    cards = [
        EvidenceCard(
            id=f"ev_{i}", event_id="evt", claim=f"c{i}",
            sources=[], confidence=ClaimConfidence.VERIFIED, publishable=True, topics=[],
        )
        for i in range(3)
    ]
    repo.upsert_cards(cards)
    assert [c.id for c in repo.list_cards()] == ["ev_0", "ev_1", "ev_2"]

    # 覆盖：同 id 批量更新而非重复
    updated = [c.model_copy(update={"claim": f"c{i}v2"}) for i, c in enumerate(cards)]
    repo.upsert_cards(updated)
    assert len(repo.list_cards()) == 3
    assert [c.claim for c in repo.list_cards()] == ["c0v2", "c1v2", "c2v2"]


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


def test_feedback_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = FeedbackRepository(store)
    repo.save_feedback(Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                                recorded_at=datetime(2026, 1, 1)))
    assert repo.get_feedback("d1").published_url == "https://x.com/u/status/1"


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


def test_decision_record_repository_roundtrip(tmp_path):
    from finch.storage.database import Store

    store = Store(tmp_path / "finch.db")
    store.init()
    repo = DecisionRecordRepository(store)
    rec = DecisionRecord(
        id="dec_j1", job_id="j1", draft_id="d1",
        action=DecisionAction.ACCEPT,
        approved_content_hash="h",
        decided_at=datetime.now(UTC),
    )
    repo.save(rec)
    assert repo.get("j1") is not None
    assert repo.get("j1").action == DecisionAction.ACCEPT
    assert len(repo.list()) == 1


def test_draft_repository_list_by_job(tmp_path):
    from finch.storage.database import Store

    store = Store(tmp_path / "finch.db")
    store.init()
    repo = DraftRepository(store)
    repo.upsert_draft(Draft(id="d1", kind=DraftKind.ORIGINAL, body="a", content_job_id="j1"))
    repo.upsert_draft(Draft(id="d2", kind=DraftKind.ORIGINAL, body="b", content_job_id="j2"))
    assert [d.id for d in repo.list_by_job("j1")] == ["d1"]
    assert repo.list_by_job("nope") == []


def test_contentjob_find_by_generation_key(tmp_path):
    from finch.content.jobs import ContentJob, ContentJobStatus, IntendedEffect
    from finch.content.models import DraftKind
    from finch.storage.repositories import ContentJobRepository

    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = ContentJobRepository(store)
    job = ContentJob(
        id="job_1",
        source_card_ids=["card_1"],
        candidate_id=None,
        reader_problem="Problem",
        audience="Engineers",
        intended_effect=IntendedEffect(understand="Solution"),
        author_position=None,
        success_criteria=[],
        recommended_format=DraftKind.REPLY,
        status=ContentJobStatus.PROPOSED,
        origin="commit",
        generation_key="gk_123",
    )
    repo.upsert_job(job)

    found = repo.find_by_generation_key("gk_123")
    assert found is not None
    assert found.id == "job_1"
    assert found.origin == "commit"
    assert repo.find_by_generation_key("nope") is None
