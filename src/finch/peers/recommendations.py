"""每日 50 人分层推荐：确定性组合 5 重点 / 15 摘要 / 30 浏览。

输入候选池（``PersonCandidate``），按评分排序 + 实践背景多样性组合，配合平台上限与冷却，
输出 ``priority/summary/browse`` 三层 + shortfall。合格候选不足时不凑数，返回实际数量与原因。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from finch.discovery.candidate_pool import PersonCandidate
from finch.peers.models import PeerProfile
from finch.settings import Settings

# 中文平台（用于 min_chinese_platforms_total 下限）。
_CHINESE_PLATFORMS = frozenset({"v2ex", "weixin", "xiaohongshu"})

# 不同角色的关键词启发式（best-effort，不进入 LLM）。
_ROLE_KEYWORDS = (
    "product manager",
    "product",
    "pm",
    "solution",
    "research",
    "researcher",
    "founder",
    "indie",
    "designer",
    "growth",
    "marketing",
    "产品",
    "解决方案",
    "研究",
    "创始人",
    "独立开发",
)


@dataclass
class Recommendation:
    """一条分层推荐。"""

    person_id: str
    candidate: PersonCandidate
    tier: str  # priority | summary | browse
    rank: int
    direction: str


@dataclass
class DailyRecommendationSet:
    """每日推荐结果：三层 + shortfall。"""

    priority: list[Recommendation] = field(default_factory=list)
    summary: list[Recommendation] = field(default_factory=list)
    browse: list[Recommendation] = field(default_factory=list)
    shortfall: dict[str, int] = field(default_factory=dict)

    @property
    def all(self) -> list[Recommendation]:
        return [*self.priority, *self.summary, *self.browse]

    @property
    def total(self) -> int:
        return len(self.all)


def _peer_blob(peer: PeerProfile) -> str:
    parts = [
        peer.display_name,
        peer.why_relevant,
        peer.current_work,
        *peer.expertise_topics,
        *peer.current_interests,
        *peer.shared_topics,
    ]
    return "\n".join(p for p in parts if p).strip().lower()


def _role_hint(peer: PeerProfile) -> bool:
    blob = _peer_blob(peer)
    return any(kw in blob for kw in _ROLE_KEYWORDS)


def _direction_for(c: PersonCandidate) -> str:
    cats = c.hit_categories
    if "long_term" in cats:
        return "peer"
    if "adjacent" in cats:
        return "adjacent"
    if _role_hint(c.peer):
        return "role"
    if "explore" in cats:
        return "serendipity"
    return "serendipity"


def _practice_topics(c: PersonCandidate) -> frozenset[str]:
    """实践背景信号：主题/兴趣标签；空 = 未知（不强制多样）。"""
    return frozenset(c.peer.expertise_topics) | frozenset(c.peer.current_interests)


def _cooled(c: PersonCandidate, clock: datetime, cooldown_days: int) -> bool:
    if c.has_new_work:
        return False
    if c.last_shown_at is None:
        return False
    return clock - c.last_shown_at < timedelta(days=cooldown_days)


def select_daily_recommendations(
    candidates: list[PersonCandidate],
    *,
    settings: Settings,
    now: datetime | None = None,
) -> DailyRecommendationSet:
    """确定性分层推荐；同分按 person_id 稳定排序保证重放一致。"""
    dp = settings.discovery.daily_people
    clock = now or datetime.now(UTC)

    active = [c for c in candidates if not _cooled(c, clock, dp.cooldown_days)]
    cooled_count = len(candidates) - len(active)
    active.sort(key=lambda c: (-c.score.total, c.person_id))

    multi = [c for c in active if c.artifact_count >= dp.min_artifacts_priority]

    result = DailyRecommendationSet()
    used: set[str] = set()
    platform_counts: dict[str, int] = {}
    rank = 0

    def accept(c: PersonCandidate, tier: str) -> bool:
        nonlocal rank
        if c.person_id in used:
            return False
        if platform_counts.get(c.platform, 0) >= dp.max_per_platform:
            return False
        used.add(c.person_id)
        platform_counts[c.platform] = platform_counts.get(c.platform, 0) + 1
        rec = Recommendation(
            person_id=c.person_id,
            candidate=c,
            tier=tier,
            rank=rank,
            direction=_direction_for(c),
        )
        rank += 1
        if tier == "priority":
            result.priority.append(rec)
        elif tier == "summary":
            result.summary.append(rec)
        else:
            result.browse.append(rec)
        return True

    # 1) priority：评分排序 → 去重 → 尽量覆盖不同实践背景（D6）。
    multi_sorted = sorted(multi, key=lambda c: (-c.score.total, c.person_id))
    picked_topics: set[str] = set()
    for c in multi_sorted:
        if len(result.priority) >= dp.priority_count:
            break
        topics = _practice_topics(c)
        if topics and topics & picked_topics:
            continue
        if accept(c, "priority"):
            picked_topics |= topics
    # 多样性跳过导致未选满时，按评分补位（不丢弃可信人物）。
    for c in multi_sorted:
        if len(result.priority) >= dp.priority_count:
            break
        accept(c, "priority")

    # 2) summary：剩余 multi 按分数填充。
    for c in [c for c in multi if c.person_id not in used]:
        if len(result.summary) >= dp.summary_count:
            break
        accept(c, "summary")

    # 3) browse：剩余 multi + single。
    for c in [c for c in active if c.person_id not in used]:
        if len(result.browse) >= dp.browse_count:
            break
        accept(c, "browse")

    # 4) shortfall：不足 target 时记录原因，不凑数。
    short = dp.target - result.total
    if short > 0:
        result.shortfall["insufficient_eligible"] = short
    if cooled_count:
        result.shortfall["cooldown"] = cooled_count

    return result


def select_home_items(
    recs: DailyRecommendationSet,
    *,
    home_limit: int = 3,
    surprise_limit: int = 1,
) -> list[Recommendation]:
    """从 priority 层选取首页重点（D7）：意外发现 ≤ surprise_limit，不足不凑数。

    priority 层已按方向 round-robin 多样化；这里按顺序取 home_limit 人，仅约束
    ``serendipity``（意外发现）数量。返回按原 rank 有序。
    """
    picks: list[Recommendation] = []
    surprise = 0
    for rec in recs.priority:
        if len(picks) >= home_limit:
            break
        if rec.direction == "serendipity" and surprise >= surprise_limit:
            continue
        picks.append(rec)
        if rec.direction == "serendipity":
            surprise += 1
    return picks
