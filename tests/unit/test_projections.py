"""投影生成：确定性只读聚合 → daily-context / pending-actions。"""

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.peers.models import PeerProfile, PlatformIdentity
from finch.projections import build_daily_context, build_pending_actions
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
    assert "pending_proposals" in daily


def test_build_pending_actions(tmp_path):
    ws = Workspace(tmp_path)
    pending = build_pending_actions(ws)
    assert set(pending) == {
        "approved_unexecuted_proposals",
        "ideas_awaiting_confirmation",
        "drafts_awaiting_review",
    }
