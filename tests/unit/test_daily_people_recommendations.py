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
    hook: str = "",
    hit_categories: set[str] | None = None,
) -> PersonCandidate:
    return PersonCandidate(
        person_id=person_id,
        peer=_peer(person_id, platform=platform),
        score=_score(total),
        artifact_ids=[f"a{i}" for i in range(artifacts)],
        platform=platform,
        last_shown_at=last_shown_at,
        has_new_work=has_new_work,
        hook=hook,
        hit_categories=hit_categories or set(),
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

    # 2 明确相关（long_term → peer）+ 4 意外（serendipity，均带 hook）。
    cands = [
        _cand("r1", total=0.9, hit_categories={"long_term"}),
        _cand("r2", total=0.88, hit_categories={"long_term"}),
        _cand("s1", total=0.87, hook="把 X 领域方法用于 Y"),
        _cand("s2", total=0.86, hook="删掉某个自主决策步骤"),
        _cand("s3", total=0.85, hook="跨领域桥接"),
        _cand("s4", total=0.84, hook="另一条 hook"),
    ]
    recs = select_daily_recommendations(cands, settings=Settings())
    home = select_home_items(recs, home_limit=3, surprise_limit=1)
    # 2 相关 + 1 意外。
    assert len(home) == 3
    directions = [r.direction for r in home]
    assert directions.count("peer") == 2
    assert directions.count("serendipity") == 1
    assert all(r.hook for r in home if r.direction == "serendipity")

    home2 = select_home_items(recs, home_limit=3, surprise_limit=3)
    assert len(home2) == 3


def test_select_home_items_skips_surprise_without_hook():
    """D7：意外位必须有具体 hook，没有 hook 的 serendipity 不占意外位（宁缺毋滥）。"""
    from finch.peers.recommendations import select_home_items

    cands = [
        _cand("r1", total=0.9, hit_categories={"long_term"}),
        _cand("r2", total=0.88, hit_categories={"long_term"}),
        _cand("s_no_hook", total=0.87, hook=""),  # 意外但无具体 hook
    ]
    recs = select_daily_recommendations(cands, settings=Settings())
    home = select_home_items(recs, home_limit=3, surprise_limit=1)
    ids = [r.person_id for r in home]
    assert "s_no_hook" not in ids
    assert len(home) == 2  # 只有 2 相关，无空泛意外占位


def test_cross_domain_hook_prefers_bridge_evidence():
    from finch.discovery.daily import _cross_domain_hook
    from finch.peers.person import CreatorEvidence, CreatorEvidenceKind

    evs = [
        CreatorEvidence(
            evidence_id="e1",
            person_id="p",
            artifact_id="a0",
            kind=CreatorEvidenceKind.CREATION,
            claim="普通创作",
        ),
        CreatorEvidence(
            evidence_id="e2",
            person_id="p",
            artifact_id="a1",
            kind=CreatorEvidenceKind.CROSS_DOMAIN_BRIDGE,
            claim="把游戏化机制用于编程练习",
            support=["删掉了自动判题步骤"],
        ),
    ]
    assert _cross_domain_hook(evs) == "把游戏化机制用于编程练习：删掉了自动判题步骤"
    assert _cross_domain_hook(evs, "fallback") == "把游戏化机制用于编程练习：删掉了自动判题步骤"
    assert _cross_domain_hook([], "fallback") == "fallback"
    assert _cross_domain_hook([], "") == ""


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


def test_question_hit_gets_question_direction_and_rank_boost():
    """命中当前问题 → direction=question，并在排序时获得确定性加分。"""
    plain = _cand("plain", total=0.55, hit_categories={"long_term"})
    question = _cand("question", total=0.5, hit_categories={"question"})
    recs = select_daily_recommendations([plain, question], settings=Settings())
    ordered = [r.person_id for r in recs.all]
    # 0.5 + 0.1 > 0.55，问题命中者排在更前。
    assert ordered[0] == "question"
    by_id = {r.person_id: r for r in recs.all}
    assert by_id["question"].direction == "question"


def test_feedback_boost_reorders_candidates():
    """推荐反馈调节量改变排序，但不改变持久化的基础分。"""
    plain = _cand("plain", total=0.6)
    boosted = _cand("boosted", total=0.55)
    recs = select_daily_recommendations(
        [plain, boosted], settings=Settings(), feedback_boost={"boosted": 0.1}
    )
    ordered = [r.person_id for r in recs.all]
    assert ordered[0] == "boosted"
    assert recs.all[0].candidate.score.total == 0.55  # 基础分未被改写
