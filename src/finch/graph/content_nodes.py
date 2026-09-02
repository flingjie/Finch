"""Writer/Critic/Daily Brief 阶段 Graph 节点（Phase 5 Task F6）：draft / critique / brief。"""

from collections.abc import Callable
from typing import cast

from pydantic import BaseModel

from ..codex.runner import CodexRunner
from ..content.claims import validate_draft
from ..content.critic import CritiqueResult, evaluate_passed
from ..content.models import DailyBrief, Draft, DraftKind
from ..evidence.models import EvidenceCard, MatchResult
from ..settings import QualityGates
from ..twitter.models import DiscussionCandidate
from .context import items_payload, parse_items
from .events import NodeResult
from .nodes import Node

WriteReplyFn = Callable[
    [CodexRunner, MatchResult, DiscussionCandidate, dict[str, EvidenceCard]], Draft | None
]
WriteOriginalFn = Callable[[CodexRunner, list[EvidenceCard]], Draft | None]
RewriteFn = Callable[[CodexRunner, Draft, CritiqueResult, dict[str, EvidenceCard]], Draft]
CritiqueFn = Callable[[CodexRunner, Draft, dict[str, EvidenceCard]], CritiqueResult]


def make_draft_node(
    runner: CodexRunner,
    write_reply: WriteReplyFn,
    write_original: WriteOriginalFn,
    gates: QualityGates,
) -> Node:
    """证据草稿节点：matches × cards × candidates → drafts。

    对每个 MatchResult 用 candidate_id 查 DiscussionCandidate 后 write_reply；
    再从 evidence_cards 生成 ≤ max_daily_original_posts 篇 write_original。空 match → drafts=[]。
    """

    class DraftNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            matches = parse_items(ctx["match_results"], MatchResult)
            if not matches:
                return NodeResult(status="succeeded", output=items_payload([]))

            cards = parse_items(ctx["evidence_cards"], EvidenceCard)
            candidates = parse_items(ctx["candidates"], DiscussionCandidate)
            cards_by_id = {card.id: card for card in cards}
            candidates_by_id = {candidate.id: candidate for candidate in candidates}

            drafts: list[Draft] = []
            for match in matches:
                candidate = candidates_by_id.get(match.candidate_id)
                if candidate is None:
                    continue
                draft = write_reply(runner, match, candidate, cards_by_id)
                if draft is not None:
                    drafts.append(draft)

            for _ in range(gates.max_daily_original_posts):
                draft = write_original(runner, cards)
                if draft is not None:
                    drafts.append(draft)

            return NodeResult(
                status="succeeded",
                output=items_payload(cast(list[BaseModel], drafts)),
            )

    return DraftNode(
        name="draft",
        reads=["match_results", "evidence_cards", "candidates"],
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
