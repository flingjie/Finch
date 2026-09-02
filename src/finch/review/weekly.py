"""周复盘分析：从草稿 / 审核 / 反馈记录汇总批准率、跳过原因与已发布候选。"""

from collections import Counter
from datetime import datetime

from pydantic import BaseModel, Field

from finch.review.models import ReviewAction, ReviewDecision
from finch.storage.repositories import DraftRepository, FeedbackRepository, ReviewRepository


class WeeklyReport(BaseModel):
    reviewed_drafts: int = 0  # 时间窗内已作出最终决策的草稿数
    approved: int = 0         # 最终决策为 approve 的草稿数
    revised: int = 0          # 修改次数（REVISE 历史事件数，含后被 approve 覆盖的）
    skipped: int = 0          # 最终决策为 skip 的草稿数
    approval_rate: float = 0.0  # approved / reviewed_drafts（无决策则 0.0）
    skip_reasons: dict[str, int] = Field(default_factory=dict)
    published_draft_ids: list[str] = Field(default_factory=list)
    published_candidate_ids: list[str] = Field(default_factory=list)


def weekly_analysis(
    drafts: DraftRepository,
    reviews: ReviewRepository,
    feedbacks: FeedbackRepository,
    *,
    since: datetime | None = None,
) -> WeeklyReport:
    """汇总 `since`（含）之后的审核与反馈数据；`since` 为 None 则汇总全部。

    批准率分母是「已审核草稿」数（approved+revised+skipped），不含待审草稿，
    否则待审会被误记为「未批准」而压低批准率。
    """
    all_drafts = drafts.list_drafts()
    decisions: dict[str, ReviewDecision] = {}
    for r in reviews.list_reviews():
        if since is not None and r.decided_at < since:
            continue
        decisions[r.draft_id] = r

    approved = sum(1 for r in decisions.values() if r.action == ReviewAction.APPROVE)
    skipped = sum(1 for r in decisions.values() if r.action == ReviewAction.SKIP)
    reviewed = len(decisions)
    # 「修改次数」来自追加式历史：revise 后被 approve 也不丢，能反映「频繁修改」。
    revised = sum(
        1
        for r in reviews.list_history()
        if r.action == ReviewAction.REVISE and (since is None or r.decided_at >= since)
    )
    skip_reasons = Counter(
        r.reason or "unknown"
        for r in decisions.values()
        if r.action == ReviewAction.SKIP
    )

    published_ids: list[str] = []
    published_cands: list[str] = []
    for d in all_drafts:
        fb = feedbacks.get_feedback(d.id)
        if fb is not None and fb.published_url and (since is None or fb.recorded_at >= since):
            published_ids.append(d.id)
            if d.candidate_id:
                published_cands.append(d.candidate_id)

    return WeeklyReport(
        reviewed_drafts=reviewed,
        approved=approved,
        revised=revised,
        skipped=skipped,
        approval_rate=approved / reviewed if reviewed else 0.0,
        skip_reasons=dict(skip_reasons),
        published_draft_ids=published_ids,
        published_candidate_ids=published_cands,
    )


def render_weekly(report: WeeklyReport) -> str:
    """把 WeeklyReport 渲染为 Markdown。"""
    lines = ["# Finch Weekly Review", ""]
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
    return "\n".join(lines)
