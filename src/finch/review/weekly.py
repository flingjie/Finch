"""周复盘分析：从草稿 / 审核 / 反馈 / ContentJob / Critic 报告汇总批准率与内容效果指标。

Task 8 新增七个指标，回答「哪些内容任务有效/失败，失败发生在选题、证据、观点、
表达还是分发」：

- evidence_coverage   证据覆盖（证据检查通过的报告占比，看证据环节）
- decision_density    立场密度（已发布草稿中，绑定 job 有 decision+tradeoff 的占比，看选题/立场）
- generic_sentence_rate 套话率（portability/specificity 失败的报告占比，看表达）
- human_correction_rate 人工修正率（需人工改事实/立场的已审草稿占比，看观点/事实）
- job_completion_rate 任务完成率（结果评估 job_completed in {yes, partly} 的占比，看选题）
- useful_reply_rate   有用回复率（回复中获得 useful_reply_count>0 的占比，看分发）
- do_not_write_rate   不写率（DO_NOT_WRITE job 占比，信息性，非失败）

所有新指标只统计「非遗留」草稿（``content_job_id is not None``），遗留草稿只读、
不参与新指标（仍参与既有 approval-rate 指标）。
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import Draft
from finch.review.models import Feedback, ReviewAction, ReviewDecision, SkipReason
from finch.storage.repositories import (
    ContentJobRepository,
    CriticReportRepository,
    DraftRepository,
    FeedbackRepository,
    ReviewRepository,
)


class NextWeekPlan(BaseModel):
    """下周计划：周报三问（计划 §4.2）。"""

    one_thing: str = ""       # 下周继续强化什么
    one_experiment: str = ""  # 只验证哪一个假设
    stop_doing: str = ""      # 明确停止什么


class WeeklyReport(BaseModel):
    reviewed_drafts: int = 0  # 时间窗内已作出最终决策的草稿数
    approved: int = 0         # 最终决策为 approve 的草稿数
    revised: int = 0          # 修改次数（REVISE 历史事件数，含后被 approve 覆盖的）
    skipped: int = 0          # 最终决策为 skip 的草稿数
    approval_rate: float = 0.0  # approved / reviewed_drafts（无决策则 0.0）
    skip_reasons: dict[str, int] = Field(default_factory=dict)
    published_draft_ids: list[str] = Field(default_factory=list)
    published_candidate_ids: list[str] = Field(default_factory=list)

    # ---- Task 8 新指标（比例 0.0–1.0；None = 无分母/无数据）----
    evidence_coverage: float | None = None
    decision_density: float | None = None
    generic_sentence_rate: float | None = None
    human_correction_rate: float | None = None
    job_completion_rate: float | None = None
    useful_reply_rate: float | None = None
    do_not_write_rate: float = 0.0  # 信息性，无「继续/停止」判定

    # ---- Task 0.2：首稿 Critic rewrite 轮数（信息性，不设门禁）----
    rewrite_rounds: dict[str, int] = Field(default_factory=dict)  # draft_id → rewrite 轮数
    rewritten_drafts: int = 0  # 有 >0 次 rewrite 的 eligible 草稿数

    # ---- Phase 4 周报三问（计划 §4.2；由方向性指标派生，见 _build_narrative）----
    weekly_insight: str = ""  # 本周最重要的一个发现
    next_week: NextWeekPlan = Field(default_factory=NextWeekPlan)


# ---- 「继续/调整/停止」建议阈值 ----
# 高者为优的指标；其余（generic_sentence_rate、human_correction_rate）低者为优。
_HIGHER_IS_BETTER = frozenset(
    {"evidence_coverage", "decision_density", "job_completion_rate", "useful_reply_rate"}
)
_HEALTHY = 0.7  # score >= 0.7 → 继续（健康）
_WEAK = 0.4     # score >= 0.4 → 调整（偏弱）；否则停止（差）

_METRIC_LABELS = {
    "evidence_coverage": "证据覆盖",
    "decision_density": "立场密度",
    "generic_sentence_rate": "套话率",
    "human_correction_rate": "人工修正率",
    "job_completion_rate": "任务完成率",
    "useful_reply_rate": "有用回复率",
    "do_not_write_rate": "不写率",
}


# ---- Phase 4 周报三问：从方向性指标派生一个洞察 + 三个下周判断（deterministic，无 LLM）----
_EVIDENCE_INSUFFICIENT = "evidence insufficient"

# (key, 中文标签, higher_is_better)；do_not_write_rate / rewrite 轮数仅信息性，不参与三问。
_DIRECTIONAL_METRICS: tuple[tuple[str, str, bool], ...] = tuple(
    (key, _METRIC_LABELS[key], key in _HIGHER_IS_BETTER)
    for key in (
        "evidence_coverage",
        "decision_density",
        "generic_sentence_rate",
        "human_correction_rate",
        "job_completion_rate",
        "useful_reply_rate",
    )
)

# 最弱指标 → 一个可验证假设（one_experiment）。假设对应计划 Phase 1–3 已识别的改进点。
_EXPERIMENT_HYPOTHESES = {
    "evidence_coverage": "验证假设：把完整 Content Job 注入首次 Writer 提示可提高证据覆盖",
    "decision_density": "验证假设：强制首稿包含 decision+tradeoff 可提高立场密度",
    "generic_sentence_rate": "验证假设：按 scope 限定表达边界可降低套话率",
    "human_correction_rate": "验证假设：审核前先展示证据置信边界可降低人工修正率",
    "job_completion_rate": "验证假设：确定性地选出一个 primary job 可提高任务完成率",
    "useful_reply_rate": "验证假设：回复更针对读者具体问题可提高有用回复率",
}

# 最弱指标 → 一个明确停止项（stop_doing）。
_STOP_ACTIONS = {
    "evidence_coverage": "停止：证据不足时不要把草稿送入审核",
    "decision_density": "停止：发布缺少 decision/tradeoff 的草稿",
    "generic_sentence_rate": "停止：使用无边界泛化的表达",
    "human_correction_rate": "停止：直接采用未经确认的事实或立场",
    "job_completion_rate": "停止：生成读者无法完成的宽泛任务",
    "useful_reply_rate": "停止：回复没有新增价值的讨论",
}

# ---- Phase 4 Task 4.3：后续能力触发门槛（仅文档参考，不实现任何新能力/新表）----
# 两周真实使用后，仅当满足下列条件才建设对应能力（计划 §4.3）：
#   Message Brief 节点：Job-aware Writer 后首稿仍常缺清晰 core/hook/proof
#   多 Content Shape：≥30 条草稿出现 ≥3 类稳定且差异明显的结构需求
#   Shape-aware Critic：统一 checker 致 ≥20% 可用内容被误拒或无意义重写
#   统一 Inbox：审核平均跨 ≥3 条命令，或用户反复进入错误队列
#   行为型 Voice Profile：≥20 次人工 revise 且 ≥3 种修改行为重复出现
#   持久化 Audience Signal：同一信号需跨 run 复用，或重复抽取成本明显
#   Content Experiment 模型：连续 4 周有稳定 outcome 数据，手工记录已成摩擦


@dataclass(frozen=True)
class _MetricSignal:
    key: str
    label: str
    higher_is_better: bool
    value: float

    @property
    def health(self) -> float:
        """健康分 0.0–1.0：把数值归一化为「越高越健康」。"""
        return self.value if self.higher_is_better else 1.0 - self.value


def _build_narrative(metrics: dict[str, float | None]) -> tuple[str, NextWeekPlan]:
    """由方向性指标派生周报三问。

    每个判断都引用具体指标名与数值；六个方向性指标全部无数据（分母不存在）时，
    统一返回 ``evidence insufficient``，不臆造结论。
    """
    signals: list[_MetricSignal] = []
    for key, label, higher in _DIRECTIONAL_METRICS:
        value = metrics.get(key)
        if value is not None:
            signals.append(_MetricSignal(key, label, higher, value))

    if not signals:
        return _EVIDENCE_INSUFFICIENT, NextWeekPlan(
            one_thing=_EVIDENCE_INSUFFICIENT,
            one_experiment=_EVIDENCE_INSUFFICIENT,
            stop_doing=_EVIDENCE_INSUFFICIENT,
        )

    strongest = max(signals, key=lambda s: s.health)
    weakest = min(signals, key=lambda s: s.health)
    most_extreme = max(signals, key=lambda s: abs(s.health - _HEALTHY))

    if most_extreme.health >= _HEALTHY:
        insight = f"{most_extreme.label} 是本周最强信号（{most_extreme.value:.0%}）"
    else:
        insight = f"{most_extreme.label} 是本周最大短板（{most_extreme.value:.0%}）"

    one_thing = f"继续强化 {strongest.label}（当前 {strongest.value:.0%}）"
    one_experiment = (
        f"{_EXPERIMENT_HYPOTHESES[weakest.key]}"
        f"（依据：{weakest.label} {weakest.value:.0%}）"
    )
    if weakest.health < _HEALTHY:
        stop_doing = (
            f"{_STOP_ACTIONS[weakest.key]}（依据：{weakest.label} {weakest.value:.0%}）"
        )
    else:
        stop_doing = "无需停止：所有方向性指标均处于健康区间"

    return insight, NextWeekPlan(
        one_thing=one_thing,
        one_experiment=one_experiment,
        stop_doing=stop_doing,
    )


def weekly_analysis(
    drafts: DraftRepository,
    reviews: ReviewRepository,
    feedbacks: FeedbackRepository,
    jobs: ContentJobRepository,
    critic_reports: CriticReportRepository,
    *,
    since: datetime | None = None,
) -> WeeklyReport:
    """汇总 `since`（含）之后的审核与反馈数据；`since` 为 None 则汇总全部。

    批准率分母是「已审核草稿」数（approved+revised+skipped），不含待审草稿，
    否则待审会被误记为「未批准」而压低批准率。
    """
    all_drafts = drafts.list_drafts()
    history = reviews.list_history()
    decisions: dict[str, ReviewDecision] = {}
    for r in reviews.list_reviews():
        if r.action == ReviewAction.CONFIRM_POSITION:
            continue  # 立场确认独立于最终 approve/skip，不参与批准率
        if since is not None and r.decided_at < since:
            continue
        decisions[r.draft_id] = r

    approved = sum(1 for r in decisions.values() if r.action == ReviewAction.APPROVE)
    skipped = sum(1 for r in decisions.values() if r.action == ReviewAction.SKIP)
    reviewed = len(decisions)
    # 「修改次数」来自追加式历史：revise 后被 approve 也不丢，能反映「频繁修改」。
    revised = sum(
        1
        for r in history
        if r.action == ReviewAction.REVISE and (since is None or r.decided_at >= since)
    )
    skip_reasons = Counter(
        r.reason or "unknown"
        for r in decisions.values()
        if r.action == ReviewAction.SKIP
    )

    # 一次批量拉取 Feedback，避免逐草稿 get_feedback 的 N+1 查询。
    all_feedbacks = feedbacks.list_feedbacks()
    window_feedbacks = [fb for fb in all_feedbacks if since is None or fb.recorded_at >= since]
    published_by_draft = {fb.draft_id: fb for fb in window_feedbacks if fb.published_url}
    outcome_by_draft = {fb.draft_id: fb for fb in window_feedbacks if fb.outcome is not None}

    published_ids: list[str] = []
    published_cands: list[str] = []
    for d in all_drafts:
        if d.id in published_by_draft:
            published_ids.append(d.id)
            if d.candidate_id:
                published_cands.append(d.candidate_id)

    # ---- Task 8 新指标：只统计非遗留草稿 ----
    eligible_ids = {d.id for d in all_drafts if d.content_job_id is not None}
    jobs_by_id = {job.id: job for job in jobs.list_jobs()}
    all_reports = critic_reports.list_all_reports(since=since)
    rewrite_rounds = _rewrite_rounds(all_reports, eligible_ids)

    evidence_coverage = _evidence_coverage(all_reports, eligible_ids)
    decision_density = _decision_density(
        all_drafts, published_by_draft, eligible_ids, jobs_by_id
    )
    generic_sentence_rate = _generic_sentence_rate(all_reports, eligible_ids)
    human_correction_rate = _human_correction_rate(
        decisions, history, eligible_ids, since
    )
    job_completion_rate = _job_completion_rate(outcome_by_draft, eligible_ids)
    useful_reply_rate = _useful_reply_rate(all_drafts, outcome_by_draft, eligible_ids)
    do_not_write_rate = _do_not_write_rate(list(jobs_by_id.values()))

    weekly_insight, next_week = _build_narrative(
        {
            "evidence_coverage": evidence_coverage,
            "decision_density": decision_density,
            "generic_sentence_rate": generic_sentence_rate,
            "human_correction_rate": human_correction_rate,
            "job_completion_rate": job_completion_rate,
            "useful_reply_rate": useful_reply_rate,
        }
    )

    return WeeklyReport(
        reviewed_drafts=reviewed,
        approved=approved,
        revised=revised,
        skipped=skipped,
        approval_rate=approved / reviewed if reviewed else 0.0,
        skip_reasons=dict(skip_reasons),
        published_draft_ids=published_ids,
        published_candidate_ids=published_cands,
        evidence_coverage=evidence_coverage,
        decision_density=decision_density,
        generic_sentence_rate=generic_sentence_rate,
        human_correction_rate=human_correction_rate,
        job_completion_rate=job_completion_rate,
        useful_reply_rate=useful_reply_rate,
        do_not_write_rate=do_not_write_rate,
        rewrite_rounds=rewrite_rounds,
        rewritten_drafts=sum(1 for n in rewrite_rounds.values() if n > 0),
        weekly_insight=weekly_insight,
        next_week=next_week,
    )


def _find_check(report: dict, name: str) -> dict | None:
    """在报告 dict 的 checks 列表里按 checker 名查找第一个匹配项。"""
    for check in report.get("checks", []):
        if check.get("checker") == name:
            return check
    return None


def _reports_for(all_reports: dict[str, list[dict]], eligible_ids: set[str]) -> list[dict]:
    """展开非遗留草稿的全部 Critic 报告（保持每草稿的 round 顺序）。"""
    return [
        report
        for draft_id, reports in all_reports.items()
        if draft_id in eligible_ids
        for report in reports
    ]


def _evidence_coverage(
    all_reports: dict[str, list[dict]], eligible_ids: set[str]
) -> float | None:
    """证据覆盖：evidence 检查通过的报告 / 全部报告。

    报告级（一个草稿每轮产生一份报告）；无证据检查的报告计入分母、不计分子。
    无报告（无分母）时返回 None，表示无数据而非 0%。
    """
    reports = _reports_for(all_reports, eligible_ids)
    if not reports:
        return None
    passed = sum(
        1
        for r in reports
        if (check := _find_check(r, "evidence")) is not None and check.get("passed") is True
    )
    return passed / len(reports)


def _decision_density(
    all_drafts: list[Draft],
    published_by_draft: dict[str, Feedback],
    eligible_ids: set[str],
    jobs_by_id: dict[str, ContentJob],
) -> float | None:
    """立场密度：已发布非遗留草稿中，绑定 job 有非空 decision 且 tradeoff 的占比。

    有 content_job_id 但 job 缺失/无立场 → 计入分母、不计分子。
    无已发布非遗留草稿（无分母）时返回 None。
    """
    published_eligible = [
        d for d in all_drafts if d.id in eligible_ids and d.id in published_by_draft
    ]
    if not published_eligible:
        return None
    with_position = 0
    for d in published_eligible:
        job = jobs_by_id.get(d.content_job_id) if d.content_job_id else None
        position = job.author_position if job is not None else None
        if position is not None and position.decision and position.tradeoff:
            with_position += 1
    return with_position / len(published_eligible)


def _generic_sentence_rate(
    all_reports: dict[str, list[dict]], eligible_ids: set[str]
) -> float | None:
    """套话率（代理）：portability 或 specificity 失败的报告 / 全部报告。

    选此定义并记录：per-sentence 计数需要草稿正文的句子总数，而 Critic 报告
    payload 只保存逐项 checks 的 locations、不含正文，故用报告级代理。
    无报告（无分母）时返回 None。
    """
    reports = _reports_for(all_reports, eligible_ids)
    if not reports:
        return None
    flagged = sum(
        1
        for r in reports
        if any(
            (check := _find_check(r, name)) is not None and check.get("passed") is False
            for name in ("portability", "specificity")
        )
    )
    return flagged / len(reports)


def _human_correction_rate(
    decisions: dict[str, ReviewDecision],
    history: list[ReviewDecision],
    eligible_ids: set[str],
    since: datetime | None,
) -> float | None:
    """人工修正率：已审非遗留草稿中，需人工改事实/立场的占比。

    修正 = 存在 REVISE 历史事件，或最终 skip 且 reason in {fact_error, no_clear_position}。
    无已审非遗留草稿（无分母）时返回 None。
    """
    reviewed_ids = {draft_id for draft_id in decisions if draft_id in eligible_ids}
    if not reviewed_ids:
        return None
    revised_ids = {
        h.draft_id
        for h in history
        if h.action == ReviewAction.REVISE
        and h.draft_id in eligible_ids
        and (since is None or h.decided_at >= since)
    }
    fact_skip_ids = {
        draft_id
        for draft_id, d in decisions.items()
        if draft_id in eligible_ids
        and d.action == ReviewAction.SKIP
        and d.reason in {SkipReason.FACT_ERROR.value, SkipReason.NO_CLEAR_POSITION.value}
    }
    return len(revised_ids | fact_skip_ids) / len(reviewed_ids)


def _job_completion_rate(
    outcome_by_draft: dict[str, Feedback], eligible_ids: set[str]
) -> float | None:
    """任务完成率：有结果评估的非遗留草稿中 job_completed in {yes, partly} 的占比。"""
    assessments = [fb for draft_id, fb in outcome_by_draft.items() if draft_id in eligible_ids]
    if not assessments:
        return None
    completed = 0
    for fb in assessments:
        outcome = fb.outcome
        if outcome is not None and outcome.job_completed in {"yes", "partly"}:
            completed += 1
    return completed / len(assessments)


def _useful_reply_rate(
    all_drafts: list[Draft],
    outcome_by_draft: dict[str, Feedback],
    eligible_ids: set[str],
) -> float | None:
    """有用回复率：已记录结果的回复（非遗留 + candidate_id 非空）中 useful_reply_count>0 的占比。"""
    reply_with_outcome = [
        d
        for d in all_drafts
        if d.id in eligible_ids and d.candidate_id is not None and d.id in outcome_by_draft
    ]
    if not reply_with_outcome:
        return None
    useful = 0
    for d in reply_with_outcome:
        outcome = outcome_by_draft[d.id].outcome
        if outcome is not None and (outcome.useful_reply_count or 0) > 0:
            useful += 1
    return useful / len(reply_with_outcome)


def _do_not_write_rate(jobs: list[ContentJob]) -> float:
    """不写率：DO_NOT_WRITE job / 全部 job。

    ContentJob 无时间戳，「时间窗内」不可判定，故统计全部 job（信息性，非失败）。
    """
    if not jobs:
        return 0.0
    return sum(1 for j in jobs if j.status == ContentJobStatus.DO_NOT_WRITE) / len(jobs)


def _rewrite_rounds(
    all_reports: dict[str, list[dict]], eligible_ids: set[str]
) -> dict[str, int]:
    """每条 eligible 草稿的 Critic rewrite 轮数：outcome == "rewrite" 的报告数。

    语义（记录以便后续读回）：critique 节点每轮跑一次检查器并持久化一份报告；
    outcome=="rewrite" 表示该轮判定需要重写并触发一次重写，pass/reject/needs_input
    为终态不重写。唯一例外是用尽 rewrite 预算仍失败的草稿：critique 节点在
    ``max_rewrite_rounds`` 处停住，最后一轮报告仍是 "rewrite"，因此计数会达到
    ``max_rewrite_rounds + 1`` —— 意为「每一轮（含最后一轮）都被判需要重写」，
    而非多算一次重写。只统计非遗留草稿，与其余 Task 8 指标一致。
    """
    return {
        draft_id: sum(1 for report in reports if report.get("outcome") == "rewrite")
        for draft_id, reports in all_reports.items()
        if draft_id in eligible_ids
    }


def _classify(metric: str, value: float) -> str:
    """把指标值映射为「继续/调整/停止」；do_not_write_rate 恒为信息性。"""
    if metric == "do_not_write_rate":
        return "信息"
    score = value if metric in _HIGHER_IS_BETTER else 1.0 - value
    if score >= _HEALTHY:
        return "继续"
    if score >= _WEAK:
        return "调整"
    return "停止"


def render_weekly(report: WeeklyReport) -> str:
    """把 WeeklyReport 渲染为 Markdown。"""
    lines = ["# Finch Weekly Review", ""]
    lines.append("## 本周洞察")
    lines.append(f"- {report.weekly_insight or _EVIDENCE_INSUFFICIENT}")
    lines.append("")
    lines.append("## 下周计划")
    lines.append(f"- {report.next_week.one_thing or _EVIDENCE_INSUFFICIENT}")
    lines.append(f"- {report.next_week.one_experiment or _EVIDENCE_INSUFFICIENT}")
    lines.append(f"- {report.next_week.stop_doing or _EVIDENCE_INSUFFICIENT}")
    lines.append("")
    lines.append(f"- 已审核草稿: {report.reviewed_drafts}")
    lines.append(f"- 批准: {report.approved} / 跳过: {report.skipped} / 修改次数: {report.revised}")
    lines.append(f"- 批准率: {report.approval_rate:.0%}")
    if report.skip_reasons:
        lines.append("- 跳过原因:")
        for reason, count in sorted(report.skip_reasons.items()):
            lines.append(f"  - {reason}: {count}")
    if report.published_draft_ids:
        lines.append(f"- 已发布草稿: {len(report.published_draft_ids)}")
        lines.append(f"- 已发布候选: {', '.join(report.published_candidate_ids)}")
    if report.rewrite_rounds:
        lines.append(f"- 需要 Critic rewrite 的草稿: {report.rewritten_drafts}")
        lines.append(f"- 各草稿 Critic rewrite 轮数: {report.rewrite_rounds}")
    lines.append("")
    lines.append("## 建议 (继续/调整/停止)")
    for metric, label in _METRIC_LABELS.items():
        value = getattr(report, metric)
        if value is None:
            lines.append(f"- {label}: 无数据")
            continue
        verdict = _classify(metric, value)
        if metric == "do_not_write_rate":
            lines.append(f"- {label}: {value:.0%} → {verdict}（不视为失败）")
        else:
            lines.append(f"- {label}: {value:.0%} → {verdict}")
    return "\n".join(lines)
