"""PeerService 归一化测试。"""

from finch.peers.models import PeerProfile, PlatformIdentity, RelationshipStage
from finch.peers.service import PeerService, peer_id_for


def test_peer_id_for_is_deterministic():
    assert peer_id_for("x", "alice") == peer_id_for("x", "alice")
    assert peer_id_for("x", "alice") != peer_id_for("x", "bob")
    assert peer_id_for("x", "alice") != peer_id_for("reddit", "alice")


def test_same_author_across_posts_produces_one_identity():
    svc = PeerService()
    profile = svc.from_author(platform="x", author_id="alice", username="Alice")

    # 同一作者的第二条帖子，再归一化一次：仍只有一个身份。
    merged = svc.merge_identity(
        profile, PlatformIdentity(platform="x", author_id="alice", username="Alice")
    )
    assert len(merged.platform_identities) == 1

    merged_again = svc.merge_identity(
        merged, PlatformIdentity(platform="x", author_id="alice")
    )
    assert len(merged_again.platform_identities) == 1


def test_distinct_platform_identity_is_added():
    svc = PeerService()
    profile = svc.from_author(platform="x", author_id="alice")
    merged = svc.merge_identity(
        profile, PlatformIdentity(platform="reddit", author_id="alice_reddit")
    )
    assert len(merged.platform_identities) == 2
    assert {i.platform for i in merged.platform_identities} == {"x", "reddit"}


def test_from_author_builds_discovered_profile():
    svc = PeerService()
    profile = svc.from_author(platform="x", author_id="alice", username="Alice")
    assert profile.id == peer_id_for("x", "alice")
    assert profile.display_name == "Alice"
    assert profile.relationship_stage.value == "discovered"


def test_merge_does_not_mutate_original():
    svc = PeerService()
    profile = svc.from_author(platform="x", author_id="alice")
    before = len(profile.platform_identities)
    svc.merge_identity(
        profile, PlatformIdentity(platform="reddit", author_id="alice_reddit")
    )
    assert len(profile.platform_identities) == before  # 原对象不变


def test_merge_discovered_returns_skeleton_when_no_existing():
    svc = PeerService()
    discovered = svc.from_author(platform="x", author_id="alice", username="Alice")
    assert svc.merge_discovered(None, discovered) is discovered


def test_merge_discovered_preserves_accumulated_fields():
    svc = PeerService()
    existing = PeerProfile(
        id=peer_id_for("x", "alice"),
        platform_identities=[PlatformIdentity(platform="x", author_id="alice")],
        display_name="Alice",
        shared_topics=["agent evals"],
        why_relevant="writes concretely about flaky evals",
        relationship_stage=RelationshipStage.CONVERSING,
        next_context="ask about the replay harness",
        possible_next_actions=["share my replay diff"],
    )
    discovered = svc.from_author(platform="x", author_id="alice", username="Alice")
    merged = svc.merge_discovered(existing, discovered)

    # 积累的关系字段不丢，新身份幂等并入（不重复）。
    assert merged.relationship_stage is RelationshipStage.CONVERSING
    assert merged.why_relevant == "writes concretely about flaky evals"
    assert merged.next_context == "ask about the replay harness"
    assert merged.shared_topics == ["agent evals"]
    assert merged.possible_next_actions == ["share my replay diff"]
    assert len(merged.platform_identities) == 1


def test_merge_discovered_does_not_mutate_existing():
    svc = PeerService()
    existing = PeerProfile(
        id=peer_id_for("x", "alice"),
        platform_identities=[PlatformIdentity(platform="x", author_id="alice")],
        why_relevant="keep me",
    )
    discovered = svc.from_author(platform="x", author_id="alice")
    svc.merge_discovered(existing, discovered)
    assert existing.why_relevant == "keep me"  # 原对象不变
