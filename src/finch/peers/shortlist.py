"""每日三槽位 shortlist：new_creator / reply_opportunity / relationship_next。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from finch.peers.models import PeerProfile, RelationshipStage
from finch.peers.scoring import PersonScoreBreakdown


class ShortlistSlot(StrEnum):
    NEW_CREATOR = "new_creator"
    REPLY_OPPORTUNITY = "reply_opportunity"
    RELATIONSHIP_NEXT = "relationship_next"


@dataclass
class ShortlistCandidate:
    peer: PeerProfile
    person_id: str
    score: PersonScoreBreakdown
    artifact_ids: list[str] = field(default_factory=list)
    why: str = ""
    platform: str = ""
    last_shown_at: datetime | None = None
    has_new_work: bool = False


@dataclass
class ShortlistItem:
    slot: ShortlistSlot
    candidate: ShortlistCandidate


def select_daily_shortlist(
    candidates: list[ShortlistCandidate],
    *,
    now: datetime | None = None,
    cooldown_days: int = 7,
    diversity_days: int = 14,
    recent_platforms: set[str] | None = None,
) -> list[ShortlistItem]:
    """选出至多 3 个槽位；宁缺毋滥；同平台 ≤2；7 天冷却（除非新作品）。"""
    clock = now or datetime.now(UTC)
    recent = recent_platforms or set()

    def cooled(c: ShortlistCandidate) -> bool:
        if c.has_new_work:
            return False
        if c.last_shown_at is None:
            return False
        return clock - c.last_shown_at < timedelta(days=cooldown_days)

    eligible = [
        c
        for c in candidates
        if len(c.artifact_ids) >= 2 and not cooled(c) and c.score.total > 0
    ]
    eligible.sort(key=lambda c: c.score.total, reverse=True)

    items: list[ShortlistItem] = []
    used_peers: set[str] = set()
    platform_counts: dict[str, int] = {}

    def can_take(c: ShortlistCandidate) -> bool:
        if c.peer.id in used_peers:
            return False
        if platform_counts.get(c.platform, 0) >= 2:
            return False
        return True

    def take(slot: ShortlistSlot, predicate) -> ShortlistCandidate | None:
        for c in eligible:
            if not can_take(c):
                continue
            if predicate(c):
                return c
        return None

    # Slot 1: new creator (discovered)
    c1 = take(
        ShortlistSlot.NEW_CREATOR,
        lambda c: c.peer.relationship_stage
        in {RelationshipStage.DISCOVERED, RelationshipStage.RELEVANT},
    )
    if c1:
        items.append(ShortlistItem(slot=ShortlistSlot.NEW_CREATOR, candidate=c1))
        used_peers.add(c1.peer.id)
        platform_counts[c1.platform] = platform_counts.get(c1.platform, 0) + 1

    # Slot 2: reply opportunity (high connection_opportunity / score)
    c2 = take(
        ShortlistSlot.REPLY_OPPORTUNITY,
        lambda c: c.score.connection_opportunity > 0 or c.score.total >= 0.3,
    )
    if c2:
        items.append(ShortlistItem(slot=ShortlistSlot.REPLY_OPPORTUNITY, candidate=c2))
        used_peers.add(c2.peer.id)
        platform_counts[c2.platform] = platform_counts.get(c2.platform, 0) + 1

    # Slot 3: relationship next (engaged+)
    engaged_stages = {
        RelationshipStage.ENGAGED,
        RelationshipStage.RECURRING,
        RelationshipStage.PRACTICING,
        RelationshipStage.CONVERSING,
        RelationshipStage.COLLABORATING,
    }
    c3 = take(
        ShortlistSlot.RELATIONSHIP_NEXT,
        lambda c: c.peer.relationship_stage in engaged_stages,
    )
    if c3:
        items.append(ShortlistItem(slot=ShortlistSlot.RELATIONSHIP_NEXT, candidate=c3))
        used_peers.add(c3.peer.id)
        platform_counts[c3.platform] = platform_counts.get(c3.platform, 0) + 1

    # Diversity: at least one from platform/domain unseen in 14d when possible
    if items and recent:
        if all(i.candidate.platform in recent for i in items):
            alt = take(
                ShortlistSlot.NEW_CREATOR,
                lambda c: c.platform not in recent,
            )
            if alt and len(items) < 3:
                items.append(ShortlistItem(slot=ShortlistSlot.NEW_CREATOR, candidate=alt))
            elif alt and items:
                # Replace lowest-score item if all from recent platforms
                worst_i = min(range(len(items)), key=lambda i: items[i].candidate.score.total)
                if alt.score.total >= items[worst_i].candidate.score.total * 0.8:
                    used_peers.discard(items[worst_i].candidate.peer.id)
                    items[worst_i] = ShortlistItem(
                        slot=items[worst_i].slot, candidate=alt
                    )

    # Cap platforms after diversity swap
    final: list[ShortlistItem] = []
    pc: dict[str, int] = {}
    for item in items:
        p = item.candidate.platform
        if pc.get(p, 0) >= 2:
            continue
        pc[p] = pc.get(p, 0) + 1
        final.append(item)
    return final[:3]
