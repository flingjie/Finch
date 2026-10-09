"""素材库领域模型：素材快照 / 讨论记录 / 同步操作 / 同步状态 / 用途追溯。

Notion 是权威来源；本地只存读取缓存（``MaterialSnapshot``）、待同步操作队列
（``SyncOperation``）、同步状态（``SyncState``）与讨论工作状态（``DiscussionRecord``），
不另建一套需要用户维护的素材库。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class MaterialSnapshot(BaseModel):
    """Notion 素材页的本地读取缓存（每次刷新覆盖）。"""

    notion_page_id: str
    page_url: str
    title: str
    raw_body_blocks: list[dict] = Field(default_factory=list)
    extractable_text: str = ""  # 仅用户区（原始记录 + 我的感触）正文
    user_reflection: str | None = None  # 「我的感触」区文本，可空
    source_urls: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    discussed: bool = False
    remote_edited_at: datetime | None = None
    source_hash: str = ""  # 仅对用户区文本算（排除 Finch 追加区）
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DiscussionRecord(BaseModel):
    """一次讨论后写回 Notion 的记录（与 topic-dialogue 的 DialogueNote 分开）。"""

    discussion_id: str
    notion_page_id: str
    source_hash: str  # 所依据的素材版本
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_judgment: str = ""
    ai_proposals: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    action: str = ""
    writeback_operation_id: str | None = None


class SyncOperationStatus(StrEnum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILED = "retryable_failed"
    BLOCKED = "blocked"


class SyncOperation(BaseModel):
    """一条待同步写操作（先落本地队列，再写远端）。"""

    operation_id: str
    type: Literal["create_material", "append_discussion", "patch_user_field"]
    target_page: str | None = None  # 页 id；create 在远端成功前为 None
    payload: dict = Field(default_factory=dict)
    status: SyncOperationStatus = SyncOperationStatus.PENDING
    attempts: int = 0
    last_error: str = ""
    expected_material_version: str = ""
    remote_result: dict | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SyncState(BaseModel):
    """同步范围与断点（增量扫描的水位线）。"""

    sync_range_marker: datetime | None = None
    committed_scan_boundary: datetime | None = None
    in_progress_cursor: str | None = None
    last_full_sync_at: datetime | None = None


class MaterialUseLink(BaseModel):
    """素材用途追溯（不新增内容状态机）。"""

    notion_page_id: str
    source_hash: str
    usage: Literal["read", "discussion", "promoted"]
    candidate_ref: str | None = None
    job_ref: str | None = None
    discussion_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def material_id_for(notion_page_id: str) -> str:
    """页 id → 稳定素材 id（幂等覆盖同一缓存文件）。"""
    digest = hashlib.sha256(notion_page_id.strip().encode("utf-8")).hexdigest()
    return f"mat_{digest[:12]}"


def source_hash_for(user_text: str) -> str:
    """用户区文本 → 内容哈希（排除 Finch 追加区，避免写回触发再同步）。"""
    return hashlib.sha256(user_text.strip().encode("utf-8")).hexdigest()


def discussion_id_for(notion_page_id: str, source_hash: str) -> str:
    raw = f"{notion_page_id}:{source_hash}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"disc_{digest[:12]}"


def create_operation_id_for(title: str, body_text: str, source_urls: list[str]) -> str:
    raw = "\x1f".join([title.strip(), body_text.strip(), ",".join(sorted(source_urls))])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"sync_create_{digest[:12]}"


def append_operation_id_for(discussion_id: str) -> str:
    return f"sync_append_{discussion_id}"


def patch_operation_id_for(notion_page_id: str, field: str) -> str:
    raw = f"{notion_page_id}:{field}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"sync_patch_{digest[:12]}"
