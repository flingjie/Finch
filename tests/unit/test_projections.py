"""投影生成：确定性只读聚合 → daily-context / pending-actions。"""

from datetime import UTC, datetime

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionProposal,
    InteractionStatus,
)
from finch.peers.models import PeerProfile, PlatformIdentity
from finch.projections import build_daily_context, build_pending_actions
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
