"""对话线索服务：串联互动、恢复上下文、判断是否需要跟进。

本服务只做确定性状态管理（稳定 id、去重追加、跟进判定）；「提出下一步」的语义判断
由 conversation-follow-up Skill（LLM）负责，本模块不注入 runner。

跟进触发：new_reply / own_commitment / new_evidence / related_update，以及未解问题。
时间陈旧只供内部复查优先级，**单独不会**触发对外联系。
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from finch.engagement.models import InteractionRecord, VerificationStatus

from .models import (
    Commitment,
    CommitmentStatus,
    ConversationThread,
    FollowUpTrigger,
    ThreadStatus,
)


def thread_id_for(peer_id: str, topic: str) -> str:
    """由 ``(peer_id, topic)`` 派生稳定 thread id（幂等）。"""
    digest = hashlib.sha256(f"{peer_id}:{topic}".encode()).hexdigest()
    return f"thread_{digest[:12]}"


def record_id_for(
    *,
    platform_message_id: str | None,
    source_url: str,
    peer_id: str,
    occurred_at: datetime,
) -> str:
    """平台消息 ID 优先；URL 后备。不含「现在」时间，重跑稳定。"""
    if platform_message_id:
        key = f"msg:{platform_message_id}"
    elif source_url:
        key = f"url:{source_url}"
    else:
        key = f"peer:{peer_id}:{occurred_at.isoformat()}"
    return f"rec_{hashlib.sha256(key.encode()).hexdigest()[:16]}"


class ConversationService:
    """对话线索的确定性操作。"""

    def open_thread(
        self,
        *,
        peer_id: str,
        topic: str,
        root_message_id: str | None = None,
    ) -> ConversationThread:
        """为 (peer, topic) 打开一条对话线索（幂等：同一对恒同 id）。"""
        return ConversationThread(
            id=thread_id_for(peer_id, topic),
            peer_id=peer_id,
            topic=topic,
            root_message_id=root_message_id,
        )

    def append_interaction(
        self, thread: ConversationThread, interaction_id: str, *, occurred_at: datetime
    ) -> ConversationThread:
        """把一次互动串进线索；同 interaction_id 幂等（不重复），并刷新最近活动时间。"""
        updated = thread.model_copy(deep=True)
        if interaction_id not in updated.interaction_ids:
            updated.interaction_ids.append(interaction_id)
        if updated.last_activity_at is None or occurred_at > updated.last_activity_at:
            updated.last_activity_at = occurred_at
        return updated

    def set_status(self, thread: ConversationThread, status: ThreadStatus) -> ConversationThread:
        """变更线索状态（ACTIVE/DORMANT/CLOSED/DEFERRED），返回新对象。"""
        return thread.model_copy(update={"status": status})

    def defer(
        self, thread: ConversationThread, *, until: datetime
    ) -> ConversationThread:
        return thread.model_copy(
            update={"status": ThreadStatus.DEFERRED, "defer_until": until}
        )

    def add_trigger(
        self, thread: ConversationThread, trigger: FollowUpTrigger
    ) -> ConversationThread:
        updated = thread.model_copy(deep=True)
        if trigger not in updated.pending_triggers:
            updated.pending_triggers.append(trigger)
        return updated

    def clear_triggers(self, thread: ConversationThread) -> ConversationThread:
        return thread.model_copy(update={"pending_triggers": []})

    def add_commitment(
        self, thread: ConversationThread, commitment: Commitment
    ) -> ConversationThread:
        updated = thread.model_copy(deep=True)
        if any(c.id == commitment.id for c in updated.commitments):
            return updated
        updated.commitments.append(commitment)
        return self.add_trigger(updated, FollowUpTrigger.OWN_COMMITMENT)

    def needs_follow_up(
        self, thread: ConversationThread, *, now: datetime, stale_days: int = 7
    ) -> bool:
        """是否需要跟进（可对外呈现）。

        CLOSED 不跟进；DEFERRED 且未到期不跟进。
        触发：pending_triggers、开放承诺、未解问题。
        ``stale_days`` 保留签名兼容，但**单独过期不触发**对外联系。
        """
        del stale_days  # time alone must not contact the other person
        if thread.status is ThreadStatus.CLOSED:
            return False
        if thread.status is ThreadStatus.DEFERRED:
            if thread.defer_until is not None and thread.defer_until > now:
                return False
        if thread.pending_triggers:
            return True
        if any(c.status is CommitmentStatus.OPEN for c in thread.commitments):
            return True
        if thread.open_questions or thread.open_question_notes:
            return True
        return False

    def is_stale_for_internal_review(
        self, thread: ConversationThread, *, now: datetime, stale_days: int = 7
    ) -> bool:
        """内部复查用：时间陈旧，不意味着应联系对方。"""
        if thread.status is ThreadStatus.CLOSED:
            return False
        if thread.last_activity_at is None:
            return True
        return (now - thread.last_activity_at).days >= stale_days

    def ingest_record(
        self,
        existing: InteractionRecord | None,
        *,
        peer_id: str,
        platform: str,
        source_url: str,
        body: str,
        occurred_at: datetime,
        platform_message_id: str | None = None,
        direction: str = "unknown",
        verification_status: VerificationStatus = VerificationStatus.UNVERIFIED,
        proposal_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> InteractionRecord:
        """幂等写入互动事实：已存在则保留原 ``occurred_at``，不漂移。"""
        rid = record_id_for(
            platform_message_id=platform_message_id,
            source_url=source_url,
            peer_id=peer_id,
            occurred_at=occurred_at,
        )
        if existing is not None and existing.id == rid:
            # Rerun: keep original occurred_at; may refresh observed_at / body if empty.
            updates: dict = {}
            if observed_at is not None:
                updates["observed_at"] = observed_at
            if body and not existing.published_body and not existing.body:
                updates["published_body"] = body
                updates["body"] = body
            return existing.model_copy(update=updates) if updates else existing
        return InteractionRecord(
            id=rid,
            proposal_id=proposal_id,
            peer_id=peer_id,
            platform=platform,
            source_url=source_url,
            published_body=body,
            body=body,
            occurred_at=occurred_at,
            observed_at=observed_at or occurred_at,
            platform_message_id=platform_message_id,
            direction=direction,  # type: ignore[arg-type]
            verification_status=verification_status,
            provenance="ingest",
        )
