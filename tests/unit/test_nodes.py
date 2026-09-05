# tests/unit/test_nodes.py
from finch.graph.content_nodes import make_define_jobs_node
from finch.graph.nodes import FailingNode, NoopNode
from finch.settings import DailyBudget


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


def test_node_context_contract_defaults():
    n = NoopNode(name="n")
    assert n.reads == []
    assert n.writes == ""
    assert n.succeeds_to == ""


def test_node_terminal_state_key_default():
    assert NoopNode(name="n").terminal_state_key == ""


class _EchoPlanRunner:
    def run(self, prompt, model, timeout=None):
        # 记录最近一次传入的卡片 id 数（用于断言裁剪生效）
        from finch.content.jobs import PlanTopicsOutput
        self.last_prompt = prompt
        return PlanTopicsOutput(items=[])


def test_define_jobs_trims_planning_cards_when_budget_set():
    from finch.evidence.models import ClaimConfidence, EvidenceCard
    from finch.graph.context import items_payload

    cards = [
        EvidenceCard(id=f"ev_{i}", event_id=f"evt_{i}", claim="c", sources=[],
                     confidence=ClaimConfidence.VERIFIED, publishable=True, topics=[])
        for i in range(30)
    ]
    runner = _EchoPlanRunner()
    node = make_define_jobs_node(runner, runner, jobs_repo=None,
                                 budget=DailyBudget(max_planning_events=5))
    # 直接调用 run，传入仅有 evidence_cards 的 context（其余读键用空 payload）
    result = node.run({
        "evidence_cards": items_payload(cards),
        "match_results": items_payload([]),
        "candidates": items_payload([]),
    })
    assert result.status == "succeeded"
    assert '"ev_4"' in runner.last_prompt  # 裁剪后保留前 5 个 event（每个 event 1 卡）
    assert '"ev_5"' not in runner.last_prompt
