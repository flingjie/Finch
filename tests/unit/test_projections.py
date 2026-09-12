"""投影生成：确定性只读聚合 → daily-context / pending-actions / today-focus。"""

from datetime import UTC, datetime, timedelta

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.conversations.models import ConversationThread
from finch.engagement.flow import RankedPeer
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionProposal,
    InteractionStatus,
)
from finch.engagement.relationship import PeerValue
from finch.peers.models import PeerProfile, PlatformIdentity
from finch.projections import build_daily_context, build_pending_actions, build_today_focus
from finch.storage.repositories import ContentJobRepository, InteractionRepository, PeerRepository
from finch.storage.workspace import Workspace


def test_build_daily_context(tmp_path):
    ws = Workspace(tmp_path)
    PeerRepository(ws).upsert(
        PeerProfile(id="p1", platform_identities=[PlatformIdentity(platform="x", author_id="a")])
    )
    ContentJobRepository(ws).upsert_job(
        ContentJob(
            id="idea_abc", source_card_ids=[], reader_problem="r",
            recommended_format="short_post", status=ContentJobStatus.PROPOSED,
        )
    )
    daily = build_daily_context(ws)
    assert [p["id"] for p in daily["peers"]] == ["p1"]
    assert [j["id"] for j in daily["ideas_awaiting_confirmation"]] == ["idea_abc"]
    assert "pending_proposals" in daily


def test_build_pending_actions(tmp_path):
    ws = Workspace(tmp_path)
    pending = build_pending_actions(ws)
    assert set(pending) == {
        "approved_unexecuted_proposals",
        "ideas_awaiting_confirmation",
        "drafts_awaiting_review",
    }


def test_daily_context_excludes_non_proposed_ideas(tmp_path):
    ws = Workspace(tmp_path)
    ContentJobRepository(ws).upsert_job(
        ContentJob(
            id="idea_confirmed", source_card_ids=[], reader_problem="r",
            recommended_format="short_post", status=ContentJobStatus.CONFIRMED,
        )
    )
    assert build_daily_context(ws)["ideas_awaiting_confirmation"] == []


def test_pending_actions_surfaces_approved_proposal(tmp_path):
    ws = Workspace(tmp_path)
    post = ExternalPost(
        id="post1", platform="x", url="https://x.com/u/1", author_id="a", author_name="A",
        content="hi", published_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    score = ConversationScore(
        relevance=0.5, novelty=0.5, discussability=0.5, practical_evidence=0.5,
        relationship_value=0.5, total=0.5, reasons=[],
    )
    cand = InteractionProposal(
        id="c1", post=post, score=score, action=InteractionAction.DRAFT_REPLY,
        approval_required=True, status=InteractionStatus.APPROVED,
    )
    InteractionRepository(ws).upsert(cand, run_id="run1")
    pending = build_pending_actions(ws)
    assert [p["id"] for p in pending["approved_unexecuted_proposals"]] == ["c1"]


def _peer_value(total):
    return PeerValue(
        topic_overlap=0.5, practical_depth=0.5, contribution_space=0.5,
        continuity_potential=0.5, repetition_penalty=0.0, promotion_risk=0.0,
        total=total, reasons=[],
    )


def test_build_today_focus_ranks_and_truncates():
    now = datetime(2026, 9, 9, tzinfo=UTC)
    old = ConversationThread(
        id="thread_old", peer_id="p", topic="old",
        open_questions=["q"], last_activity_at=now - timedelta(days=30),
    )
    recent = ConversationThread(
        id="thread_recent", peer_id="p", topic="recent", last_activity_at=now,
    )
    never = ConversationThread(id="thread_never", peer_id="p", topic="never")

    p1 = RankedPeer(profile=PeerProfile(id="p1", platform_identities=[]), value=_peer_value(0.9))
    p2 = RankedPeer(profile=PeerProfile(id="p2", platform_identities=[]), value=_peer_value(0.5))
    p3 = RankedPeer(profile=PeerProfile(id="p3", platform_identities=[]), value=_peer_value(0.7))
    p4 = RankedPeer(profile=PeerProfile(id="p4", platform_identities=[]), value=_peer_value(0.1))

    focus = build_today_focus(
        peers=[p1, p2, p3, p4],
        contributions=[],
        threads=[recent, old, never],
        ideas=[],
        now=now,
    )
    assert [t.id for t in focus["conversations"]["items"]] == ["thread_never", "thread_old"]
    assert focus["conversations"]["total"] == 3
    assert [rp.profile.id for rp in focus["peers"]["items"]] == ["p1", "p3", "p2", "p4"]
    assert focus["peers"]["total"] == 4
    assert focus["opportunities"]["items"] == []
