"""问题发现的展示记录：把「展示顺序 → 稳定 problem_id」持久化，支持跨会话「展开第二个」等引用。

镜像 ``products/presentation.py`` 的稳定 id + 幂等写入；不同点是展示由 Skill（Codex）完成，
所以记录经 ``finch problem-discovery present`` CLI 命令写入、由 Skill 调用，非 CLI 渲染侧落库。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from finch.storage.workspace import Workspace


class ProblemPresentationRecord(BaseModel):
    """一次展示的有序问题列表（序号 → problem_id）。"""

    id: str
    problem_ids: list[str] = Field(default_factory=list)
    batch: str = ""
    presented_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProblemPresentationRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self._dir = ws.dir("problem_discovery/presentations")

    def _path(self, record_id: str) -> str:
        return f"problem_discovery/presentations/{Workspace.safe_filename(record_id)}.yaml"

    def get(self, record_id: str) -> ProblemPresentationRecord | None:
        return self.ws.read_yaml(
            self._dir / f"{Workspace.safe_filename(record_id)}.yaml",
            ProblemPresentationRecord,
        )

    def save(self, record: ProblemPresentationRecord) -> None:
        self.ws.write_yaml(self._dir / f"{Workspace.safe_filename(record.id)}.yaml", record)

    def list_all(self) -> list[ProblemPresentationRecord]:
        out: list[ProblemPresentationRecord] = []
        for path in sorted(self._dir.glob("*.yaml")):
            obj = self.ws.read_yaml(path, ProblemPresentationRecord)
            if obj is not None:
                out.append(obj)
        return out

    def _stable_id(self, problem_ids: list[str], batch: str) -> str:
        key = "|".join([batch, *problem_ids])
        return f"pd_{hashlib.sha256(key.encode()).hexdigest()[:16]}"

    def record_shown(
        self,
        problem_ids: list[str],
        *,
        batch: str = "",
        now: datetime | None = None,
    ) -> ProblemPresentationRecord:
        """记录一次展示顺序（幂等：同批同序覆盖并更新时间）。"""
        clock = now or datetime.now(UTC)
        record = ProblemPresentationRecord(
            id=self._stable_id(problem_ids, batch),
            problem_ids=list(problem_ids),
            batch=batch,
            presented_at=clock,
        )
        self.save(record)
        return record

    def latest(self) -> ProblemPresentationRecord | None:
        items = sorted(self.list_all(), key=lambda r: r.presented_at, reverse=True)
        return items[0] if items else None
