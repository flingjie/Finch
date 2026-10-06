"""AngleBrief 仓库（文件工作区，原子写）。"""

from __future__ import annotations

from pathlib import Path

from finch.angle_discovery.models import AngleBrief
from finch.storage.workspace import Workspace


class AngleBriefRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("angle_briefs")

    def upsert(self, brief: AngleBrief) -> AngleBrief:
        path = Path(self._dir) / f"{Workspace.safe_filename(brief.id)}.yaml"
        self.ws.write_yaml(path, brief)
        return brief

    def get(self, brief_id: str) -> AngleBrief | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(brief_id)}.yaml"
        return self.ws.read_yaml(path, AngleBrief)

    def list(self) -> list[str]:
        return sorted(p.stem for p in Path(self._dir).glob("*.yaml"))
