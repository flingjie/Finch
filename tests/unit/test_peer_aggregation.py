"""同行聚合单元测试。"""

from datetime import datetime

from finch.engagement.models import ExternalPost
from finch.engagement.peer_aggregation import PeerBundle, aggregate_by_peer


def _post(
    pid="p1", platform="x", author_id="alice", author_name="Alice", **overrides
) -> ExternalPost:
    data = dict(
        id=pid,
        platform=platform,
        url=f"https://x.com/{author_id}/status/{pid}",
        author_id=author_id,
        author_name=author_name,
        content="How do you test agent reliability in production?",
        published_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    data.update(overrides)
    return ExternalPost(**data)


def test_empty_posts_yield_no_bundles():
    assert aggregate_by_peer([]) == []


def test_same_author_across_posts_aggregates_into_one_bundle():
    posts = [
        _post(pid="p1", author_id="alice"),
        _post(pid="p2", author_id="alice"),
        _post(pid="p3", author_id="bob", author_name="Bob"),
    ]
    bundles = aggregate_by_peer(posts)
    assert len(bundles) == 2
    by_id = {b.profile.platform_identities[0].author_id: b for b in bundles}
    assert [p.id for p in by_id["alice"].posts] == ["p1", "p2"]
    assert [p.id for p in by_id["bob"].posts] == ["p3"]


def test_same_author_id_on_different_platforms_are_distinct_peers():
    posts = [
        _post(pid="p1", platform="x", author_id="alice"),
        _post(pid="p2", platform="reddit", author_id="alice"),
    ]
    bundles = aggregate_by_peer(posts)
    # 同一 author_id 但不同 platform 是不同的同行（id 派生含 platform）。
    assert len(bundles) == 2


def test_bundle_profile_is_peer_profile_with_stable_id():
    posts = [_post(pid="p1", author_id="alice")]
    bundle = aggregate_by_peer(posts)[0]
    assert isinstance(bundle, PeerBundle)
    identity = bundle.profile.platform_identities[0]
    assert identity.platform == "x"
    assert identity.author_id == "alice"
    assert bundle.profile.display_name == "Alice"
    assert identity.url == "https://x.com/Alice"
    assert bundle.profile.source_refs == ["https://x.com/alice/status/p1"]


def test_aggregate_collects_post_urls_as_source_refs():
    posts = [
        _post(pid="p1", author_id="alice"),
        _post(pid="p2", author_id="alice"),
    ]
    bundle = aggregate_by_peer(posts)[0]
    assert bundle.profile.source_refs == [
        "https://x.com/alice/status/p1",
        "https://x.com/alice/status/p2",
    ]
