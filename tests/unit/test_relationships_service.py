"""关系领域服务单测：幂等 upsert + 状态转换 + 回溯 + 草稿≠实际 不变量。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.relationships.ids import contact_id_for, event_id_for
from finch.relationships.models import (
    Contact,
    NextStep,
    NextStepStatus,
    PersonProfile,
    TaskEvent,
)
from finch.relationships.repository import (
    ContactRepository,
    NextStepRepository,
    PersonRepository,
    TaskEventRepository,
)
from finch.relationships.service import RelationshipsService
from finch.storage.workspace import Workspace


def _service(ws: Workspace) -> RelationshipsService:
    return RelationshipsService(
        people=PersonRepository(ws),
        events=TaskEventRepository(ws),
        contacts=ContactRepository(ws),
        next_steps=NextStepRepository(ws),
    )


def _occurred() -> datetime:
    return datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def test_upsert_event_computes_deterministic_id_and_is_idempotent(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = _service(ws)
    e = TaskEvent(person_id="person_a", occurred_at=_occurred(), source_refs=["twitter:post:1"])
    out = svc.upsert_event(e)
    assert out.event_id == event_id_for("person_a", _occurred().isoformat(), "twitter:post:1")
    # 重跑同一事件 → 同 id，不产生第二个文件。
    svc.upsert_event(e)
    assert [ev.event_id for ev in ws_read(svc.events)] == [out.event_id]


def ws_read(repo):
    return repo.list_all()


def test_upsert_contact_preserves_draft_flag(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = _service(ws)
    c = Contact(person_id="person_a", occurred_at=_occurred(), is_draft=True)
    out = svc.upsert_contact(c)
    assert out.is_draft is True  # 生成草稿永不自动标记已联系
    assert out.contact_id == contact_id_for("person_a", _occurred().isoformat(), "")


def test_mark_contact_sent_flips_draft_to_actual(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = _service(ws)
    out = svc.upsert_contact(Contact(person_id="person_a", occurred_at=_occurred(), is_draft=True))
    updated = svc.mark_contact_sent(out.contact_id)
    assert updated is not None
    assert updated.is_draft is False
    # 只有作者显式标记已发送才会翻转。
    assert svc.contacts.get(out.contact_id).is_draft is False


def test_mark_contact_sent_missing_returns_none(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    assert _service(ws).mark_contact_sent("contact_nope") is None


def test_mark_next_step_changes_status(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = _service(ws)
    n = svc.upsert_next_step(NextStep(person_id="person_a", suggestion="观察更新"))
    updated = svc.mark_next_step(n.next_id, NextStepStatus.PAUSED)
    assert updated is not None
    assert updated.status == NextStepStatus.PAUSED
    assert svc.mark_next_step("next_nope", NextStepStatus.DONE) is None


def test_traceback_orders_and_distinguishes_draft(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = _service(ws)
    svc.people.upsert(PersonProfile(person_id="person_a", display_name="Alice"))
    svc.upsert_event(
        TaskEvent(
            person_id="person_a",
            occurred_at=datetime(2026, 10, 1, tzinfo=UTC),
            task="审 agent 输出",
        )
    )
    draft = svc.upsert_contact(
        Contact(
            person_id="person_a",
            occurred_at=datetime(2026, 10, 2, tzinfo=UTC),
            is_draft=True,
        )
    )
    actual = svc.upsert_contact(
        Contact(
            person_id="person_a",
            occurred_at=datetime(2026, 10, 3, tzinfo=UTC),
            is_draft=False,
        )
    )
    tb = svc.traceback("person_a")
    assert tb is not None
    assert tb.person.display_name == "Alice"
    assert [e.task for e in tb.events] == ["审 agent 输出"]
    # 草稿与实际的交流记录都保留，但可区分。
    assert {c.contact_id for c in tb.contacts} == {draft.contact_id, actual.contact_id}
    by_id = {c.contact_id: c for c in tb.contacts}
    assert by_id[draft.contact_id].is_draft is True
    assert by_id[actual.contact_id].is_draft is False


def test_traceback_missing_person_returns_none(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    assert _service(ws).traceback("person_nope") is None
