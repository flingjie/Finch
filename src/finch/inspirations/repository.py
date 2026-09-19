"""灵感笔记仓库（文件工作区，原子写）。"""

from __future__ import annotations

from pathlib import Path

from finch.inspirations.models import Inspiration
from finch.storage.workspace import Workspace


class InspirationRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("inspirations")

    def save(self, inspiration: Inspiration) -> None:
        path = Path(self._dir) / f"{Workspace.safe_filename(inspiration.id)}.yaml"
        self.ws.write_yaml(path, inspiration)

    def get(self, inspiration_id: str) -> Inspiration | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(inspiration_id)}.yaml"
        return self.ws.read_yaml(path, Inspiration)

    def list_all(self) -> list[Inspiration]:
        out: list[Inspiration] = []
        for path in sorted(Path(self._dir).glob("insp_*.yaml")):
            row = self.ws.read_yaml(path, Inspiration)
            if row is not None:
                out.append(row)
        return out
