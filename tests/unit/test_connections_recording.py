"""统一互动事实写入（ConnectionRecorder / 幂等 id / episode）单元测试。"""

from datetime import UTC, datetime, timedelta

from finch.connections.recording import (
    ConnectionRecorder,
    episode_id_for,
    record_id_for,
    resolve_episode_id,
)
from finch.connections.service import count_bidirectional_episodes
from finch.engagement.models import InteractionRecord, VerificationStatus
from finch.peers.models import RelationshipStage
from finch.peers.service import PeerService
from finch.storage.repositories import (
    ConversationThreadRepository,
    InteractionRecordRepository,
    PeerRepository,
)
from finch.storage.workspace import Workspace


def _peer():
    return PeerService().from_author(platform="x", author_id="alice", username="alice")


def _record(
    *,
    rid="rec_1",
    peer_id="peer_abc",
    direction="outbound",
    occurred_at=None,
    thread_id=None,
    episode_id=None,
) -> InteractionRecord:
    return InteractionRecord(
        id=rid,
        peer_id=peer_id,
        platform="x",
        source_url="https://x.com/a/status/1",
        published_body="hello",
        body="hello",
        occurred_at=occurred_at or datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        direction=direction,  # type: ignore[arg-type]
        thread_id=thread_id,
        episode_id=episode_id,
    )


def test_record_id_for_stable_and_namespaced():
    t = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    a = record_id_for(
        namespace="default", platform_message_id="msg_1", request_id=None,
        source_url="u", peer_id="p", occurred_at=t,
    )
    b = record_id_for(
        namespace="default", platform_message_id="msg_1", request_id=None,
        source_url="u2", peer_id="p", occurred_at=t,
    )
    assert a == b  # msg 优先，source_url 不影响
    assert a.startswith("rec_")
    # 非 default 命名空间加前缀隔离
    c = record_id_for(
        namespace="other", platform_message_id="msg_1", request_id=None,
        source_url="u", peer_id="p", occurred_at=t,
    )
    assert c != a
    # 稳定 request_id 幂等
    d = record_id_for(
        namespace="ns", platform_message_id=None, request_id="r1",
        source_url="", peer_id="p", occurred_at=t,
    )
    e = record_id_for(
        namespace="ns", platform_message_id=None, request_id="r1",
        source_url="", peer_id="p", occurred_at=t,
    )
    assert d == e


def test_episode_id_for_deterministic():
    assert episode_id_for(thread_id="t", root_record_id="r") == episode_id_for(
        thread_id="t", root_record_id="r"
    )
    assert episode_id_for(thread_id="t", root_record_id="r") != episode_id_for(
        thread_id="t", root_record_id="r2"
    )


def test_resolve_episode_id_inbound_joins_recent_outbound():
    now = datetime(2026, 9, 10, tzinfo=UTC)
    out = _record(
        rid="rec_out", direction="outbound",
        occurred_at=datetime(2026, 9, 9, tzinfo=UTC),
        thread_id="t", episode_id="ep_out",
    )
    got = resolve_episode_id(
        thread_id="t", direction="inbound", reply_to_id=None,
        records_on_thread=[out], now=now, reply_observation_days=14,
        new_record_id="rec_new",
    )
    assert got == "ep_out"


def test_resolve_episode_id_reply_to_id_wins():
    now = datetime(2026, 9, 10, tzinfo=UTC)
    out = _record(
        rid="rec_out", direction="outbound",
        occurred_at=datetime(2026, 9, 9, tzinfo=UTC),
        thread_id="t", episode_id="ep_out",
    )
    got = resolve_episode_id(
        thread_id="t", direction="inbound", reply_to_id="rec_out",
        records_on_thread=[out], now=now, reply_observation_days=14,
        new_record_id="rec_new",
    )
    assert got == "ep_out"


def test_resolve_episode_id_new_episode_when_no_recent_outbound():
    now = datetime(2026, 9, 10, tzinfo=UTC)
    old = _record(
        rid="rec_out", direction="outbound",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        thread_id="t", episode_id="ep_old",
    )
    got = resolve_episode_id(
        thread_id="t", direction="inbound", reply_to_id=None,
        records_on_thread=[old], now=now, reply_observation_days=14,
        new_record_id="rec_new",
    )
    assert got == episode_id_for(thread_id="t", root_record_id="rec_new")


def test_count_bidirectional_episodes():
    # 两条单向消息不构成双向 episode
    one_way = [_record(rid="r1", direction="outbound"), _record(rid="r2", direction="outbound")]
    assert count_bidirectional_episodes(one_way) == 0
    # 同一 episode 内 in+out 计 1
    both = [
        _record(rid="r1", direction="outbound", episode_id="ep1"),
        _record(rid="r2", direction="inbound", episode_id="ep1"),
    ]
    assert count_bidirectional_episodes(both) == 1
    # 两个独立双向 episode 计 2
    two = [
        _record(rid="r1", direction="outbound", episode_id="ep1"),
        _record(rid="r2", direction="inbound", episode_id="ep1"),
        _record(rid="r3", direction="outbound", episode_id="ep2"),
        _record(rid="r4", direction="inbound", episode_id="ep2"),
    ]
    assert count_bidirectional_episodes(two) == 2
    # 空正文不参与
    empty = [
        InteractionRecord(
            id="r1", peer_id="p", platform="x", source_url="u",
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            direction="outbound", episode_id="ep1",
        )
    ]
    assert count_bidirectional_episodes(empty) == 0


def test_recorder_record_idempotent_keeps_occurred_at(tmp_path):
    ws = Workspace(tmp_path)
    recorder = ConnectionRecorder(
        interactions=InteractionRecordRepository(ws),
        threads=ConversationThreadRepository(ws),
    )
    t1 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    r1 = recorder.record(
        peer_id="peer_abc", platform="x", body="hello", direction="outbound",
        occurred_at=t1, request_id="r1", namespace="connections.record",
    )
    assert r1.created is True
    assert r1.record.occurred_at == t1
    assert r1.thread.interaction_ids == [r1.record.id]

    t2 = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    r2 = recorder.record(
        peer_id="peer_abc", platform="x", body="hello", direction="outbound",
        occurred_at=t2, request_id="r1", namespace="connections.record",
    )
    assert r2.created is False
    assert r2.record.occurred_at == t1  # 重跑不改发生时间
    assert r2.record.id == r1.record.id
    assert len(recorder.interactions.list_all()) == 1


def test_record_and_project_two_one_way_no_recurring(tmp_path):
    ws = Workspace(tmp_path)
    recorder = ConnectionRecorder(
        interactions=InteractionRecordRepository(ws),
        threads=ConversationThreadRepository(ws),
        peers=PeerRepository(ws),
    )
    peer = _peer()  # DISCOVERED
    t1 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    _, updated = recorder.record_and_project(
        peer=peer, person_id="person_1",
        peer_id=peer.id, platform="x", body="hi", direction="outbound",
        occurred_at=t1, request_id="a", namespace="connections.record",
        verification_status=VerificationStatus.USER_ATTESTED,
    )
    # 首次单向记录不产生双向 episode → 不升级
    assert updated.relationship_stage is RelationshipStage.DISCOVERED

    _, updated2 = recorder.record_and_project(
        peer=updated, person_id="person_1",
        peer_id=peer.id, platform="x", body="hi2", direction="outbound",
        occurred_at=t1 + timedelta(days=1), request_id="b",
        namespace="connections.record",
        verification_status=VerificationStatus.USER_ATTESTED,
    )
    # 两条单向仍不升级 recurring
    assert updated2.relationship_stage is RelationshipStage.DISCOVERED


def test_first_contact_review_window_only_on_new_thread(tmp_path):
    ws = Workspace(tmp_path)
    recorder = ConnectionRecorder(
        interactions=InteractionRecordRepository(ws),
        threads=ConversationThreadRepository(ws),
    )
    t1 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    r = recorder.record(
        peer_id="peer_abc", platform="x", body="hi", direction="outbound",
        occurred_at=t1, request_id="a", namespace="connections.record",
        review_after_hours=24,
    )
    assert r.thread.follow_up_review_at == t1 + timedelta(hours=24)
    # 复用已有线索（第二次）不再重置复查窗口
    t2 = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    r2 = recorder.record(
        peer_id="peer_abc", platform="x", body="reply", direction="inbound",
        occurred_at=t2, request_id="b", namespace="connections.record",
        thread_id=r.thread.id, review_after_hours=24,
    )
    assert r2.thread.follow_up_review_at == t1 + timedelta(hours=24)
