"""匹配阶段 Graph 节点（Phase 4 Task C1）：recall 与 match_evidence。"""

from typing import cast

from pydantic import BaseModel

from ..codex.runner import CodexRunner
from ..evidence.judge import judge_batch
from ..evidence.matcher import recall
from ..evidence.models import EvidenceCard, MatchResult, RankedCandidate
from ..evidence.scoring import apply_gates, formula_score, relationship_value, timing_value
from ..settings import QualityGates, TwitterSettings
from ..twitter.models import DiscussionCandidate
from .context import items_payload, parse_items
from .events import NodeResult
from .nodes import Node


def make_recall_node(gates: QualityGates) -> Node:
    """确定性召回节点：candidates × cards → ranked_candidates。

    读取 candidates 与 evidence_cards，经 Jaccard 召回后写入 ranked_candidates。
    """

    class RecallNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            candidates = parse_items(ctx["candidates"], DiscussionCandidate)
            cards = parse_items(ctx["evidence_cards"], EvidenceCard)
            ranked = recall(candidates, cards, top_k=gates.match_top_k)
            return NodeResult(
                status="succeeded",
                output=items_payload(cast(list[BaseModel], ranked)),
            )

    return RecallNode(
        name="recall",
        reads=["candidates", "evidence_cards"],
        writes="ranked_candidates",
        succeeds_to="CANDIDATES_RANKED",
    )


def make_match_node(
    runner: CodexRunner,
    gates: QualityGates,
    twitter: TwitterSettings,
) -> Node:
    """证据匹配节点：judge_batch 评分 + 门禁，写入 match_results。

    judge 未返回的 candidate 跳过（不编造分数）；card_ids 始终取自召回结果。
    judge_batch 抛错时由 runtime 捕获并标记 retryable=True。
    """

    class MatchNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            ranked = parse_items(ctx["ranked_candidates"], RankedCandidate)
            cards = parse_items(ctx["evidence_cards"], EvidenceCard)
            candidates = parse_items(ctx["candidates"], DiscussionCandidate)

            judge_output = judge_batch(runner, ranked, candidates, cards)
            scores_by_id = {item.candidate_id: item.scores for item in judge_output.items}
            candidates_by_id = {c.id: c for c in candidates}

            results: list[MatchResult] = []
            for rc in ranked:
                scores = scores_by_id.get(rc.candidate_id)
                if scores is None:
                    continue
                cand = candidates_by_id[rc.candidate_id]
                timing = timing_value(cand.published_at, gates.timing_default)
                relationship = relationship_value(
                    cand.author_handle,
                    high_value_authors=twitter.high_value_authors,
                    blocked_authors=twitter.blocked_authors,
                )
                results.append(
                    MatchResult(
                        candidate_id=rc.candidate_id,
                        card_ids=rc.card_ids,
                        scores=scores,
                        timing=timing,
                        relationship_value=relationship,
                        score=formula_score(scores, timing, relationship),
                    )
                )

            results = apply_gates(results, gates)
            return NodeResult(
                status="succeeded",
                output=items_payload(cast(list[BaseModel], results)),
            )

    return MatchNode(
        name="match_evidence",
        reads=["ranked_candidates", "evidence_cards", "candidates"],
        writes="match_results",
        succeeds_to="EVIDENCE_MATCHED",
    )
