"""人物 shortlist 展示记录：冷却与平台多样性。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from finch.storage.workspace import Workspace


class PersonPresentationRecord(BaseModel):
    """一次 shortlist 展示（按 person_id）。"""

    id: str
    person_id: str
    peer_id: str = ""
    platform: str = ""
    slot: str = ""
    presented_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PersonPresentationRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("people/presentations")

    def _path(self, record_id: str) -> Path:
        return Path(self._dir) / f"{Workspace.safe_filename(record_id)}.yaml"

    def save(self, record: PersonPresentationRecord) -> None:
        self.ws.write_yaml(self._path(record.id), record)

    def list_all(self) -> list[PersonPresentationRecord]:
        out: list[PersonPresentationRecord] = []
        for path in sorted(Path(self._dir).glob("*.yaml")):
            row = self.ws.read_yaml(path, PersonPresentationRecord)
            if row is not None:
                out.append(row)
        return out

    def last_shown_at(self, person_id: str) -> datetime | None:
        times = [r.presented_at for r in self.list_all() if r.person_id == person_id]
        return max(times) if times else None

    def recent_platforms(self, *, within_days: int = 14, now: datetime | None = None) -> set[str]:
        clock = now or datetime.now(UTC)
        cutoff = clock - timedelta(days=within_days)
        out: set[str] = set()
        for r in self.list_all():
            if r.presented_at >= cutoff and r.platform:
                out.add(r.platform)
        return out

    def record_shown(
        self,
        *,
        person_id: str,
        peer_id: str = "",
        platform: str = "",
        slot: str = "",
        now: datetime | None = None,
    ) -> PersonPresentationRecord:
        record = PersonPresentationRecord(
            id=f"pp_{uuid4().hex[:12]}",
            person_id=person_id,
            peer_id=peer_id,
            platform=platform,
            slot=slot,
            presented_at=now or datetime.now(UTC),
        )
        self.save(record)
        return record
