"""讨论摘要仓库（文件工作区；一个 note 一个 YAML，原子写）."""

from __future__ import annotations

from pathlib import Path

from finch.dialogue.models import DialogueNote
from finch.storage.workspace import Workspace


class DialogueRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("dialogue")

    def _path(self, note_id: str) -> Path:
        return self._dir / f"{self.ws.safe_filename(note_id)}.yaml"

    def get(self, note_id: str) -> DialogueNote | None:
        return self.ws.read_yaml(self._path(note_id), DialogueNote)

    def save(self, note: DialogueNote) -> None:
        self.ws.write_yaml(self._path(note.id), note)

    def delete(self, note_id: str) -> bool:
        path = self._path(note_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_all(self) -> list[DialogueNote]:
        out: list[DialogueNote] = []
        for path in sorted(self._dir.glob("*.yaml")):
            note = self.ws.read_yaml(path, DialogueNote)
            if note is not None:
                out.append(note)
        return out
