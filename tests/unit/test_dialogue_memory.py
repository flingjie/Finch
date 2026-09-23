"""dialogue 记忆：模型 / 仓库 / 服务（幂等 + revision 冲突 + 来源隔离）。"""

from __future__ import annotations

import json

import pytest

from finch.dialogue.models import (
    DialogueCheckpoint,
    DialogueNote,
    PositionStatus,
)
from finch.dialogue.repository import DialogueRepository
from finch.dialogue.service import DialogueService, DialogueServiceError
from finch.storage.workspace import Workspace


def _checkpoint(checkpoint_id: str, *, position: str = "先做 Skill 再代码化") -> DialogueCheckpoint:
    return DialogueCheckpoint(
        checkpoint_id=checkpoint_id,
        user_position=position,
        position_status=PositionStatus.TENTATIVE,
        conditions=["流程稳定之前"],
    )


def _note(note_id: str = "dlg_test", topic_key: str = "skill-to-code") -> DialogueNote:
    return DialogueNote(
        id=note_id,
        topic="skill 代码化",
        topic_key=topic_key,
        checkpoints=[_checkpoint("cp_1")],
    )


def test_note_serializes_to_json_with_enum():
    note = _note()
    payload = json.loads(note.model_dump_json())
    assert payload["id"] == "dlg_test"
    assert payload["checkpoints"][0]["position_status"] == "tentative"


def test_repository_roundtrip_and_list(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = DialogueRepository(ws)

    repo.save(_note("dlg_a", "a"))
    repo.save(_note("dlg_b", "b"))

    assert repo.get("dlg_a").topic_key == "a"
    assert {n.id for n in repo.list_all()} == {"dlg_a", "dlg_b"}


def test_repository_delete(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = DialogueRepository(ws)

    repo.save(_note("dlg_a", "a"))
    assert repo.delete("dlg_a") is True
    assert repo.get("dlg_a") is None
    assert repo.delete("dlg_a") is False


def test_save_creates_then_appends_bumping_revision(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    created = svc.save(_note("dlg_a", "a"), expected_revision=0)
    assert created.revision == 1

    update = DialogueNote(
        id="dlg_a", topic="skill 代码化", topic_key="a",
        checkpoints=[_checkpoint("cp_2", position="改：流程稳定前先不代码化")],
    )
    updated = svc.save(update, expected_revision=1)
    assert updated.revision == 2
    assert [c.checkpoint_id for c in updated.checkpoints] == ["cp_1", "cp_2"]


def test_save_idempotent_on_same_checkpoint(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    svc.save(_note("dlg_a", "a"), expected_revision=0)
    retry = svc.save(_note("dlg_a", "a"), expected_revision=1)
    assert retry.revision == 1
    assert len(retry.checkpoints) == 1


def test_save_revision_conflict_raises(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    svc.save(_note("dlg_a", "a"), expected_revision=0)
    with pytest.raises(DialogueServiceError):
        svc.save(_note("dlg_a", "a"), expected_revision=0)


def test_save_create_conflict_when_id_exists(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    svc.save(_note("dlg_a", "a"), expected_revision=0)
    with pytest.raises(DialogueServiceError):
        svc.save(
            DialogueNote(
                id="dlg_a", topic="另一个主题", topic_key="a",
                checkpoints=[_checkpoint("cp_9")],
            ),
            expected_revision=0,
        )


def test_search_matches_topic_key_and_limits(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    svc.save(_note("dlg_a", "skill-to-code"), expected_revision=0)
    svc.save(_note("dlg_b", "community-entry"), expected_revision=0)

    hits = svc.search("skill", limit=3)
    assert [n.id for n in hits] == ["dlg_a"]


def test_show_and_forget(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    svc.save(_note("dlg_a", "a"), expected_revision=0)
    assert svc.show("dlg_a").topic_key == "a"
    assert svc.show("missing") is None
    assert svc.forget("dlg_a") is True
    assert svc.forget("dlg_a") is False
