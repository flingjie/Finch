"""投影生成：确定性只读聚合 → daily-context / pending-actions / today-focus。"""

from datetime import UTC, datetime, timedelta

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.conversations.models import ConversationThread
from finch.peers.models import PeerProfile, PlatformIdentity
from finch.projections import build_daily_context, build_pending_actions, build_today_focus
from finch.storage.repositories import ContentJobRepository, PeerRepository
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


def test_build_pending_actions(tmp_path):
    ws = Workspace(tmp_path)
    pending = build_pending_actions(ws)
    assert set(pending) == {
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

    focus = build_today_focus(
        threads=[recent, old, never],
        ideas=[],
        now=now,
    )
    assert [t.id for t in focus["conversations"]["items"]] == ["thread_never", "thread_old"]
    assert focus["conversations"]["total"] == 3
