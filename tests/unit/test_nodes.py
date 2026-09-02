# tests/unit/test_nodes.py
from finch.graph.nodes import FailingNode, NoopNode


def test_noop_node_succeeds():
    r = NoopNode(name="noop").run({})
    assert r.status == "succeeded"


def test_failing_node_returns_failed():
    r = FailingNode(name="boom", retryable=True, error_code="E_BOOM").run({})
    assert r.status == "failed"
    assert r.retryable is True
    assert r.error_code == "E_BOOM"


def test_node_carries_contract_fields():
    n = NoopNode(name="n", idempotency_key="k1", timeout_seconds=5.0)
    assert n.idempotency_key == "k1"
    assert n.side_effect == "none"
