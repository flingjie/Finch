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
from finch.review.models import DecisionAction, ReviewAction
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
    # 向后兼容投影：ReviewDecision(APPROVE)
    assert ReviewRepository(store).get_review("d1") is not None
    assert ReviewRepository(store).get_review("d1").action == ReviewAction.APPROVE


def test_skip_marks_do_not_write(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    ContentJobRepository(store).upsert_job(_job("j1"))
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="b", content_job_id="j1")
    )
    PositionApprovalRepository(store).approve(
        position_fingerprint(_job("j1").author_position), "j1"
    )
    rec = _svc(store).skip("j1", "not_now")
    assert rec.action == DecisionAction.SKIP
    job = ContentJobRepository(store).get_job("j1")
    assert job.status == ContentJobStatus.DO_NOT_WRITE
    assert job.reject_reason == "not_now"
    # 向后兼容投影：ReviewDecision(SKIP)
    assert ReviewRepository(store).get_review("d1") is not None
    assert ReviewRepository(store).get_review("d1").action == ReviewAction.SKIP
    # 跳过后撤销此前批准
    assert PositionApprovalRepository(store).find_active(
        position_fingerprint(job.author_position)
    ) is None


def test_content_hash_deterministic():
    assert content_hash("a") == content_hash("a")
    assert content_hash("a") != content_hash("b")


def test_revise_rewrites_and_persists(monkeypatch, tmp_path):
    from finch.content.critic import CritiqueResult

    store = Store(tmp_path / "finch.db")
    store.init()
    ContentJobRepository(store).upsert_job(_job("j1"))
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="before", content_job_id="j1", run_id="r1")
    )
    svc = _svc(store)

    class _Runner:
        def run(self, prompt, model):
            return Draft(id="d1", kind=DraftKind.ORIGINAL, body="after", claims=[])

    monkeypatch.setattr(
        "finch.review.decision.rewrite_with_instruction",
        lambda runner, draft, instruction, cards_by_id, job=None: Draft(
            id=draft.id, kind=draft.kind, body="after", claims=[], content_job_id="j1", run_id="r1"
        ),
    )
    monkeypatch.setattr(
        "finch.review.decision.critique",
        lambda runner, draft, cards_by_id: CritiqueResult(passed=True, checks=[]),
    )

    result = svc.revise("j1", "语气弱一点", runner=_Runner(), cards_by_id={}, gates=None)
    assert result["new_body"] == "after"
    assert "before" in result["diff"] and "after" in result["diff"]
    # 修订正文已落库，后续 accept 会绑定到新正文
    assert DraftRepository(store).get_draft("d1").body == "after"
    rec = DecisionRecordRepository(store).get("j1")
    assert rec is not None and rec.action == DecisionAction.REVISE
    # 修订历史写入 list_history（周复盘「修改次数/修正率」口径）
    assert [h.action for h in ReviewRepository(store).list_history()] == [ReviewAction.REVISE]
