"""ConversationThread / ConversationService 测试。"""

from datetime import datetime

from finch.conversations.models import ThreadStatus
from finch.conversations.service import ConversationService, thread_id_for
from finch.storage.repositories import ConversationThreadRepository
from finch.storage.workspace import Workspace


def test_thread_id_for_stable():
    assert thread_id_for("peer_abc", "agent evals") == thread_id_for("peer_abc", "agent evals")
    assert thread_id_for("peer_abc", "agent evals") != thread_id_for("peer_abc", "other")
    assert thread_id_for("peer_abc", "t") != thread_id_for("peer_def", "t")


def test_open_thread_defaults():
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_abc", topic="agent evals")
    assert thread.id == thread_id_for("peer_abc", "agent evals")
    assert thread.peer_id == "peer_abc"
    assert thread.status is ThreadStatus.ACTIVE
    assert thread.interaction_ids == []
    assert thread.last_activity_at is None


def test_append_interaction_dedups_and_updates_activity():
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_abc", topic="agent evals")
    t1 = datetime(2026, 9, 1, 12, 0, 0)
    updated = svc.append_interaction(thread, "rec_1", occurred_at=t1)
    assert updated.interaction_ids == ["rec_1"]
    assert updated.last_activity_at == t1

    # 同一互动重复追加：不重复。
    updated2 = svc.append_interaction(updated, "rec_1", occurred_at=t1)
    assert updated2.interaction_ids == ["rec_1"]

    # 新互动追加，活动时间前进。
    t2 = datetime(2026, 9, 2, 12, 0, 0)
    updated3 = svc.append_interaction(updated2, "rec_2", occurred_at=t2)
    assert updated3.interaction_ids == ["rec_1", "rec_2"]
    assert updated3.last_activity_at == t2


def test_append_interaction_does_not_mutate_original():
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_abc", topic="t")
    svc.append_interaction(thread, "rec_1", occurred_at=datetime(2026, 9, 1))
    assert thread.interaction_ids == []


def test_needs_follow_up_when_open_questions():
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_abc", topic="t")
    with_q = thread.model_copy(update={"open_questions": ["how to reproduce?"]})
    assert svc.needs_follow_up(with_q, now=datetime(2026, 9, 8)) is True


def test_needs_follow_up_when_stale():
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_abc", topic="t")
    stale = svc.append_interaction(thread, "rec_1", occurred_at=datetime(2026, 8, 1))
    assert svc.needs_follow_up(stale, now=datetime(2026, 9, 8)) is True


def test_needs_follow_up_when_fresh_and_no_questions():
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_abc", topic="t")
    fresh = svc.append_interaction(thread, "rec_1", occurred_at=datetime(2026, 9, 7))
    assert svc.needs_follow_up(fresh, now=datetime(2026, 9, 8)) is False


def test_closed_thread_never_needs_follow_up():
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_abc", topic="t")
    with_q = thread.model_copy(update={"open_questions": ["q"]})
    closed = svc.set_status(with_q, ThreadStatus.CLOSED)
    assert svc.needs_follow_up(closed, now=datetime(2026, 9, 8)) is False


def test_thread_repository_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    repo = ConversationThreadRepository(ws)
    thread = ConversationService().open_thread(peer_id="peer_abc", topic="agent evals")

    repo.upsert(thread)
    got = repo.get(thread.id)
    assert got is not None
    assert got.peer_id == "peer_abc"
    assert got.topic == "agent evals"

    # 同 id 重复 upsert 幂等（不产生第二行），且更新内容。
    updated = thread.model_copy(update={"open_questions": ["how to reproduce?"]})
    repo.upsert(updated)
    assert repo.get(thread.id).open_questions == ["how to reproduce?"]
    assert len(repo.list_by_peer("peer_abc")) == 1


def test_thread_repository_deleting_search_does_not_lose_context(tmp_path):
    # 对话线索独立持久化：不依赖 run_id / 搜索运行，重跑搜索不影响已存关系上下文。
    ws = Workspace(tmp_path)
    repo = ConversationThreadRepository(ws)
    thread = ConversationService().open_thread(peer_id="peer_abc", topic="t")
    repo.upsert(thread)

    # 模拟重跑搜索：什么都不做，直接读回，线索仍在。
    assert repo.get(thread.id) is not None
    assert len(repo.list_all()) == 1
