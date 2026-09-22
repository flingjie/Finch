"""Unit tests for the community-scout domain (models/repository/service) + week_label."""

from __future__ import annotations

from datetime import UTC, datetime

from finch.communities.models import (
    CommunityProfile,
    CommunityResult,
    community_id_for,
)
from finch.communities.service import CommunityService, week_label
from finch.settings import Settings
from finch.storage.workspace import Workspace


def test_week_label_iso():
    assert week_label(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-W01"
    # 跨年周一（2025-12-29）仍属 2026-W01，不落在 2025-W53。
    assert week_label(datetime(2025, 12, 29, tzinfo=UTC)) == "2026-W01"
    assert week_label(datetime(2026, 12, 31, tzinfo=UTC)) == "2026-W53"
    assert week_label(datetime(2025, 1, 1, tzinfo=UTC)) == "2025-W01"


def test_community_id_is_content_addressed():
    a = community_id_for("Temporal Community")
    b = community_id_for("Temporal Community")
    c = community_id_for("Temporal Community ")
    d = community_id_for("Different Community")
    assert a == b == c
    assert a != d


def _profile(name: str = "Temporal Community") -> CommunityProfile:
    return CommunityProfile(name=name, platforms=["GitHub Discussions"], fit_score=86)


def test_save_derives_id_and_appends_candidate(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    saved = svc.save(_profile())
    assert saved.id.startswith("comm_")
    # 追加日志：同 name 再次保存 → 同 id，追加一条（不覆盖）。
    again = svc.save(_profile())
    assert again.id == saved.id
    assert len(svc.list_candidates()) == 2


def test_inspect_hit_and_miss(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    saved = svc.save(_profile())
    assert svc.inspect(saved.id) is not None
    assert svc.inspect("comm_missing") is None


def test_record_feedback_appends_and_latest(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    saved = svc.save(_profile())
    svc.record_feedback(saved.id, CommunityResult.JOINED, note="申请了")
    svc.record_feedback(saved.id, CommunityResult.INTERACTED)
    fb = svc.list_feedback()
    assert [f.result for f in fb] == [CommunityResult.JOINED, CommunityResult.INTERACTED]
    assert svc.repo.latest_feedback(saved.id).result == CommunityResult.INTERACTED


def test_snapshot_context_roundtrips(tmp_path):
    settings = Settings(paths={"var_dir": tmp_path})  # type: ignore[arg-type]
    settings.paths.var_dir = tmp_path
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = CommunityService(ws)
    ctx = svc.snapshot_context(settings, ws)
    svc.repo.write_context(ctx)
    back = svc.repo.read_context()
    assert back is not None
    assert back.week == ctx.week
    assert back.interests == list(settings.interests.long_term_interests)
