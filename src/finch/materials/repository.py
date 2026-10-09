"""素材库仓库：快照缓存 / 讨论记录 / 同步状态 / 操作日志 / 用途日志。

目录布局（``<var>/materials/``）：快照与讨论每个一条 YAML；操作与用途用 JSONL 追加
（同 ``operation_id`` 最新行生效，参照 ``CriticReportRepository``）。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from finch.materials.models import (
    DiscussionRecord,
    MaterialSnapshot,
    MaterialUseLink,
    SyncOperation,
    SyncOperationStatus,
    SyncState,
    material_id_for,
)
from finch.storage.workspace import Workspace


class MaterialRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._snapshots = workspace.dir("materials/snapshots")
        self._discussions = workspace.dir("materials/discussions")
        self._sync_state = workspace.dir("materials") / "sync-state.yaml"

    def _snapshot_path(self, block_id: str) -> Path:
        return self._snapshots / f"{Workspace.safe_filename(material_id_for(block_id))}.yaml"

    def _discussion_path(self, discussion_id: str) -> Path:
        return self._discussions / f"{Workspace.safe_filename(discussion_id)}.yaml"

    def save_snapshot(self, snapshot: MaterialSnapshot) -> None:
        self.ws.write_yaml(self._snapshot_path(snapshot.block_id), snapshot)

    def get_snapshot(self, block_id: str) -> MaterialSnapshot | None:
        return self.ws.read_yaml(self._snapshot_path(block_id), MaterialSnapshot)

    def list_snapshots(self) -> list[MaterialSnapshot]:
        out: list[MaterialSnapshot] = []
        for path in sorted(self._snapshots.glob("mat_*.yaml")):
            snapshot = self.ws.read_yaml(path, MaterialSnapshot)
            if snapshot is not None:
                out.append(snapshot)
        return out

    def save_discussion(self, discussion: DiscussionRecord) -> None:
        self.ws.write_yaml(self._discussion_path(discussion.discussion_id), discussion)

    def get_discussion(self, discussion_id: str) -> DiscussionRecord | None:
        return self.ws.read_yaml(self._discussion_path(discussion_id), DiscussionRecord)

    def list_discussions(self, block_id: str) -> list[DiscussionRecord]:
        return [d for d in self._list_discussions() if d.block_id == block_id]

    def _list_discussions(self) -> list[DiscussionRecord]:
        out: list[DiscussionRecord] = []
        for path in sorted(self._discussions.glob("disc_*.yaml")):
            discussion = self.ws.read_yaml(path, DiscussionRecord)
            if discussion is not None:
                out.append(discussion)
        return out

    def save_sync_state(self, state: SyncState) -> None:
        self.ws.write_yaml(self._sync_state, state)

    def get_sync_state(self) -> SyncState:
        existing = self.ws.read_yaml(self._sync_state, SyncState)
        return existing if existing is not None else SyncState()


class SyncOperationLog:
    """同步操作日志（append-only JSONL；同 ``operation_id`` 最新行生效）。"""

    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._path = workspace.dir("materials") / "operations.jsonl"

    def append(self, op: SyncOperation) -> None:
        self.ws.append_jsonl(self._path, op.model_dump(mode="json"))

    def get(self, operation_id: str) -> SyncOperation | None:
        for row in reversed(self._all_raw()):
            if row.get("operation_id") == operation_id:
                try:
                    return SyncOperation.model_validate(row)
                except ValidationError:
                    return None
        return None

    def list_all(self) -> list[SyncOperation]:
        latest: dict[str, SyncOperation] = {}
        for row in self._all_raw():
            try:
                op = SyncOperation.model_validate(row)
            except ValidationError:
                continue
            latest[op.operation_id] = op
        return list(latest.values())

    def list_pending(self) -> list[SyncOperation]:
        statuses = (SyncOperationStatus.PENDING, SyncOperationStatus.RETRYABLE_FAILED)
        return [op for op in self.list_all() if op.status in statuses]

    def list_blocked(self) -> list[SyncOperation]:
        return [op for op in self.list_all() if op.status == SyncOperationStatus.BLOCKED]

    def _all_raw(self) -> list[dict]:
        return self.ws.read_jsonl(self._path)


class MaterialUseLinkRepository:
    """素材用途日志（append-only JSONL）。"""

    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._path = workspace.dir("materials") / "usage.jsonl"

    def append(self, link: MaterialUseLink) -> None:
        self.ws.append_jsonl(self._path, link.model_dump(mode="json"))

    def list_for_block(self, block_id: str) -> list[MaterialUseLink]:
        return [link for link in self.list_all() if link.block_id == block_id]

    def list_all(self) -> list[MaterialUseLink]:
        out: list[MaterialUseLink] = []
        for row in self.ws.read_jsonl(self._path):
            try:
                out.append(MaterialUseLink.model_validate(row))
            except ValidationError:
                continue
        return out
