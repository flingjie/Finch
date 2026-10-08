"""MindMap 仓库（文件工作区，原子写）。"""

from __future__ import annotations

from pathlib import Path

from finch.angle_discovery.mindmap_models import MindMap
from finch.storage.workspace import Workspace


class MindMapRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("mind_maps")

    def upsert(self, mmap: MindMap) -> MindMap:
        path = Path(self._dir) / f"{Workspace.safe_filename(mmap.id)}.yaml"
        self.ws.write_yaml(path, mmap)
        return mmap

    def get(self, map_id: str) -> MindMap | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(map_id)}.yaml"
        return self.ws.read_yaml(path, MindMap)

    def list(self) -> list[str]:
        return sorted(p.stem for p in Path(self._dir).glob("*.yaml"))
