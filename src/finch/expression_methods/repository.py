"""ExpressionMethod 仓库（文件工作区，原子写）。"""

from __future__ import annotations

from pathlib import Path

from finch.expression_methods.models import ExpressionMethod
from finch.storage.workspace import Workspace


class ExpressionMethodRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("expression_methods")

    def upsert(self, method: ExpressionMethod) -> None:
        path = Path(self._dir) / f"{Workspace.safe_filename(method.id)}.yaml"
        self.ws.write_yaml(path, method)

    def get(self, method_id: str) -> ExpressionMethod | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(method_id)}.yaml"
        return self.ws.read_yaml(path, ExpressionMethod)

    def list_all(self) -> list[ExpressionMethod]:
        out: list[ExpressionMethod] = []
        for path in sorted(Path(self._dir).glob("*.yaml")):
            row = self.ws.read_yaml(path, ExpressionMethod)
            if row is not None:
                out.append(row)
        return out
