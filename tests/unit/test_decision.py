from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    IntendedEffect,
    PositionSource,
    SuccessCriterion,
    position_fingerprint,
)
from finch.content.models import Draft, DraftKind
from finch.review.decision import DecisionService, content_hash
from finch.review.models import DecisionAction
from finch.storage.database import Store
from finch.storage.repositories import (
    ContentJobRepository,
    DecisionRecordRepository,
    DraftRepository,
    PositionApprovalRepository,
    ReviewRepository,
)


def _job(job_id="j1"):
    return ContentJob(
        id=job_id, source_card_ids=["ev1"], reader_problem="r", audience="a",
        intended_effect=IntendedEffect(understand="u"),
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        success_criteria=[SuccessCriterion(id="c1", description="d", measurement="critic")],
        recommended_format=DraftKind.ORIGINAL, status=ContentJobStatus.NEEDS_INPUT,
    )


def _svc(store):
    return DecisionService(
        jobs=ContentJobRepository(store),
        drafts=DraftRepository(store),
        approvals=PositionApprovalRepository(store),
        reviews=ReviewRepository(store),
        decisions=DecisionRecordRepository(store),
    )


def test_accept_confirms_position_and_approves_draft(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    ContentJobRepository(store).upsert_job(_job("j1"))
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="final body", content_job_id="j1")
    )
    rec = _svc(store).accept("j1")

    assert rec.action == DecisionAction.ACCEPT
    assert rec.position_source == PositionSource.HUMAN_CONFIRMED
    assert rec.approved_content_hash == content_hash("final body")

    job = ContentJobRepository(store).get_job("j1")
    assert job.author_position.confirmed is True
    assert job.author_position.position_source == PositionSource.HUMAN_CONFIRMED
    # 向后兼容投影：PositionApproval 落库
    assert PositionApprovalRepository(store).find_active(
        position_fingerprint(job.author_position)
    ) is not None
    # 决策记录落库
    assert DecisionRecordRepository(store).get("j1") is not None


def test_skip_marks_do_not_write(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    ContentJobRepository(store).upsert_job(_job("j1"))
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="b", content_job_id="j1")
    )
    rec = _svc(store).skip("j1", "not_now")
    assert rec.action == DecisionAction.SKIP
    job = ContentJobRepository(store).get_job("j1")
    assert job.status == ContentJobStatus.DO_NOT_WRITE
    assert job.reject_reason == "not_now"


def test_content_hash_deterministic():
    assert content_hash("a") == content_hash("a")
    assert content_hash("a") != content_hash("b")
