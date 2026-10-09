"""MaterialService：capture / read / list / search / sync / record_discussion / promote。

Notion 是权威来源；本地缓存 + 待同步队列 + 讨论工作状态。capture 先生成操作 ID、
落队列再写远端；read 读页 + 正文块 → 快照缓存；sync 增量（重叠时间窗 + 内容哈希去重），
满轮且无错才推进水位线。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from finch.content.jobs import ContentJob
from finch.ideas.service import IdeaService
from finch.materials.field_map import (
    blocks_for_create,
    blocks_for_discussion,
    page_to_snapshot,
    properties_for_create,
    properties_for_patch,
    query_filter_incremental,
    sorts_incremental,
    user_region_text,
)
from finch.materials.models import (
    DiscussionRecord,
    MaterialSnapshot,
    MaterialUseLink,
    SyncOperation,
    SyncState,
    append_operation_id_for,
    create_operation_id_for,
    discussion_id_for,
    patch_operation_id_for,
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
    mode: str = "incremental"
    scanned: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[str] = Field(default_factory=list)
    committed_scan_boundary: datetime | None = None
    last_full_sync_at: datetime | None = None


class MaterialService:
    def __init__(
        self,
        workspace: Workspace,
        client: NotionClient,
        database_id: str,
        *,
        overlap: timedelta = timedelta(hours=1),
    ) -> None:
        self.ws = workspace
        self.client = client
        self.database_id = database_id
        self.overlap = overlap
        self.repo = MaterialRepository(workspace)
        self.log = SyncOperationLog(workspace)
        self.usage = MaterialUseLinkRepository(workspace)
        self.queue = WriteQueue(self.log, client, database_id)

    # ---- P1：记录与读取 ----

    def capture(
        self,
        *,
        title: str,
        body_text: str,
        source_urls: list[str] | None = None,
        tags: list[str] | None = None,
        reflection: str | None = None,
    ) -> SyncOperation:
        """保存一条素材：先生成操作 ID、落队列，再（由 queue 写远端）。"""
        urls = list(source_urls or [])
        tag_list = list(tags or [])
        blocks = blocks_for_create(body_text, reflection)
        extractable = user_region_text(body_text, reflection)
        source_hash = source_hash_for(extractable)
        operation_id = create_operation_id_for(title, body_text, urls)
        op = SyncOperation(
            operation_id=operation_id,
            type="create_material",
            payload={"properties": properties_for_create(title, urls, tag_list, operation_id),
                     "children": blocks},
            expected_material_version=source_hash,
        )
        return self.queue.enqueue(op)

    def read(self, notion_page_id: str) -> MaterialSnapshot:
        """读页 + 正文块 → 快照缓存，并记一条 read 用途。"""
        page = self.client.get_page(notion_page_id)
        children = self.client.list_all_block_children(notion_page_id)
        snapshot = page_to_snapshot(page, children)
        self.repo.save_snapshot(snapshot)
        self.usage.append(
            MaterialUseLink(
                notion_page_id=notion_page_id, source_hash=snapshot.source_hash, usage="read"
            )
        )
        return snapshot

    def list_materials(
        self, *, tags: list[str] | None = None, discussed: bool | None = None
    ) -> list[MaterialSnapshot]:
        """本地缓存列表（可过滤），按远端修改时间倒序。"""
        rows = self.repo.list_snapshots()
        if tags:
            rows = [r for r in rows if any(tag in r.tags for tag in tags)]
        if discussed is not None:
            rows = [r for r in rows if r.discussed == discussed]
        return sorted(
            rows,
            key=lambda r: r.remote_edited_at or datetime(1970, 1, 1, tzinfo=UTC),
            reverse=True,
        )

    def search(self, query: str, *, limit: int = 3) -> list[MaterialSnapshot]:
        """关键词召回（标题 / 正文 / 感触 / 标签），按命中次数排序。"""
        q = query.strip().lower()
        if not q:
            return []
        scored: list[tuple[int, MaterialSnapshot]] = []
        for snapshot in self.repo.list_snapshots():
            haystack = "\n".join(
                [
                    snapshot.title,
                    snapshot.extractable_text,
                    snapshot.user_reflection or "",
                    " ".join(snapshot.tags),
                ]
            ).lower()
            if q not in haystack:
                continue
            score = sum(haystack.count(word) for word in q.split())
            scored.append((score, snapshot))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [snapshot for _, snapshot in scored[:limit]]

    def sync(self, *, full: bool = False) -> SyncResult:
        """增量（默认）或全量拉取；满轮且无错才推进水位线。"""
        state = self.repo.get_sync_state()
        mode = "full" if full else "incremental"
        result = SyncResult(mode=mode)
        filter_ = (
            None
            if full
            else query_filter_incremental(state.committed_scan_boundary, self.overlap)
        )
        try:
            pages = self.client.query_all(
                self.database_id, filter=filter_, sorts=sorts_incremental()
            )
        except Exception as exc:  # 远端不可用：保留缓存，如实报错。
            result.errors.append(str(exc))
            return result
        result.scanned = len(pages)
        boundary = state.committed_scan_boundary
        for page in pages:
            try:
                children = self.client.list_all_block_children(page["id"])
                snapshot = page_to_snapshot(page, children)
            except Exception as exc:
                result.errors.append(f"{page['id']}: {exc}")
                continue
            existing = self.repo.get_snapshot(page["id"])
            if existing is not None and existing.source_hash == snapshot.source_hash:
                result.unchanged += 1
                continue
            self.repo.save_snapshot(snapshot)
            result.updated += 1
            if snapshot.remote_edited_at is not None and (
                boundary is None or snapshot.remote_edited_at > boundary
            ):
                boundary = snapshot.remote_edited_at
        if not result.errors:
            now = datetime.now(UTC)
            if full:
                state = SyncState(committed_scan_boundary=now, last_full_sync_at=now)
            else:
                state = state.model_copy(update={"committed_scan_boundary": boundary})
            self.repo.save_sync_state(state)
            result.committed_scan_boundary = state.committed_scan_boundary
            result.last_full_sync_at = state.last_full_sync_at
        return result

    # ---- P2：讨论回写与提升 ----

    def record_discussion(
        self,
        *,
        notion_page_id: str,
        user_judgment: str,
        ai_proposals: list[str] | None = None,
        open_questions: list[str] | None = None,
        action: str = "",
    ) -> DiscussionRecord:
        """保存讨论记录，并排队回写（追加讨论块 + 已讨论标记）。"""
        snapshot = self.repo.get_snapshot(notion_page_id)
        if snapshot is None:
            snapshot = self.read(notion_page_id)
        discussion_id = discussion_id_for(notion_page_id, snapshot.source_hash)
        record = DiscussionRecord(
            discussion_id=discussion_id,
            notion_page_id=notion_page_id,
            source_hash=snapshot.source_hash,
            user_judgment=user_judgment,
            ai_proposals=list(ai_proposals or []),
            open_questions=list(open_questions or []),
            action=action,
        )
        self.repo.save_discussion(record)
        append_op = SyncOperation(
            operation_id=append_operation_id_for(discussion_id),
            type="append_discussion",
            target_page=notion_page_id,
            payload={"children": blocks_for_discussion(discussion_id, record)},
            expected_material_version=snapshot.source_hash,
        )
        self.queue.enqueue(append_op)
        patch_op = SyncOperation(
            operation_id=patch_operation_id_for(notion_page_id, "discussed"),
            type="patch_user_field",
            target_page=notion_page_id,
            payload={"properties": properties_for_patch(discussed=True)},
            expected_material_version=snapshot.source_hash,
        )
        self.queue.enqueue(patch_op)
        record = record.model_copy(update={"writeback_operation_id": append_op.operation_id})
        self.repo.save_discussion(record)
        self.usage.append(
            MaterialUseLink(
                notion_page_id=notion_page_id,
                source_hash=snapshot.source_hash,
                usage="discussion",
                discussion_ref=discussion_id,
            )
        )
        return record

    def promote(
        self,
        notion_page_id: str,
        *,
        core_point: str,
        reader_problem: str = "",
        why_worth_saying: str = "",
        intent: Literal["stance", "exploration"] = "stance",
    ) -> ContentJob:
        """素材 → IdeaCandidate → ContentJob（幂等；重复提炼返回同一 job）。"""
        snapshot = self.repo.get_snapshot(notion_page_id)
        if snapshot is None:
            snapshot = self.read(notion_page_id)
        discussions = self.repo.list_discussions(notion_page_id)
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
                notion_page_id=notion_page_id,
                source_hash=snapshot.source_hash,
                usage="promoted",
                candidate_ref=job.id,
                job_ref=job.id,
            )
        )
        return job
