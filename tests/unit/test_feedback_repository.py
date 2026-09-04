"""FeedbackSnapshot + ConversationEvidence 仓储单元测试（Phase 6，内存 sqlite，无网络）。"""

from datetime import datetime

import pytest

from finch.engagement.models import ConversationEvidence, FeedbackSnapshot
from finch.storage.database import Store
from finch.storage.repositories import (
    ConversationEvidenceRepository,
    FeedbackSnapshotRepository,
)


def _snapshot(interaction_id: str = "x:p1:draft_reply", **overrides) -> FeedbackSnapshot:
    data = dict(
        id=f"fb_{interaction_id}",
        interaction_id=interaction_id,
        replies=2,
        likes=1,
        meaningful=True,
        captured_at=datetime(2026, 9, 4, 12, 0, 0),
    )
    data.update(overrides)
    return FeedbackSnapshot(**data)


def _evidence(
    interaction_id: str = "x:p1:draft_reply", idx: int = 0, **overrides
) -> ConversationEvidence:
    data = dict(
        id=f"ce_{interaction_id}_{idx}",
        interaction_id=interaction_id,
        post_id="p1",
        kind="hypothesis",
        statement="Replay makes failures testable.",
        verified=False,
    )
    data.update(overrides)
    return ConversationEvidence(**data)


def test_feedback_snapshot_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = FeedbackSnapshotRepository(store)
    repo.upsert(_snapshot())

    got = repo.get("x:p1:draft_reply")
    assert got is not None
    assert got.replies == 2
    assert got.likes == 1
    assert got.meaningful is True
    assert [s.id for s in repo.list_all()] == ["fb_x:p1:draft_reply"]


def test_feedback_snapshot_upsert_idempotent(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = FeedbackSnapshotRepository(store)
    repo.upsert(_snapshot())
    repo.upsert(_snapshot(replies=5, meaningful=False))

    assert len(repo.list_all()) == 1
    got = repo.get("x:p1:draft_reply")
    assert got.replies == 5
    assert got.meaningful is False


def test_conversation_evidence_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = ConversationEvidenceRepository(store)
    repo.upsert(_evidence())

    listed = repo.list_by_interaction("x:p1:draft_reply")
    assert [e.id for e in listed] == ["ce_x:p1:draft_reply_0"]
    assert listed[0].kind == "hypothesis"
    assert listed[0].origin == "conversation"
    assert listed[0].verified is False


def test_list_unverified_and_mark_verified(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = ConversationEvidenceRepository(store)
    repo.upsert(_evidence(idx=0))
    repo.upsert(_evidence(idx=1, kind="experiment"))

    assert len(repo.list_unverified()) == 2

    repo.mark_verified("ce_x:p1:draft_reply_0")
    unverified = repo.list_unverified()
    assert [e.id for e in unverified] == ["ce_x:p1:draft_reply_1"]


def test_mark_verified_idempotent(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = ConversationEvidenceRepository(store)
    repo.upsert(_evidence())

    repo.mark_verified("ce_x:p1:draft_reply_0")
    repo.mark_verified("ce_x:p1:draft_reply_0")

    listed = repo.list_by_interaction("x:p1:draft_reply")
    assert len(listed) == 1
    assert listed[0].verified is True


def test_mark_verified_missing_raises(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = ConversationEvidenceRepository(store)
    with pytest.raises(KeyError):
        repo.mark_verified("ce_missing_0")


def test_conversation_evidence_upsert_idempotent(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = ConversationEvidenceRepository(store)
    repo.upsert(_evidence(statement="a"))
    repo.upsert(_evidence(statement="b"))

    listed = repo.list_by_interaction("x:p1:draft_reply")
    assert len(listed) == 1
    assert listed[0].statement == "b"


def test_traceability_feedback_to_evidence(tmp_path):
    """Phase 6 验收：从互动可回溯到反馈快照与 conversation 证据（同一 interaction_id）。"""
    store = Store(tmp_path / "db.sqlite")
    store.init()
    fb_repo = FeedbackSnapshotRepository(store)
    ev_repo = ConversationEvidenceRepository(store)

    interaction_id = "x:p1:draft_reply"
    fb_repo.upsert(_snapshot(interaction_id=interaction_id))
    ev_repo.upsert(_evidence(interaction_id=interaction_id, idx=0))
    ev_repo.upsert(_evidence(interaction_id=interaction_id, idx=1, kind="question"))

    snapshot = fb_repo.get(interaction_id)
    assert snapshot is not None and snapshot.interaction_id == interaction_id
    evidence = ev_repo.list_by_interaction(interaction_id)
    assert len(evidence) == 2
    assert all(e.interaction_id == interaction_id for e in evidence)
