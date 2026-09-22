"""CommunityService：上下文快照 / 保存社区卡 / 查看 / 记录反馈（薄持久化，无评分）。

评分、去重、采集都留在 Skill 的 references 里，由 agent 判断；这里只做确定性持久化
与反馈记录，保证 4 周验证能度量"第二次交流"。
"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.communities.models import (
    CommunityContext,
    CommunityFeedback,
    CommunityProfile,
    CommunityResult,
    community_id_for,
)
from finch.communities.repository import CommunityRepository
from finch.content.jobs import ContentJobStatus
from finch.settings import Settings
from finch.storage.repositories import ContentJobRepository, PeerRepository
from finch.storage.workspace import Workspace


def week_label(now: datetime | None = None) -> str:
    """ISO 周标签：``2026-W39``（用于周报命名与候选归属）。"""
    clock = now or datetime.now(UTC)
    iso = clock.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


class CommunityService:
    def __init__(self, workspace: Workspace) -> None:
        self.repo = CommunityRepository(workspace)

    def snapshot_context(
        self,
        settings: Settings,
        ws: Workspace,
        *,
        now: datetime | None = None,
    ) -> CommunityContext:
        """快照当前实践上下文：读 interests + 聚合已有 peers/ideas（可审计）。"""
        peers = PeerRepository(ws).list_all()
        jobs = ContentJobRepository(ws).list_jobs()
        return CommunityContext(
            week=week_label(now),
            interests=list(settings.interests.long_term_interests),
            current_questions=list(settings.interests.current_questions),
            practice_refs=list(settings.interests.practice_refs),
            active_peers=[p.id for p in peers][:50],
            recent_ideas=[
                j.core_message
                for j in jobs
                if j.status == ContentJobStatus.PROPOSED
            ][:20],
        )

    def save(self, profile: CommunityProfile) -> CommunityProfile:
        """保存社区卡：id 由 name 内容寻址（幂等）；追加 candidates.jsonl。"""
        if not profile.id:
            profile = profile.model_copy(update={"id": community_id_for(profile.name)})
        self.repo.append_candidate(profile)
        return profile

    def inspect(self, community_id: str) -> CommunityProfile | None:
        return self.repo.get_candidate(community_id)

    def record_feedback(
        self,
        community_id: str,
        result: CommunityResult,
        *,
        note: str = "",
    ) -> CommunityFeedback:
        feedback = CommunityFeedback(community_id=community_id, result=result, note=note)
        self.repo.append_feedback(feedback)
        return feedback

    def list_candidates(self) -> list[CommunityProfile]:
        return self.repo.list_candidates()

    def list_feedback(self) -> list[CommunityFeedback]:
        return self.repo.list_feedback()
