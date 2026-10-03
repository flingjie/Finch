"""ArticleReport 仓库（文件工作区，原子写）。"""

from __future__ import annotations

from pathlib import Path

from finch.article.models import ArticleReport
from finch.storage.workspace import Workspace


class ArticleReportRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("article_reports")

    def upsert(self, report: ArticleReport) -> None:
        path = Path(self._dir) / f"{Workspace.safe_filename(report.id)}.yaml"
        self.ws.write_yaml(path, report)

    def get(self, report_id: str) -> ArticleReport | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(report_id)}.yaml"
        return self.ws.read_yaml(path, ArticleReport)

    def list_all(self) -> list[ArticleReport]:
        out: list[ArticleReport] = []
        for path in sorted(Path(self._dir).glob("*.yaml")):
            row = self.ws.read_yaml(path, ArticleReport)
            if row is not None:
                out.append(row)
        return out
