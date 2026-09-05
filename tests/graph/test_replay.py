# tests/graph/test_replay.py
from finch.graph.events import NodeResult
from finch.graph.nodes import Node, NoopNode
from finch.graph.replay import replay
from finch.graph.runtime import GraphRuntime
from finch.storage.database import Store


class CountingNode(Node):
    """记录 run() 被实际执行的次数（不变量：replay 不得重跑已成功节点）。"""

    model_config = {"extra": "allow"}

    def run(self, ctx: dict) -> NodeResult:
        self.calls.append(self.name)
        return NodeResult(status="succeeded")


def _store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.init()
    return s


def test_replay_from_node_reuses_prior_results(tmp_path):
    store = _store(tmp_path)
    nodes = [NoopNode(name="a", idempotency_key="ka"),
             NoopNode(name="b", idempotency_key="kb"),
             NoopNode(name="c", idempotency_key="kc")]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "COMPLETED"

    # 从 b 重放：已成功节点全部复用（幂等），不重复写入，仍 COMPLETED。
    run2 = replay(store, nodes, run_id=run.id, from_node="b")
    assert run2.state == "COMPLETED"
    assert len(store.list_nodes(run.id)) == 3  # 复用 a，不重复插入


def test_replay_does_not_rerun_succeeded_nodes(tmp_path):
    """不变量（计划 §6 Task 0.3）：replay 复用已成功节点记录，不再次执行 run()。"""
    store = _store(tmp_path)
    calls: list[str] = []

    nodes = [
        CountingNode(name="a", idempotency_key="ka", calls=calls),
        CountingNode(name="b", idempotency_key="kb", calls=calls),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "COMPLETED"
    assert calls == ["a", "b"]

    # 全量 replay（from_node=None）：已成功的 a/b 必须被跳过，run() 不再执行。
    run2 = replay(store, nodes, run_id=run.id)
    assert run2.state == "COMPLETED"
    assert calls == ["a", "b"]
    assert len(store.list_nodes(run.id)) == 2
