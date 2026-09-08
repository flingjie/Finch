"""关系评分：确定性的 peer_value 与 relationship_value。

peer_value（默认等权，基础分落在 [0,1]）：
  = 0.25·topic_overlap + 0.25·practical_depth + 0.25·contribution_space + 0.25·continuity_potential
    − repetition_penalty − promotion_risk

全部为确定性特征（无 LLM）。LLM 仍负责逐帖四维语义评分（relevance / novelty /
discussability / practical_evidence）；关系维度由本模块从 PeerProfile 与历史互动确定性
计算，替换原「单帖印象」的 relationship_value。

对应连接优先改造 Phase 2：把「哪些帖子值得回」升级为「哪些人值得持续交流」。
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from finch.conversations.models import ConversationThread
from finch.settings import PeerValueWeights

from .models import InteractionRecord
from .peer_aggregation import PeerBundle

# 机会信号：帖子暴露真实问题/分歧/未解决机制（有可贡献空间）。
_OPPORTUNITY_SIGNALS = frozenset({
    "doesn't work", "does not work", "not working", "broken", "bug", "fails", "failed",
    "error", "crash", "problem", "issue", "struggling", "hard to", "too hard", "can't",
    "cannot", "won't", "workaround", "missing", "gap", "unexpected", "turns out",
    "不行", "坏了", "崩溃", "问题", "坑", "踩坑", "反例", "缺口", "翻车",
})

# 推广噪音信号：纯资讯/融资/发布，不是可对话的技术内容。
_PROMOTION_SIGNALS = frozenset({
    "announces", "announced", "launches", "launched", "release", "released", "shipping",
    "shipped", "new version", "raised", "raises", "funding", "series a", "series b",
    "seed", "valuation", "acquires", "acquired", "ipo", "investment",
    "新闻", "融资", "发布", "上线", "估值", "收购",
})

# 实践深度信号：真实案例/代码/实验/复现记录。
_PRACTICAL_SIGNALS = frozenset({
    "in practice", "turns out", "reproduce", "reproduced", "reproduction", "we ran",
    "we tested", "benchmark", "experiment", "code", "repo", "trace", "replay",
    "实测", "复现", "实验", "代码", "跑了一遍", "试了",
})


@dataclass(frozen=True)
class PeerHistory:
    """同行的历史互动上下文（由仓储读入；空历史表示首次发现）。"""

    interactions: list[InteractionRecord] = field(default_factory=list)
    threads: list[ConversationThread] = field(default_factory=list)
    rejected_or_ignored: int = 0


class PeerValue(BaseModel):
    """确定性的同行价值：六维特征 + 汇总 total（total 只在代码里算）。"""

    topic_overlap: float = Field(ge=0, le=1)
    practical_depth: float = Field(ge=0, le=1)
    contribution_space: float = Field(ge=0, le=1)
    continuity_potential: float = Field(ge=0, le=1)
    repetition_penalty: float = Field(ge=0, le=1)
    promotion_risk: float = Field(ge=0, le=1)
    total: float
    reasons: list[str]


def _topic_overlap(bundle: PeerBundle, interests: list[str]) -> float:
    """同行帖子正文覆盖用户兴趣词的比例（0–1）。"""
    if not interests:
        return 0.0
    content = " ".join(p.content.casefold() for p in bundle.posts)
    matched = sum(1 for i in interests if i.casefold() in content)
    return matched / len(interests)


def _fraction_with_signal(bundle: PeerBundle, signals: frozenset[str]) -> float:
    posts = bundle.posts
    if not posts:
        return 0.0
    return sum(1 for p in posts if any(s in p.content.casefold() for s in signals)) / len(posts)


def _practical_depth(bundle: PeerBundle) -> float:
    return _fraction_with_signal(bundle, _PRACTICAL_SIGNALS)


def _contribution_space(bundle: PeerBundle) -> float:
    return _fraction_with_signal(bundle, _OPPORTUNITY_SIGNALS)


def _continuity_potential(history: PeerHistory) -> float:
    """有对话线索 = 1.0；仅有互动记录 = 0.5；首次发现 = 0.0。"""
    if history.threads:
        return 1.0
    if history.interactions:
        return 0.5
    return 0.0


def _repetition_penalty(history: PeerHistory) -> float:
    """已多次低质量互动（拒绝/忽略）的同行被降权；每 5 次扣满 1.0。"""
    return min(1.0, 0.2 * history.rejected_or_ignored)


def _promotion_risk(bundle: PeerBundle) -> float:
    return _fraction_with_signal(bundle, _PROMOTION_SIGNALS)


def compute_peer_value(
    bundle: PeerBundle,
    *,
    interests: list[str],
    history: PeerHistory | None = None,
    weights: PeerValueWeights | None = None,
) -> PeerValue:
    """确定性计算同行价值六维特征并按权重汇总 total（LLM 不参与）。"""
    history = history or PeerHistory()
    w = weights or PeerValueWeights()

    topic_overlap = _topic_overlap(bundle, interests)
    practical_depth = _practical_depth(bundle)
    contribution_space = _contribution_space(bundle)
    continuity_potential = _continuity_potential(history)
    repetition_penalty = _repetition_penalty(history)
    promotion_risk = _promotion_risk(bundle)

    total = (
        w.topic_overlap * topic_overlap
        + w.practical_depth * practical_depth
        + w.contribution_space * contribution_space
        + w.continuity_potential * continuity_potential
        - w.repetition_penalty * repetition_penalty
        - w.promotion_risk * promotion_risk
    )
    total = max(0.0, min(1.0, total))

    reasons = [
        f"topic_overlap={topic_overlap:.2f}",
        f"practical_depth={practical_depth:.2f}",
        f"contribution_space={contribution_space:.2f}",
        f"continuity_potential={continuity_potential:.2f}",
        f"repetition_penalty={repetition_penalty:.2f}",
        f"promotion_risk={promotion_risk:.2f}",
    ]

    return PeerValue(
        topic_overlap=topic_overlap,
        practical_depth=practical_depth,
        contribution_space=contribution_space,
        continuity_potential=continuity_potential,
        repetition_penalty=repetition_penalty,
        promotion_risk=promotion_risk,
        total=total,
        reasons=reasons,
    )


def compute_relationship_value(peer_value: PeerValue) -> float:
    """从 peer 特征派生确定性的 relationship_value（0–1），替换原 LLM 单帖印象。

    取「主题重叠」与「连续互动潜力」的均值：既有共同兴趣、又值得延续关系的同行得分高。
    """
    return max(0.0, min(1.0, (peer_value.topic_overlap + peer_value.continuity_potential) / 2.0))


def rank_peers(
    bundles: list[PeerBundle],
    *,
    interests: list[str],
    history_by_peer: dict[str, PeerHistory] | None = None,
    weights: PeerValueWeights | None = None,
) -> list[tuple[PeerBundle, PeerValue]]:
    """按 peer_value.total 降序排序同行；同分按 peer id 稳定排序。"""
    history_by_peer = history_by_peer or {}
    scored = [
        (bundle, compute_peer_value(
            bundle,
            interests=interests,
            history=history_by_peer.get(bundle.profile.id),
            weights=weights,
        ))
        for bundle in bundles
    ]
    scored.sort(key=lambda pair: (-pair[1].total, pair[0].profile.id))
    return scored
