"""Unit tests for conversation observation notes (Value Discovery v2)."""

from datetime import UTC, datetime, timedelta

import pytest

from finch.conversations.models import ConversationThread, ObservationKind, ThreadNote
from finch.conversations.service import (
    ConversationService,
    ConversationServiceError,
    active_observation_notes,
    note_id_for,
)


def _thread() -> ConversationThread:
    return ConversationThread(
        id="thread_test",
        peer_id="peer_1",
        topic="tool-a",
        interaction_ids=["rec_1"],
        revision=1,
    )


def test_note_id_stable():
    a = note_id_for("rec_1", ObservationKind.USAGE_FEEDBACK, "ran with help")
    b = note_id_for("rec_1", ObservationKind.USAGE_FEEDBACK, "ran with help")
    assert a == b


def test_polite_interest_cannot_be_usage_success():
    svc = ConversationService()
    with pytest.raises(ConversationServiceError, match="polite interest"):
        svc.build_observation_note(
            kind=ObservationKind.USAGE_FEEDBACK,
            text="很有趣，有空看看",
            source_ref="rec_1",
        )


def test_assisted_success_usage_feedback_allowed():
    svc = ConversationService()
    note = svc.build_observation_note(
        kind=ObservationKind.USAGE_FEEDBACK,
        text="跑通了，但你得帮我配环境",
        source_ref="rec_1",
        tool_ref="tool-a",
    )
    assert note.kind is ObservationKind.USAGE_FEEDBACK
    assert note.tool_ref == "tool-a"


def test_upsert_idempotent_same_note():
    svc = ConversationService()
    thread = _thread()
    note = svc.build_observation_note(
        kind="usage_feedback",
        text="跑通了，需要帮忙配置",
        source_ref="rec_1",
    )
    t1 = svc.upsert_observation_note(
        thread, note, expected_revision=1, known_interaction_ids={"rec_1"}
    )
    assert t1.revision == 2
    t2 = svc.upsert_observation_note(
        t1, note, expected_revision=2, known_interaction_ids={"rec_1"}
    )
    assert t2.revision == 2
    assert len(active_observation_notes(t2)) == 1


def test_supersede_corrects_without_inventing_extra_active():
    svc = ConversationService()
    thread = _thread()
    first = svc.build_observation_note(
        kind="usage_feedback",
        text="跑通了",
        source_ref="rec_1",
    )
    thread = svc.upsert_observation_note(
        thread, first, expected_revision=1, known_interaction_ids={"rec_1"}
    )
    corrected = svc.build_observation_note(
        kind="usage_feedback",
        text="我之前说快了，其实还是失败",
        source_ref="rec_1",
        supersedes_id=first.id,
    )
    thread = svc.upsert_observation_note(
        thread, corrected, expected_revision=2, known_interaction_ids={"rec_1"}
    )
    active = active_observation_notes(thread)
    assert len(active) == 1
    assert "失败" in active[0].text
    assert len(thread.observation_notes) == 2
    assert thread.observation_notes[0].superseded is True


def test_missing_source_ref_rejected():
    svc = ConversationService()
    with pytest.raises(ConversationServiceError, match="source_ref"):
        svc.build_observation_note(
            kind="problem",
            text="regression pain",
            source_ref="",
        )


def test_dangling_source_ref_rejected():
    svc = ConversationService()
    thread = _thread()
    note = ThreadNote(
        id="note_x",
        text="something",
        source_ref="rec_missing",
        kind=ObservationKind.PROBLEM,
    )
    with pytest.raises(ConversationServiceError, match="source_ref not found"):
        svc.upsert_observation_note(
            thread, note, expected_revision=1, known_interaction_ids={"rec_1"}
        )


def test_revision_conflict():
    svc = ConversationService()
    thread = _thread()
    note = svc.build_observation_note(
        kind="problem", text="pain", source_ref="rec_1"
    )
    with pytest.raises(ConversationServiceError, match="revision conflict"):
        svc.upsert_observation_note(
            thread, note, expected_revision=9, known_interaction_ids={"rec_1"}
        )


def test_old_thread_without_observation_notes_loads():
    # Simulate legacy YAML-shaped dict (no observation_notes / revision).
    legacy = ConversationThread.model_validate(
        {
            "id": "thread_legacy",
            "peer_id": "peer_1",
            "topic": "general",
            "open_questions": ["what next?"],
        }
    )
    assert legacy.observation_notes == []
    assert legacy.revision == 1


def test_seven_days_alone_does_not_need_follow_up():
    svc = ConversationService()
    thread = ConversationThread(
        id="t",
        peer_id="p",
        topic="x",
        last_activity_at=datetime.now(UTC) - timedelta(days=10),
    )
    assert svc.needs_follow_up(thread, now=datetime.now(UTC)) is False
