"""确定性投影：从工作区只读聚合生成 projections/*.json（非事实源，可重建可删）。"""

from datetime import UTC, datetime
from typing import TypedDict

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.conversations.models import ConversationThread
from finch.conversations.service import ConversationService
from finch.engagement.flow import RankedPeer
from finch.engagement.models import Opportunity
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
    peers: FocusSection[RankedPeer]
    opportunities: FocusSection[Opportunity]
    ideas: FocusSection[ContentJob]


def build_today_focus(
    *,
    peers: list[RankedPeer],
    threads: list[ConversationThread],
    ideas: list[ContentJob],
    opportunities: list[Opportunity] | None = None,
    now: datetime | None = None,
    opportunity_limit: int = 10,
) -> TodayFocus:
    """确定性「今日聚焦」投影：对话区独立；新发现机会 8–12；无草稿浏览列表。"""
    now = now or datetime.now(UTC)
    conv_sorted = sorted(threads, key=lambda t: _thread_overdue_key(t, now))
    peer_sorted = sorted(peers, key=lambda rp: (-rp.value.total, rp.profile.id))
    opp_list = opportunities if opportunities is not None else []
    if not opp_list:
        # Rebuild from repo is caller's job; empty means none.
        opp_sorted: list[Opportunity] = []
    else:
        opp_sorted = sorted(opp_list, key=lambda o: (-o.score_total, o.id))
    idea_sorted = sorted(ideas, key=lambda j: (not _position_complete(j), j.id))
    return {
        "conversations": {"items": conv_sorted[:2], "total": len(conv_sorted)},
        "peers": {"items": peer_sorted[:opportunity_limit], "total": len(peer_sorted)},
        "opportunities": {
            "items": opp_sorted[:opportunity_limit],
            "total": len(opp_sorted),
        },
        "ideas": {"items": idea_sorted[:1], "total": len(idea_sorted)},
    }
