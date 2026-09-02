"""确定性 Graph Runtime：顺序执行、幂等、失败与恢复。"""

import json
from datetime import UTC, datetime
from uuid import uuid4

from ..storage.database import NodeRecord, RunRecord, Store
from .events import NodeResult
from .nodes import Node
from .state import GraphState


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GraphRuntime:
    def __init__(self, store: Store, nodes: list[Node]):
        self.store = store
        self.nodes = nodes

    def run(self, run_id: str | None = None) -> RunRecord:
        run_id = run_id or uuid4().hex
        self.store.upsert_run(
            RunRecord(id=run_id, state=GraphState.CREATED.value, updated_at=_utcnow())
        )

        final_state = GraphState.COMPLETED
        for node in self.nodes:
            existing = self.store.find_node(run_id, node.name, node.idempotency_key)
            if existing is not None and existing.status == "succeeded":
                continue

            result = self._safe_run(node)
            self._persist_node(run_id, node, result)

            # Phase 1 不自动重试；retryable/max_retries 留待后续 Phase 实现重试策略。
            if result.status == "failed":
                final_state = GraphState.FAILED
                break

        self.store.upsert_run(
            RunRecord(id=run_id, state=final_state.value, updated_at=_utcnow())
        )
        run = self.store.get_run(run_id)
        assert run is not None
        return run

    def _safe_run(self, node: Node) -> NodeResult:
        try:
            return node.run({})
        except Exception as exc:  # noqa: BLE001
            return NodeResult(status="failed", retryable=True, error_code=type(exc).__name__)

    def _persist_node(self, run_id: str, node: Node, result: NodeResult) -> None:
        record = NodeRecord(
            id=f"{run_id}:{node.name}:{node.idempotency_key}",
            run_id=run_id,
            node_name=node.name,
            idempotency_key=node.idempotency_key,
            status=result.status,
            output_json=json.dumps(result.output),
            error_code=result.error_code,
            created_at=_utcnow(),
        )
        self.store.upsert_node(record)
