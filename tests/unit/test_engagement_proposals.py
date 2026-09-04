"""互动策略与草稿生成单元测试（Phase 4，无网络、无 LLM）。"""

from datetime import datetime

import pytest

from finch.codex.runner import CodexRunner
from finch.engagement.models import ConversationScore, ExternalPost, InteractionAction
from finch.engagement.proposals import (
    ProposalBatchOutput,
    ProposalItem,
    choose_action,
    generate_proposals,
)
from finch.engagement.scoring import ScoredPost
from finch.settings import EngagementSettings


class FakeRunner(CodexRunner):
    def __init__(self, items=None):
        self.calls = 0
        self.last_prompt = ""
        self.items = items or []

    def run(self, prompt, output_model, **kwargs):
        self.calls += 1
        self.last_prompt = prompt
        return ProposalBatchOutput(items=self.items)


def _score(**overrides) -> ConversationScore:
    data = dict(
        relevance=0.5,
        novelty=0.5,
        discussability=0.5,
        practical_evidence=0.5,
        relationship_value=0.5,
        total=0.8,
        reasons=["reason"],
    )
    data.update(overrides)
    return ConversationScore(**data)


def _scored(pid, *, score=None) -> ScoredPost:
    post = ExternalPost(
        id=pid,
        platform="x",
        url=f"https://x.com/u/status/{pid}",
        author_id="u",
        author_name="U",
        content="How do you test agent reliability in production?",
        published_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    return ScoredPost(post=post, score=score or _score())


def _item(pid, **overrides) -> ProposalItem:
    data = dict(
        post_id=pid,
        draft="Have you tried recording a failure replay and diffing it?",
        intent="propose a verification method",
        source_summary="the claim that reliability is hard to test in prod",
        factual_risks=["assumes failure replay is available in their stack"],
    )
    data.update(overrides)
    return ProposalItem(**data)


# ---- choose_action ----


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (
            _score(discussability=0.8, novelty=0.8, practical_evidence=0.8),
            InteractionAction.DRAFT_QUOTE,
        ),
        (
            _score(discussability=0.8, novelty=0.8, practical_evidence=0.2),
            InteractionAction.DRAFT_REPLY,
        ),
        (
            _score(discussability=0.3, novelty=0.2, relationship_value=0.9),
            InteractionAction.OBSERVE_AUTHOR,
        ),
        (
            _score(discussability=0.3, novelty=0.2, relationship_value=0.2, relevance=0.9),
            InteractionAction.BOOKMARK,
        ),
        (
            _score(discussability=0.1, novelty=0.1, relationship_value=0.1, relevance=0.1),
            InteractionAction.IGNORE,
        ),
    ],
)
def test_choose_action_deterministic_mapping(score, expected):
    assert choose_action(score) is expected


def test_choose_action_prefers_reply_over_observe():
    # 可交流性 + 增量都高时，即使关系价值高也优先草稿类动作。
    score = _score(discussability=0.9, novelty=0.9, relationship_value=0.9,
                   practical_evidence=0.0)
    assert choose_action(score) is InteractionAction.DRAFT_REPLY


# ---- generate_proposals ----


def test_generate_proposals_empty_input_skips_llm():
    runner = FakeRunner()
    assert generate_proposals(runner, [], EngagementSettings()) == []
    assert runner.calls == 0


def test_generate_proposals_ignores_below_min_candidate_score():
    post = _scored(
        "p1",
        score=_score(total=0.5, relevance=0.9, discussability=0.9, novelty=0.9,
                     practical_evidence=0.9),
    )
    runner = FakeRunner()
    candidates = generate_proposals(
        runner, [post], EngagementSettings(min_candidate_score=0.72)
    )
    assert candidates == []
    assert runner.calls == 0


def test_generate_proposals_populates_draft_fields():
    post = _scored("p1", score=_score(discussability=0.9, novelty=0.9, practical_evidence=0.9))
    runner = FakeRunner(items=[_item("p1")])
    candidates = generate_proposals(runner, [post], EngagementSettings())

    assert len(candidates) == 1
    c = candidates[0]
    assert c.action is InteractionAction.DRAFT_QUOTE
    assert c.draft == "Have you tried recording a failure replay and diffing it?"
    assert c.intent == "propose a verification method"
    assert c.source_summary == "the claim that reliability is hard to test in prod"
    assert c.factual_risks == ["assumes failure replay is available in their stack"]


def test_generate_proposals_caps_bookmarks_and_reply_drafts():
    posts = [
        _scored(f"b{i}", score=_score(relevance=0.9, discussability=0.2, novelty=0.2))
        for i in range(5)
    ]
    posts += [
        _scored(f"r{i}", score=_score(discussability=0.9, novelty=0.9, practical_evidence=0.1))
        for i in range(5)
    ]
    # LLM 返回全部 5 条草稿（超限也返回，验证代码确定性封顶而非依赖模型）。
    runner = FakeRunner(items=[_item(f"r{i}") for i in range(5)])
    candidates = generate_proposals(
        runner, posts, EngagementSettings(max_bookmarks=2, max_reply_drafts=3)
    )

    bookmarks = [c for c in candidates if c.action == InteractionAction.BOOKMARK]
    replies = [
        c for c in candidates
        if c.action in (InteractionAction.DRAFT_REPLY, InteractionAction.DRAFT_QUOTE)
    ]
    assert len(bookmarks) == 2
    assert len(replies) == 3
    assert [c.post.id for c in bookmarks] == ["b0", "b1"]
    assert [c.post.id for c in replies] == ["r0", "r1", "r2"]


def test_approval_required_true_only_for_reply_and_quote():
    posts = [
        _scored("reply", score=_score(discussability=0.9, novelty=0.9, practical_evidence=0.1)),
        _scored("quote", score=_score(discussability=0.9, novelty=0.9, practical_evidence=0.9)),
        _scored("bookmark", score=_score(relevance=0.9, discussability=0.2, novelty=0.2)),
        _scored("observe", score=_score(relationship_value=0.9, discussability=0.2, novelty=0.2)),
        _scored("ignore", score=_score(relevance=0.1, discussability=0.1, novelty=0.1)),
    ]
    runner = FakeRunner(items=[_item("reply"), _item("quote")])
    candidates = generate_proposals(runner, posts, EngagementSettings())

    by_id = {c.post.id: c for c in candidates}
    assert by_id["reply"].approval_required is True
    assert by_id["quote"].approval_required is True
    assert by_id["bookmark"].approval_required is False
    assert by_id["observe"].approval_required is False
    # IGNORE 帖子不进入候选列表。
    assert "ignore" not in by_id


def test_generate_proposals_never_produces_draft_dm():
    posts = [
        _scored("p1", score=_score(discussability=0.9, novelty=0.9, practical_evidence=0.1)),
        _scored("p2", score=_score(relationship_value=0.9, discussability=0.2, novelty=0.2)),
    ]
    runner = FakeRunner(items=[_item("p1")])
    candidates = generate_proposals(runner, posts, EngagementSettings())
    assert candidates
    assert all(c.action is not InteractionAction.DRAFT_DM for c in candidates)


def test_generate_proposals_drops_reply_without_draft():
    posts = [
        _scored("p1", score=_score(discussability=0.9, novelty=0.9, practical_evidence=0.1)),
        _scored("p2", score=_score(discussability=0.9, novelty=0.9, practical_evidence=0.1)),
    ]
    runner = FakeRunner(items=[_item("p1")])
    candidates = generate_proposals(runner, posts, EngagementSettings())
    assert [c.post.id for c in candidates] == ["p1"]


def test_generate_proposals_drops_blank_draft():
    post = _scored("p1", score=_score(discussability=0.9, novelty=0.9, practical_evidence=0.1))
    runner = FakeRunner(items=[_item("p1", draft="   ")])
    candidates = generate_proposals(runner, [post], EngagementSettings())
    assert candidates == []


def test_generate_proposals_makes_single_batch_llm_call():
    posts = [
        _scored("p1", score=_score(discussability=0.9, novelty=0.9, practical_evidence=0.1)),
        _scored("p2", score=_score(discussability=0.9, novelty=0.9, practical_evidence=0.1)),
    ]
    runner = FakeRunner(items=[_item("p1"), _item("p2")])
    generate_proposals(runner, posts, EngagementSettings())
    assert runner.calls == 1
