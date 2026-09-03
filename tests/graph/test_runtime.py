# tests/graph/test_runtime.py
from finch.graph.events import NodeResult
from finch.graph.nodes import FailingNode, Node, NoopNode
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


# New tests for GraphContext

class ProducerNode(Node):
    def run(self, ctx: dict) -> NodeResult:
        return NodeResult(status="succeeded", output={"items": [{"id": "x"}]})


class ConsumerNode(Node):
    def run(self, ctx: dict) -> NodeResult:
        items = ctx["cards"]["items"]
        return NodeResult(status="succeeded", output={"n": len(items)})


class BoomAfterProduce(Node):
    def run(self, ctx: dict) -> NodeResult:
        return NodeResult(status="failed", error_code="E_BOOM", retryable=True)


def test_context_passes_between_nodes(tmp_path):
    store = _store(tmp_path)
    nodes = [
        ProducerNode(name="p", writes="cards", succeeds_to="EVENTS_EXTRACTED"),
        ConsumerNode(name="c", reads=["cards"], writes="out", succeeds_to="EVIDENCE_MATCHED"),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "EVIDENCE_MATCHED"
    out = store.find_node(run.id, "c", "default")
    assert '"n": 1' in out.output_json or '"n":1' in out.output_json.replace(" ", "")


def test_recovery_hydrates_skipped_producer(tmp_path):
    store = _store(tmp_path)
    run1 = GraphRuntime(store, [
        ProducerNode(name="p", writes="cards"),
        BoomAfterProduce(name="c", reads=["cards"]),
    ]).run()
    assert run1.state == "FAILED"
    run2 = GraphRuntime(store, [
        ProducerNode(name="p", writes="cards"),
        ConsumerNode(name="c", reads=["cards"], writes="out"),
    ]).run(run_id=run1.id)
    assert run2.state == "COMPLETED"
    rec = store.find_node(run2.id, "c", "default")
    assert rec.status == "succeeded"


def test_missing_read_fails_without_silent_drop(tmp_path):
    store = _store(tmp_path)
    run = GraphRuntime(store, [ConsumerNode(name="c", reads=["cards"])]).run()
    assert run.state == "FAILED"
    rec = store.find_node(run.id, "c", "default")
    assert rec.error_code == "MISSING_CONTEXT"


def test_blocked_error_sets_blocked_state(tmp_path):
    class Blocked(Node):
        def run(self, ctx: dict) -> NodeResult:
            return NodeResult(status="failed", error_code="BLOCKED", retryable=False)
    run = GraphRuntime(_store(tmp_path), [Blocked(name="pre")]).run()
    assert run.state == "BLOCKED"


class NeedsInputNode(Node):
    def run(self, ctx: dict) -> NodeResult:
        return NodeResult(status="needs_input", output={"items": [{"id": "j1"}]})


def test_needs_input_stops_pipeline_and_marks_run(tmp_path):
    store = _store(tmp_path)
    nodes = [
        NoopNode(name="a", idempotency_key="ka"),
        NeedsInputNode(name="gate", idempotency_key="kb"),
        NoopNode(name="c", idempotency_key="kc"),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "NEEDS_INPUT"
    gate = store.find_node(run.id, "gate", "kb")
    assert gate is not None and gate.status == "needs_input"
    # downstream node never runs
    assert store.find_node(run.id, "c", "kc") is None


class DynNode(Node):
    def run(self, ctx: dict) -> NodeResult:
        return NodeResult(status="succeeded", output={"terminal_state": "WAITING_FOR_REVIEW"})


def test_terminal_state_key_overrides_succeeds_to(tmp_path):
    node = DynNode(name="n", succeeds_to="DRAFTED", terminal_state_key="terminal_state")
    run = GraphRuntime(_store(tmp_path), [node]).run()
    assert run.state == "WAITING_FOR_REVIEW"


def test_terminal_state_key_replay(tmp_path):
    store = _store(tmp_path)
    node = DynNode(name="n", writes="brief", terminal_state_key="terminal_state")
    r1 = GraphRuntime(store, [node]).run()
    assert r1.state == "WAITING_FOR_REVIEW"
    r2 = GraphRuntime(store, [node]).run(run_id=r1.id)
    assert r2.state == "WAITING_FOR_REVIEW"  # 从持久化 output 重放，非回落 succeeds_to
