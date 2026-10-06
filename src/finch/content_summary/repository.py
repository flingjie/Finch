"""ContentSummary 仓库（文件工作区，原子写）。"""

from __future__ import annotations

from pathlib import Path

from finch.content_summary.models import ContentSummary
from finch.storage.workspace import Workspace


class ContentSummaryRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("content_summaries")

    def upsert(self, summary: ContentSummary) -> ContentSummary:
        existing = self.get(summary.id)
        if existing is not None:
            summary = summary.model_copy(
                update={"source_refs": _union_keep_order(existing.source_refs, summary.source_refs)}
            )
        path = Path(self._dir) / f"{Workspace.safe_filename(summary.id)}.yaml"
        self.ws.write_yaml(path, summary)
        return summary

    def get(self, summary_id: str) -> ContentSummary | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(summary_id)}.yaml"
        return self.ws.read_yaml(path, ContentSummary)

    def list(self) -> list[str]:
        return sorted(p.stem for p in Path(self._dir).glob("*.yaml"))


def _union_keep_order(a: list[str], b: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in (*a, *b):
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
