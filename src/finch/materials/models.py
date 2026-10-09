"""素材库领域模型：素材快照 / 讨论记录 / 同步操作 / 同步状态 / 用途追溯。

素材以 Notion 的 toggle（``<details>``）块存放在月度页面里，Notion 是权威来源；
本地只存读取缓存、待同步操作队列、同步状态与讨论工作状态，不另建素材库。
一条素材 = 父页面里的一个 toggle 块，``block_id`` 是其唯一标识。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class MaterialSnapshot(BaseModel):
    """一条素材（toggle 块）的本地读取缓存。"""

    block_id: str  # toggle 块 id（素材唯一标识）
    page_id: str  # 父页面（月度页）id
    page_url: str
    title: str  # toggle summary
    raw_body_blocks: list[dict] = Field(default_factory=list)  # toggle 的 children
    extractable_text: str = ""  # 仅用户区（原始记录 + 我的感触）
    user_reflection: str | None = None  # 「我的感触」区文本，可空
    discussed: bool = False  # 派生：children 是否含 Finch 讨论记录
    remote_edited_at: datetime | None = None  # 父页面 last_edited_time
    source_hash: str = ""  # 仅对用户区文本算（排除 Finch 追加区）
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DiscussionRecord(BaseModel):
    """一次讨论后写回 Notion 的记录（与 topic-dialogue 的 DialogueNote 分开）。"""

    discussion_id: str
    block_id: str  # 素材（toggle）id
    page_id: str  # 父页面 id
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
    type: Literal["append_material", "append_discussion"]
    target_block: str | None = None  # append_material=父页 id；append_discussion=toggle id
    payload: dict = Field(default_factory=dict)
    status: SyncOperationStatus = SyncOperationStatus.PENDING
    attempts: int = 0
    last_error: str = ""
    expected_material_version: str = ""
    remote_result: dict | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SyncState(BaseModel):
    """同步范围与断点（父页面级，单页重读即可，无分页游标）。"""

    committed_scan_boundary: datetime | None = None
    last_full_sync_at: datetime | None = None


class MaterialUseLink(BaseModel):
    """素材用途追溯（不新增内容状态机）。"""

    block_id: str
    page_id: str
    source_hash: str
    usage: Literal["read", "discussion", "promoted"]
    candidate_ref: str | None = None
    job_ref: str | None = None
    discussion_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def material_id_for(block_id: str) -> str:
    """toggle 块 id → 稳定素材 id（幂等覆盖同一缓存文件）。"""
    digest = hashlib.sha256(block_id.strip().encode("utf-8")).hexdigest()
    return f"mat_{digest[:12]}"


def source_hash_for(user_text: str) -> str:
    """用户区文本 → 内容哈希（排除 Finch 追加区，避免写回触发再同步）。"""
    return hashlib.sha256(user_text.strip().encode("utf-8")).hexdigest()


def discussion_id_for(block_id: str, source_hash: str) -> str:
    raw = f"{block_id}:{source_hash}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"disc_{digest[:12]}"


def append_material_operation_id_for(title: str, body_text: str) -> str:
    raw = "\x1f".join([title.strip(), body_text.strip()])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"sync_mat_{digest[:12]}"


def append_discussion_operation_id_for(discussion_id: str) -> str:
    return f"sync_disc_{discussion_id}"
