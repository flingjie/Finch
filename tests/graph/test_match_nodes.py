from datetime import UTC, datetime

from finch.codex.runner import CodexRunner
from finch.evidence.judge import BatchJudgeItem, BatchJudgeOutput
from finch.evidence.models import ClaimConfidence, EvidenceCard, JudgeScores
from finch.evidence.scoring import formula_score
from finch.graph.context import items_payload
from finch.graph.events import NodeResult
from finch.graph.match_nodes import make_match_node, make_recall_node
from finch.graph.nodes import Node
from finch.graph.runtime import GraphRuntime
from finch.settings import QualityGates, TwitterSettings
from finch.storage.database import Store
from finch.twitter.models import DiscussionCandidate


def _store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.init()
    return s


class Seed(Node):
    model_config = {"extra": "allow"}

    def run(self, ctx: dict) -> NodeResult:
        return NodeResult(status="succeeded", output=self.seed)


def _card() -> EvidenceCard:
    return EvidenceCard(
        id="ev1", event_id="e", claim="token bucket rate limiting", sources=[],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=["rate"],
    )


def _cand() -> DiscussionCandidate:
    return DiscussionCandidate(
        id="t1", author_handle="u", text="token bucket for the agent loop",
        url="https://x.com/u/status/1",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_recall_node_writes_ranked(tmp_path):
    gates = QualityGates(match_top_k=10)
    store = _store(tmp_path)
    nodes = [
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_cand()])),
        make_recall_node(gates),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "CANDIDATES_RANKED"
    rec = store.find_node(run.id, "recall", "default")
    assert rec is not None
    assert "t1" in rec.output_json


class FakeJudgeRunner(CodexRunner):
    def __init__(self, scores: JudgeScores):
        self.calls = 0
        self.scores = scores

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return BatchJudgeOutput(
            items=[BatchJudgeItem(candidate_id="t1", scores=self.scores)]
        )


def test_match_node_scores_and_preserves_recalled_cards(tmp_path):
    scores = JudgeScores(
        relevance=0.9, evidence_strength=0.9, incremental_value=0.9, discussability=0.9,
    )
    runner = FakeJudgeRunner(scores)
    gates = QualityGates()
    store = _store(tmp_path)
    nodes = [
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_cand()])),
        make_recall_node(gates),
        make_match_node(runner, gates, TwitterSettings()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "EVIDENCE_MATCHED"
    rec = store.find_node(run.id, "match_evidence", "default")
    assert rec is not None
    assert "ev1" in rec.output_json
    expected = formula_score(scores, timing=1.0, relationship_value=0.5)
    assert str(expected)[:4] in rec.output_json.replace(" ", "")


def test_empty_ranked_skips_codex(tmp_path):
    scores = JudgeScores(
        relevance=0.9, evidence_strength=0.9, incremental_value=0.9, discussability=0.9,
    )
    runner = FakeJudgeRunner(scores)
    gates = QualityGates()
    unrelated = DiscussionCandidate(
        id="t9", author_handle="u", text="banana muffin recipe",
        url="https://x.com/u/status/9",
    )
    store = _store(tmp_path)
    nodes = [
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([unrelated])),
        make_recall_node(gates),
        make_match_node(runner, gates, TwitterSettings()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "EVIDENCE_MATCHED"
    assert runner.calls == 0
    rec = store.find_node(run.id, "match_evidence", "default")
    assert rec is not None
    assert rec.output_json.replace(" ", "") in ('{"items":[]}', '{"items": []}')
