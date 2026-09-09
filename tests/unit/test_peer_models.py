"""PeerProfile / PlatformIdentity / RelationshipStage 模型测试。"""

from finch.peers.models import (
    PeerProfile,
    PlatformIdentity,
    RelationshipStage,
)


def test_relationship_stage_values():
    assert RelationshipStage.DISCOVERED.value == "discovered"
    assert RelationshipStage.RELEVANT.value == "relevant"
    assert RelationshipStage.ENGAGED.value == "engaged"
    assert RelationshipStage.CONVERSING.value == "conversing"
    assert RelationshipStage.COLLABORATING.value == "collaborating"
    assert RelationshipStage.DORMANT.value == "dormant"


def test_peer_profile_defaults():
    profile = PeerProfile(
        id="peer_abc",
        platform_identities=[PlatformIdentity(platform="x", author_id="alice")],
    )
    assert profile.display_name == ""
    assert profile.relationship_stage is RelationshipStage.DISCOVERED
    assert profile.expertise_topics == []
    assert profile.current_interests == []
    assert profile.shared_topics == []
    assert profile.last_meaningful_interaction_at is None
    assert profile.source_refs == []


def test_peer_profile_json_roundtrip():
    profile = PeerProfile(
        id="peer_abc",
        platform_identities=[
            PlatformIdentity(
                platform="x", author_id="alice", username="Alice", url="https://x.com/alice"
            ),
        ],
        display_name="Alice",
        expertise_topics=["agent evals"],
        shared_topics=["production reliability"],
        why_relevant="writes concretely about production evals",
    )
    restored = PeerProfile.model_validate_json(profile.model_dump_json())
    assert restored == profile


def test_peer_profile_possible_next_actions_defaults_empty():
    profile = PeerProfile(
        id="peer_abc",
        platform_identities=[PlatformIdentity(platform="x", author_id="alice")],
    )
    assert profile.possible_next_actions == []
