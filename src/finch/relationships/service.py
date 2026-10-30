"""关系领域确定性服务：幂等 upsert + 回溯 + 状态转换（无 LLM）。

不变式：草稿/模拟交流（``is_draft=True``）永不自动标记为已联系；只有显式
``mark_contact_sent`` 才把草稿翻转为实际交流。事件/交流/下一步的 ID 由内容派生，
重跑幂等。
"""

from __future__ import annotations

from datetime import datetime

from finch.relationships.ids import contact_id_for, event_id_for, next_id_for
from finch.relationships.models import (
    Contact,
    NextStep,
    NextStepStatus,
    PersonProfile,
    PersonTraceback,
    TaskEvent,
)
from finch.relationships.repository import (
    ContactRepository,
    NextStepRepository,
    PersonRepository,
    TaskEventRepository,
)


def _occurred_key(occurred_at: datetime | None) -> str:
    return occurred_at.isoformat() if occurred_at else ""


def _source_key(source_refs: list[str]) -> str:
    return ",".join(sorted(source_refs))


class RelationshipsService:
    """人物/事件/交流/下一步的确定性 CRUD + 回溯。"""

    def __init__(
        self,
        *,
        people: PersonRepository,
        events: TaskEventRepository,
        contacts: ContactRepository,
        next_steps: NextStepRepository,
    ) -> None:
        self.people = people
        self.events = events
        self.contacts = contacts
        self.next_steps = next_steps

    def upsert_person(self, person: PersonProfile) -> PersonProfile:
        self.people.upsert(person)
        return person

    def upsert_event(self, event: TaskEvent) -> TaskEvent:
        event = event.model_copy(
            update={
                "event_id": event_id_for(
                    event.person_id,
                    _occurred_key(event.occurred_at),
                    _source_key(event.source_refs),
                )
            }
        )
        self.events.upsert(event)
        return event

    def upsert_contact(self, contact: Contact) -> Contact:
        contact = contact.model_copy(
            update={
                "contact_id": contact_id_for(
                    contact.person_id,
                    _occurred_key(contact.occurred_at),
                    _source_key(contact.source_refs),
                )
            }
        )
        self.contacts.upsert(contact)
        return contact

    def upsert_next_step(self, step: NextStep) -> NextStep:
        step = step.model_copy(
            update={"next_id": next_id_for(step.person_id, step.suggestion)}
        )
        self.next_steps.upsert(step)
        return step

    def mark_contact_sent(self, contact_id: str) -> Contact | None:
        """显式把草稿翻转为实际交流；缺失返回 None。"""
        contact = self.contacts.get(contact_id)
        if contact is None:
            return None
        contact = contact.model_copy(update={"is_draft": False})
        self.contacts.upsert(contact)
        return contact

    def mark_next_step(self, next_id: str, status: NextStepStatus) -> NextStep | None:
        step = self.next_steps.get(next_id)
        if step is None:
            return None
        step = step.model_copy(update={"status": status})
        self.next_steps.upsert(step)
        return step

    def traceback(self, person_id: str) -> PersonTraceback | None:
        """人物 → 事件 → 交流 → 下一步，按时间回溯；缺失返回 None。"""
        person = self.people.get(person_id)
        if person is None:
            return None
        events = sorted(
            self.events.list_by_person(person_id),
            key=lambda e: e.occurred_at or e.created_at,
        )
        contacts = sorted(
            self.contacts.list_by_person(person_id), key=lambda c: c.occurred_at
        )
        steps = sorted(
            self.next_steps.list_by_person(person_id), key=lambda s: s.created_at
        )
        return PersonTraceback(person=person, events=events, contacts=contacts, next_steps=steps)
