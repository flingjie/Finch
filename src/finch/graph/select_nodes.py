"""Select 阶段 Graph 节点：先选后写，不阻塞。

Note: 选择节点把 define_jobs 的规划/预过滤/展开与 position_gate 的选择合并，去掉
立场阻塞；decision/tradeoff 为空时不阻塞，job 照常进入 ready_jobs。
"""

from typing import cast

from pydantic import BaseModel

from ..content.jobs import (
    ContentJob,
    TopicProposal,
    expand_content_job,
    plan_content_topics,
    select_planning_evidence,
)
from ..evidence.models import ClaimConfidence, EvidenceCard, MatchResult
from ..llm.base import StructuredInferenceRunner
from ..settings import DailyBudget, QualityGates
from ..storage.repositories import ContentJobRepository
from ..twitter.models import DiscussionCandidate
from .context import items_payload, parse_items
from .events import NodeResult
from .nodes import Node

_STRONG_CONFIDENCE = {ClaimConfidence.VERIFIED, ClaimConfidence.SUPPORTED}


def _topic_sort_key(
    topic: TopicProposal,
    cards_by_id: dict[str, EvidenceCard],
) -> tuple[bool, float]:
    """主题级确定性排序键（先选后写，展开前可用）：有讨论上下文 > 证据置信占比。

    立场/why_now 只在 expand 后可得，故不参与主题级排序（spec §5 B 的务实落地）。
    sort 稳定，id 顺序作为 tie-break。
    """
    ratio = 0.0
    cards = [cards_by_id[cid] for cid in topic.card_ids if cid in cards_by_id]
    if cards:
        ratio = sum(1 for c in cards if c.confidence in _STRONG_CONFIDENCE) / len(cards)
    return (topic.candidate_id is not None, ratio)


def make_select_node(
    plan_runner: StructuredInferenceRunner,
    expand_runner: StructuredInferenceRunner,
    expand_concurrency: int = 4,
    jobs_repo: ContentJobRepository | None = None,
    budget: DailyBudget | None = None,
    gates: QualityGates | None = None,
) -> Node:
    """选择节点（先选后写，不阻塞）：match_results/evidence_cards/candidates → ready_jobs。

    合并 define_jobs 的规划/预过滤/展开与 position_gate 的选择，去掉立场阻塞：
    1. 无卡短路（绝不调用 runner）。
    2. ``select_planning_evidence`` 裁剪 → ``plan_content_topics`` 一次聚类。
    3. 预过滤（沿用 define_jobs）：candidate_id 必须在 match、card_ids 非空且属于可用
       范围（reply 取 match 的 card_ids，original 取 all_card_ids）、topic.id 去重。
    4. 确定性排序主题（``_topic_sort_key``：有讨论上下文 > 证据置信占比）。
    5. 只展开 Top K（K = ``gates.max_daily_original_posts``，默认 1）；展开失败递补
       下一个主题（最多一次）。
    6. 校验去重（``validate_source_cards`` + job.id 去重）→ upsert（若 jobs_repo）。
    7. 输出 ready_jobs（单一份）+ ``output["unexpanded_topics"]``（未展开主题标题，调试用）。

    decision/tradeoff 为空时不阻塞：job 照常进入 ready_jobs；是否 must_ask 由 inbox 投影
    在下游计算（spec §4.1）。
    """

    class SelectNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            match_results = parse_items(ctx["match_results"], MatchResult)
            cards = parse_items(ctx["evidence_cards"], EvidenceCard)
            candidates = parse_items(ctx["candidates"], DiscussionCandidate)

            # 无卡短路：绝不调用 runner（保留「无卡不调用 LLM」语义）。
            if not cards:
                return NodeResult(status="succeeded", output=items_payload([]))

            cards_by_id = {card.id: card for card in cards}
            candidates_by_id = {candidate.id: candidate for candidate in candidates}
            match_by_candidate = {mr.candidate_id: mr for mr in match_results}
            all_card_ids = list({cid for mr in match_results for cid in mr.card_ids})

            planning_cards = (
                select_planning_evidence(cards, match_results, budget)
                if budget is not None
                else cards
            )

            topics = plan_content_topics(plan_runner, planning_cards, match_results, candidates)

            # 预过滤 topic（避免浪费 expand LLM 调用）：candidate_id 非空但不在 match
            # 中、card_ids 为空、或 card_ids 不是可用范围的子集（reply 取 match 的
            # card_ids，original 取 all_card_ids）都会跳过。按 topic.id 去重，避免重复
            # 主题展开出重复 job id。
            kept_topics: list[TopicProposal] = []
            seen_topic_ids: set[str] = set()
            for topic in topics.items:
                if topic.id in seen_topic_ids:
                    continue
                seen_topic_ids.add(topic.id)
                if topic.candidate_id is not None:
                    match = match_by_candidate.get(topic.candidate_id)
                    if match is None:
                        continue
                    available_ids = match.card_ids
                else:
                    available_ids = all_card_ids
                if not topic.card_ids:
                    continue
                if not set(topic.card_ids).issubset(set(available_ids)):
                    continue
                kept_topics.append(topic)

            # 确定性排序（先选后写）：有讨论上下文优先，其次证据置信占比；sort 稳定，
            # id 顺序作为 tie-break。
            ordered_topics = sorted(
                kept_topics,
                key=lambda topic: _topic_sort_key(topic, cards_by_id),
                reverse=True,
            )

            k = gates.max_daily_original_posts if gates is not None else 1

            warnings: list[str] = []

            def _expand(topic: TopicProposal) -> ContentJob | None:
                candidate = (
                    candidates_by_id[topic.candidate_id]
                    if topic.candidate_id is not None
                    else None
                )
                try:
                    return expand_content_job(expand_runner, topic, cards_by_id, candidate)
                except Exception as exc:  # noqa: BLE001
                    # 故障隔离：单个 topic 展开失败不拖垮整个节点，并触发一次递补。
                    warnings.append(
                        f"select: expand failed for topic {topic.id}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    return None

            # 只展开前 K 个；展开失败递补下一个主题（最多一次）。
            jobs: list[ContentJob] = []
            index = 0
            fallbacks = 0
            while index < len(ordered_topics) and len(jobs) < k and fallbacks <= 1:
                job = _expand(ordered_topics[index])
                index += 1
                if job is not None:
                    jobs.append(job)
                else:
                    fallbacks += 1

            # 校验去重：source_card_ids 属于该 job 自身卡范围（candidate_id 非空取
            # match 的 card_ids，original 取 all_card_ids）；再按 job.id 去重，避免重复
            # id 在 upsert 时互相覆盖、下游处理重复列表。
            valid_jobs: list[ContentJob] = []
            seen_job_ids: set[str] = set()
            for job in jobs:
                if job.candidate_id is not None:
                    match = match_by_candidate.get(job.candidate_id)
                    if match is None:
                        continue
                    available_ids = match.card_ids
                else:
                    available_ids = all_card_ids
                if not job.validate_source_cards(available_ids):
                    continue
                if job.id in seen_job_ids:
                    continue
                seen_job_ids.add(job.id)
                valid_jobs.append(job)

            if jobs_repo is not None:
                jobs_repo.upsert_jobs(valid_jobs)

            output = items_payload(cast(list[BaseModel], valid_jobs))
            output["unexpanded_topics"] = [t.title for t in ordered_topics[index:]]
            if warnings:
                output["warnings"] = warnings
            return NodeResult(
                status="succeeded",
                output=output,
                warnings=warnings,
            )

    return SelectNode(
        name="select",
        reads=["match_results", "evidence_cards", "candidates"],
        writes="ready_jobs",
        succeeds_to="JOBS_SELECTED",
    )
