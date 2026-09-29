"""community-scout 薄 loop: feedback 回灌纯函数 + 编排器（见 CommunityLoop）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from finch.communities.models import (
    CommunityCandidate,
    CommunityFeedback,
    CommunityResult,
    FeedbackFacts,
    RunIntent,
    community_id_for,
)

_ENGAGED = {
    CommunityResult.JOINED,
    CommunityResult.INTERACTED,
    CommunityResult.REPEATED,
    CommunityResult.CONTRIBUTED,
}


def derive_feedback_facts(
    latest_by_identity: dict[str, CommunityFeedback],
    *,
    now: datetime | None = None,
    suppress_window: timedelta = timedelta(weeks=4),
) -> FeedbackFacts:
    """由「每个 identity 的最新一条 feedback」派生出确定性事实。

    硬门禁（Python 强制）：
    - ``ignored`` 在 suppress_window 内 → 排除；
    - ``no_time`` 永不排除（当次约束，不永久过滤）；
    - 已发生互动（joined/interacted/repeated/contributed）→ 进入「继续」框架。
    软排序：每个 identity 的「result + reason_kind + note」摘要，注入 judge。
    """
    clock = now or datetime.now(UTC)
    facts = FeedbackFacts()
    for identity, fb in latest_by_identity.items():
        if fb.result == CommunityResult.IGNORED:
            age = clock - fb.at
            if age < suppress_window:
                weeks = max(1, int(age.days / 7))
                facts.excluded[identity] = f"ignored {weeks}w ago"
        if fb.result in _ENGAGED:
            facts.continue_framing.append(identity)
        summary = " ".join(
            p for p in [fb.result.value, fb.reason_kind, fb.note] if p
        ).strip()
        facts.summaries[identity] = summary
    return facts


def candidate_identity(c: CommunityCandidate) -> str:
    """候选的跨周去重键：有规范 URL 用 URL，否则回退 name-hash id（与 identity_key 一致）。"""
    return c.canonical_url or community_id_for(c.name)


class CommunitySearchSource(Protocol):
    """search 动作依赖的窄接口：给定意图与目标，返回有界候选列表（只读）。"""

    def search(self, intent: RunIntent, goal: str, limit: int) -> list[CommunityCandidate]: ...
