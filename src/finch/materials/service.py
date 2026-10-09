"""MaterialService：capture / read / list / search / sync / record_discussion / promote。

素材以 toggle 块存放在父页面（月度页）里，Notion 是权威来源；本地只存缓存 + 待同步
队列 + 讨论工作状态。capture 生成操作 ID、落队列再写远端；sync 重读父页面、按内容
哈希去重后重建缓存。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from finch.content.jobs import ContentJob
from finch.ideas.service import IdeaService
from finch.materials.field_map import (
    blocks_for_discussion,
    material_toggle_block,
    toggle_to_snapshot,
    user_region_text,
)
from finch.materials.models import (
    DiscussionRecord,
    MaterialSnapshot,
    MaterialUseLink,
    SyncOperation,
    SyncState,
    append_discussion_operation_id_for,
    append_material_operation_id_for,
    discussion_id_for,
    source_hash_for,
)
from finch.materials.promotion import MaterialPromotionService
from finch.materials.queue import WriteQueue
from finch.materials.repository import (
    MaterialRepository,
    MaterialUseLinkRepository,
    SyncOperationLog,
)
from finch.notion.client import NotionClient
from finch.storage.repositories import ContentJobRepository
from finch.storage.workspace import Workspace


class SyncResult(BaseModel):
    mode: str = "full"
    scanned: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[str] = Field(default_factory=list)
    committed_scan_boundary: datetime | None = None
    last_full_sync_at: datetime | None = None


class MaterialService:
    def __init__(self, workspace: Workspace, client: NotionClient, parent_page_id: str) -> None:
        self.ws = workspace
        self.client = client
        self.parent_page_id = parent_page_id
        self.repo = MaterialRepository(workspace)
        self.log = SyncOperationLog(workspace)
        self.usage = MaterialUseLinkRepository(workspace)
        self.queue = WriteQueue(self.log, client, parent_page_id)

    # ---- P1：记录与读取 ----

    def capture(
        self, *, title: str, body_text: str, reflection: str | None = None
    ) -> SyncOperation:
        """保存一条素材：生成操作 ID、落队列，再（由 queue 追加 toggle 块到父页面）。"""
        toggle = material_toggle_block(title, body_text, reflection)
        extractable = user_region_text(body_text, reflection)
        source_hash = source_hash_for(extractable)
        operation_id = append_material_operation_id_for(title, body_text)
        op = SyncOperation(
            operation_id=operation_id,
            type="append_material",
            target_block=self.parent_page_id,
            payload={"children": [toggle]},
            expected_material_version=source_hash,
        )
        return self.queue.enqueue(op)

    def read(self, block_id: str) -> MaterialSnapshot:
        """读一条素材（toggle 块 + children）→ 缓存，并记一条 read 用途。"""
        page = self.client.get_page(self.parent_page_id)
        blocks = self.client.list_all_block_children(self.parent_page_id)
        block = next(
            (b for b in blocks if b.get("id") == block_id and b.get("type") == "toggle"), None
        )
        if block is None:
            raise ValueError(f"material block {block_id} not found in page {self.parent_page_id}")
        children = self.client.list_all_block_children(block_id)
        snapshot = toggle_to_snapshot(page, block, children)
        self.repo.save_snapshot(snapshot)
        self.usage.append(
            MaterialUseLink(
                block_id=block_id,
                page_id=snapshot.page_id,
                source_hash=snapshot.source_hash,
                usage="read",
            )
        )
        return snapshot

    def list_materials(self, *, discussed: bool | None = None) -> list[MaterialSnapshot]:
        """本地缓存列表（可按已讨论过滤），按远端修改时间倒序。"""
        rows = self.repo.list_snapshots()
        if discussed is not None:
            rows = [r for r in rows if r.discussed == discussed]
        return sorted(
            rows,
            key=lambda r: r.remote_edited_at or datetime(1970, 1, 1, tzinfo=UTC),
            reverse=True,
        )

    def search(self, query: str, *, limit: int = 3) -> list[MaterialSnapshot]:
        """关键词召回（标题 / 正文 / 感触），按命中次数排序。"""
        q = query.strip().lower()
        if not q:
            return []
        scored: list[tuple[int, MaterialSnapshot]] = []
        for snapshot in self.repo.list_snapshots():
            haystack = "\n".join(
                [snapshot.title, snapshot.extractable_text, snapshot.user_reflection or ""]
            ).lower()
            if q not in haystack:
                continue
            score = sum(haystack.count(word) for word in q.split())
            scored.append((score, snapshot))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [snapshot for _, snapshot in scored[:limit]]

    def sync(self, *, full: bool = False) -> SyncResult:
        """重读父页面、按内容哈希去重后重建缓存（单页，无分页游标）。"""
        state = self.repo.get_sync_state()
        result = SyncResult(mode="full" if full else "incremental")
        try:
            page = self.client.get_page(self.parent_page_id)
            top_blocks = self.client.list_all_block_children(self.parent_page_id)
        except Exception as exc:  # 远端不可用：保留缓存，如实报错。
            result.errors.append(str(exc))
            return result
        for block in top_blocks:
            if block.get("type") != "toggle":
                continue
            result.scanned += 1
            try:
                children = self.client.list_all_block_children(block["id"])
                snapshot = toggle_to_snapshot(page, block, children)
            except Exception as exc:
                result.errors.append(f"{block['id']}: {exc}")
                continue
            existing = self.repo.get_snapshot(block["id"])
            if existing is not None and existing.source_hash == snapshot.source_hash:
                result.unchanged += 1
                continue
            self.repo.save_snapshot(snapshot)
            result.updated += 1
        if not result.errors:
            now = datetime.now(UTC)
            state = SyncState(committed_scan_boundary=now, last_full_sync_at=now)
            self.repo.save_sync_state(state)
            result.committed_scan_boundary = state.committed_scan_boundary
            result.last_full_sync_at = state.last_full_sync_at
        return result

    # ---- P2：讨论回写与提升 ----

    def record_discussion(
        self,
        *,
        block_id: str,
        user_judgment: str,
        ai_proposals: list[str] | None = None,
        open_questions: list[str] | None = None,
        action: str = "",
    ) -> DiscussionRecord:
        """保存讨论记录，并排队回写（追加讨论块到该 toggle 的 children）。"""
        snapshot = self.repo.get_snapshot(block_id)
        if snapshot is None:
            snapshot = self.read(block_id)
        discussion_id = discussion_id_for(block_id, snapshot.source_hash)
        record = DiscussionRecord(
            discussion_id=discussion_id,
            block_id=block_id,
            page_id=snapshot.page_id,
            source_hash=snapshot.source_hash,
            user_judgment=user_judgment,
            ai_proposals=list(ai_proposals or []),
            open_questions=list(open_questions or []),
            action=action,
        )
        self.repo.save_discussion(record)
        append_op = SyncOperation(
            operation_id=append_discussion_operation_id_for(discussion_id),
            type="append_discussion",
            target_block=block_id,
            payload={"children": blocks_for_discussion(discussion_id, record)},
            expected_material_version=snapshot.source_hash,
        )
        self.queue.enqueue(append_op)
        record = record.model_copy(update={"writeback_operation_id": append_op.operation_id})
        self.repo.save_discussion(record)
        self.usage.append(
            MaterialUseLink(
                block_id=block_id,
                page_id=snapshot.page_id,
                source_hash=snapshot.source_hash,
                usage="discussion",
                discussion_ref=discussion_id,
            )
        )
        return record

    def promote(
        self,
        block_id: str,
        *,
        core_point: str,
        reader_problem: str = "",
        why_worth_saying: str = "",
        intent: Literal["stance", "exploration"] = "stance",
    ) -> ContentJob:
        """素材 → IdeaCandidate → ContentJob（幂等；重复提炼返回同一 job）。"""
        snapshot = self.repo.get_snapshot(block_id)
        if snapshot is None:
            snapshot = self.read(block_id)
        discussions = self.repo.list_discussions(block_id)
        discussion = discussions[-1] if discussions else None
        candidate = MaterialPromotionService().from_material(
            snapshot,
            core_point=core_point,
            reader_problem=reader_problem,
            why_worth_saying=why_worth_saying,
            intent=intent,
            discussion=discussion,
        )
        job = IdeaService(ContentJobRepository(self.ws)).create_candidate(candidate)
        self.usage.append(
            MaterialUseLink(
                block_id=block_id,
                page_id=snapshot.page_id,
                source_hash=snapshot.source_hash,
                usage="promoted",
                candidate_ref=job.id,
                job_ref=job.id,
            )
        )
        return job
