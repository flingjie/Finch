"""Write 阶段 Graph 节点：写稿 + 条件 Critic。

Note: writer.py functions now accept optional ContentJob param to stamp content_job_id
and position_statement on Drafts.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple, cast

from pydantic import BaseModel

from ..codex.runner import CodexRunner
from ..content.checkers.aggregate import AggregateOutcome, aggregate_checks
from ..content.checkers.base import CheckContext, Checker, CheckResult
from ..content.claims import validate_draft
from ..content.critic import _run_checks, default_checker_suite
from ..content.jobs import ContentJob
from ..content.models import Draft, DraftKind, DraftWarning
from ..content.voice import VoiceProfile
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
RewriteFn = Callable[
    [
        CodexRunner,
        Draft,
        list[CheckResult],
        dict[str, EvidenceCard],
        ContentJob | None,
    ],
    Draft,
]


class _DraftPlan(NamedTuple):
    """draft 节点写任务：Phase 1 选择产物，Phase 2 并行写入，Phase 3 按成功数应用 cap。"""

    is_reply: bool
    candidate: DiscussionCandidate | None
    job_cards: list[EvidenceCard]
    job: ContentJob


def make_write_node(
    runner: CodexRunner,
    write_reply: WriteReplyFn,
    write_original: WriteOriginalFn,
    rewrite: RewriteFn,
    gates: QualityGates,
    *,
    checkers: list[Checker] | None = None,
    voice_profile: VoiceProfile | None = None,
) -> Node:
    """写稿 + 条件 Critic 节点：ready_jobs × cards → drafts。

    Phase 1（写稿）：沿用旧 draft 节点的 reply/original 路由与 cap（以 recommended_format
    路由，cap 计尝试而非成功）。Phase 2（条件 Critic）：L0 确定性证据门禁（``validate_draft``）
    永远执行；L1（8 检查器 + 定向重写）仅在 L0 失败或 ``llm_critique_mode == "always"`` 时
    运行。L1 沿用旧 critique 的聚合去向：pass 保留、reject（hard_fail）丢弃、rewrite 定向
    重写至多 ``max_rewrite_rounds`` 轮（重写后复验 ``validate_draft``）；needs_input 不再
    停图，改为记 warning 到 reports/draft_warnings。
    """

    suite: list[Checker] = (
        checkers
        if checkers is not None
        else default_checker_suite(runner, voice_profile)
    )

    class WriteNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            jobs = parse_items(ctx.get("ready_jobs", []), ContentJob)
            if not jobs:
                return NodeResult(status="succeeded", output=items_payload([]))

            cards = parse_items(ctx["evidence_cards"], EvidenceCard)
            candidates = parse_items(ctx.get("candidates", []), DiscussionCandidate)
            cards_by_id = {card.id: card for card in cards}
            candidates_by_id = {candidate.id: candidate for candidate in candidates}

            # Phase 1（串行确定性选择 + cap 预留）：按原顺序做 card 子集、路由、
            # candidate 存在性检查，并为每个即将写入的 job 预留 cap 名额。cap 计
            # 尝试而非成功——本意是 LLM 预算上限，保证写入次数绝不超出预算。
            plans: list[_DraftPlan] = []
            reply_count = 0
            original_count = 0
            for job in jobs:
                job_cards = [
                    cards_by_id[cid] for cid in job.source_card_ids if cid in cards_by_id
                ]
                if not job_cards:
                    continue

                # 路由权威是 recommended_format，而非 candidate_id（F7）。ORIGINAL 永远
                # 写 original；REPLY 需要 candidate_id 查找候选，缺则回退 original 语义。
                candidate_id = job.candidate_id
                is_reply = (
                    job.recommended_format == DraftKind.REPLY and candidate_id is not None
                )
                if is_reply:
                    assert candidate_id is not None
                    candidate = candidates_by_id.get(candidate_id)
                    if candidate is None:
                        continue
                    if reply_count >= gates.max_daily_replies:
                        continue
                    reply_count += 1
                    plans.append(_DraftPlan(True, candidate, job_cards, job))
                else:
                    if original_count >= gates.max_daily_original_posts:
                        continue
                    original_count += 1
                    plans.append(_DraftPlan(False, None, job_cards, job))

            def _write_draft(plan: _DraftPlan) -> Draft | None:
                if plan.is_reply:
                    assert plan.candidate is not None
                    return write_reply(runner, None, plan.candidate, cards_by_id, plan.job)
                return write_original(runner, plan.job_cards, plan.job)

            # Phase 1（并行写入）：写任务之间无共享可变状态，`pool.map` 保序。
            if not plans:
                written: list[Draft | None] = []
            elif len(plans) == 1:
                written = [_write_draft(plans[0])]
            else:
                with ThreadPoolExecutor(max_workers=min(len(plans), 8)) as pool:
                    written = list(pool.map(_write_draft, plans))

            # Phase 1（收集）：cap 已在 Phase 1 预留，按顺序丢弃 None，并打上 run_id。
            run_id = ctx.get("run_id", "")
            drafts = [
                d.model_copy(update={"run_id": run_id})
                for d in written
                if d is not None
            ]

            # Phase 2（条件 Critic）：L0 确定性证据门禁必跑；L1 检查器套件按需跑。
            matches = parse_items(ctx["match_results"], MatchResult)
            match_by_candidate = {match.candidate_id: match for match in matches}
            jobs_by_id = {job.id: job for job in jobs}

            kept: list[Draft] = []
            warnings: list[str] = []
            draft_warnings: list[DraftWarning] = []
            reports: list[dict] = []

            def _warn(draft_id: str, checker: str, message: str) -> None:
                """同时记录人类可读字符串（NodeResult）与按 draft 归属的结构化警告。"""
                warnings.append(f"draft {draft_id}: {message}")
                draft_warnings.append(
                    DraftWarning(draft_id=draft_id, checker=checker, message=message)
                )

            for draft in drafts:
                if draft.candidate_id is not None:
                    match = match_by_candidate.get(draft.candidate_id)
                    if match is None:
                        _warn(
                            draft.id,
                            "routing",
                            f"no match for candidate {draft.candidate_id}",
                        )
                        continue
                    card_ids = set(match.card_ids)
                else:
                    card_ids = set(cards_by_id)

                draft_job = (
                    jobs_by_id.get(draft.content_job_id) if draft.content_job_id else None
                )

                l0_violations = validate_draft(draft, card_ids=card_ids)
                if gates.llm_critique_mode != "always" and not l0_violations:
                    # L0 全过且非 always：不进 L1，草稿原样保留。
                    kept.append(draft)
                    continue

                current = draft
                for i in range(gates.max_rewrite_rounds + 1):
                    check_ctx = CheckContext(draft=current, cards=cards, job=draft_job)
                    checks = _run_checks(suite, check_ctx)
                    outcome = aggregate_checks(checks)
                    reports.append(
                        {
                            "draft_id": draft.id,
                            "round": i,
                            "version": current.model_dump(mode="json"),
                            "checks": [check.model_dump(mode="json") for check in checks],
                            "outcome": outcome,
                        }
                    )

                    if outcome == AggregateOutcome.PASS:
                        kept.append(current)
                        break

                    if outcome == AggregateOutcome.REJECT:
                        for check in checks:
                            if check.severity == "hard_fail" and not check.passed:
                                _warn(
                                    draft.id,
                                    check.checker,
                                    f"rejected by {check.checker} "
                                    f"({', '.join(check.locations) or 'n/a'})",
                                )
                        break

                    if outcome == AggregateOutcome.NEEDS_INPUT:
                        # needs_input 不再停图：记 warning 到 reports/draft_warnings，
                        # 草稿不进 kept（与旧 critique 停图前状态一致）。
                        offending = [
                            check
                            for check in checks
                            if check.severity == "high"
                            and check.requires_human_input
                            and not check.passed
                        ]
                        for check in offending:
                            _warn(
                                draft.id,
                                check.checker,
                                f"needs human input ({check.checker})",
                            )
                        break

                    # rewrite：只把失败检查器的指令交给 writer
                    failed = [check for check in checks if not check.passed]
                    if i == gates.max_rewrite_rounds:
                        _warn(
                            draft.id,
                            "critique",
                            f"failed critique after {gates.max_rewrite_rounds} rewrites",
                        )
                        break
                    current = rewrite(runner, current, failed, cards_by_id, draft_job)
                    violations = validate_draft(current, card_ids=card_ids)
                    if violations:
                        _warn(
                            current.id,
                            "evidence",
                            f"rewrite produced invalid claims: {violations}",
                        )
                        break

            out = items_payload(cast(list[BaseModel], kept))
            out["warnings"] = warnings
            out["draft_warnings"] = [w.model_dump(mode="json") for w in draft_warnings]
            out["reports"] = reports
            return NodeResult(
                status="succeeded",
                output=out,
                warnings=warnings,
            )

    return WriteNode(
        name="write",
        reads=["ready_jobs", "evidence_cards", "candidates", "match_results"],
        writes="drafts",
        succeeds_to="DRAFTED",
    )
