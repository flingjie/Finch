"""统一互动事实写入：幂等 id、episode 关联、线索绑定、首触复查窗口。

``connections record``、``conversations ingest``、``connections follow-up`` 三条 CLI
共用 :class:`ConnectionRecorder` 作为唯一事实写入入口，保证幂等键与线索绑定一致。
记录先持久化、线索后按 interaction_id 去重追加：中途失败可重跑修复，不重复计数、
不刷新发生时间。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from finch.connections.service import (
    apply_stage_upgrade,
    count_bidirectional_episodes,
    review_relationship,
)
from finch.conversations.models import ConversationThread, FollowUpTrigger
from finch.conversations.service import ConversationService, thread_id_for
from finch.engagement.models import InteractionRecord, VerificationStatus
from finch.peers.models import PeerProfile
from finch.storage.repositories import (
    ConversationThreadRepository,
    InteractionRecordRepository,
    PeerRepository,
)


def record_id_for(
    *,
    namespace: str,
    platform_message_id: str | None,
    request_id: str | None,
    source_url: str,
    peer_id: str,
    occurred_at: datetime,
) -> str:
    """统一互动事实幂等键。

    优先级：平台消息 ID → 稳定请求 ID → 来源 URL → peer:occurred_at 兜底。
    ``default`` 命名空间的 msg 键不带前缀，与旧 :func:`conversations.service.record_id_for`
    一致，避免既有消息记录 id 漂移；非 default 命名空间加前缀隔离入口。
    """
    if platform_message_id:
        key = (
            f"msg:{platform_message_id}"
            if namespace == "default"
            else f"msg:{namespace}:{platform_message_id}"
        )
    elif request_id:
        key = f"req:{namespace}:{request_id}"
    elif source_url:
        key = f"url:{source_url}"
    else:
        key = f"peer:{peer_id}:{occurred_at.isoformat()}"
    return f"rec_{hashlib.sha256(key.encode()).hexdigest()[:16]}"


def episode_id_for(*, thread_id: str, root_record_id: str) -> str:
    """由 (thread, 根记录) 派生稳定 episode id。"""
    digest = hashlib.sha256(f"{thread_id}:{root_record_id}".encode()).hexdigest()
    return f"episode_{digest[:12]}"


def resolve_episode_id(
    *,
    thread_id: str,
    direction: str,
    reply_to_id: str | None,
    records_on_thread: list[InteractionRecord],
    now: datetime,
    reply_observation_days: int,
    new_record_id: str,
) -> str:
    """解析记录归属的 episode：一次接触/会话 = 一个 episode。

    inbound 优先复用 ``reply_to_id`` 的 episode；否则并入观察窗口内最近的 outbound
    episode；否则以本记录为根新建。outbound 无 ``reply_to_id`` 时新建 episode（第二个
    独立接触），有 ``reply_to_id`` 时并入其 episode。
    """
    by_id = {r.id: r for r in records_on_thread}
    if reply_to_id and reply_to_id in by_id:
        base = by_id[reply_to_id]
        return base.episode_id or episode_id_for(
            thread_id=thread_id, root_record_id=base.id
        )
    if direction == "inbound":
        cutoff = now - timedelta(days=reply_observation_days)
        recent_outbound = [
            r
            for r in records_on_thread
            if r.direction == "outbound" and r.occurred_at >= cutoff
        ]
        if recent_outbound:
            latest = max(recent_outbound, key=lambda r: r.occurred_at)
            return latest.episode_id or episode_id_for(
                thread_id=thread_id, root_record_id=latest.id
            )
    return episode_id_for(thread_id=thread_id, root_record_id=new_record_id)


@dataclass
class RecordedInteraction:
    """一次记录的结果：互动记录 + 绑定后的线索 + 是否新建（False = 复用已有记录）。"""

    record: InteractionRecord
    thread: ConversationThread
    created: bool


class ConnectionRecorder:
    """统一互动事实写入入口（幂等 + 线索绑定 + 首触复查窗口 + 阶段投影）。"""

    def __init__(
        self,
        *,
        interactions: InteractionRecordRepository,
        threads: ConversationThreadRepository,
        peers: PeerRepository | None = None,
    ) -> None:
        self.interactions = interactions
        self.threads = threads
        self.peers = peers

    def record(
        self,
        *,
        peer_id: str,
        platform: str,
        body: str,
        direction: str,
        occurred_at: datetime,
        source_url: str = "",
        platform_message_id: str | None = None,
        request_id: str | None = None,
        namespace: str = "default",
        topic: str = "general",
        thread_id: str | None = None,
        reply_to_id: str | None = None,
        opportunity_id: str | None = None,
        proposal_id: str | None = None,
        verification_status: VerificationStatus = VerificationStatus.UNVERIFIED,
        observed_at: datetime | None = None,
        review_after_hours: int = 24,
        now: datetime | None = None,
        reply_observation_days: int = 14,
        provenance: str | None = None,
    ) -> RecordedInteraction:
        """幂等登记一次真实互动，并绑定/创建线索、关联 episode、更新首触复查窗口。

        顺序保证「记录先持久化、线索后追加」：中途失败重跑会复用已写记录，只补绑定，
        不重复计数、不刷新 ``occurred_at``。首触复查窗口仅在新建空线索时应用（不回填）。
        """
        if now is None:
            now = datetime.now(UTC)

        rid = record_id_for(
            namespace=namespace,
            platform_message_id=platform_message_id,
            request_id=request_id,
            source_url=source_url,
            peer_id=peer_id,
            occurred_at=occurred_at,
        )
        existing = self.interactions.get(rid)
        if existing is None and platform_message_id:
            existing = self.interactions.find_by_platform_message_id(platform_message_id)
        if existing is None and source_url:
            existing = self.interactions.find_by_source_url(source_url)

        tid = (
            thread_id
            or (existing.thread_id if existing is not None else None)
            or thread_id_for(peer_id, topic)
        )

        svc = ConversationService()
        thread = self.threads.get(tid)
        if thread is None:
            thread = svc.open_thread(peer_id=peer_id, topic=topic)
            if thread.id != tid:
                thread = thread.model_copy(update={"id": tid})
            # 首触复查窗口：仅在新建空线索时应用（新接触；旧线索不回填）。
            thread = svc.open_first_contact_review(
                thread, anchor=occurred_at, review_after_hours=review_after_hours
            )

        if existing is not None:
            created = False
            updates: dict = {}
            if observed_at is not None:
                updates["observed_at"] = observed_at
            if body and not existing.published_body and not existing.body:
                updates["published_body"] = body
                updates["body"] = body
            if existing.thread_id is None:
                updates["thread_id"] = tid
            record = existing.model_copy(update=updates) if updates else existing
            self.interactions.upsert(record)
        else:
            created = True
            records_on_thread = [
                r
                for r in self.interactions.list_by_peer(peer_id)
                if r.id in set(thread.interaction_ids)
            ]
            if reply_to_id:
                rt = self.interactions.get(reply_to_id)
                if rt is not None and rt.id not in {r.id for r in records_on_thread}:
                    records_on_thread = records_on_thread + [rt]
            episode_id = resolve_episode_id(
                thread_id=tid,
                direction=direction,
                reply_to_id=reply_to_id,
                records_on_thread=records_on_thread,
                now=now,
                reply_observation_days=reply_observation_days,
                new_record_id=rid,
            )
            record = InteractionRecord(
                id=rid,
                proposal_id=proposal_id,
                opportunity_id=opportunity_id,
                peer_id=peer_id,
                platform=platform,
                source_url=source_url,
                published_body=body,
                body=body,
                occurred_at=occurred_at,
                observed_at=observed_at or occurred_at,
                platform_message_id=platform_message_id,
                direction=direction,  # type: ignore[arg-type]
                reply_to_id=reply_to_id,
                verification_status=verification_status,
                provenance=provenance if provenance is not None else namespace,
                thread_id=tid,
                episode_id=episode_id,
                request_id=request_id,
                namespace=namespace,
            )
            self.interactions.upsert(record)

        # 绑定线索（幂等追加）+ 新 inbound 触发 NEW_REPLY。
        thread = svc.append_interaction(thread, record.id, occurred_at=record.occurred_at)
        if created and direction == "inbound":
            thread = svc.add_trigger(thread, FollowUpTrigger.NEW_REPLY)
        self.threads.upsert(thread)

        return RecordedInteraction(record=record, thread=thread, created=created)

    def record_and_project(
        self,
        *,
        peer: PeerProfile,
        person_id: str,
        natural_next_reason: str = "",
        **record_kwargs,
    ) -> tuple[RecordedInteraction, PeerProfile]:
        """登记互动并按真实 episode 证据建议关系阶段升级。"""
        result = self.record(**record_kwargs)
        updated = self.project_stage(
            peer, person_id=person_id, natural_next_reason=natural_next_reason
        )
        return result, updated

    def project_stage(
        self,
        peer: PeerProfile,
        *,
        person_id: str,
        natural_next_reason: str = "",
    ) -> PeerProfile:
        """按双向 episode 数（非记录条数）建议阶段并写回 peer。"""
        if self.peers is None:
            raise RuntimeError(
                "ConnectionRecorder requires a PeerRepository for project_stage"
            )
        records = self.interactions.list_by_peer(peer.id)
        review = review_relationship(
            peer,
            person_id=person_id,
            bidirectional_exchanges=count_bidirectional_episodes(records),
            natural_next_reason=natural_next_reason,
        )
        updated = apply_stage_upgrade(peer, review)
        self.peers.upsert(updated)
        return updated
