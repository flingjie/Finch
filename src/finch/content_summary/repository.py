"""ContentSummary 仓库（文件工作区，原子写）。"""

from __future__ import annotations

from pathlib import Path

from finch.content_summary.models import ContentSummary
from finch.storage.workspace import Workspace


class ContentSummaryRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("content_summaries")

    def upsert(self, summary: ContentSummary) -> None:
        path = Path(self._dir) / f"{Workspace.safe_filename(summary.id)}.yaml"
        self.ws.write_yaml(path, summary)

    def get(self, summary_id: str) -> ContentSummary | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(summary_id)}.yaml"
        return self.ws.read_yaml(path, ContentSummary)
