"""D10 展示语义：三事件分离、幂等键、冷却版本。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from finch.peers.presentation import (
    CURRENT_SEMANTICS_VERSION,
    LEGACY_SEMANTICS_VERSION,
    PersonPresentationRecord,
    PersonPresentationRepository,
)
from finch.storage.workspace import Workspace


def _repo(ws: Workspace) -> PersonPresentationRepository:
    return PersonPresentationRepository(ws)


def test_record_shown_is_idempotent_within_snapshot(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = _repo(ws)

    first = repo.record_shown(
        person_id="p1", platform="x", surface="home", snapshot_id="snap_1"
    )
    second = repo.record_shown(
        person_id="p1", platform="x", surface="home", snapshot_id="snap_1"
    )

    assert first.id == second.id
    assert first.presented_at == second.presented_at
    # 重复打开同一快照不新增记录，冷却不被反复延长。
    assert len(repo.list_all()) == 1


def test_record_shown_new_snapshot_advances_last_shown(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = _repo(ws)

    t0 = datetime.now(UTC) - timedelta(days=3)
    repo.record_shown(
        person_id="p1", platform="x", surface="home", snapshot_id="snap_1", now=t0
    )
    repo.record_shown(
        person_id="p1", platform="x", surface="home", snapshot_id="snap_2"
    )

    assert repo.last_shown_at("p1") >= datetime.now(UTC) - timedelta(minutes=1)


def test_last_shown_ignores_legacy_records(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = _repo(ws)

    # 历史“生成即曝光”记录：语义版本 "1"，不得参与冷却。
    repo.save(
        PersonPresentationRecord(
            id="pp_legacy",
            person_id="p1",
            platform="x",
            presented_at=datetime.now(UTC) - timedelta(days=1),
            presentation_semantics_version=LEGACY_SEMANTICS_VERSION,
        )
    )
    assert repo.last_shown_at("p1") is None
    assert repo.recent_platforms() == set()


def test_selected_event_does_not_affect_cooldown(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = _repo(ws)

    repo.record_selected(person_id="p1", platform="x", surface="person")

    assert repo.last_shown_at("p1") is None
    # selected 也写入了新语义版本，便于追溯但不算展示。
    assert any(r.event == "selected" for r in repo.list_all())


def test_record_shown_stamps_current_semantics_version(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = _repo(ws)

    rec = repo.record_shown(person_id="p1", platform="x", surface="home", snapshot_id="s")
    assert rec.presentation_semantics_version == CURRENT_SEMANTICS_VERSION


def test_new_records_default_to_legacy_semantics(tmp_path):
    # 读取旧文件（缺 presentation_semantics_version）时默认视为旧语义，不参与冷却。
    rec = PersonPresentationRecord(id="pp_x", person_id="p1")
    assert rec.presentation_semantics_version == LEGACY_SEMANTICS_VERSION


def test_recent_platforms_only_new_presented(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = _repo(ws)

    repo.record_shown(person_id="p1", platform="x", surface="home", snapshot_id="s1")
    repo.record_selected(person_id="p2", platform="github", surface="person")

    assert repo.recent_platforms() == {"x"}
