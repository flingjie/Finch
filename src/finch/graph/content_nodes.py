"""Writer/Critic/Daily Brief 阶段 Graph 节点（Phase 5 Task F6）：draft / critique / brief。

Note: writer.py functions now accept optional ContentJob param to stamp content_job_id
and position_statement on Drafts.
"""

from collections.abc import Callable
from typing import cast

from pydantic import BaseModel

from ..codex.runner import CodexRunner
from ..content.claims import validate_draft
from ..content.critic import CritiqueResult, evaluate_passed
from ..content.jobs import ContentJob, ContentJobStatus, define_content_jobs
from ..content.models import DailyBrief, Draft, DraftKind
from ..evidence.models import EvidenceCard, MatchResult
from ..settings import QualityGates
from ..twitter.models import DiscussionCandidate
from .context import items_payload, parse_items
from .events import NodeResult
from .nodes import Node

# WriteReplyFn: updated signature to accept optional ContentJob
# Note: match is now MatchResult | None when coming from ContentJob
WriteReplyFn = Callable[
    [
        CodexRunner,
        MatchResult | None,
        DiscussionCandidate,
        dict[str, EvidenceCard],
        ContentJob | None,
    ],
    Draft | None,
]
WriteOriginalFn = Callable[[CodexRunner, list[EvidenceCard], ContentJob | None], Draft | None]
RewriteFn = Callable[[CodexRunner, Draft, CritiqueResult, dict[str, EvidenceCard]], Draft]
CritiqueFn = Callable[[CodexRunner, Draft, dict[str, EvidenceCard]], CritiqueResult]


def make_draft_node(
    runner: CodexRunner,
    write_reply: WriteReplyFn,
    write_original: WriteOriginalFn,
    gates: QualityGates,
) -> Node:
    """证据草稿节点：ready_jobs × cards → drafts。

    对每个 ready job：candidate_id 为空 → write_original；否则查 DiscussionCandidate 后
    write_reply（查不到 candidate 则跳过该 job）。空 ready_jobs → drafts=[]（幂等）。
    """

    class DraftNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            jobs = parse_items(ctx.get("ready_jobs", []), ContentJob)
            if not jobs:
                return NodeResult(status="succeeded", output=items_payload([]))

            cards = parse_items(ctx["evidence_cards"], EvidenceCard)
            candidates = parse_items(ctx.get("candidates", []), DiscussionCandidate)
            cards_by_id = {card.id: card for card in cards}
            candidates_by_id = {candidate.id: candidate for candidate in candidates}

            drafts: list[Draft] = []
            for job in jobs:
                # Build card subset from job's source_card_ids
                job_cards = [cards_by_id[cid] for cid in job.source_card_ids if cid in cards_by_id]
                if not job_cards:
                    continue

                if job.candidate_id is None:
                    draft = write_original(runner, job_cards, job)
                else:
                    candidate = candidates_by_id.get(job.candidate_id)
                    if candidate is None:
                        continue
                    draft = write_reply(runner, None, candidate, cards_by_id, job)

                if draft is not None:
                    drafts.append(draft)

            return NodeResult(
                status="succeeded",
                output=items_payload(cast(list[BaseModel], drafts)),
            )

    return DraftNode(
        name="draft",
        reads=["ready_jobs", "evidence_cards", "candidates"],
        writes="drafts",
        succeeds_to="DRAFTED",
    )


def make_critique_node(
    runner: CodexRunner,
    rewrite: RewriteFn,
    critique: CritiqueFn,
    gates: QualityGates,
) -> Node:
    """草稿审查节点：对每条 draft 最多 max_rewrite_rounds 次重写，每次重写后再审查。"""

    class CritiqueNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            drafts = parse_items(ctx["drafts"], Draft)
            matches = parse_items(ctx["match_results"], MatchResult)
            cards = parse_items(ctx["evidence_cards"], EvidenceCard)

            cards_by_id = {card.id: card for card in cards}
            match_by_candidate = {match.candidate_id: match for match in matches}

            kept: list[Draft] = []
            warnings: list[str] = []

            for draft in drafts:
                if draft.candidate_id is not None:
                    match = match_by_candidate.get(draft.candidate_id)
                    if match is None:
                        warnings.append(
                            f"draft {draft.id}: no match for candidate {draft.candidate_id}"
                        )
                        continue
                    card_ids = set(match.card_ids)
                else:
                    card_ids = set(cards_by_id)

                current = draft
                for i in range(gates.max_rewrite_rounds + 1):
                    result = critique(runner, current, cards_by_id)
                    if evaluate_passed(result, gates):
                        kept.append(current)
                        break
                    if i == gates.max_rewrite_rounds:
                        warnings.append(
                            f"draft {current.id} failed critique after "
                            f"{gates.max_rewrite_rounds} rewrites"
                        )
                        break
                    current = rewrite(runner, current, result, cards_by_id)
                    violations = validate_draft(current, card_ids=card_ids)
                    if violations:
                        warnings.append(
                            f"draft {current.id} rewrite produced invalid claims: {violations}"
                        )
                        break

            return NodeResult(
                status="succeeded",
                output=items_payload(cast(list[BaseModel], kept)),
                warnings=warnings,
            )

    return CritiqueNode(
        name="critique",
        reads=["drafts", "match_results", "evidence_cards"],
        writes="drafts",
        succeeds_to="CRITIQUED",
    )


def make_brief_node(gates: QualityGates) -> Node:
    """每日简报节点：渲染 DailyBrief 并写入动态终态。"""

    class BriefNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            drafts = parse_items(ctx["drafts"], Draft)
            reply_count = sum(1 for draft in drafts if draft.kind == DraftKind.REPLY)
            original_count = sum(1 for draft in drafts if draft.kind == DraftKind.ORIGINAL)
            body = "\n".join(f"- {draft.body}" for draft in drafts)
            brief = DailyBrief(
                run_id="daily",
                has_drafts=len(drafts) > 0,
                reply_count=reply_count,
                original_count=original_count,
                body=body,
            )
            output = items_payload([brief])
            output["terminal_state"] = (
                "WAITING_FOR_REVIEW" if brief.has_drafts else "COMPLETED"
            )
            return NodeResult(status="succeeded", output=output)

    return BriefNode(
        name="brief",
        reads=["drafts", "match_results"],
        writes="brief",
        succeeds_to="WAITING_FOR_REVIEW",
        terminal_state_key="terminal_state",
    )


def make_define_jobs_node(runner: CodexRunner) -> Node:
    """定义内容任务节点：match_results/evidence_cards/candidates → content_jobs。

    通过 Codex runner 调用 define_content_jobs prompt 生成 ContentJob 列表。
    对每个 job 验证 source_card_ids 是 match 的 card_ids 的子集，不合法的 job 被过滤。
    """

    class DefineJobsNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            match_results = parse_items(ctx["match_results"], MatchResult)
            cards = parse_items(ctx["evidence_cards"], EvidenceCard)

            # Pass all cards to the LLM for job generation
            jobs_output = define_content_jobs(runner, cards)

            # Filter jobs: keep only those whose source_card_ids are a subset of the
            # union of all matched card_ids (invalid jobs are dropped).
            available_ids = list({cid for mr in match_results for cid in mr.card_ids})
            valid_jobs = [
                job for job in jobs_output.items if job.validate_source_cards(available_ids)
            ]

            return NodeResult(
                status="succeeded",
                output=items_payload(cast(list[BaseModel], valid_jobs)),
            )

    return DefineJobsNode(
        name="define_jobs",
        reads=["match_results", "evidence_cards", "candidates"],
        writes="content_jobs",
        succeeds_to="JOBS_DEFINED",
    )


def make_position_gate_node() -> Node:
    """位置确认门节点：content_jobs → ready_jobs (deterministic, no LLM)。

    对每个 job 判断：
    - DO_NOT_WRITE: 跳过（不写入 ready_jobs，不报错）
    - READY: author_position 存在且 decision/tradeoff 非空且 confirmed=True
    - 其他（缺 position、decision/tradeoff 为空、未确认）→ needs_input

    如果任何 job 需要输入，返回 needs_input 并记录这些 job；
    否则写入 ready_jobs 为所有 READY 的 job。
    """

    class PositionGateNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            jobs = parse_items(ctx["content_jobs"], ContentJob)

            needs_input_jobs: list[ContentJob] = []
            ready_jobs: list[ContentJob] = []

            for job in jobs:
                if job.status == ContentJobStatus.DO_NOT_WRITE:
                    # Skip DO_NOT_WRITE jobs silently (not written to ready_jobs)
                    continue

                position = job.author_position
                ready = (
                    position is not None
                    and bool(position.decision)
                    and bool(position.tradeoff)
                    and position.confirmed
                )
                if ready:
                    ready_jobs.append(job)
                else:
                    needs_input_jobs.append(job)

            # If any job needs input, stop and let user supply position
            if needs_input_jobs:
                return NodeResult(
                    status="needs_input",
                    output=items_payload(cast(list[BaseModel], needs_input_jobs)),
                )

            # All remaining jobs are READY
            return NodeResult(
                status="succeeded",
                output=items_payload(cast(list[BaseModel], ready_jobs)),
            )

    return PositionGateNode(
        name="position_gate",
        reads=["content_jobs"],
        writes="ready_jobs",
        succeeds_to="POSITIONS_READY",
    )
