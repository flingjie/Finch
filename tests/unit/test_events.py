from finch.graph.events import DomainEvent, NodeResult


def test_node_result_contract_matches_spec():
    r = NodeResult(status="succeeded", output={"x": 1})
    assert r.warnings == []
    assert r.retryable is False
    assert r.error_code is None


def test_node_result_events_default_empty():
    r = NodeResult(status="failed", output={}, retryable=True, error_code="E1")
    assert r.events == []
