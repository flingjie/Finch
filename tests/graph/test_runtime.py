
from finch.graph.nodes import FailingNode, NoopNode
from finch.graph.runtime import GraphRuntime
from finch.storage.database import Store


def _store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.init()
    return s


def test_empty_graph_reaches_completed(tmp_path):
    rt = GraphRuntime(_store(tmp_path), nodes=[])
    run = rt.run()
    assert run.state == "COMPLETED"


def test_successful_run_records_nodes(tmp_path):
    store = _store(tmp_path)
    rt = GraphRuntime(store, nodes=[NoopNode(name="a", idempotency_key="ka"),
                                    NoopNode(name="b", idempotency_key="kb")])
    run = rt.run()
    assert run.state == "COMPLETED"
    assert len(store.list_nodes(run.id)) == 2


def test_failed_node_marks_run_failed(tmp_path):
    store = _store(tmp_path)
    rt = GraphRuntime(store, nodes=[NoopNode(name="a", idempotency_key="ka"),
                                    FailingNode(name="boom", idempotency_key="kb",
                                                retryable=False)])
    run = rt.run()
    assert run.state == "FAILED"
    # 失败节点也落记录
    assert store.find_node(run.id, "boom", "kb") is not None


def test_recovery_skips_completed_nodes(tmp_path):
    store = _store(tmp_path)
    rt = GraphRuntime(store, nodes=[NoopNode(name="a", idempotency_key="ka"),
                                    FailingNode(name="boom", idempotency_key="kb",
                                                retryable=True)])
    run1 = rt.run()
    assert run1.state == "FAILED"

    # 第二次用同一 run_id 恢复：a 已成功跳过，boom 重试。这里换成可成功的节点来模拟"修复后重跑"。
    rt2 = GraphRuntime(store, nodes=[NoopNode(name="a", idempotency_key="ka"),
                                     NoopNode(name="boom", idempotency_key="kb")])
    run2 = rt2.run(run_id=run1.id)
    assert run2.state == "COMPLETED"
