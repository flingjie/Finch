"""确定性投影：从工作区只读聚合生成 projections/*.json（非事实源，可重建可删）。"""

from datetime import UTC, datetime

from finch.content.jobs import ContentJobStatus
from finch.conversations.service import ConversationService
from finch.engagement.models import InteractionStatus
from finch.inbox.models import InboxTrack
from finch.inbox.service import list_items
from finch.storage.repositories import (
    ContentJobRepository,
    ConversationThreadRepository,
    DecisionRecordRepository,
    DraftRepository,
    EvidenceRepository,
    InteractionRepository,
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
        "pending_proposals": [
            c.model_dump(mode="json") for c in InteractionRepository(ws).list_pending()
        ],
    }


def build_pending_actions(ws: Workspace) -> dict:
    jobs_repo = ContentJobRepository(ws)
    interactions = InteractionRepository(ws)
    return {
        "approved_unexecuted_proposals": [
            c.model_dump(mode="json")
            for c in interactions.list_all()
            if c.status == InteractionStatus.APPROVED
        ],
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
                interactions=interactions,
                cards=EvidenceRepository(ws),
            )
            if i.track == InboxTrack.ORIGINAL
        ],
    }
