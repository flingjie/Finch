"""Writer/Critic/Daily Brief 节点 7–9 测试（Phase 5 Task F6）。"""

from finch.codex.runner import CodexRunner
from finch.content.critic import CritiqueResult
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard, JudgeScores, MatchResult
from finch.graph.content_nodes import make_brief_node, make_critique_node, make_draft_node
from finch.graph.context import items_payload
from finch.graph.events import NodeResult
from finch.graph.nodes import Node
from finch.graph.runtime import GraphRuntime
from finch.settings import QualityGates
from finch.storage.database import Store
from finch.twitter.models import DiscussionCandidate


def _store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.init()
    return s


class Seed(Node):
    model_config = {"extra": "allow"}

    def run(self, ctx):
        return NodeResult(status="succeeded", output=self.seed)


def test_brief_node_terminal_state(tmp_path):
    # 无稿 → COMPLETED
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([])),
        make_brief_node(QualityGates()),
    ]
    run = GraphRuntime(_store(tmp_path), nodes).run()
    assert run.state == "COMPLETED"


def test_brief_node_waiting_when_drafts(tmp_path):
    from finch.content.models import Draft, DraftKind

    d = Draft(id="d", kind=DraftKind.REPLY, candidate_id="t", body="hi", claims=[])
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([d])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([])),
        make_brief_node(QualityGates()),
    ]
    run = GraphRuntime(_store(tmp_path), nodes).run()
    assert run.state == "WAITING_FOR_REVIEW"


def _card():
    return EvidenceCard(
        id="ev1",
        event_id="e",
        claim="token bucket rate limiting",
        sources=[],
        confidence=ClaimConfidence.VERIFIED,
        publishable=True,
        topics=["rate"],
    )


def _match():
    return MatchResult(
        candidate_id="t1",
        card_ids=["ev1"],
        scores=JudgeScores(
            relevance=0.9, evidence_strength=0.9, incremental_value=0.9, discussability=0.9
        ),
        timing=1.0,
        relationship_value=0.5,
        score=0.9,
    )


def _candidate():
    return DiscussionCandidate(
        id="t1",
        author_handle="u",
        text="token bucket for the agent loop",
        url="https://x.com/u/status/1",
    )


def _reply_draft():
    return Draft(
        id="d1",
        kind=DraftKind.REPLY,
        candidate_id="t1",
        language="en",
        body="hi",
        claims=[
            ClaimRef(statement="x", evidence_card_id="ev1", confidence=ClaimConfidence.VERIFIED)
        ],
    )


def test_draft_node_writes_reply_and_original(tmp_path):
    original = Draft(
        id="d2",
        kind=DraftKind.ORIGINAL,
        candidate_id=None,
        language="zh",
        body="日记",
        claims=[
            ClaimRef(statement="x", evidence_card_id="ev1", confidence=ClaimConfidence.VERIFIED)
        ],
    )

    def write_reply(runner, match, candidate, cards_by_id):
        return _reply_draft()

    def write_original(runner, cards):
        return original

    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_draft_node(CodexRunner(), write_reply, write_original, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "draft", "default")
    assert rec is not None
    assert "d1" in rec.output_json and "d2" in rec.output_json


def test_draft_node_empty_match_writes_empty(tmp_path):
    calls = {"reply": 0, "original": 0}

    def write_reply(runner, match, candidate, cards_by_id):
        calls["reply"] += 1
        return _reply_draft()

    def write_original(runner, cards):
        calls["original"] += 1
        return _reply_draft()

    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([])),
        make_draft_node(CodexRunner(), write_reply, write_original, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "draft", "default")
    assert rec is not None
    assert rec.output_json.replace(" ", "") == '{"items":[]}'
    assert calls == {"reply": 0, "original": 0}


def test_critique_node_rewrites_until_pass(tmp_path):
    calls = {"critique": 0, "rewrite": 0}
    fixed = _reply_draft().model_copy(update={"body": "v2"})

    def critique(runner, draft, cards_by_id):
        calls["critique"] += 1
        if calls["critique"] == 1:
            return CritiqueResult(passed=False, quality_score=0.5)
        return CritiqueResult(passed=True, quality_score=0.8)

    def rewrite(runner, draft, critique_result, cards_by_id):
        calls["rewrite"] += 1
        return fixed

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([_reply_draft()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_critique_node(CodexRunner(), rewrite, critique, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert "v2" in rec.output_json
    assert calls == {"critique": 2, "rewrite": 1}


def test_critique_node_drops_unfixable_draft(tmp_path):
    def critique(runner, draft, cards_by_id):
        return CritiqueResult(passed=False, quality_score=0.5)

    def rewrite(runner, draft, critique_result, cards_by_id):
        return draft.model_copy(update={"body": "v2"})

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([_reply_draft()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_critique_node(
            CodexRunner(), rewrite, critique, QualityGates(max_rewrite_rounds=2)
        ),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert rec.output_json.replace(" ", "") == '{"items":[]}'


def test_critique_node_keeps_draft_fixed_by_single_rewrite(tmp_path):
    calls = {"critique": 0, "rewrite": 0}
    fixed = _reply_draft().model_copy(update={"body": "fixed"})

    def critique(runner, draft, cards_by_id):
        calls["critique"] += 1
        if calls["critique"] == 1:
            return CritiqueResult(passed=False, quality_score=0.5)
        return CritiqueResult(passed=True, quality_score=0.8)

    def rewrite(runner, draft, critique_result, cards_by_id):
        calls["rewrite"] += 1
        return fixed

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([_reply_draft()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_critique_node(
            CodexRunner(), rewrite, critique, QualityGates(max_rewrite_rounds=1)
        ),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert "fixed" in rec.output_json
    assert calls == {"critique": 2, "rewrite": 1}


def test_critique_node_keeps_draft_fixed_by_second_rewrite(tmp_path):
    calls = {"critique": 0, "rewrite": 0}
    fixed = _reply_draft().model_copy(update={"body": "fixed"})

    def critique(runner, draft, cards_by_id):
        calls["critique"] += 1
        if calls["critique"] < 3:
            return CritiqueResult(passed=False, quality_score=0.5)
        return CritiqueResult(passed=True, quality_score=0.8)

    def rewrite(runner, draft, critique_result, cards_by_id):
        calls["rewrite"] += 1
        if calls["rewrite"] < 2:
            return draft
        return fixed

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([_reply_draft()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_critique_node(
            CodexRunner(), rewrite, critique, QualityGates(max_rewrite_rounds=2)
        ),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert "fixed" in rec.output_json
    assert calls == {"critique": 3, "rewrite": 2}


def test_critique_node_warns_on_invalid_rewritten_claims():
    invalid = _reply_draft().model_copy(
        update={
            "claims": [
                ClaimRef(
                    statement="x", evidence_card_id="ev_999", confidence=ClaimConfidence.VERIFIED
                )
            ]
        }
    )

    def critique(runner, draft, cards_by_id):
        return CritiqueResult(passed=False, quality_score=0.5)

    def rewrite(runner, draft, critique_result, cards_by_id):
        return invalid

    node = make_critique_node(CodexRunner(), rewrite, critique, QualityGates())
    result = node.run(
        {
            "drafts": items_payload([_reply_draft()]),
            "match_results": items_payload([_match()]),
            "evidence_cards": items_payload([_card()]),
        }
    )
    assert result.status == "succeeded"
    assert result.output["items"] == []
    assert any("invalid claims" in w for w in result.warnings)
