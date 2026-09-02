# tests/graph/test_replay.py
from finch.graph.nodes import FailingNode, NoopNode
from finch.graph.replay import replay
from finch.graph.runtime import GraphRuntime
from finch.storage.database import Store


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

    # 从 b 重放：a 跳过，b、c 重新执行，仍 COMPLETED。
    run2 = replay(store, nodes, run_id=run.id, from_node="b")
    assert run2.state == "COMPLETED"
    assert len(store.list_nodes(run.id)) == 3  # 复用 a，不重复插入
