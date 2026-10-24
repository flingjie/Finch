"""ComparisonRecord 仓库（文件工作区，原子写）。"""

from __future__ import annotations

from pathlib import Path

from finch.comparison.models import ComparisonRecord
from finch.storage.workspace import Workspace


class ComparisonRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("comparisons")

    def upsert(self, record: ComparisonRecord) -> ComparisonRecord:
        path = Path(self._dir) / f"{Workspace.safe_filename(record.id)}.yaml"
        self.ws.write_yaml(path, record)
        return record

    def get(self, record_id: str) -> ComparisonRecord | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(record_id)}.yaml"
        return self.ws.read_yaml(path, ComparisonRecord)

    def list_all(self) -> list[ComparisonRecord]:
        out: list[ComparisonRecord] = []
        for path in sorted(Path(self._dir).glob("*.yaml")):
            obj = self.ws.read_yaml(path, ComparisonRecord)
            if obj is not None:
                out.append(obj)
        return out
