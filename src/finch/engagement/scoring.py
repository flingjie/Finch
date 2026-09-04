"""互动价值评分：确定性规则过滤 + 确定性加权总分 + 模型逐维评分。

对应执行计划 Phase 3：
1. 先用确定性规则过滤（过短、纯转发、已互动帖子）；
2. 再由模型对五个维度分别评分，并要求给出可审计理由；
3. 在代码中确定性计算加权总分，模型不得直接决定最终总分；
4. 仅保留 total >= min_candidate_score 的帖子；
5. 最终排序优先考虑实践证据和可交流性，热度只作辅助特征。
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from ..codex.runner import CodexRunner
from ..settings import ScoringWeights
from .models import ConversationScore, ExternalPost

_PROMPT_PATH = Path("prompts/score-engagement.md")

# 纯转发/引用的前导标记（简单启发式，见 _is_repost 文档）。
_REPOST_PREFIXES = ("rt @", "rt@", "repost", "转", "转发")

# 热度信号提取用的常见指标键（仅作排序 tiebreaker）。
_POPULARITY_KEYS = ("likes", "favorites", "replies", "reposts", "retweets", "comments")


class ConversationScoreInput(BaseModel):
    """模型返回的五维评分；不含 total（total 只能由 weighted_total 确定性计算）。"""

    relevance: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    discussability: float = Field(ge=0, le=1)
    practical_evidence: float = Field(ge=0, le=1)
    relationship_value: float = Field(ge=0, le=1)
    reasons: list[str]


class ScoreItem(BaseModel):
    """单条帖子的模型评分（post_id 用于回联原帖）。"""

    post_id: str
    scores: ConversationScoreInput


class ScoreBatchOutput(BaseModel):
    """一次 batch 评分返回。"""

    items: list[ScoreItem]


@dataclass(frozen=True)
class ScoredPost:
    """帖子及其确定性计算后的总评分（InteractionCandidate 就绪）。"""

    post: ExternalPost
    score: ConversationScore


def weighted_total(dims: ConversationScoreInput, weights: ScoringWeights) -> float:
    """确定性加权总分，钳制到 0..1；这是唯一计算 total 的位置。"""
    total = (
        weights.relevance * dims.relevance
        + weights.novelty * dims.novelty
        + weights.discussability * dims.discussability
        + weights.practical_evidence * dims.practical_evidence
        + weights.relationship_value * dims.relationship_value
    )
    return max(0.0, min(1.0, total))


def _is_repost(content: str) -> bool:
    """是否为纯转发/引用。

    简单启发式：
    - 去除空白后为空（无任何原创内容）；
    - 以转发标记开头（RT @ / Repost / 转 / 转发）；
    - 仅包含一个链接（引用原文但未加评注）。
    主题排除与 platform+id 去重由搜索层负责，不在这里重复实现。
    """
    stripped = content.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if any(lowered.startswith(prefix) for prefix in _REPOST_PREFIXES):
        return True
    if stripped.startswith(("http://", "https://")) and " " not in stripped:
        return True
    return False


def prefilter_posts(
    posts: list[ExternalPost],
    *,
    min_length: int,
    skip_ids: set[str] | None = None,
) -> list[ExternalPost]:
    """确定性规则预过滤：丢弃过短、纯转发与已互动（skip_ids）的帖子。"""
    skip = skip_ids or set()
    kept: list[ExternalPost] = []
    for post in posts:
        if post.id in skip:
            continue
        if _is_repost(post.content):
            continue
        if len(post.content.strip()) < min_length:
            continue
        kept.append(post)
    return kept


def _to_json_text(models: Sequence[BaseModel]) -> str:
    return json.dumps([m.model_dump(mode="json") for m in models])


def _assemble(
    post: ExternalPost, dims: ConversationScoreInput, weights: ScoringWeights
) -> ScoredPost:
    total = weighted_total(dims, weights)
    score = ConversationScore(
        relevance=dims.relevance,
        novelty=dims.novelty,
        discussability=dims.discussability,
        practical_evidence=dims.practical_evidence,
        relationship_value=dims.relationship_value,
        total=total,
        reasons=dims.reasons,
    )
    return ScoredPost(post=post, score=score)


def score_posts(
    runner: CodexRunner,
    posts: list[ExternalPost],
    weights: ScoringWeights,
) -> list[ScoredPost]:
    """一次 Codex 调用完成整批帖子的五维评分；total 由 weighted_total 确定性计算。

    posts 为空时不调用 runner，直接返回空列表（0 次调用）。
    模型漏评的帖子保守丢弃，不参与后续排序。
    """
    if not posts:
        return []
    prompt = _PROMPT_PATH.read_text().format(posts=_to_json_text(posts))
    output = cast(ScoreBatchOutput, runner.run(prompt, ScoreBatchOutput))
    by_id = {item.post_id: item.scores for item in output.items}
    scored: list[ScoredPost] = []
    for post in posts:
        dims = by_id.get(post.id)
        if dims is None:
            continue
        scored.append(_assemble(post, dims, weights))
    return scored


def _popularity(metrics: dict[str, int | float]) -> float:
    """从 metrics 提取热度信号（仅作排序 tiebreaker）。"""
    return sum(float(metrics.get(key, 0.0)) for key in _POPULARITY_KEYS)


def _rank_key(sp: ScoredPost) -> tuple[float, float, float]:
    return (
        sp.score.practical_evidence + sp.score.discussability,
        sp.score.total,
        _popularity(sp.post.metrics),
    )


def rank_candidates(
    scored: list[ScoredPost],
    *,
    min_candidate_score: float,
) -> list[ScoredPost]:
    """按 total 阈值过滤，并按实践证据 + 可交流性优先排序；热度仅作辅助 tiebreaker。"""
    kept = [sp for sp in scored if sp.score.total >= min_candidate_score]
    kept.sort(key=_rank_key, reverse=True)
    return kept
