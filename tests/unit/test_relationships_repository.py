"""关系领域仓储单测：读写 roundtrip + list_by_person + latest。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.relationships.models import (
    Briefing,
    Contact,
    ImportedNote,
    NextStep,
    PersonProfile,
    TaskEvent,
)
from finch.relationships.repository import (
    BriefingRepository,
    ContactRepository,
    NextStepRepository,
    NoteRepository,
    PersonRepository,
    TaskEventRepository,
)
from finch.storage.workspace import Workspace


def _person(person_id: str) -> PersonProfile:
    return PersonProfile(person_id=person_id, display_name="Alice")


def _event(event_id: str, person_id: str) -> TaskEvent:
    return TaskEvent(event_id=event_id, person_id=person_id, task="审计 agent 输出")


def test_person_repository_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = PersonRepository(ws)
    repo.upsert(_person("person_a"))
    assert repo.get("person_a").display_name == "Alice"
    assert [p.person_id for p in repo.list_all()] == ["person_a"]


def test_task_event_repository_list_by_person(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = TaskEventRepository(ws)
    repo.upsert(_event("evt_1", "person_a"))
    repo.upsert(_event("evt_2", "person_b"))
    repo.upsert(_event("evt_3", "person_a"))
    assert {e.event_id for e in repo.list_by_person("person_a")} == {"evt_1", "evt_3"}


def test_contact_repository_list_by_person(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = ContactRepository(ws)
    repo.upsert(
        Contact(contact_id="contact_1", person_id="person_a", occurred_at=datetime.now(UTC))
    )
    repo.upsert(
        Contact(contact_id="contact_2", person_id="person_b", occurred_at=datetime.now(UTC))
    )
    assert [c.contact_id for c in repo.list_by_person("person_a")] == ["contact_1"]


def test_next_step_repository_list_by_person(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = NextStepRepository(ws)
    repo.upsert(NextStep(next_id="next_1", person_id="person_a", suggestion="观察更新"))
    repo.upsert(NextStep(next_id="next_2", person_id="person_b", suggestion="发反馈"))
    assert [n.next_id for n in repo.list_by_person("person_a")] == ["next_1"]


def test_briefing_repository_latest(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = BriefingRepository(ws)
    repo.upsert(
        Briefing(
            briefing_id="brief_1",
            created_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    )
    repo.upsert(
        Briefing(
            briefing_id="brief_2",
            created_at=datetime(2026, 10, 2, tzinfo=UTC),
        )
    )
    assert repo.latest().briefing_id == "brief_2"


def test_note_repository_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = NoteRepository(ws)
    repo.upsert(ImportedNote(note_id="note_1", kind="summary", text="对方约一周后反馈"))
    assert repo.get("note_1").text == "对方约一周后反馈"
