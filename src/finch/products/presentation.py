"""产品展示记录：展示 / 选择两个独立事件，冷却只按「新语义 presented」计。

简化自 ``peers/presentation.py``：本轮只做每日产品列表的冷却去重，不做平台多样性。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from finch.storage.workspace import Workspace

EVENT_PRESENTED = "presented"
EVENT_SELECTED = "selected"


class ProductPresentationRecord(BaseModel):
    """一次产品展示或选择事件。"""

    id: str
    product_id: str
    surface: str = ""  # home | browse
    snapshot_id: str = ""
    event: str = EVENT_PRESENTED
    presented_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProductPresentationRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws
        self._dir = ws.dir("products/presentations")

    def _path(self, record_id: str) -> str:
        return f"products/presentations/{Workspace.safe_filename(record_id)}.yaml"

    def get(self, record_id: str) -> ProductPresentationRecord | None:
        path = self._dir / f"{Workspace.safe_filename(record_id)}.yaml"
        return self.ws.read_yaml(path, ProductPresentationRecord)

    def save(self, record: ProductPresentationRecord) -> None:
        path = self._dir / f"{Workspace.safe_filename(record.id)}.yaml"
        self.ws.write_yaml(path, record)

    def list_all(self) -> list[ProductPresentationRecord]:
        out: list[ProductPresentationRecord] = []
        for path in sorted(self._dir.glob("*.yaml")):
            obj = self.ws.read_yaml(path, ProductPresentationRecord)
            if obj is not None:
                out.append(obj)
        return out

    def _presented(self) -> list[ProductPresentationRecord]:
        return [r for r in self.list_all() if r.event == EVENT_PRESENTED]

    def last_shown_at(self, product_id: str) -> datetime | None:
        times = [r.presented_at for r in self._presented() if r.product_id == product_id]
        return max(times) if times else None

    def _stable_id(
        self, *, product_id: str, surface: str, snapshot_id: str, event: str, batch: str
    ) -> str:
        key = f"{snapshot_id or batch}|{product_id}|{surface}|{event}"
        return f"pp_{hashlib.sha256(key.encode()).hexdigest()[:16]}"

    def record_shown(
        self,
        *,
        product_id: str,
        surface: str = "",
        snapshot_id: str = "",
        now: datetime | None = None,
    ) -> ProductPresentationRecord:
        """记录一次展示（幂等：同一 snapshot+product+surface 不重复写入）。"""
        clock = now or datetime.now(UTC)
        batch = snapshot_id or clock.strftime("%Y-%m-%d")
        record_id = self._stable_id(
            product_id=product_id,
            surface=surface,
            snapshot_id=snapshot_id,
            event=EVENT_PRESENTED,
            batch=batch,
        )
        existing = self.get(record_id)
        if existing is not None:
            return existing
        record = ProductPresentationRecord(
            id=record_id,
            product_id=product_id,
            surface=surface,
            snapshot_id=snapshot_id,
            event=EVENT_PRESENTED,
            presented_at=clock,
        )
        self.save(record)
        return record

    def record_selected(
        self,
        *,
        product_id: str,
        surface: str = "",
        snapshot_id: str = "",
        now: datetime | None = None,
    ) -> ProductPresentationRecord:
        """记录一次明确选择（查看/深入；不参与冷却）。"""
        clock = now or datetime.now(UTC)
        batch = snapshot_id or clock.strftime("%Y-%m-%d")
        record_id = self._stable_id(
            product_id=product_id,
            surface=surface,
            snapshot_id=snapshot_id,
            event=EVENT_SELECTED,
            batch=batch,
        )
        existing = self.get(record_id)
        if existing is not None:
            return existing
        record = ProductPresentationRecord(
            id=record_id,
            product_id=product_id,
            surface=surface,
            snapshot_id=snapshot_id,
            event=EVENT_SELECTED,
            presented_at=clock,
        )
        self.save(record)
        return record
