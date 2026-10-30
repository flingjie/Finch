"""关系领域周度简报单测：确定性门禁 + 幂等缓存。"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from finch.relationships.briefing import (
    BriefingDraft,
    BriefingEntryDraft,
    build_briefing,
    generate_briefing,
)
from finch.relationships.models import BriefingChannel, PersonProfile
from finch.relationships.repository import BriefingRepository, PersonRepository
from finch.storage.workspace import Workspace


def _now() -> datetime:
    return datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def _person(person_id: str, *, refs: list[str] | None = None) -> PersonProfile:
    return PersonProfile(
        person_id=person_id, display_name="Alice", source_refs=refs or ["twitter:post:1"]
    )


def _build(draft: BriefingDraft, *, persons=None, limit: int = 3) -> object:
    return build_briefing(
        draft,
        persons=persons or [_person("person_a")],
        events=[],
        contacts=[],
        next_steps=[],
        fingerprint="fp",
        limit=limit,
        now=_now(),
    )


def test_build_briefing_drops_empty_entry_point_and_unknown_person():
    draft = BriefingDraft(
        entries=[
            BriefingEntryDraft(person_id="person_a", entry_point=""),
            BriefingEntryDraft(person_id="person_zzz", entry_point="x"),
            BriefingEntryDraft(person_id="person_a", entry_point="reproduce the bug"),
        ]
    )
    b = _build(draft)
    assert len(b.entries) == 1
    assert b.entries[0].entry_point == "reproduce the bug"


def test_build_briefing_drops_unevidenced_refs_and_forces_contribution_empty():
    draft = BriefingDraft(
        entries=[
            BriefingEntryDraft(
                person_id="person_a",
                entry_point="x",
                contribution="我也遇到过",
                basis_refs=["other:ref"],
            )
        ]
    )
    b = _build(draft)
    e = b.entries[0]
    assert e.basis_refs == []
    assert e.contribution == ""
    assert any("未验证" in u for u in e.unknowns)


def test_build_briefing_keeps_valid_refs_and_contribution():
    draft = BriefingDraft(
        entries=[
            BriefingEntryDraft(
                person_id="person_a",
                entry_point="x",
                contribution="repro test",
                basis_refs=["twitter:post:1"],
            )
        ]
    )
    b = _build(draft)
    e = b.entries[0]
    assert e.basis_refs == ["twitter:post:1"]
    assert e.contribution == "repro test"


def test_build_briefing_coerces_channel():
    draft = BriefingDraft(
        entries=[BriefingEntryDraft(person_id="person_a", entry_point="x", channel="bogus")]
    )
    b = _build(draft)
    assert b.entries[0].channel == BriefingChannel.OBSERVE


def test_build_briefing_caps_to_limit_preserving_order():
    persons = [_person(f"person_{c}") for c in "abcde"]
    draft = BriefingDraft(
        entries=[BriefingEntryDraft(person_id=p.person_id, entry_point="x") for p in persons]
    )
    b = _build(draft, persons=persons, limit=3)
    assert len(b.entries) == 3
    assert [e.person_id for e in b.entries] == ["person_a", "person_b", "person_c"]


def test_build_briefing_insufficient_when_no_entries():
    b = _build(BriefingDraft(entries=[]))
    assert b.insufficient is True


class _BoomRunner:
    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        raise AssertionError("should not be called when idempotent")


def test_generate_briefing_reuses_cached(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = BriefingRepository(ws)
    person = _person("person_a")
    PersonRepository(ws).upsert(person)

    class _Runner:
        def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
            assert output_model is BriefingDraft
            return BriefingDraft(
                entries=[BriefingEntryDraft(person_id="person_a", entry_point="x")]
            )

    kwargs = dict(
        people=[person], events=[], contacts=[], next_steps=[], repository=repo, limit=3
    )
    first = generate_briefing(_Runner(), **kwargs)
    assert first is not None
    second = generate_briefing(_BoomRunner(), **kwargs)
    assert second is not None
    assert second.briefing_id == first.briefing_id


def test_generate_briefing_no_people_returns_none(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = BriefingRepository(ws)
    out = generate_briefing(
        _BoomRunner(), people=[], events=[], contacts=[], next_steps=[], repository=repo, limit=3
    )
    assert out is None
