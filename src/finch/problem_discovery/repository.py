"""问题发现领域文件仓储：镜像 ``products/repository.py`` 的读写模式，落新目录避免与旧数据冲突。"""

from __future__ import annotations

from pydantic import BaseModel

from finch.problem_discovery.models import DiscoveryRun, ImportedDiscussion, Problem
from finch.storage.workspace import Workspace

_NS = "problem_discovery"
_SHADOW_NS = "problem_discovery/shadow"


def _ns(shadow: bool = False) -> str:
    return _SHADOW_NS if shadow else _NS


def _write(ws: Workspace, name: str, key: str, model) -> None:
    ws.write_yaml(ws.dir(name) / f"{ws.safe_filename(key)}.yaml", model)


def _read[T: BaseModel](ws: Workspace, name: str, key: str, model_cls: type[T]) -> T | None:
    return ws.read_yaml(ws.dir(name) / f"{ws.safe_filename(key)}.yaml", model_cls)


def _list_all[T: BaseModel](ws: Workspace, name: str, model_cls: type[T]) -> list[T]:
    out: list[T] = []
    for path in sorted(ws.dir(name).glob("*.yaml")):
        obj = ws.read_yaml(path, model_cls)
        if obj is not None:
            out.append(obj)
    return out


class ProblemRepository:
    """``var/problem_discovery[/shadow]/problems/<problem_id>.yaml``（幂等 upsert）。"""

    def __init__(self, ws: Workspace, *, shadow: bool = False) -> None:
        self.ws = ws
        self.shadow = shadow
        self._root = f"{_ns(shadow)}/problems"

    def upsert(self, problem: Problem) -> None:
        _write(self.ws, self._root, problem.problem_id, problem)

    def get(self, problem_id: str) -> Problem | None:
        return _read(self.ws, self._root, problem_id, Problem)

    def list_all(self) -> list[Problem]:
        return _list_all(self.ws, self._root, Problem)


class DiscussionRepository:
    """Discussions YAML store shared by control and shadow paths."""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, discussion: ImportedDiscussion) -> None:
        _write(self.ws, f"{_NS}/discussions", discussion.discussion_id, discussion)

    def get(self, discussion_id: str) -> ImportedDiscussion | None:
        return _read(self.ws, f"{_NS}/discussions", discussion_id, ImportedDiscussion)

    def list_all(self) -> list[ImportedDiscussion]:
        return _list_all(self.ws, f"{_NS}/discussions", ImportedDiscussion)


class DiscoveryRunRepository:
    """``var/problem_discovery[/shadow]/runs/<run_id>.yaml``。"""

    def __init__(self, ws: Workspace, *, shadow: bool = False) -> None:
        self.ws = ws
        self.shadow = shadow
        self._root = f"{_ns(shadow)}/runs"

    def upsert(self, run: DiscoveryRun) -> None:
        _write(self.ws, self._root, run.run_id, run)

    def get(self, run_id: str) -> DiscoveryRun | None:
        return _read(self.ws, self._root, run_id, DiscoveryRun)

    def list_all(self) -> list[DiscoveryRun]:
        return _list_all(self.ws, self._root, DiscoveryRun)

    def latest(self) -> DiscoveryRun | None:
        items = sorted(self.list_all(), key=lambda r: r.created_at, reverse=True)
        return items[0] if items else None
