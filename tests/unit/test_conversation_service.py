"""ConversationThread / ConversationService 测试。"""

from datetime import UTC, datetime, timedelta

from finch.conversations.models import (
    Commitment,
    CommitmentStatus,
    FollowUpTrigger,
    ThreadStatus,
)
from finch.conversations.service import ConversationService, record_id_for, thread_id_for
from finch.engagement.models import VerificationStatus
from finch.storage.repositories import ConversationThreadRepository, InteractionRecordRepository
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

    updated2 = svc.append_interaction(updated, "rec_1", occurred_at=t1)
    assert updated2.interaction_ids == ["rec_1"]

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


def test_stale_alone_does_not_need_follow_up():
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_abc", topic="t")
    stale = svc.append_interaction(thread, "rec_1", occurred_at=datetime(2026, 8, 1))
    assert svc.needs_follow_up(stale, now=datetime(2026, 9, 8)) is False
    assert svc.is_stale_for_internal_review(stale, now=datetime(2026, 9, 8)) is True


def test_needs_follow_up_on_new_reply_trigger():
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_abc", topic="t")
    thread = svc.add_trigger(thread, FollowUpTrigger.NEW_REPLY)
    assert svc.needs_follow_up(thread, now=datetime(2026, 9, 8)) is True


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


def test_defer_suppresses_follow_up_until_due():
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_abc", topic="t")
    thread = svc.add_trigger(thread, FollowUpTrigger.NEW_REPLY)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    deferred = svc.defer(thread, until=now + timedelta(days=3))
    assert svc.needs_follow_up(deferred, now=now) is False
    assert svc.needs_follow_up(deferred, now=now + timedelta(days=4)) is True


def test_ingest_record_idempotent_keeps_occurred_at(tmp_path):
    ws = Workspace(tmp_path)
    repo = InteractionRecordRepository(ws)
    svc = ConversationService()
    t1 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    rec = svc.ingest_record(
        None,
        peer_id="peer_abc",
        platform="x",
        source_url="https://x.com/a/status/1",
        body="hello",
        occurred_at=t1,
        platform_message_id="msg_1",
        verification_status=VerificationStatus.USER_ATTESTED,
        direction="outbound",
    )
    repo.upsert(rec)
    existing = repo.get(rec.id)
    t2 = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    again = svc.ingest_record(
        existing,
        peer_id="peer_abc",
        platform="x",
        source_url="https://x.com/a/status/1",
        body="hello",
        occurred_at=t2,
        platform_message_id="msg_1",
        observed_at=t2,
    )
    assert again.id == rec.id
    assert again.occurred_at == t1
    assert again.observed_at == t2
    repo.upsert(again)
    assert len(repo.list_all()) == 1
    assert record_id_for(
        platform_message_id="msg_1",
        source_url="https://x.com/a/status/1",
        peer_id="peer_abc",
        occurred_at=t2,
    ) == rec.id


def test_add_commitment_triggers_follow_up():
    svc = ConversationService()
    thread = svc.open_thread(peer_id="peer_abc", topic="t")
    thread = svc.add_commitment(
        thread,
        Commitment(
            id="c1",
            owner="self",
            source_ref="user:said",
            status=CommitmentStatus.OPEN,
            text="I'll share the replay diff",
        ),
    )
    assert FollowUpTrigger.OWN_COMMITMENT in thread.pending_triggers
    assert svc.needs_follow_up(thread, now=datetime(2026, 9, 8)) is True


def test_thread_repository_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    repo = ConversationThreadRepository(ws)
    thread = ConversationService().open_thread(peer_id="peer_abc", topic="agent evals")

    repo.upsert(thread)
    got = repo.get(thread.id)
    assert got is not None
    assert got.peer_id == "peer_abc"
    assert got.topic == "agent evals"

    updated = thread.model_copy(update={"open_questions": ["how to reproduce?"]})
    repo.upsert(updated)
    assert repo.get(thread.id).open_questions == ["how to reproduce?"]
    assert len(repo.list_by_peer("peer_abc")) == 1


def test_thread_repository_deleting_search_does_not_lose_context(tmp_path):
    ws = Workspace(tmp_path)
    repo = ConversationThreadRepository(ws)
    thread = ConversationService().open_thread(peer_id="peer_abc", topic="t")
    repo.upsert(thread)

    assert repo.get(thread.id) is not None
    assert len(repo.list_all()) == 1
