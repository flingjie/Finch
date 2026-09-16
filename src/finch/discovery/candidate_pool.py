"""Person 候选池：聚合人物 → 命中标签 → 确定性排序 → 最多 100 人。

输入是 ``build_shortlist_candidates`` 产出的 ``ShortlistCandidate``（已含确定性分数、
artifact 列表、平台与冷却信号）；这里补充兴趣命中标签、按 person_id 去重、封顶并给每个
被淘汰候选一个 reason_code。跨平台同一自然人不在此猜测合并（沿用 ``PersonService.propose_link``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from finch.peers.models import PeerProfile
from finch.peers.scoring import PersonScoreBreakdown
from finch.peers.shortlist import ShortlistCandidate
from finch.settings import Settings

# 淘汰 reason_code（供 shortfall 汇报）。
NO_ARTIFACTS = "no_artifacts"
OVER_CAP = "over_cap"
DUPLICATE_PERSON = "duplicate_person"

# 兴趣类别（命中标签分组，供 Phase 3 发现方向软配额使用）。
_CATEGORY_FIELDS = (
    ("long_term", "long_term_interests"),
    ("question", "current_questions"),
    ("explore", "explore_directions"),
    ("adjacent", "adjacent_queries"),
)


@dataclass(frozen=True)
class InterestHit:
    """一次兴趣命中：类别 + 命中词。"""

    category: str
    label: str


@dataclass
class PersonCandidate:
    """候选池中的一个人物（含命中标签，供分层推荐消费）。"""

    person_id: str
    peer: PeerProfile
    score: PersonScoreBreakdown
    artifact_ids: list[str]
    hit_labels: list[str] = field(default_factory=list)
    hit_categories: set[str] = field(default_factory=set)
    platform: str = ""
    last_shown_at: datetime | None = None
    has_new_work: bool = False

    @property
    def artifact_count(self) -> int:
        return len(self.artifact_ids)


@dataclass
class PersonCandidatePool:
    """最多 ``max_size`` 人的确定性候选池。"""

    candidates: list[PersonCandidate] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)


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


def match_interests(peer: PeerProfile, *, settings: Settings) -> list[InterestHit]:
    """对 peer 文本做兴趣命中（大小写不敏感子串；<3 字符的词跳过）。"""
    blob = _peer_blob(peer)
    hits: list[InterestHit] = []
    seen: set[tuple[str, str]] = set()
    for category, field_name in _CATEGORY_FIELDS:
        terms = getattr(settings.interests, field_name)
        for term in terms:
            if len(term.strip()) < 3:
                continue
            key = term.strip().lower()
            if key in blob:
                hit = (category, term.strip())
                if hit not in seen:
                    seen.add(hit)
                    hits.append(InterestHit(category=category, label=term.strip()))
    return hits


def build_pool(
    candidates: list[ShortlistCandidate],
    *,
    settings: Settings,
    max_size: int = 100,
) -> PersonCandidatePool:
    """将 shortlist 候选丰富为候选池：命中标签 + 去重 + 封顶 + reason_code。"""
    pool = PersonCandidatePool()
    by_person: dict[str, PersonCandidate] = {}

    for c in candidates:
        if not c.artifact_ids:
            pool.rejected[NO_ARTIFACTS] = pool.rejected.get(NO_ARTIFACTS, 0) + 1
            continue
        hits = match_interests(c.peer, settings=settings)
        cand = PersonCandidate(
            person_id=c.person_id,
            peer=c.peer,
            score=c.score,
            artifact_ids=list(c.artifact_ids),
            hit_labels=[h.label for h in hits],
            hit_categories={h.category for h in hits},
            platform=c.platform,
            last_shown_at=c.last_shown_at,
            has_new_work=c.has_new_work,
        )
        existing = by_person.get(c.person_id)
        if existing is not None:
            # 跨平台合并后同一 person 只保留分数更高的一条。
            if cand.score.total > existing.score.total:
                by_person[c.person_id] = cand
            else:
                pool.rejected[DUPLICATE_PERSON] = (
                    pool.rejected.get(DUPLICATE_PERSON, 0) + 1
                )
            continue
        by_person[c.person_id] = cand

    ordered = sorted(
        by_person.values(),
        key=lambda c: (-c.score.total, c.person_id),
    )
    pool.candidates = ordered[:max_size]
    for _ in ordered[max_size:]:
        pool.rejected[OVER_CAP] = pool.rejected.get(OVER_CAP, 0) + 1
    return pool
