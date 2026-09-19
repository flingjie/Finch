"""InspirationService：保存 / 追加笔记 / 归档（幂等，append-only）。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.inspirations.models import (
    Inspiration,
    InspirationNote,
    InspirationOrigin,
    inspiration_id_for,
)
from finch.inspirations.repository import InspirationRepository
from finch.storage.workspace import Workspace


class InspirationService:
    def __init__(self, workspace: Workspace) -> None:
        self.repo = InspirationRepository(workspace)

    def save(
        self,
        *,
        text: str,
        origin: InspirationOrigin = InspirationOrigin.USER_INPUT,
        source_refs: list[str] | None = None,
        person_ids: list[str] | None = None,
        conversation_id: str | None = None,
    ) -> Inspiration:
        """幂等保存：相同 text 返回既有灵感，不覆盖用户原话。"""
        iid = inspiration_id_for(text)
        existing = self.repo.get(iid)
        if existing is not None:
            return existing
        inspiration = Inspiration(
            id=iid,
            text=text.strip(),
            origin=origin,
            source_refs=list(source_refs or []),
            person_ids=list(person_ids or []),
            conversation_id=conversation_id,
        )
        self.repo.save(inspiration)
        return inspiration

    def note(self, inspiration_id: str, *, text: str) -> Inspiration | None:
        """追加笔记（append-only）。"""
        existing = self.repo.get(inspiration_id)
        if existing is None:
            return None
        updated = existing.model_copy(
            update={
                "notes": [*existing.notes, InspirationNote(text=text.strip())],
                "updated_at": datetime.now(UTC),
            }
        )
        self.repo.save(updated)
        return updated

    def archive(self, inspiration_id: str) -> Inspiration | None:
        existing = self.repo.get(inspiration_id)
        if existing is None:
            return None
        updated = existing.model_copy(
            update={
                "archived_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )
        self.repo.save(updated)
        return updated

    def list(self, *, include_archived: bool = False) -> list[Inspiration]:
        rows = self.repo.list_all()
        if include_archived:
            return rows
        return [r for r in rows if r.archived_at is None]
