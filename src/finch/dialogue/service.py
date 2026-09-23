"""DialogueService：保存（幂等追加 + revision 冲突）/ 检索 / 删除。

单用户串行写入假设：revision 检查只防「重读后覆盖」，不提供多进程事务保证。
``save`` 失败只影响记忆保存，不阻断当前对话（由调用方告知「尚未保存」并重试）。
"""

from __future__ import annotations

from datetime import datetime

from finch.dialogue.models import DialogueCheckpoint, DialogueNote, PositionStatus
from finch.dialogue.repository import DialogueRepository
from finch.storage.workspace import Workspace


class DialogueServiceError(ValueError):
    """保存冲突或校验失败。"""


def _dedupe_checkpoints(
    checkpoints: list[DialogueCheckpoint],
) -> list[DialogueCheckpoint]:
    """同一 payload 内相同 ``checkpoint_id`` 只保留第一条。"""
    seen: set[str] = set()
    out: list[DialogueCheckpoint] = []
    for checkpoint in checkpoints:
        if checkpoint.checkpoint_id in seen:
            continue
        seen.add(checkpoint.checkpoint_id)
        out.append(checkpoint)
    return out


def _require_confirmation_quote(checkpoints: list[DialogueCheckpoint]) -> None:
    """``user_confirmed`` 必须带用户认可原话，不能由空引用升级成立场。"""
    for checkpoint in checkpoints:
        if (
            checkpoint.position_status == PositionStatus.USER_CONFIRMED
            and not checkpoint.confirmation_quote.strip()
        ):
            raise DialogueServiceError("user_confirmed requires confirmation_quote")


class DialogueService:
    def __init__(self, workspace: Workspace) -> None:
        self.repo = DialogueRepository(workspace)

    def save(self, incoming: DialogueNote, *, expected_revision: int) -> DialogueNote:
        """创建（``expected_revision==0``）或更新一条 note。

        - 创建：``id`` 已存在则冲突。
        - 更新：``revision`` 不符则冲突；按 ``checkpoint_id`` 去重追加，
          不重复追加已存在的 checkpoint（幂等重试返回现状，不 bump revision）。
          同一次入参里重复的 ``checkpoint_id`` 只保留第一条。
        - ``position_status=user_confirmed`` 且 ``confirmation_quote`` 为空则拒绝。
        - 更新时身份字段（topic / topic_key / origin_refs）沿用已有值，忽略入参。
        """
        incoming_checkpoints = _dedupe_checkpoints(incoming.checkpoints)
        existing = self.repo.get(incoming.id)
        if expected_revision == 0:
            if existing is not None:
                raise DialogueServiceError(
                    f"note already exists: {incoming.id} "
                    f"(current revision {existing.revision})"
                )
            _require_confirmation_quote(incoming_checkpoints)
            note = incoming.model_copy(
                update={"revision": 1, "checkpoints": incoming_checkpoints}
            )
            self.repo.save(note)
            return note
        if existing is None:
            raise DialogueServiceError(
                f"note not found: {incoming.id} "
                f"(expected revision {expected_revision})"
            )
        if existing.revision != expected_revision:
            raise DialogueServiceError(
                f"revision conflict: expected {expected_revision}, "
                f"current {existing.revision}"
            )
        known = {c.checkpoint_id for c in existing.checkpoints}
        new_checkpoints = [
            c for c in incoming_checkpoints if c.checkpoint_id not in known
        ]
        _require_confirmation_quote(new_checkpoints)
        if not new_checkpoints:
            return existing
        note = existing.model_copy(
            update={
                "checkpoints": [*existing.checkpoints, *new_checkpoints],
                "revision": existing.revision + 1,
            }
        )
        self.repo.save(note)
        return note

    def show(self, note_id: str) -> DialogueNote | None:
        return self.repo.get(note_id)

    def forget(self, note_id: str) -> bool:
        return self.repo.delete(note_id)

    def search(self, query: str, *, limit: int = 3) -> list[DialogueNote]:
        """关键词召回：query 分词匹配 topic / topic_key / checkpoint 文本，按
        （命中数降序，created_at 降序）返回最多 ``limit`` 条。读取不调用网络。"""
        tokens = [t for t in query.lower().split() if t]
        if not tokens:
            return []
        scored: list[tuple[int, datetime, DialogueNote]] = []
        for note in self.repo.list_all():
            haystack = f"{note.topic} {note.topic_key}".lower()
            for ref in note.origin_refs:
                haystack += f" {ref.type} {ref.ref}".lower()
            for cp in note.checkpoints:
                haystack += f" {cp.user_position.lower()}"
                haystack += f" {cp.confirmation_quote.lower()}"
                haystack += f" {' '.join(cp.conditions).lower()}"
                haystack += f" {' '.join(cp.open_questions).lower()}"
                haystack += f" {' '.join(cp.assistant_hypotheses).lower()}"
                haystack += f" {cp.change_reason.lower()}"
            score = sum(1 for t in tokens if t in haystack)
            if score > 0:
                scored.append((score, note.created_at, note))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [note for _, _, note in scored[:limit]]
