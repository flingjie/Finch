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
from ..content.jobs import (
    ContentJob,
    ContentJobStatus,
    TopicProposal,
    expand_content_job,
    plan_content_topics,
    select_planning_evidence,
    select_primary_job,
)
from ..content.models import DailyBrief, Draft, DraftKind, DraftWarning
from ..content.voice import VoiceProfile
from ..evidence.models import EvidenceCard, MatchResult
from ..llm.base import StructuredInferenceRunner
from ..settings import DailyBudget, QualityGates
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
    runner: StructuredInferenceRunner | None,
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
                                _warn(
                                    draft.id,
                                    check.checker,
                                    f"rejected by {check.checker} "
                                    f"({', '.join(check.locations) or 'n/a'})",
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
                        for check in offending:
                            draft_warnings.append(
                                DraftWarning(
                                    draft_id=draft.id,
                                    checker=check.checker,
                                    message=f"needs human input ({check.checker})",
                                )
                            )
                        out = items_payload(cast(list[BaseModel], []))
                        out["warnings"] = input_warnings
                        out["draft_warnings"] = [
                            w.model_dump(mode="json") for w in draft_warnings
                        ]
                        out["reports"] = reports
                        return NodeResult(
                            status="needs_input",
                            output=out,
                            warnings=input_warnings,
                        )

                    # rewrite：只把失败检查器的指令交给 writer
                    failed = [check for check in checks if not check.passed]
                    if i == gates.max_rewrite_rounds:
                        _warn(
                            draft.id,
                            "critique",
                            f"failed critique after {gates.max_rewrite_rounds} rewrites",
                        )
                        break
                    current = rewrite(runner, current, failed, cards_by_id, job)
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


def _job_title(job: ContentJob) -> str:
    """首选标题：优先 ``core_message``，其次 ``reader_problem``，最后 job id。"""
    return job.core_message or job.reader_problem or job.id


def _fresh_job(
    job: ContentJob | None,
    jobs_repo: ContentJobRepository | None,
) -> ContentJob | None:
    """取 repo 里的最新 job 版本（resume 后采纳用户 confirm-position 的编辑）。"""
    if job is None or jobs_repo is None:
        return job
    fresh = jobs_repo.get_job(job.id)
    return fresh if fresh is not None else job


def _pick_primary_draft(
    drafts: list[Draft],
    primary_job: ContentJob | None,
) -> Draft | None:
    """把 primary job 绑定到其草稿；无 primary job 时取第一条草稿。"""
    if primary_job is not None:
        for draft in drafts:
            if draft.content_job_id == primary_job.id:
                return draft
    return drafts[0] if drafts else None


def _main_not_write_reason(
    jobs: list[ContentJob],
    deferred: list[dict],
    primary_job: ContentJob | None,
) -> str:
    """无草稿时的「不建议写」具体原因（计划 Task 3.2 / no_good_candidate golden case）。"""
    for job in jobs:
        if job.status == ContentJobStatus.DO_NOT_WRITE and job.reject_reason:
            return f"job「{job.id}」被标记暂不写：{job.reject_reason}"
    if not jobs:
        return "没有生成任何候选 job（无证据卡或讨论未匹配）"
    if primary_job is not None:
        return "首选 job 存在但未产出可发布草稿（写入失败或被 Critic 拒绝）"
    if deferred:
        first = deferred[0]
        return f"无候选达到发布门槛（{first.get('reason', '未提供理由')}）"
    return "证据不足以支撑可发布内容"


_LEGACY_WARNING = re.compile(r"^draft\s+(\S+)\s*:\s*(.*)$", re.DOTALL)


def _legacy_warning(text: str) -> DraftWarning:
    """把旧版 ``draft {id}: {message}`` 字符串解析回 DraftWarning（checker 记空）。"""
    match = _LEGACY_WARNING.match(text)
    if match is None:
        return DraftWarning(draft_id="", checker="", message=text)
    return DraftWarning(draft_id=match.group(1), checker="", message=match.group(2))


def _collect_draft_warnings(payload: dict) -> list[DraftWarning]:
    """从 critique 输出读取结构化警告；兼容旧版字符串 ``warnings`` 字段。"""
    raw = payload.get("draft_warnings") or []
    if raw:
        warnings: list[DraftWarning] = []
        for item in raw:
            if isinstance(item, dict):
                warnings.append(DraftWarning.model_validate(item))
            else:
                warnings.append(_legacy_warning(str(item)))
        return warnings
    legacy = payload.get("warnings") or []
    return [_legacy_warning(str(w)) for w in legacy]


def _conclusion(
    primary_draft: Draft | None,
    primary_job: ContentJob | None,
    jobs: list[ContentJob],
    deferred: list[dict],
) -> str:
    if primary_draft is not None:
        title = _job_title(primary_job) if primary_job is not None else primary_draft.id
        return f"今日建议发布 1 篇内容，首选：{title}。"
    reason = _main_not_write_reason(jobs, deferred, primary_job)
    return f"今日没有建议发布的内容。不建议写原因：{reason}。"


def _primary_section(primary_job: ContentJob | None) -> str:
    if primary_job is None:
        return "无（本轮没有选出 primary job）"
    lines = [
        f"首选：{_job_title(primary_job)}",
        f"job id：{primary_job.id}",
        f"形式：{primary_job.recommended_format.value}",
    ]
    if primary_job.audience:
        lines.append(f"目标读者：{primary_job.audience}")
    return "\n".join(lines)


def _why_now_section(primary_job: ContentJob | None) -> str:
    if primary_job is None:
        return "无"
    why_now = primary_job.why_now or "无"
    audience_evidence = primary_job.audience_evidence or "无"
    return f"为什么现在值得说：{why_now}\n公共问题/受众证据：{audience_evidence}"


def _position_section(primary_job: ContentJob | None) -> str:
    if primary_job is None:
        return "无"
    position = primary_job.author_position
    if position is None:
        return "无（尚未形成作者立场）"
    lines = [
        f"判断：{position.claim or '无'}",
        f"决定：{position.decision or '无'}",
        f"取舍：{position.tradeoff or '无'}",
    ]
    if position.change_mind_if:
        lines.append(f"什么会改变判断：{position.change_mind_if}")
    return "\n".join(lines)


def _evidence_section(
    primary_job: ContentJob | None,
    cards_by_id: dict[str, EvidenceCard],
    candidates_by_id: dict[str, DiscussionCandidate],
) -> str:
    if primary_job is None:
        return "无"
    lines = [f"工程证据：{_format_evidence(primary_job, cards_by_id)}"]
    if primary_job.candidate_id:
        candidate = candidates_by_id.get(primary_job.candidate_id)
        if candidate is not None:
            lines.append(f"讨论上下文：{candidate.text}（{candidate.url}）")
        else:
            lines.append("讨论上下文：无")
    else:
        lines.append("讨论上下文：无（原创，无外部讨论）")
    return "\n".join(lines)


def _risk_section(
    primary_draft: Draft | None,
    draft_warnings: list[DraftWarning],
) -> str:
    if primary_draft is None:
        return "无"
    own = [w for w in draft_warnings if w.draft_id == primary_draft.id]
    if not own:
        return "无"
    return "\n".join(f"{w.checker or '未知检查器'}：{w.message}" for w in own)


def _draft_section(
    primary_draft: Draft | None,
    extra_drafts: list[Draft],
    draft_warnings: list[DraftWarning],
) -> str:
    if primary_draft is None and not extra_drafts:
        return "无（本轮没有产出草稿）"
    blocks: list[str] = []
    if primary_draft is not None:
        blocks.append(primary_draft.body)
    for draft in extra_drafts:
        own = [w for w in draft_warnings if w.draft_id == draft.id]
        risk = "；".join(f"{w.checker or '未知'}: {w.message}" for w in own) or "无"
        blocks.append(f"[草稿 {draft.id}]\n{draft.body}\n[草稿 {draft.id} 未解决风险：{risk}]")
    return "\n\n".join(blocks)


def _commands_section(primary_draft: Draft | None) -> str:
    if primary_draft is None:
        return "无草稿可操作"
    return (
        f"- 批准：finch review approve {primary_draft.id}\n"
        f"- 修订：finch review revise {primary_draft.id} --file <path>\n"
        f"- 跳过：finch review skip {primary_draft.id} --reason <reason>"
    )


def _not_now_section(deferred: list[dict]) -> str:
    if not deferred:
        return "无"
    lines: list[str] = []
    for item in deferred:
        job_id = item.get("job_id", "?")
        reason = item.get("reason", "未提供理由")
        lines.append(f"- {job_id}：{reason}")
    return "\n".join(lines)


def _funnel_section(
    jobs: list[ContentJob],
    cards: list[EvidenceCard],
    candidates: list[DiscussionCandidate],
    match_results: list[MatchResult],
    primary_job: ContentJob | None,
    drafts: list[Draft],
    track_failures: list[str],
) -> str:
    publishable = sum(1 for card in cards if card.publishable)
    lines = [
        f"提取证据卡：{len(cards)} 张（其中可发布 {publishable} 张）",
        f"收集讨论：{len(candidates)} 条",
        f"匹配讨论：{len(match_results)} 条",
        f"生成 jobs：{len(jobs)} 个",
        f"选出 primary：{'1 个' if primary_job is not None else '0 个'}",
        f"产出草稿：{len(drafts)} 篇",
    ]
    if track_failures:
        lines.append("轨道失败：" + "；".join(track_failures))
    else:
        lines.append("轨道失败：无（原创轨道内未捕获到节点级失败）")
    lines.append("互动轨道：由双轨调度单独汇报（原创 Brief 不感知）")
    return "\n".join(lines)


def _render_daily_brief(
    *,
    primary_job: ContentJob | None,
    primary_draft: Draft | None,
    extra_drafts: list[Draft],
    deferred: list[dict],
    jobs: list[ContentJob],
    cards: list[EvidenceCard],
    candidates: list[DiscussionCandidate],
    match_results: list[MatchResult],
    draft_warnings: list[DraftWarning],
    cards_by_id: dict[str, EvidenceCard],
    candidates_by_id: dict[str, DiscussionCandidate],
    track_failures: list[str],
) -> str:
    """渲染决策优先的 Daily Brief（计划 §3.1 的 10 段固定顺序）。"""
    evidence = _evidence_section(primary_job, cards_by_id, candidates_by_id)
    funnel = _funnel_section(
        jobs,
        cards,
        candidates,
        match_results,
        primary_job,
        [d for d in [primary_draft, *extra_drafts] if d is not None],
        track_failures,
    )
    sections = [
        f"## 1. 今日结论\n{_conclusion(primary_draft, primary_job, jobs, deferred)}",
        f"## 2. 今日首选\n{_primary_section(primary_job)}",
        f"## 3. 为什么值得说\n{_why_now_section(primary_job)}",
        f"## 4. 作者判断与取舍\n{_position_section(primary_job)}",
        f"## 5. 工程证据与讨论上下文\n{evidence}",
        f"## 6. 未解决风险\n{_risk_section(primary_draft, draft_warnings)}",
        f"## 7. 草稿正文\n{_draft_section(primary_draft, extra_drafts, draft_warnings)}",
        f"## 8. 命令\n{_commands_section(primary_draft)}",
        f"## 9. NOT NOW\n{_not_now_section(deferred)}",
        f"## 10. 本轮漏斗和轨道失败\n{funnel}",
    ]
    return "\n\n".join(sections)


def make_brief_node(
    gates: QualityGates,
    jobs_repo: ContentJobRepository | None = None,
    run_id: str | None = None,
) -> Node:
    """每日简报节点：渲染决策优先 DailyBrief 并写入动态终态（计划 Task 3.1/3.2/3.4）。

    - 今日首选来自 position_gate 输出的 primary（``ready_jobs.items``）。
    - NOT NOW 来自 position_gate 输出的 ``deferred``（job_id + reason）。
    - 无草稿时仍输出完整 10 段结构与「不建议写」具体原因，绝不返回空白。
    - 未解决风险只展示当前草稿的 DraftWarning（按 draft_id 归属）。
    """

    class BriefNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            drafts = parse_items(ctx["drafts"], Draft)
            jobs = parse_items(ctx.get("content_jobs", []), ContentJob)
            cards = parse_items(ctx.get("evidence_cards", []), EvidenceCard)
            candidates = parse_items(ctx.get("candidates", []), DiscussionCandidate)
            match_results = parse_items(ctx.get("match_results", []), MatchResult)

            cards_by_id = {card.id: card for card in cards}
            jobs_by_id = {job.id: job for job in jobs}
            candidates_by_id = {candidate.id: candidate for candidate in candidates}

            # primary 与 deferred 来自 position_gate 输出（ready_jobs payload）。
            gate = ctx.get("ready_jobs", {})
            primary_jobs = parse_items(gate, ContentJob)
            primary_job = _fresh_job(primary_jobs[0] if primary_jobs else None, jobs_repo)

            primary_draft = _pick_primary_draft(drafts, primary_job)
            if primary_job is None and primary_draft is not None and primary_draft.content_job_id:
                primary_job = _fresh_job(
                    jobs_by_id.get(primary_draft.content_job_id), jobs_repo
                )

            deferred = gate.get("deferred", []) or []
            draft_warnings = _collect_draft_warnings(ctx["drafts"])
            track_failures = list(ctx.get("content_jobs", {}).get("warnings", []))

            extra_drafts = [d for d in drafts if d is not primary_draft]

            reply_count = sum(1 for d in drafts if d.kind == DraftKind.REPLY)
            original_count = sum(1 for d in drafts if d.kind == DraftKind.ORIGINAL)

            resolved_run_id = ctx.get("run_id") or run_id or "daily"

            body = _render_daily_brief(
                primary_job=primary_job,
                primary_draft=primary_draft,
                extra_drafts=extra_drafts,
                deferred=deferred,
                jobs=jobs,
                cards=cards,
                candidates=candidates,
                match_results=match_results,
                draft_warnings=draft_warnings,
                cards_by_id=cards_by_id,
                candidates_by_id=candidates_by_id,
                track_failures=track_failures,
            )

            brief = DailyBrief(
                run_id=resolved_run_id,
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
        reads=[
            "drafts",
            "content_jobs",
            "evidence_cards",
            "ready_jobs",
            "candidates",
            "match_results",
        ],
        writes="brief",
        succeeds_to="WAITING_FOR_REVIEW",
        terminal_state_key="terminal_state",
    )


def make_define_jobs_node(
    plan_runner: StructuredInferenceRunner,
    expand_runner: StructuredInferenceRunner,
    expand_concurrency: int = 4,
    jobs_repo: ContentJobRepository | None = None,
    budget: DailyBudget | None = None,
) -> Node:
    """定义内容任务节点：match_results/evidence_cards/candidates → content_jobs。

    两阶段：先 ``plan_content_topics`` 一次性把证据卡聚类成主题，再并行
    ``expand_content_job`` 把每个主题展开成完整 ContentJob（``pool.map`` 保序）。
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
                    # 故障隔离：单个 topic 展开失败不拖垮整个节点，其余 topic 照常产出。
                    warnings.append(
                        f"define_jobs: expand failed for topic {topic.id}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    return None

            # 并行展开：单个 topic 串行；多个 topic 用 ``pool.map`` 保证顺序与串行一致。
            if not kept_topics:
                expanded: list[ContentJob | None] = []
            elif len(kept_topics) == 1:
                expanded = [_expand(kept_topics[0])]
            else:
                with ThreadPoolExecutor(
                    max_workers=min(len(kept_topics), expand_concurrency)
                ) as pool:
                    expanded = list(pool.map(_expand, kept_topics))

            jobs = [job for job in expanded if job is not None]

            # Filter jobs: validate source_card_ids against the job's OWN card scope —
            # its candidate's match card_ids when candidate_id is set, otherwise the union
            # of all matched card_ids (original). A reply job carrying a card from a
            # different candidate would later be dropped, so reject it here (F5).
            # 再按 job.id 去重，避免重复 id 在 upsert 时互相覆盖、下游处理重复列表。
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
            if warnings:
                output["warnings"] = warnings
            return NodeResult(
                status="succeeded",
                output=output,
                warnings=warnings,
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
    """位置确认门：确定性选出唯一 primary，只有 primary 可阻塞内容生成。

    - ``DO_NOT_WRITE`` job 永久拒绝，不参与选择。
    - 只有 primary job 可进入 ``ready_jobs``；其余 job 保存为 proposed/deferred
      （不删除、不永久 do_not_write），并附带 not now 理由。
    - primary 缺失已确认立场时，只问最多 3 个问题（``missing_questions``）→
      ``needs_input``。
    - 用户拒绝 primary 后，确定性选择下一个 job，最多递补一次（避免无限循环）。

    若提供 jobs_repo，则对每个 job 取 repo 里的最新版本（fallback 到 context），
    这样 resume 时用户通过 jobs answer/confirm-position/reject 的编辑会被采纳。
    """

    class PositionGateNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            context_jobs = parse_items(ctx["content_jobs"], ContentJob)
            cards = parse_items(ctx.get("evidence_cards", {}), EvidenceCard)
            cards_by_id = {card.id: card for card in cards}

            # 取 repo 里的最新版本（resume 路径），回退到 context。
            jobs: list[ContentJob] = []
            for context_job in context_jobs:
                job = context_job
                if jobs_repo is not None:
                    fresh = jobs_repo.get_job(context_job.id)
                    if fresh is not None:
                        job = fresh
                jobs.append(job)

            active = [job for job in jobs if job.status != ContentJobStatus.DO_NOT_WRITE]
            # 只有「用户拒绝」的 job（DO_NOT_WRITE 且带 reject_reason）计入递补额度；
            # 模型自行判定 do_not_write（无 reject_reason）不占用「递补一次」，否则
            # 首日两个模型 do_not_write 主题就会误停剩余可写 job。
            user_rejected = [
                job
                for job in jobs
                if job.status == ContentJobStatus.DO_NOT_WRITE and job.reject_reason
            ]

            # 最多递补一次：primary 与其一次递补都被用户拒绝后停止提议，避免无限循环。
            if len(user_rejected) > 1:
                output = items_payload([])
                output["deferred"] = [
                    {"job_id": job.id, "reason": "fallback exhausted"}
                    for job in active
                ]
                return NodeResult(
                    status="succeeded",
                    output=output,
                    warnings=["position gate stopped after one fallback"],
                )

            primary, deferred = select_primary_job(active, cards_by_id=cards_by_id)

            output = items_payload([])
            if deferred:
                output["deferred"] = [
                    {"job_id": d.job.id, "reason": d.reason} for d in deferred
                ]
                if jobs_repo is not None:
                    for d in deferred:
                        # 保存为 proposed（not now，可恢复），不删除、不 do_not_write。
                        jobs_repo.upsert_job(
                            d.job.model_copy(
                                update={"status": ContentJobStatus.PROPOSED}
                            )
                        )

            if primary is None:
                # 没有可提议的 job（全部 DO_NOT_WRITE 或为空）。
                return NodeResult(status="succeeded", output=output)

            position = primary.author_position
            ready = (
                position is not None
                and bool(position.decision)
                and bool(position.tradeoff)
                and position.confirmed
            )
            if ready:
                output["items"] = [primary.model_dump(mode="json")]
                return NodeResult(status="succeeded", output=output)

            # primary 缺已确认立场：只问最多 3 个问题。
            output["items"] = [primary.model_dump(mode="json")]
            output["questions"] = list(primary.missing_questions)[:3]
            return NodeResult(
                status="needs_input",
                output=output,
                warnings=[f"primary job {primary.id} needs a confirmed position"],
            )

    return PositionGateNode(
        name="position_gate",
        reads=["content_jobs", "evidence_cards"],
        writes="ready_jobs",
        succeeds_to="POSITIONS_READY",
    )
