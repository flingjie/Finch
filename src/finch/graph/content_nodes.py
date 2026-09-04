"""Writer/Critic/Daily Brief 阶段 Graph 节点（Phase 5 Task F6）：draft / critique / brief。

Note: writer.py functions now accept optional ContentJob param to stamp content_job_id
and position_statement on Drafts.
"""

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple, cast

from pydantic import BaseModel

from ..codex.runner import CodexRunner
from ..content.checkers.actionability import ActionabilityChecker
from ..content.checkers.aggregate import AggregateOutcome, aggregate_checks
from ..content.checkers.base import CheckContext, Checker, CheckResult
from ..content.checkers.decision import DecisionChecker
from ..content.checkers.evidence import EvidenceChecker
from ..content.checkers.portability import PortabilityChecker
from ..content.checkers.safety import SafetyChecker
from ..content.checkers.specificity import SpecificityChecker
from ..content.checkers.structure import StructureChecker
from ..content.checkers.voice import VoiceChecker
from ..content.claims import validate_draft
from ..content.jobs import ContentJob, ContentJobStatus, define_content_jobs
from ..content.models import DailyBrief, Draft, DraftKind
from ..content.voice import VoiceProfile
from ..evidence.models import EvidenceCard, MatchResult
from ..settings import QualityGates
from ..storage.repositories import ContentJobRepository
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


def make_draft_node(
    runner: CodexRunner,
    write_reply: WriteReplyFn,
    write_original: WriteOriginalFn,
    gates: QualityGates,
) -> Node:
    """证据草稿节点：ready_jobs × cards → drafts。

    对每个 ready job：以 recommended_format 路由（ORIGINAL → write_original；REPLY →
    查 DiscussionCandidate 后 write_reply）。recommended_format 为 REPLY 但 candidate_id
    为空时回退到 original 语义。空 ready_jobs → drafts=[]（幂等）。
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

            # Phase 2（并行写入）：写任务之间无共享可变状态，`pool.map` 保序。
            if not plans:
                written: list[Draft | None] = []
            elif len(plans) == 1:
                written = [_write_draft(plans[0])]
            else:
                with ThreadPoolExecutor(max_workers=min(len(plans), 8)) as pool:
                    written = list(pool.map(_write_draft, plans))

            # Phase 3（收集）：cap 已在 Phase 1 预留，按顺序丢弃 None。
            drafts = [d for d in written if d is not None]

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


def default_checker_suite(
    runner: CodexRunner,
    voice_profile: VoiceProfile | None = None,
) -> list[Checker]:
    """Critic Suite 默认检查器套件（Task 6）：现有 4 个 + 新增 4 个 = 8 个。

    顺序即执行顺序；VoiceChecker 需要 VoiceProfile（默认空画像）。
    """
    profile = voice_profile if voice_profile is not None else VoiceProfile()
    return [
        EvidenceChecker(runner),
        DecisionChecker(runner),
        SpecificityChecker(runner),
        PortabilityChecker(runner),
        VoiceChecker(runner, profile),
        StructureChecker(runner),
        ActionabilityChecker(runner),
        SafetyChecker(runner),
    ]


def _run_checks(suite: list[Checker], check_ctx: CheckContext) -> list[CheckResult]:
    """并行执行 Critic Suite，结果顺序与串行一致（``pool.map`` 保序）。

    单个 checker 退化为串行；多个 checker 时以 ``len(suite)`` 个 worker 并行，
    各自内部状态只读（CodexRunner 每次调用独立子进程 + 临时目录），线程安全。
    """
    if len(suite) <= 1:
        return [checker.check(check_ctx) for checker in suite]
    with ThreadPoolExecutor(max_workers=len(suite)) as pool:
        return list(pool.map(lambda checker: checker.check(check_ctx), suite))


def make_critique_node(
    runner: CodexRunner,
    rewrite: RewriteFn,
    gates: QualityGates,
    checkers: list[Checker] | None = None,
    voice_profile: VoiceProfile | None = None,
) -> Node:
    """草稿审查节点：Critic Suite 逐项检查 → 确定性聚合 → 定向重写。

    对每条 draft：跑检查器套件，``aggregate_checks`` 决定去向：
    - pass → 保留
    - reject（hard_fail）→ 丢弃，绝不到 review
    - needs_input → 停下管线（runtime 处理）
    - rewrite → 定向重写（只传失败检查器的指令），最多 max_rewrite_rounds 次
    每轮产出 checker report（供 Task 7 持久化为 DraftVersionRecord/CriticReportRecord）。
    """

    suite: list[Checker] = (
        checkers
        if checkers is not None
        else default_checker_suite(runner, voice_profile)
    )

    class CritiqueNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            drafts = parse_items(ctx["drafts"], Draft)
            matches = parse_items(ctx["match_results"], MatchResult)
            cards = parse_items(ctx["evidence_cards"], EvidenceCard)
            content_jobs = parse_items(ctx.get("content_jobs") or {}, ContentJob)
            ready_jobs = parse_items(ctx.get("ready_jobs") or {}, ContentJob)

            cards_by_id = {card.id: card for card in cards}
            match_by_candidate = {match.candidate_id: match for match in matches}
            jobs_by_id = {job.id: job for job in [*content_jobs, *ready_jobs]}

            kept: list[Draft] = []
            warnings: list[str] = []
            reports: list[dict] = []

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

                job = jobs_by_id.get(draft.content_job_id) if draft.content_job_id else None

                current = draft
                for i in range(gates.max_rewrite_rounds + 1):
                    check_ctx = CheckContext(draft=current, cards=cards, job=job)
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
                                warnings.append(
                                    f"draft {draft.id}: rejected by {check.checker} "
                                    f"({', '.join(check.locations) or 'n/a'})"
                                )
                        break

                    if outcome == AggregateOutcome.NEEDS_INPUT:
                        offending = [
                            check
                            for check in checks
                            if check.severity == "high"
                            and check.requires_human_input
                            and not check.passed
                        ]
                        input_warnings = [
                            f"draft {draft.id}: needs human input ({check.checker})"
                            for check in offending
                        ]
                        out = items_payload(cast(list[BaseModel], []))
                        out["warnings"] = input_warnings
                        out["reports"] = reports
                        return NodeResult(
                            status="needs_input",
                            output=out,
                            warnings=input_warnings,
                        )

                    # rewrite：只把失败检查器的指令交给 writer
                    failed = [check for check in checks if not check.passed]
                    if i == gates.max_rewrite_rounds:
                        warnings.append(
                            f"draft {draft.id} failed critique after "
                            f"{gates.max_rewrite_rounds} rewrites"
                        )
                        break
                    current = rewrite(runner, current, failed, cards_by_id, job)
                    violations = validate_draft(current, card_ids=card_ids)
                    if violations:
                        warnings.append(
                            f"draft {current.id} rewrite produced invalid claims: {violations}"
                        )
                        break

            out = items_payload(cast(list[BaseModel], kept))
            out["warnings"] = warnings
            out["reports"] = reports
            return NodeResult(
                status="succeeded",
                output=out,
                warnings=warnings,
            )

    return CritiqueNode(
        name="critique",
        reads=["drafts", "match_results", "evidence_cards", "content_jobs", "ready_jobs"],
        writes="drafts",
        succeeds_to="CRITIQUED",
    )


def _format_position(job: ContentJob) -> str:
    position = job.author_position
    if position is None:
        return "无"
    decision = position.decision or "无"
    tradeoff = position.tradeoff or "无"
    return f"{decision}；取舍：{tradeoff}"


def _format_evidence(job: ContentJob, cards_by_id: dict[str, EvidenceCard]) -> str:
    cards = [cards_by_id[cid] for cid in job.source_card_ids if cid in cards_by_id]
    if not cards:
        return "无"
    parts: list[str] = []
    for card in cards:
        if card.sources:
            src = "；".join(s.url for s in card.sources)
            parts.append(f"{card.claim}（{src}）")
        else:
            parts.append(card.claim)
    return "；".join(parts)


def _recommended_action(job: ContentJob | None) -> str:
    if job is None:
        return "确认观点"
    if job.status == ContentJobStatus.DO_NOT_WRITE:
        return "暂不写"
    position = job.author_position
    if (
        position is not None
        and position.confirmed
        and bool(position.decision)
        and bool(position.tradeoff)
    ):
        return "待人工审核（发布需手动）"
    return "确认观点"


def _render_candidate_brief(
    job: ContentJob | None,
    draft: Draft,
    cards_by_id: dict[str, EvidenceCard],
    warnings: list[str],
) -> str:
    """按 Spec §7 渲染单个候选的 6 项。"""
    lines = [f"## 候选 {draft.id}"]
    # 精确前缀匹配（词边界）：避免 draft_1 继承 draft_10 的 warning（F6）。
    prefix = re.compile(rf"^draft {re.escape(draft.id)}\b")
    draft_warnings = [w for w in warnings if prefix.match(w)]
    critic_risk = "；".join(draft_warnings) if draft_warnings else "无"
    if job is None:
        lines.append("1. 要完成的工作：无")
        lines.append("2. 目标读者与期望动作：无")
        lines.append("3. 证据来源：无")
        lines.append("4. 核心判断与取舍：无")
        lines.append(f"5. Critic 未解决风险：{critic_risk}")
        lines.append("6. 推荐动作：确认观点")
        lines.append(f"草稿正文：{draft.body}")
        return "\n".join(lines)

    understand = job.intended_effect.understand or "无"
    action = job.intended_effect.action or "无"
    lines.append(f"1. 要完成的工作：{job.reader_problem}（期望读者理解：{understand}）")
    lines.append(f"2. 目标读者与期望动作：{job.audience}；期望动作：{action}")
    lines.append(f"3. 证据来源：{_format_evidence(job, cards_by_id)}")
    lines.append(f"4. 核心判断与取舍：{_format_position(job)}")
    lines.append(f"5. Critic 未解决风险：{critic_risk}")
    lines.append(f"6. 推荐动作：{_recommended_action(job)}")
    lines.append(f"草稿正文：{draft.body}")
    return "\n".join(lines)


def make_brief_node(
    gates: QualityGates,
    jobs_repo: ContentJobRepository | None = None,
) -> Node:
    """每日简报节点：渲染 DailyBrief 并写入动态终态。"""

    class BriefNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            drafts = parse_items(ctx["drafts"], Draft)
            jobs = parse_items(ctx.get("content_jobs", []), ContentJob)
            cards = parse_items(ctx.get("evidence_cards", []), EvidenceCard)
            cards_by_id = {card.id: card for card in cards}
            jobs_by_id = {job.id: job for job in jobs}
            warnings: list[str] = ctx["drafts"].get("warnings", [])

            reply_count = sum(1 for draft in drafts if draft.kind == DraftKind.REPLY)
            original_count = sum(1 for draft in drafts if draft.kind == DraftKind.ORIGINAL)

            blocks: list[str] = []
            for draft in drafts:
                job = None
                if draft.content_job_id:
                    if jobs_repo is not None:
                        job = jobs_repo.get_job(draft.content_job_id)
                    if job is None:
                        job = jobs_by_id.get(draft.content_job_id)
                blocks.append(_render_candidate_brief(job, draft, cards_by_id, warnings))

            body = "\n\n".join(blocks)
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
        reads=["drafts", "content_jobs", "evidence_cards"],
        writes="brief",
        succeeds_to="WAITING_FOR_REVIEW",
        terminal_state_key="terminal_state",
    )


def make_define_jobs_node(
    runner: CodexRunner,
    jobs_repo: ContentJobRepository | None = None,
) -> Node:
    """定义内容任务节点：match_results/evidence_cards/candidates → content_jobs。

    通过 Codex runner 调用 define_content_jobs prompt 生成 ContentJob 列表。
    对每个 job 验证：
    - source_card_ids 是该 job 自身卡片范围的子集：candidate_id 非空时取该候选
      match 的 card_ids；candidate_id 为空（original）时取全部 matched card_ids 的并集
    - candidate_id（若非空）存在于 match_results 的 candidate ids 中
    不合法的 job 被过滤。可选 jobs_repo 会把合法 job upsert 进 ContentJobRepository。
    """

    class DefineJobsNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            match_results = parse_items(ctx["match_results"], MatchResult)
            cards = parse_items(ctx["evidence_cards"], EvidenceCard)

            # Pass all cards to the LLM for job generation
            jobs_output = define_content_jobs(runner, cards)

            # Filter jobs: validate source_card_ids against the job's OWN card scope —
            # its candidate's match card_ids when candidate_id is set, otherwise the union
            # of all matched card_ids (original). A reply job carrying a card from a
            # different candidate would later be dropped, so reject it here (F5).
            match_by_candidate = {mr.candidate_id: mr for mr in match_results}
            all_card_ids = list({cid for mr in match_results for cid in mr.card_ids})
            valid_jobs: list[ContentJob] = []
            for job in jobs_output.items:
                if job.candidate_id is not None:
                    match = match_by_candidate.get(job.candidate_id)
                    if match is None:
                        continue
                    available_ids = match.card_ids
                else:
                    available_ids = all_card_ids
                if job.validate_source_cards(available_ids):
                    valid_jobs.append(job)

            if jobs_repo is not None:
                jobs_repo.upsert_jobs(valid_jobs)

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


def make_position_gate_node(
    jobs_repo: ContentJobRepository | None = None,
) -> Node:
    """位置确认门节点：content_jobs → ready_jobs (deterministic, no LLM)。

    对每个 job 判断：
    - DO_NOT_WRITE: 跳过（不写入 ready_jobs，不报错）
    - READY: author_position 存在且 decision/tradeoff 非空且 confirmed=True
    - 其他（缺 position、decision/tradeoff 为空、未确认）→ needs_input

    若提供 jobs_repo，则对每个 job 取 repo 里的最新版本（fallback 到 context），
    这样 resume 时用户通过 jobs answer/confirm-position 的编辑会被采纳。

    如果任何 job 需要输入，返回 needs_input 并记录这些 job；
    否则写入 ready_jobs 为所有 READY 的 job。
    """

    class PositionGateNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            context_jobs = parse_items(ctx["content_jobs"], ContentJob)

            needs_input_jobs: list[ContentJob] = []
            ready_jobs: list[ContentJob] = []

            for context_job in context_jobs:
                job = context_job
                if jobs_repo is not None:
                    fresh = jobs_repo.get_job(context_job.id)
                    if fresh is not None:
                        job = fresh

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
