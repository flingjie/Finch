"""关系评分（peer_value / relationship_value）单元测试。"""

from datetime import datetime

import pytest

from finch.conversations.models import ConversationThread
from finch.engagement.models import ExternalPost, InteractionRecord
from finch.engagement.peer_aggregation import PeerBundle, aggregate_by_peer
from finch.engagement.relationship import (
    PeerHistory,
    compute_peer_value,
    compute_relationship_value,
    rank_peers,
)


def _post(pid="p1", author_id="alice", content="", **overrides) -> ExternalPost:
    data = dict(
        id=pid,
        platform="x",
        url=f"https://x.com/{author_id}/status/{pid}",
        author_id=author_id,
        author_name="Alice",
        content=content or "How do you test agent reliability?",
        published_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    data.update(overrides)
    return ExternalPost(**data)


def _bundle(*posts: ExternalPost) -> PeerBundle:
    bundles = aggregate_by_peer(list(posts))
    return bundles[0]


def _record(peer_id="peer_abc") -> InteractionRecord:
    return InteractionRecord(
        id="rec_1",
        proposal_id="x:p1:draft_reply",
        peer_id=peer_id,
        platform="x",
        source_url="https://x.com/alice/status/1",
        occurred_at=datetime(2026, 9, 1, 12, 0, 0),
    )


def test_topic_overlap_reflects_interest_coverage():
    bundle = _bundle(
        _post(pid="p1", content="agent reliability in production is hard"),
    )
    value = compute_peer_value(bundle, interests=["agent reliability", "observability"])
    assert value.topic_overlap == pytest.approx(0.5)  # 只覆盖一个兴趣词


def test_practical_depth_counts_practical_signals():
    practical = _bundle(_post(pid="p1", content="we ran a benchmark and reproduced it"))
    fluffy = _bundle(_post(pid="p1", content="here are some thoughts"))
    assert compute_peer_value(practical, interests=[]).practical_depth == 1.0
    assert compute_peer_value(fluffy, interests=[]).practical_depth == 0.0


def test_contribution_space_requires_an_open_problem():
    problem = _bundle(_post(pid="p1", content="this keeps crashing, any workaround?"))
    announcement = _bundle(_post(pid="p1", content="we just launched a new version"))
    assert compute_peer_value(problem, interests=[]).contribution_space == 1.0
    assert compute_peer_value(announcement, interests=[]).contribution_space == 0.0


def test_repeated_low_quality_interactions_downweight():
    bundle = _bundle(_post(pid="p1", content="agent reliability is hard, any workaround?"))
    fresh = compute_peer_value(bundle, interests=["agent reliability"], history=PeerHistory())
    penalized = compute_peer_value(
        bundle,
        interests=["agent reliability"],
        history=PeerHistory(rejected_or_ignored=5),
    )
    assert penalized.repetition_penalty == 1.0
    assert penalized.total < fresh.total


def test_popular_promotional_peer_scores_low_and_excluded_from_top_n():
    promo = _bundle(_post(pid="p1", content="we raised a series b, shipping fast"))
    substantive = _bundle(
        _post(pid="p2", author_id="bob", content="agent reliability in practice: we ran a test")
    )
    # 仅热门但无可贡献空间/推广的同行 peer_value 为 0。
    promo_value = compute_peer_value(promo, interests=["agent reliability"])
    assert promo_value.promotion_risk == 1.0
    assert promo_value.contribution_space == 0.0
    assert promo_value.total == 0.0

    ranked = rank_peers(
        [promo, substantive], interests=["agent reliability"]
    )
    assert ranked[0][0].profile.platform_identities[0].author_id == "bob"


def test_total_is_clamped_to_unit_interval():
    bundle = _bundle(_post(pid="p1", content="we ran a test and reproduced the bug"))
    value = compute_peer_value(bundle, interests=[])
    assert 0.0 <= value.total <= 1.0


def test_continuity_potential_reflects_history():
    bundle = _bundle(_post(pid="p1", content="how do you test agent reliability?"))
    thread = ConversationThread(id="thread_1", peer_id="peer_abc", topic="agent reliability")
    value = compute_peer_value(
        bundle, interests=[], history=PeerHistory(threads=[thread])
    )
    assert value.continuity_potential == 1.0


def test_relationship_value_is_deterministic_from_peer_value():
    bundle = _bundle(_post(pid="p1", content="agent reliability is hard"))
    value = compute_peer_value(bundle, interests=["agent reliability"])
    rel = compute_relationship_value(value)
    assert rel == pytest.approx((value.topic_overlap + value.continuity_potential) / 2.0)
    assert 0.0 <= rel <= 1.0


def test_rank_peers_stable_on_tie():
    a = _bundle(_post(pid="p1", author_id="alice", content="same"))
    b = _bundle(_post(pid="p2", author_id="bob", content="same"))
    ranked = rank_peers([b, a], interests=[])
    # 同分按 peer id 稳定排序。
    assert ranked[0][0].profile.id <= ranked[1][0].profile.id
