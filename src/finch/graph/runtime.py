"""确定性 Graph Runtime：顺序执行、幂等、失败与恢复。"""

import json
from datetime import UTC, datetime
from uuid import uuid4

from ..storage.database import NodeRecord, RunRecord, Store
from .context import GraphContext, MissingContextError
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
        ctx = GraphContext()
        for node in self.nodes:
            existing = self.store.find_node(run_id, node.name, node.idempotency_key)
            if existing is not None and existing.status == "succeeded":
                ctx.hydrate(node.writes, existing.output_json)
                if node.succeeds_to:
                    final_state = GraphState(node.succeeds_to)
                continue
            try:
                projected = ctx.project(node.reads)
            except MissingContextError:
                result = NodeResult(status="failed", error_code="MISSING_CONTEXT", retryable=False)
                self._persist_node(run_id, node, result)
                final_state = GraphState.FAILED
                break
            result = self._safe_run(node, projected)
            self._persist_node(run_id, node, result)
            if result.status == "failed":
                final_state = (
                    GraphState.BLOCKED if result.error_code == "BLOCKED" else GraphState.FAILED
                )
                break
            ctx.put(node.writes, result.output)
            if node.succeeds_to:
                final_state = GraphState(node.succeeds_to)

        self.store.upsert_run(
            RunRecord(id=run_id, state=final_state.value, updated_at=_utcnow())
        )
        run = self.store.get_run(run_id)
        assert run is not None
        return run

    def _safe_run(self, node: Node, ctx: dict) -> NodeResult:
        try:
            return node.run(ctx)
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
