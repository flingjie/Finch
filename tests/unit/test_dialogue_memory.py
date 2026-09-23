"""dialogue 记忆：模型 / 仓库 / 服务（幂等 + revision 冲突 + 来源隔离）。"""

from __future__ import annotations

import json
from pathlib import Path

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
