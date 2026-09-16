"""Unit tests for Person candidate pool."""

from __future__ import annotations

from finch.discovery.candidate_pool import (
    DUPLICATE_PERSON,
    NO_ARTIFACTS,
    OVER_CAP,
    build_pool,
    match_interests,
)
from finch.peers.models import PeerProfile, PlatformIdentity
from finch.peers.scoring import PersonScoreBreakdown
from finch.peers.shortlist import ShortlistCandidate
from finch.settings import InterestsSettings, Settings


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


def _peer(person_id: str, *, text: str = "") -> PeerProfile:
    return PeerProfile(
        id=f"peer_{person_id}",
        platform_identities=[PlatformIdentity(platform="x", author_id=f"u_{person_id}")],
        why_relevant=text,
    )


def _cand(
    person_id: str,
    *,
    total: float = 0.5,
    artifacts: list[str] | None = None,
    text: str = "",
) -> ShortlistCandidate:
    return ShortlistCandidate(
        peer=_peer(person_id, text=text),
        person_id=person_id,
        score=_score(total),
        artifact_ids=artifacts if artifacts is not None else ["a1", "a2"],
    )


def _settings() -> Settings:
    return Settings(
        interests=InterestsSettings(
            long_term_interests=["agent reliability"],
            explore_directions=["durable execution"],
            adjacent_queries=["distributed systems"],
        )
    )


def test_match_interests_hits_categories():
    peer = _peer("p1", text="working on agent reliability and durable execution")
    hits = match_interests(peer, settings=_settings())
    labels = {h.label for h in hits}
    assert "agent reliability" in labels
    assert "durable execution" in labels
    assert {h.category for h in hits} == {"long_term", "explore"}


def test_build_pool_dedupes_duplicate_person():
    pool = build_pool(
        [
            _cand("p1", total=0.9),
            _cand("p1", total=0.4),  # duplicate person, lower score
        ],
        settings=_settings(),
    )
    assert len(pool.candidates) == 1
    assert pool.candidates[0].score.total == 0.9
    assert pool.rejected.get(DUPLICATE_PERSON) == 1


def test_build_pool_caps_at_max_size():
    cands = [_cand(f"p{i}", total=1.0 - i * 0.1) for i in range(5)]
    pool = build_pool(cands, settings=_settings(), max_size=3)
    assert len(pool.candidates) == 3
    assert pool.rejected.get(OVER_CAP) == 2


def test_build_pool_rejects_no_artifacts():
    pool = build_pool([_cand("p1", artifacts=[])], settings=_settings())
    assert pool.candidates == []
    assert pool.rejected.get(NO_ARTIFACTS) == 1


def test_build_pool_stable_tiebreak():
    # 同分按 person_id 稳定排序，保证重放确定性。
    a = _cand("b", total=0.5)
    b = _cand("a", total=0.5)
    pool = build_pool([a, b], settings=_settings())
    assert [c.person_id for c in pool.candidates] == ["a", "b"]
