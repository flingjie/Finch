"""机会聚合仓储：``var/opportunities/<id>/opportunity.yaml`` + ``events.jsonl`` + ``artifacts/``。

快照用 ``atomic_write`` 单文件原子替换；事件用 ``append_jsonl`` 追加。读改写由
``OpportunityRepository.locked`` 提供按机会的可重入写锁（进程内 RLock + 跨进程
``flock``），``expected_revision`` 仍作乐观校验的补充。
"""

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from finch.opportunities.models import Artifact, Opportunity, OpportunityEvent
from finch.storage.workspace import Workspace

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


class _OpportunityLock:
    """按机会目录的可重入写锁：进程内 RLock + 跨进程 ``flock``。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._rl = threading.RLock()
        self._held = 0
        self._file: Any = None

    def __enter__(self) -> "_OpportunityLock":
        self._rl.acquire()
        if self._held == 0:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self._path, "a")
            if fcntl is not None:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
        self._held += 1
        return self

    def __exit__(self, *exc) -> None:
        self._held -= 1
        if self._held == 0:
            if fcntl is not None and self._file is not None:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            if self._file is not None:
                self._file.close()
                self._file = None
        self._rl.release()


class OpportunityRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self._locks_guard = threading.Lock()
        self._locks: dict[str, _OpportunityLock] = {}

    def _dir(self, opportunity_id: str) -> Path:
        return self.ws.dir("opportunities") / self.ws.safe_filename(opportunity_id)

    @contextmanager
    def locked(self, opportunity_id: str):
        """对该机会的读改写加写锁（可重入）。"""
        key = str(self._dir(opportunity_id))
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = _OpportunityLock(self._dir(opportunity_id) / ".lock")
                self._locks[key] = lock
        with lock:
            yield

    def save(self, opportunity: Opportunity) -> None:
        self.ws.write_yaml(self._dir(opportunity.id) / "opportunity.yaml", opportunity)

    def get(self, opportunity_id: str) -> Opportunity | None:
        return self.ws.read_yaml(
            self._dir(opportunity_id) / "opportunity.yaml", Opportunity
        )

    def append_event(self, event: OpportunityEvent) -> None:
        path = self._dir(event.opportunity_id) / "events.jsonl"
        # 幂等：同一 event_id 已落盘则跳过（修复重放不会产生重复事件）。
        if any(e.get("event_id") == event.event_id for e in self.ws.read_jsonl(path)):
            return
        self.ws.append_jsonl(path, event.model_dump(mode="json"))

    def list_events(self, opportunity_id: str) -> list[OpportunityEvent]:
        rows = self.ws.read_jsonl(self._dir(opportunity_id) / "events.jsonl")
        return [OpportunityEvent.model_validate(row) for row in rows]

    def list_all(self) -> list[Opportunity]:
        out: list[Opportunity] = []
        for path in sorted(self.ws.dir("opportunities").glob("*/opportunity.yaml")):
            obj = self.ws.read_yaml(path, Opportunity)
            if obj is not None:
                out.append(obj)
        return out


class ArtifactRepository:
    """成果对象仓储：``var/opportunities/<id>/artifacts/<artifact_id>.yaml``。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _dir(self, opportunity_id: str) -> Path:
        return (
            self.ws.dir("opportunities")
            / self.ws.safe_filename(opportunity_id)
            / "artifacts"
        )

    def save(self, artifact: Artifact) -> None:
        if artifact.opportunity_id is None:
            raise ValueError(f"artifact {artifact.id} has no opportunity_id")
        self.ws.write_yaml(
            self._dir(artifact.opportunity_id) / f"{self.ws.safe_filename(artifact.id)}.yaml",
            artifact,
        )

    def get(self, opportunity_id: str, artifact_id: str) -> Artifact | None:
        return self.ws.read_yaml(
            self._dir(opportunity_id) / f"{self.ws.safe_filename(artifact_id)}.yaml",
            Artifact,
        )

    def list_by_opportunity(self, opportunity_id: str) -> list[Artifact]:
        d = self._dir(opportunity_id)
        if not d.exists():
            return []
        out: list[Artifact] = []
        for path in sorted(d.glob("*.yaml")):
            obj = self.ws.read_yaml(path, Artifact)
            if obj is not None:
                out.append(obj)
        return out

    def write_content(self, opportunity_id: str, artifact_id: str, body: str) -> None:
        """把成果正文写为 Markdown 文件（与元数据 yaml 同目录，``artifact.path`` 引用它）。"""
        path = self._dir(opportunity_id) / f"{self.ws.safe_filename(artifact_id)}.md"
        self.ws.atomic_write(path, body)

    def read_content(self, opportunity_id: str, artifact_id: str) -> str | None:
        path = self._dir(opportunity_id) / f"{self.ws.safe_filename(artifact_id)}.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
