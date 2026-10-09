"""写队列：``pending → in_flight → succeeded / retryable_failed / blocked`` 状态机。

所有写操作先落本地队列再写远端；同一 ``operation_id`` 追加多行、最新行生效。
append_material 用 toggle 标题去重；append_discussion 用块标记 + 尾部补齐恢复部分写入。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from finch.materials.field_map import block_plain_text
from finch.materials.models import SyncOperation, SyncOperationStatus
from finch.materials.repository import SyncOperationLog
from finch.notion.client import NotionClient, NotionError

# 可重试的 HTTP 状态码（临时失败）；超时/网络错误（status_code=None）同样可重试。
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class DrainResult(BaseModel):
    processed: int = 0
    succeeded: int = 0
    retryable_failed: int = 0
    blocked: int = 0
    operations: list[SyncOperation] = Field(default_factory=list)


class WriteQueue:
    def __init__(
        self,
        log: SyncOperationLog,
        client: NotionClient,
        parent_page_id: str,
        *,
        max_attempts: int = 3,
    ) -> None:
        self.log = log
        self.client = client
        self.parent_page_id = parent_page_id
        self.max_attempts = max_attempts

    def enqueue(self, op: SyncOperation) -> SyncOperation:
        """幂等入队：同 operation_id 已存在则不重复追加。"""
        if self.log.get(op.operation_id) is None:
            self.log.append(op)
        return op

    def drain(self, *, limit: int | None = None) -> DrainResult:
        """执行待同步操作（pending / retryable_failed），逐条推进状态。"""
        result = DrainResult()
        for op in self.log.list_pending():
            if limit is not None and result.processed >= limit:
                break
            updated = self._execute(op)
            result.processed += 1
            if updated.status == SyncOperationStatus.SUCCEEDED:
                result.succeeded += 1
            elif updated.status == SyncOperationStatus.RETRYABLE_FAILED:
                result.retryable_failed += 1
            elif updated.status == SyncOperationStatus.BLOCKED:
                result.blocked += 1
            result.operations.append(updated)
        return result

    def _execute(self, op: SyncOperation) -> SyncOperation:
        started = op.model_copy(
            update={
                "status": SyncOperationStatus.IN_FLIGHT,
                "attempts": op.attempts + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self.log.append(started)
        try:
            if started.type == "append_material":
                finished = self._execute_append_material(started)
            else:
                finished = self._execute_append_discussion(started)
        except NotionError as exc:
            finished = self._mark_failure(started, exc)
        self.log.append(finished)
        return finished

    def _execute_append_material(self, op: SyncOperation) -> SyncOperation:
        children: list[dict] = op.payload.get("children", [])
        if not children:
            return self._transition(op, SyncOperationStatus.SUCCEEDED)
        toggle = children[0]
        title = block_plain_text(toggle)
        page_id = op.target_block or self.parent_page_id
        # 重试前先查：响应丢失但块已落地 → 按 toggle 标题去重恢复，不重复追加。
        if op.attempts > 1:
            for block in self.client.list_all_block_children(page_id):
                if block.get("type") == "toggle" and block_plain_text(block) == title:
                    return self._transition(
                        op, SyncOperationStatus.SUCCEEDED,
                        remote_result={"already_appended": True, "block_id": block["id"]},
                    )
        resp = self.client.append_block_children(page_id, children)
        created = (resp.get("results") or [{}])[0]
        return self._transition(
            op, SyncOperationStatus.SUCCEEDED, remote_result={"block_id": created.get("id")}
        )

    def _execute_append_discussion(self, op: SyncOperation) -> SyncOperation:
        children: list[dict] = op.payload.get("children", [])
        if not children:
            return self._transition(op, SyncOperationStatus.SUCCEEDED)
        toggle_id = op.target_block
        if not toggle_id:
            return self._transition(
                op, SyncOperationStatus.BLOCKED, last_error="append_discussion missing target_block"
            )
        marker_text = block_plain_text(children[0])
        content = children[1:]
        existing = self.client.list_all_block_children(toggle_id)
        idx = next(
            (i for i, block in enumerate(existing) if block_plain_text(block) == marker_text), -1
        )
        if idx == -1:
            self.client.append_block_children(toggle_id, children)
            return self._transition(
                op, SyncOperationStatus.SUCCEEDED, remote_result={"appended": len(children)}
            )

        tail = existing[idx + 1 :]
        matched = 0
        for j, expected in enumerate(content):
            if j >= len(tail) or block_plain_text(tail[j]) != block_plain_text(expected):
                break
            matched = j + 1
        if matched >= len(content):
            return self._transition(
                op, SyncOperationStatus.SUCCEEDED, remote_result={"already_complete": True}
            )
        if matched == 0 and tail:
            # 标记在但内容对不上（用户已编辑）——不覆盖、不重复追加。
            return self._transition(
                op, SyncOperationStatus.SUCCEEDED, remote_result={"skipped_user_edited": True}
            )
        to_append = content[matched:]
        self.client.append_block_children(toggle_id, to_append)
        return self._transition(
            op, SyncOperationStatus.SUCCEEDED, remote_result={"appended": len(to_append)}
        )

    def _mark_failure(self, op: SyncOperation, exc: NotionError) -> SyncOperation:
        if exc.status_code in _RETRYABLE_STATUS or exc.status_code is None:
            if op.attempts >= self.max_attempts:
                return self._transition(op, SyncOperationStatus.BLOCKED, last_error=str(exc))
            return self._transition(op, SyncOperationStatus.RETRYABLE_FAILED, last_error=str(exc))
        return self._transition(op, SyncOperationStatus.BLOCKED, last_error=str(exc))

    def _transition(
        self,
        op: SyncOperation,
        status: SyncOperationStatus,
        *,
        last_error: str | None = None,
        remote_result: dict | None = None,
        target_block: str | None = None,
    ) -> SyncOperation:
        updates: dict = {"status": status, "updated_at": datetime.now(UTC)}
        if last_error is not None:
            updates["last_error"] = last_error
        if remote_result is not None:
            updates["remote_result"] = remote_result
        if target_block is not None:
            updates["target_block"] = target_block
        return op.model_copy(update=updates)
