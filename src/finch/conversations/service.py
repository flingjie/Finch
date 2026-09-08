"""对话线索服务：串联互动、恢复上下文、判断是否需要跟进。

本服务只做确定性状态管理（稳定 id、去重追加、跟进判定）；「提出下一步」的语义判断
由 conversation-follow-up Skill（LLM）负责，本模块不注入 runner。
"""

import hashlib
from datetime import datetime

from .models import ConversationThread, ThreadStatus


def thread_id_for(peer_id: str, topic: str) -> str:
    """由 ``(peer_id, topic)`` 派生稳定 thread id（幂等）。"""
    digest = hashlib.sha256(f"{peer_id}:{topic}".encode()).hexdigest()
    return f"thread_{digest[:12]}"


class ConversationService:
    """对话线索的确定性操作。"""

    def open_thread(self, *, peer_id: str, topic: str) -> ConversationThread:
        """为 (peer, topic) 打开一条对话线索（幂等：同一对恒同 id）。"""
        return ConversationThread(
            id=thread_id_for(peer_id, topic), peer_id=peer_id, topic=topic
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
        """变更线索状态（ACTIVE/DORMANT/CLOSED），返回新对象。"""
        return thread.model_copy(update={"status": status})

    def needs_follow_up(
        self, thread: ConversationThread, *, now: datetime, stale_days: int = 7
    ) -> bool:
        """是否需要跟进：CLOSED 不再跟进；有未解问题或超过 stale_days 未活动则跟进。"""
        if thread.status is ThreadStatus.CLOSED:
            return False
        if thread.open_questions:
            return True
        if thread.last_activity_at is None:
            return True
        return (now - thread.last_activity_at).days >= stale_days
