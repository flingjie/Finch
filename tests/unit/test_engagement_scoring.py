"""互动价值评分单元测试（Phase 3）。"""

from datetime import datetime

import pytest

from finch.codex.runner import CodexRunner
from finch.engagement.models import ConversationScore, ExternalPost
from finch.engagement.scoring import (
    ConversationScoreInput,
    ScoreBatchOutput,
    ScoredPost,
    ScoreItem,
    prefilter_posts,
    rank_candidates,
    score_posts,
    weighted_total,
)
from finch.settings import ScoringWeights

_DIM_FIELDS = [
    "relevance",
    "novelty",
    "discussability",
    "practical_evidence",
    "relationship_value",
]


class FakeRunner(CodexRunner):
    def __init__(self, items=None):
        self.calls = 0
        self.last_prompt = ""
        self.items = items or []

    def run(self, prompt, output_model, **kwargs):
        self.calls += 1
        self.last_prompt = prompt
        return ScoreBatchOutput(items=self.items)


def _make_post(**overrides) -> ExternalPost:
    data = dict(
        id="post_1",
        platform="x",
        url="https://x.com/alice/status/1",
        author_id="alice",
        author_name="Alice",
        content="How do you test agent reliability?",
        published_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    data.update(overrides)
    return ExternalPost(**data)


def _dims(**overrides) -> ConversationScoreInput:
    data = {field: 0.5 for field in _DIM_FIELDS}
    data["reasons"] = ["reason"]
    data.update(overrides)
    return ConversationScoreInput(**data)


def _scored(
    pid,
    *,
    practical=0.0,
    discussability=0.0,
    total=0.0,
    likes=0,
) -> ScoredPost:
    post = _make_post(id=pid, metrics={"likes": likes})
    score = ConversationScore(
        relevance=0.5,
        novelty=0.5,
        discussability=discussability,
        practical_evidence=practical,
        relationship_value=0.5,
        total=total,
        reasons=["reason"],
    )
    return ScoredPost(post=post, score=score)


# ---- weighted_total ----


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("relevance", 0.25),
        ("novelty", 0.25),
        ("discussability", 0.20),
        ("practical_evidence", 0.20),
        ("relationship_value", 0.10),
    ],
)
def test_weighted_total_uses_documented_weights(field, expected):
    kwargs = {f: 0.0 for f in _DIM_FIELDS}
    kwargs[field] = 1.0
    dims = ConversationScoreInput(**kwargs, reasons=[])
    assert weighted_total(dims, ScoringWeights()) == pytest.approx(expected)


def test_weighted_total_clamps_to_unit_interval():
    dims = _dims(relevance=1.0, novelty=1.0, discussability=1.0,
                 practical_evidence=1.0, relationship_value=1.0)
    over = ScoringWeights(
        relevance=0.6, novelty=0.6, discussability=0.6,
        practical_evidence=0.6, relationship_value=0.6,
    )
    assert weighted_total(dims, over) == 1.0

    neg = ScoringWeights(
        relevance=-1.0, novelty=-1.0, discussability=-1.0,
        practical_evidence=-1.0, relationship_value=-1.0,
    )
    assert weighted_total(dims, neg) == 0.0


def test_weighted_total_is_deterministic():
    dims = _dims(relevance=0.8, novelty=0.6, discussability=0.5,
                 practical_evidence=0.9, relationship_value=0.4)
    expected = 0.25 * 0.8 + 0.25 * 0.6 + 0.20 * 0.5 + 0.20 * 0.9 + 0.10 * 0.4
    assert weighted_total(dims, ScoringWeights()) == weighted_total(dims, ScoringWeights())
    assert weighted_total(dims, ScoringWeights()) == pytest.approx(expected)


# ---- prefilter_posts ----


def test_prefilter_keeps_real_content_and_drops_short_repost_skipped():
    posts = [
        _make_post(id="keep", content="A substantive post with real cases and code."),
        _make_post(id="short", content="hi"),
        _make_post(id="rt", content="RT @someone: check this out"),
        _make_post(id="repost", content="Repost: interesting thread"),
        _make_post(id="zhuanfa", content="转发微博"),
        _make_post(id="linkonly", content="https://x.com/u/status/9"),
        _make_post(id="already", content="substantive content that was already handled"),
    ]
    kept = prefilter_posts(posts, min_length=10, skip_ids={"already"})
    assert [p.id for p in kept] == ["keep"]


def test_prefilter_drops_whitespace_only_and_strips_before_length_check():
    posts = [
        _make_post(id="blank", content="   \n\t  "),
        _make_post(id="padded", content="  hi  "),
        _make_post(id="exact", content="1234567890"),
    ]
    kept = prefilter_posts(posts, min_length=10, skip_ids=set())
    # "padded" 去掉空白后只有 2 个字符；"exact" 恰好 10 个字符，保留。
    assert [p.id for p in kept] == ["exact"]


# ---- score_posts ----


def test_score_posts_empty_input_does_not_call_runner():
    runner = FakeRunner()
    assert score_posts(runner, [], ScoringWeights()) == []
    assert runner.calls == 0


def test_score_posts_computes_total_and_preserves_reasons():
    post = _make_post(id="p1", content="A substantive post with real cases and code.")
    dims = _dims(
        relevance=0.8,
        novelty=0.6,
        discussability=0.5,
        practical_evidence=0.9,
        relationship_value=0.4,
        reasons=["on topic", "new", "debatable", "has code", "known author"],
    )
    runner = FakeRunner(items=[ScoreItem(post_id="p1", scores=dims)])
    scored = score_posts(runner, [post], ScoringWeights())

    assert runner.calls == 1
    assert len(scored) == 1
    sp = scored[0]
    assert sp.post.id == "p1"
    assert sp.score.relevance == 0.8
    assert sp.score.reasons == dims.reasons
    assert sp.score.total == pytest.approx(weighted_total(dims, ScoringWeights()))


def test_llm_output_model_has_no_total_field():
    # 模型输出结构里没有 total 字段，模型无法覆盖代码计算的总分。
    assert "total" not in ConversationScoreInput.model_fields
    assert "total" not in ScoreItem.model_fields


def test_score_posts_drops_posts_model_skipped():
    post_a = _make_post(id="a", content="first post with enough content")
    post_b = _make_post(id="b", content="second post with enough content")
    runner = FakeRunner(items=[ScoreItem(post_id="a", scores=_dims())])
    scored = score_posts(runner, [post_a, post_b], ScoringWeights())
    assert [sp.post.id for sp in scored] == ["a"]


# ---- rank_candidates ----


def test_rank_candidates_filters_by_threshold_and_ranks_evidence_first():
    scored = [
        _scored("below", practical=0.9, discussability=0.9, total=0.5, likes=99999),
        _scored("evidence", practical=0.9, discussability=0.8, total=0.8, likes=0),
        _scored("popular", practical=0.3, discussability=0.3, total=0.9, likes=100000),
    ]
    ranked = rank_candidates(scored, min_candidate_score=0.72)
    # "below" 未过阈值；"evidence" 实践证据+可交流性更高，排在纯热度帖 "popular" 之前。
    assert [sp.post.id for sp in ranked] == ["evidence", "popular"]


def test_rank_candidates_uses_popularity_only_as_tiebreaker():
    a = _scored("a", practical=0.5, discussability=0.5, total=0.8, likes=10)
    b = _scored("b", practical=0.5, discussability=0.5, total=0.8, likes=100)
    ranked = rank_candidates([a, b], min_candidate_score=0.72)
    assert [sp.post.id for sp in ranked] == ["b", "a"]
