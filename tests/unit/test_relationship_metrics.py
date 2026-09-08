"""关系质量指标（compute_relationship_metrics）单元测试。"""

from datetime import UTC, datetime, timedelta

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import DraftKind
from finch.conversations.models import ConversationThread
from finch.engagement.metrics import compute_relationship_metrics
from finch.engagement.models import FeedbackSnapshot, InteractionRecord
from finch.peers.models import PeerProfile, PlatformIdentity, RelationshipStage

_NOW = datetime(2026, 9, 8, tzinfo=UTC)


def _peer(stage=RelationshipStage.RELEVANT) -> PeerProfile:
    return PeerProfile(
        id="peer_abc",
        platform_identities=[PlatformIdentity(platform="x", author_id="alice")],
        relationship_stage=stage,
    )


def _record(peer_id="peer_abc", rid="rec_1") -> InteractionRecord:
    return InteractionRecord(
        id=rid, proposal_id="p1", peer_id=peer_id, platform="x",
        source_url="https://x.com/alice/status/1", occurred_at=_NOW,
    )


def _job(origin="conversation") -> ContentJob:
    return ContentJob(
        id="idea_1", source_card_ids=[], reader_problem="r",
        recommended_format=DraftKind.ORIGINAL, status=ContentJobStatus.PROPOSED,
        core_message="c", origin=origin,
    )


def _snapshot(meaningful=True) -> FeedbackSnapshot:
    return FeedbackSnapshot(
        id="s1", interaction_id="rec_1", meaningful=meaningful, captured_at=_NOW
    )


def test_all_zero_when_no_data():
    m = compute_relationship_metrics(
        peers=[], interactions=[], threads=[], snapshots=[], jobs=[], now=_NOW
    )
    assert m.meaningful_interactions == 0
    assert m.continued_conversations == 0
    assert m.repeat_peers == 0
    assert m.new_relevant_peers == 0
    assert m.ideas_from_conversations == 0
    assert m.collaboration_signals == 0
    assert m.stale_conversations == 0


def test_counts_relationship_signals():
    thread = ConversationThread(
        id="t1", peer_id="peer_abc", topic="agent evals",
        interaction_ids=["rec_1", "rec_2"],
        agreements=["replays help"],
        possible_experiments=["diff replays"],
        last_activity_at=_NOW - timedelta(days=1),
    )
    stale = ConversationThread(
        id="t2", peer_id="peer_abc", topic="t",
        open_questions=["how?"], last_activity_at=_NOW,
    )
    m = compute_relationship_metrics(
        peers=[_peer(), _peer(stage=RelationshipStage.DISCOVERED)],
        interactions=[_record(), _record(rid="rec_2"), _record(peer_id="peer_other", rid="rec_3")],
        threads=[thread, stale],
        snapshots=[_snapshot(), _snapshot(meaningful=False)],
        jobs=[_job(), _job(origin="user")],
        now=_NOW,
    )
    assert m.meaningful_interactions == 1
    assert m.continued_conversations == 1  # 只有 t1 有 >=2 互动
    assert m.repeat_peers == 1  # peer_abc 互动 2 次
    assert m.new_relevant_peers == 1  # 只有 RELEVANT 越过 DISCOVERED
    assert m.ideas_from_conversations == 1
    assert m.collaboration_signals == 1  # 只有 t1 有 agreements/experiments
    assert m.stale_conversations == 1  # 只有 t2 有未解问题
