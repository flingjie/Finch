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
from finch.profile.models import load_practice_profile
from finch.settings import Settings
from finch.storage.repositories import ContentJobRepository, PeerRepository
from finch.storage.workspace import Workspace


class CommunityNotFoundError(Exception):
    """对不存在的社区 id 记录反馈时的校验失败。"""


def _practice_refs(settings: Settings) -> list[str]:
    """非空 practice profile → confirmed 条目 refs 展平去重；否则回退配置。"""
    profile = load_practice_profile(settings.paths.practice_profile_path)
    if profile.is_empty():
        return list(settings.interests.practice_refs)
    seen: dict[str, None] = {}
    for item in profile.confirmed_items():
        for ref in item.evidence_refs:
            seen.setdefault(ref, None)
    return list(seen)


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
            practice_refs=_practice_refs(settings),
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
        reason_kind: str = "",
        interaction_ref: str = "",
        ref_kind: str = "",
    ) -> CommunityFeedback:
        if self.inspect(community_id) is None:
            raise CommunityNotFoundError(community_id)
        feedback = CommunityFeedback(
            community_id=community_id,
            result=result,
            note=note,
            reason_kind=reason_kind,
            interaction_ref=interaction_ref,
            ref_kind=ref_kind,
        )
        self.repo.append_feedback(feedback)
        return feedback

    def list_candidates(self) -> list[CommunityProfile]:
        return self.repo.list_candidates()

    def list_feedback(self) -> list[CommunityFeedback]:
        return self.repo.list_feedback()

    def feedback_for(self, community_id: str) -> list[CommunityFeedback]:
        """某个社区的全部反馈（供 Skill 回访读取）。"""
        return self.repo.feedback_for(community_id)
