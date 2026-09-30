"""确定性投影：从工作区只读聚合生成 projections/*.json（非事实源，可重建可删）。"""

from datetime import UTC, datetime
from typing import TypedDict

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.conversations.models import ConversationThread
from finch.conversations.service import ConversationService
from finch.inbox.models import InboxTrack
from finch.inbox.service import list_items
from finch.storage.repositories import (
    ContentJobRepository,
    ConversationThreadRepository,
    DecisionRecordRepository,
    DraftRepository,
    EvidenceRepository,
    PeerRepository,
)
from finch.storage.workspace import Workspace


def build_daily_context(ws: Workspace) -> dict:
    now = datetime.now(UTC)
    threads = ConversationThreadRepository(ws).list_all()
    jobs = ContentJobRepository(ws).list_jobs()
    return {
        "peers": [p.model_dump(mode="json") for p in PeerRepository(ws).list_all()],
        "conversations_needing_follow_up": [
            t.model_dump(mode="json")
            for t in threads
            if ConversationService().needs_follow_up(t, now=now)
        ],
        "ideas_awaiting_confirmation": [
            j.model_dump(mode="json")
            for j in jobs
            if j.status == ContentJobStatus.PROPOSED
        ],
    }


def build_pending_actions(ws: Workspace) -> dict:
    jobs_repo = ContentJobRepository(ws)
    return {
        "ideas_awaiting_confirmation": [
            j.model_dump(mode="json")
            for j in jobs_repo.list_jobs()
            if j.status == ContentJobStatus.PROPOSED
        ],
        "drafts_awaiting_review": [
            i.model_dump(mode="json")
            for i in list_items(
                jobs=jobs_repo,
                drafts=DraftRepository(ws),
                decisions=DecisionRecordRepository(ws),
                cards=EvidenceRepository(ws),
            )
            if i.track == InboxTrack.ORIGINAL
        ],
    }


def _thread_overdue_key(thread: ConversationThread, now: datetime) -> tuple:
    if thread.last_activity_at is None:
        age = 10**9  # 从未互动 → 最需跟进
    else:
        age = (now - thread.last_activity_at).days
    return (-age, -len(thread.open_questions), thread.id)


def _position_complete(job: ContentJob) -> bool:
    position = job.author_position
    return position is not None and bool(position.decision) and bool(position.tradeoff)


class FocusSection[T](TypedDict):
    """今日聚焦的一段：排序+截断后的 items 与全量 total。"""

    items: list[T]
    total: int


class TodayFocus(TypedDict):
    """今日聚焦投影（确定性，无 LLM）。"""

    conversations: FocusSection[ConversationThread]
    ideas: FocusSection[ContentJob]


def build_today_focus(
    *,
    threads: list[ConversationThread],
    ideas: list[ContentJob],
    now: datetime | None = None,
) -> TodayFocus:
    """确定性「今日聚焦」投影：对话区独立；无草稿浏览列表。"""
    now = now or datetime.now(UTC)
    conv_sorted = sorted(threads, key=lambda t: _thread_overdue_key(t, now))
    idea_sorted = sorted(ideas, key=lambda j: (not _position_complete(j), j.id))
    return {
        "conversations": {"items": conv_sorted[:2], "total": len(conv_sorted)},
        "ideas": {"items": idea_sorted[:1], "total": len(idea_sorted)},
    }
