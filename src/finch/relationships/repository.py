"""关系领域文件仓储：镜像 ``products/repository.py`` 的读写模式，落新目录避免与旧数据冲突。"""

from __future__ import annotations

from pydantic import BaseModel

from finch.relationships.models import (
    Briefing,
    Contact,
    ImportedNote,
    NextStep,
    PersonProfile,
    TaskEvent,
)
from finch.storage.workspace import Workspace

_NS = "relationships"


def _write(ws: Workspace, name: str, key: str, model) -> None:
    ws.write_yaml(ws.dir(name) / f"{ws.safe_filename(key)}.yaml", model)


def _read[T: BaseModel](ws: Workspace, name: str, key: str, model_cls: type[T]) -> T | None:
    return ws.read_yaml(ws.dir(name) / f"{ws.safe_filename(key)}.yaml", model_cls)


def _list_all[T: BaseModel](ws: Workspace, name: str, model_cls: type[T]) -> list[T]:
    out: list[T] = []
    for path in sorted(ws.dir(name).glob("*.yaml")):
        obj = ws.read_yaml(path, model_cls)
        if obj is not None:
            out.append(obj)
    return out


class PersonRepository:
    """``var/relationships/people/<person_id>.yaml``（幂等 upsert）。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, person: PersonProfile) -> None:
        _write(self.ws, f"{_NS}/people", person.person_id, person)

    def get(self, person_id: str) -> PersonProfile | None:
        return _read(self.ws, f"{_NS}/people", person_id, PersonProfile)

    def list_all(self) -> list[PersonProfile]:
        return _list_all(self.ws, f"{_NS}/people", PersonProfile)


class TaskEventRepository:
    """``var/relationships/events/<event_id>.yaml``。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, event: TaskEvent) -> None:
        _write(self.ws, f"{_NS}/events", event.event_id, event)

    def get(self, event_id: str) -> TaskEvent | None:
        return _read(self.ws, f"{_NS}/events", event_id, TaskEvent)

    def list_all(self) -> list[TaskEvent]:
        return _list_all(self.ws, f"{_NS}/events", TaskEvent)

    def list_by_person(self, person_id: str) -> list[TaskEvent]:
        return [e for e in self.list_all() if e.person_id == person_id]


class ContactRepository:
    """``var/relationships/contacts/<contact_id>.yaml``。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, contact: Contact) -> None:
        _write(self.ws, f"{_NS}/contacts", contact.contact_id, contact)

    def get(self, contact_id: str) -> Contact | None:
        return _read(self.ws, f"{_NS}/contacts", contact_id, Contact)

    def list_all(self) -> list[Contact]:
        return _list_all(self.ws, f"{_NS}/contacts", Contact)

    def list_by_person(self, person_id: str) -> list[Contact]:
        return [c for c in self.list_all() if c.person_id == person_id]


class NextStepRepository:
    """``var/relationships/next/<next_id>.yaml``。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, step: NextStep) -> None:
        _write(self.ws, f"{_NS}/next", step.next_id, step)

    def get(self, next_id: str) -> NextStep | None:
        return _read(self.ws, f"{_NS}/next", next_id, NextStep)

    def list_all(self) -> list[NextStep]:
        return _list_all(self.ws, f"{_NS}/next", NextStep)

    def list_by_person(self, person_id: str) -> list[NextStep]:
        return [n for n in self.list_all() if n.person_id == person_id]


class BriefingRepository:
    """``var/relationships/briefings/<briefing_id>.yaml``。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, briefing: Briefing) -> None:
        _write(self.ws, f"{_NS}/briefings", briefing.briefing_id, briefing)

    def get(self, briefing_id: str) -> Briefing | None:
        return _read(self.ws, f"{_NS}/briefings", briefing_id, Briefing)

    def list_all(self) -> list[Briefing]:
        return _list_all(self.ws, f"{_NS}/briefings", Briefing)

    def latest(self) -> Briefing | None:
        items = sorted(self.list_all(), key=lambda b: b.created_at, reverse=True)
        return items[0] if items else None


class NoteRepository:
    """``var/relationships/notes/<note_id>.yaml``（手工导入的原始材料）。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, note: ImportedNote) -> None:
        _write(self.ws, f"{_NS}/notes", note.note_id, note)

    def get(self, note_id: str) -> ImportedNote | None:
        return _read(self.ws, f"{_NS}/notes", note_id, ImportedNote)

    def list_all(self) -> list[ImportedNote]:
        return _list_all(self.ws, f"{_NS}/notes", ImportedNote)
