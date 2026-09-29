"""community-scout 薄 loop: feedback 回灌纯函数 + 编排器（见 CommunityLoop）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from finch.communities.models import (
    CommunityFeedback,
    CommunityResult,
    FeedbackFacts,
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
