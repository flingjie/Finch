"""Unit tests for tiered daily 50-person recommendations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from finch.discovery.candidate_pool import PersonCandidate
from finch.peers.models import PeerProfile, PlatformIdentity
from finch.peers.recommendations import select_daily_recommendations
from finch.peers.scoring import PersonScoreBreakdown
from finch.settings import Settings

_PLATFORMS = ["x", "reddit", "github", "v2ex", "weixin", "xiaohongshu"]


def _score(total: float) -> PersonScoreBreakdown:
    return PersonScoreBreakdown(
        sustained_creation=0.5,
        first_hand=0.5,
        sharing_willingness=0.5,
        cross_domain=0.0,
        joint_practice=0.0,
        connection_opportunity=0.0,
        total=total,
        popularity_context={},
    )


def _peer(person_id: str, platform: str = "x") -> PeerProfile:
    return PeerProfile(
        id=f"peer_{person_id}",
        platform_identities=[
            PlatformIdentity(platform=platform, author_id=f"u_{person_id}")  # type: ignore[arg-type]
        ],
    )


def _cand(
    person_id: str,
    *,
    total: float = 0.5,
    platform: str = "x",
    artifacts: int = 2,
    last_shown_at: datetime | None = None,
    has_new_work: bool = False,
) -> PersonCandidate:
    return PersonCandidate(
        person_id=person_id,
        peer=_peer(person_id, platform=platform),
        score=_score(total),
        artifact_ids=[f"a{i}" for i in range(artifacts)],
        platform=platform,
        last_shown_at=last_shown_at,
        has_new_work=has_new_work,
    )


def test_50_qualified_yields_exactly_50():
    cands = [
        _cand(f"p{i:03d}", total=0.99 - (i % 10) * 0.01, platform=_PLATFORMS[i % 6])
        for i in range(60)
    ]
    recs = select_daily_recommendations(cands, settings=Settings())
    assert recs.total == 50
    assert len(recs.priority) == 5
    assert len(recs.summary) == 15
    assert len(recs.browse) == 30
    ids = [r.person_id for r in recs.all]
    assert len(set(ids)) == 50


def test_per_platform_cap_20():
    cands = [_cand(f"x{i}", platform="x", total=0.9) for i in range(25)]
    cands += [_cand(f"r{i}", platform="reddit", total=0.8) for i in range(30)]
    recs = select_daily_recommendations(cands, settings=Settings())
    from collections import Counter

    platform_counts = Counter(r.candidate.platform for r in recs.all)
    assert all(v <= 20 for v in platform_counts.values())


def test_shortfall_reported_when_insufficient():
    cands = [_cand(f"p{i}") for i in range(10)]
    recs = select_daily_recommendations(cands, settings=Settings())
    assert recs.total == 10
    assert recs.shortfall.get("insufficient_eligible") == 40


def test_cooldown_excludes_recently_shown():
    now = datetime.now(UTC)
    recent = now - timedelta(days=1)
    fresh = _cand("fresh", last_shown_at=recent)
    new_work = _cand("new_work", last_shown_at=recent, has_new_work=True)
    recs = select_daily_recommendations(
        [fresh, new_work], settings=Settings(), now=now
    )
    ids = [r.person_id for r in recs.all]
    assert "fresh" not in ids
    assert "new_work" in ids
    assert recs.shortfall.get("cooldown") == 1


def test_stable_tiebreak_by_person_id():
    a = _cand("b", total=0.5)
    b = _cand("a", total=0.5)
    recs = select_daily_recommendations([a, b], settings=Settings())
    ordered = [r.person_id for r in recs.all]
    assert ordered == sorted(ordered)


def test_recommendation_entries_serialize_tiers():
    """F1：DailyRecommendationSet 序列化为可持久化的快照条目，字段与顺序完整。"""
    from finch.discovery.daily import _recommendation_entries

    cands = [_cand(f"p{i:03d}", total=0.9 - i * 0.01) for i in range(5)]
    recs = select_daily_recommendations(cands, settings=Settings())
    entries = _recommendation_entries(recs)
    assert len(entries) == recs.total
    assert {e.person_id for e in entries} == {r.person_id for r in recs.all}
    assert all(e.tier in {"priority", "summary", "browse"} for e in entries)
    assert all(e.peer_id == f"peer_{e.person_id}" for e in entries)


def test_select_home_items_limits_surprise():
    """D7：首页重点从 priority 取，意外发现（serendipity）受 surprise_limit 约束。"""
    from finch.peers.recommendations import select_home_items

    cands = [_cand(f"p{i:03d}", total=0.9 - i * 0.01) for i in range(6)]
    recs = select_daily_recommendations(cands, settings=Settings())
    # 无 hit_categories → 方向为 serendipity；surprise_limit=1 只保留 1 个意外发现。
    home = select_home_items(recs, home_limit=3, surprise_limit=1)
    assert len(home) == 1
    assert home[0].direction == "serendipity"

    home2 = select_home_items(recs, home_limit=3, surprise_limit=3)
    assert len(home2) == 3
    assert {r.person_id for r in home2} == {r.person_id for r in recs.priority[:3]}


def test_priority_prefers_practice_diversity():
    """D6：不同实践背景即使分低也优先进入重点。"""

    def cand(person_id: str, topics: list[str], total: float) -> PersonCandidate:
        peer = PeerProfile(
            id=f"peer_{person_id}",
            platform_identities=[
                PlatformIdentity(platform="x", author_id=f"u_{person_id}")  # type: ignore[arg-type]
            ],
            expertise_topics=topics,
        )
        return PersonCandidate(
            person_id=person_id,
            peer=peer,
            score=_score(total),
            artifact_ids=["a0", "a1"],
            platform="x",
        )

    cands = [
        cand("p1", ["agents"], 0.9),
        cand("p2", ["agents"], 0.89),
        cand("p3", ["agents"], 0.88),
        cand("p4", ["museum"], 0.87),
    ]
    recs = select_daily_recommendations(cands, settings=Settings())
    prio_ids = [r.person_id for r in recs.priority]
    assert "p4" in prio_ids  # 不同实践背景即使分低也进重点
    assert prio_ids.index("p4") < prio_ids.index("p2")  # 多样性优先于同背景高分
