"""关系领域模型 + ID 单测：确定性、默认值、枚举。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.relationships.ids import (
    briefing_id_for,
    contact_id_for,
    event_id_for,
    next_id_for,
    note_id_for,
    person_id_for,
)
from finch.relationships.models import (
    Attribution,
    Briefing,
    BriefingChannel,
    CommitmentStatus,
    Contact,
    ContactDirection,
    NextStep,
    NextStepStatus,
    PersonProfile,
    TaskEvent,
)


def test_person_id_deterministic_and_prefixed():
    assert person_id_for("github.com/alice") == person_id_for("github.com/alice")
    assert person_id_for("github.com/alice").startswith("person_")
    assert person_id_for("github.com/alice") != person_id_for("github.com/bob")


def test_record_ids_deterministic():
    assert event_id_for("person_a", "2026-10-30T00:00:00+00:00", "twitter:post:1").startswith(
        "evt_"
    )
    assert contact_id_for("person_a", "2026-10-30T00:00:00+00:00", "").startswith("contact_")
    assert next_id_for("person_a", "观察更新").startswith("next_")
    assert briefing_id_for("fp", "2026-10-30T00:00:00+00:00").startswith("brief_")
    assert note_id_for("https://x.com/a/1", "").startswith("note_")


def test_person_profile_defaults():
    p = PersonProfile(person_id="person_x")
    assert p.display_name == ""
    assert p.role == ""
    assert p.current_focus == ""
    assert p.handles == []
    assert p.source_refs == []


def test_task_event_missing_fields_left_empty():
    e = TaskEvent(event_id="evt_x", person_id="person_x")
    assert e.task == "" and e.expectation == "" and e.trust_boundary == ""
    assert e.problem == "" and e.workaround == "" and e.cognitive_change == ""
    assert e.author_contribution == ""


def test_contact_draft_defaults_false():
    c = Contact(contact_id="contact_x", person_id="person_x", occurred_at=datetime.now(UTC))
    assert c.is_draft is False
    assert c.direction == ContactDirection.OUTBOUND


def test_next_step_default_status_proposed():
    n = NextStep(next_id="next_x", person_id="person_x")
    assert n.status == NextStepStatus.PROPOSED


def test_briefing_defaults():
    b = Briefing(briefing_id="brief_x")
    assert b.entries == []
    assert b.insufficient is False


def test_enum_values():
    assert Attribution.RAW.value == "raw"
    assert Attribution.SYSTEM_INFERENCE.value == "system_inference"
    assert BriefingChannel.OBSERVE.value == "observe"
    assert CommitmentStatus.OPEN.value == "open"
