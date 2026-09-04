"""InteractionRepository 单元测试（Phase 5，内存/临时 sqlite，无网络）。"""

from datetime import datetime

import pytest
from sqlmodel import Session, select

from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionCandidate,
    InteractionStatus,
)
from finch.storage.database import Store
from finch.storage.repositories import InteractionCandidateRecord, InteractionRepository


def _candidate(
    pid: str = "p1",
    action: InteractionAction = InteractionAction.DRAFT_REPLY,
    **overrides,
) -> InteractionCandidate:
    post = ExternalPost(
        id=pid,
        platform="x",
        url=f"https://x.com/alice/status/{pid}",
        author_id="alice",
        author_name="Alice",
        content="How do you test agent reliability in production?",
        published_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    score = ConversationScore(
        relevance=0.8,
        novelty=0.8,
        discussability=0.8,
        practical_evidence=0.8,
        relationship_value=0.8,
        total=0.8,
        reasons=["on topic"],
    )
    data = dict(
        id=f"x:{pid}:{action.value}",
        post=post,
        score=score,
        action=action,
        draft="Have you tried recording a failure replay?",
        approval_required=True,
    )
    data.update(overrides)
    return InteractionCandidate(**data)


def _repo(tmp_path) -> InteractionRepository:
    store = Store(tmp_path / "db.sqlite")
    store.init()
    return InteractionRepository(store)


def _row_count(store: Store) -> int:
    with Session(store.engine) as session:
        return len(list(session.exec(select(InteractionCandidateRecord))))


def test_upsert_get_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = InteractionRepository(store)
    candidate = _candidate()
    repo.upsert(candidate, run_id="run-1")

    got = repo.get(candidate.id)
    assert got is not None
    assert got.id == "x:p1:draft_reply"
    assert got.post.id == "p1"
    assert got.action is InteractionAction.DRAFT_REPLY
    assert got.status is InteractionStatus.PROPOSED
    assert got.draft == "Have you tried recording a failure replay?"


def test_upsert_idempotent_overwrites_same_id(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = InteractionRepository(store)
    candidate = _candidate()
    repo.upsert(candidate, run_id="run-1")
    repo.upsert(candidate.model_copy(update={"draft": "new draft"}), run_id="run-2")

    assert _row_count(store) == 1
    assert repo.get(candidate.id).draft == "new draft"


def test_list_pending_only_proposed(tmp_path):
    repo = _repo(tmp_path)
    pending = _candidate(pid="p1")
    approved = _candidate(pid="p2")
    repo.upsert(pending, run_id="run-1")
    repo.upsert(approved, run_id="run-1")
    repo.approve(approved.id)

    listed = repo.list_pending()
    assert [c.id for c in listed] == [pending.id]


def test_approve_twice_is_safe(tmp_path):
    repo = _repo(tmp_path)
    candidate = _candidate()
    repo.upsert(candidate, run_id="run-1")

    repo.approve(candidate.id)
    assert repo.get(candidate.id).status is InteractionStatus.APPROVED
    repo.approve(candidate.id)  # 幂等：重复批准仍是 APPROVED
    assert repo.get(candidate.id).status is InteractionStatus.APPROVED
    assert repo.get(candidate.id).reject_reason is None


def test_reject_records_reason(tmp_path):
    repo = _repo(tmp_path)
    candidate = _candidate()
    repo.upsert(candidate, run_id="run-1")

    repo.reject(candidate.id, "fact_error")
    got = repo.get(candidate.id)
    assert got.status is InteractionStatus.REJECTED
    assert got.reject_reason == "fact_error"
    # 拒绝后不再出现在 pending 队列。
    assert repo.list_pending() == []


def test_edit_saves_revision_without_approving(tmp_path):
    repo = _repo(tmp_path)
    candidate = _candidate()
    repo.upsert(candidate, run_id="run-1")

    repo.edit(candidate.id, "revised draft v2")
    got = repo.get(candidate.id)
    assert got.revised_draft == "revised draft v2"
    assert got.draft == "Have you tried recording a failure replay?"  # 原草稿保留
    assert got.status is InteractionStatus.PROPOSED  # 保存版本不改变发布权限


def test_record_execution_idempotent(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = InteractionRepository(store)
    candidate = _candidate()
    repo.upsert(candidate, run_id="run-1")
    repo.approve(candidate.id)

    repo.record_execution(candidate.id, "approved", "sent ok")
    assert repo.get(candidate.id).status is InteractionStatus.EXECUTED

    repo.record_execution(candidate.id, "approved", "sent ok")  # 幂等：不重复执行
    assert repo.get(candidate.id).status is InteractionStatus.EXECUTED
    assert _row_count(store) == 1


def test_missing_candidate_raises_key_error(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(KeyError):
        repo.approve("x:missing:draft_reply")
    with pytest.raises(KeyError):
        repo.reject("x:missing:draft_reply", "not_now")
    with pytest.raises(KeyError):
        repo.edit("x:missing:draft_reply", "revised")
    with pytest.raises(KeyError):
        repo.record_execution("x:missing:draft_reply", "approved", "sent")
