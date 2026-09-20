"""人物展示记录：展示语义、冷却与平台多样性。

D10：区分三个独立事件——
- ``generated``：快照生成即记录，不是曝光（无需本领域单独落库，快照本身即是凭证）；
- ``presented``：用户界面实际输出的条目（不推断用户认真阅读）；
- ``selected``：用户明确要求查看、深入或准备（不推断为已经交流）。

``presented`` 用 ``snapshot_id + person_id + surface``（旧短列表无快照时按日期批次）的
稳定幂等键，重复打开同一快照不反复延长冷却。``presentation_semantics_version`` 标记语义：
历史“生成即曝光”的数据保留供追溯，但不参与冷却（``"1"``），新写入使用 ``"2"``。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from finch.storage.workspace import Workspace

# 展示语义版本：v1 = 历史“生成即曝光”（不参与冷却）；v2 = 三事件分离（当前）。
CURRENT_SEMANTICS_VERSION = "2"
LEGACY_SEMANTICS_VERSION = "1"

EVENT_PRESENTED = "presented"
EVENT_SELECTED = "selected"


class PersonPresentationRecord(BaseModel):
    """一次人物展示或选择事件（按 person_id）。"""

    id: str
    person_id: str
    peer_id: str = ""
    platform: str = ""
    slot: str = ""
    surface: str = ""  # home | browse | shortlist | person
    snapshot_id: str = ""  # 为空表示旧短列表路径（按日期批次幂等）
    event: str = EVENT_PRESENTED  # presented | selected
    presented_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    presentation_semantics_version: str = LEGACY_SEMANTICS_VERSION


class PersonPresentationRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("people/presentations")

    def _path(self, record_id: str) -> Path:
        return Path(self._dir) / f"{Workspace.safe_filename(record_id)}.yaml"

    def get(self, record_id: str) -> PersonPresentationRecord | None:
        return self.ws.read_yaml(self._path(record_id), PersonPresentationRecord)

    def save(self, record: PersonPresentationRecord) -> None:
        self.ws.write_yaml(self._path(record.id), record)

    def list_all(self) -> list[PersonPresentationRecord]:
        out: list[PersonPresentationRecord] = []
        for path in sorted(Path(self._dir).glob("*.yaml")):
            row = self.ws.read_yaml(path, PersonPresentationRecord)
            if row is not None:
                out.append(row)
        return out

    def _presented_new(self) -> list[PersonPresentationRecord]:
        """参与冷却/多样性的事件：仅新语义的 presented（selected 与旧记录不算）。"""
        return [
            r
            for r in self.list_all()
            if r.event == EVENT_PRESENTED
            and r.presentation_semantics_version == CURRENT_SEMANTICS_VERSION
        ]

    def last_shown_at(self, person_id: str) -> datetime | None:
        times = [r.presented_at for r in self._presented_new() if r.person_id == person_id]
        return max(times) if times else None

    def recent_platforms(self, *, within_days: int = 14, now: datetime | None = None) -> set[str]:
        clock = now or datetime.now(UTC)
        cutoff = clock - timedelta(days=within_days)
        out: set[str] = set()
        for r in self._presented_new():
            if r.presented_at >= cutoff and r.platform:
                out.add(r.platform)
        return out

    def _stable_id(
        self,
        *,
        person_id: str,
        surface: str,
        snapshot_id: str,
        event: str,
        batch: str,
    ) -> str:
        key = f"{snapshot_id or batch}|{person_id}|{surface}|{event}"
        return f"pp_{hashlib.sha256(key.encode()).hexdigest()[:16]}"

    def record_shown(
        self,
        *,
        person_id: str,
        peer_id: str = "",
        platform: str = "",
        slot: str = "",
        surface: str = "",
        snapshot_id: str = "",
        now: datetime | None = None,
    ) -> PersonPresentationRecord:
        """记录一次实际展示（幂等：同一 snapshot+person+surface 不重复写入）。"""
        surface = surface or ("shortlist" if slot else "")
        clock = now or datetime.now(UTC)
        batch = snapshot_id or clock.strftime("%Y-%m-%d")
        record_id = self._stable_id(
            person_id=person_id,
            surface=surface,
            snapshot_id=snapshot_id,
            event=EVENT_PRESENTED,
            batch=batch,
        )
        existing = self.get(record_id)
        if existing is not None:
            return existing
        record = PersonPresentationRecord(
            id=record_id,
            person_id=person_id,
            peer_id=peer_id,
            platform=platform,
            slot=slot,
            surface=surface,
            snapshot_id=snapshot_id,
            event=EVENT_PRESENTED,
            presented_at=clock,
            presentation_semantics_version=CURRENT_SEMANTICS_VERSION,
        )
        self.save(record)
        return record

    def record_selected(
        self,
        *,
        person_id: str,
        peer_id: str = "",
        platform: str = "",
        surface: str = "",
        snapshot_id: str = "",
        now: datetime | None = None,
    ) -> PersonPresentationRecord:
        """记录一次明确选择（查看/深入/准备；不影响冷却）。"""
        clock = now or datetime.now(UTC)
        batch = snapshot_id or clock.strftime("%Y-%m-%d")
        record_id = self._stable_id(
            person_id=person_id,
            surface=surface,
            snapshot_id=snapshot_id,
            event=EVENT_SELECTED,
            batch=batch,
        )
        existing = self.get(record_id)
        if existing is not None:
            return existing
        record = PersonPresentationRecord(
            id=record_id,
            person_id=person_id,
            peer_id=peer_id,
            platform=platform,
            surface=surface,
            snapshot_id=snapshot_id,
            event=EVENT_SELECTED,
            presented_at=clock,
            presentation_semantics_version=CURRENT_SEMANTICS_VERSION,
        )
        self.save(record)
        return record
