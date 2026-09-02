"""周复盘分析：从草稿 / 审核 / 反馈记录汇总批准率、跳过原因与已发布候选。"""

from collections import Counter

from pydantic import BaseModel, Field

from finch.review.models import ReviewAction
from finch.storage.repositories import DraftRepository, FeedbackRepository, ReviewRepository


class WeeklyReport(BaseModel):
    total_drafts: int = 0
    approved: int = 0
    revised: int = 0
    skipped: int = 0
    approval_rate: float = 0.0
    skip_reasons: dict[str, int] = Field(default_factory=dict)
    published_draft_ids: list[str] = Field(default_factory=list)
    published_candidate_ids: list[str] = Field(default_factory=list)


def weekly_analysis(
    drafts: DraftRepository,
    reviews: ReviewRepository,
    feedbacks: FeedbackRepository,
) -> WeeklyReport:
    """汇总本周（当前库中全部）草稿的审核与反馈数据。"""
    all_drafts = drafts.list_drafts()
    # ReviewRepository 按 draft_id 幂等（id="rev_<draft_id>"），list_reviews 每稿一条最终决策。
    decisions = {r.draft_id: r for r in reviews.list_reviews()}

    approved = sum(1 for r in decisions.values() if r.action == ReviewAction.APPROVE)
    revised = sum(1 for r in decisions.values() if r.action == ReviewAction.REVISE)
    skipped = sum(1 for r in decisions.values() if r.action == ReviewAction.SKIP)
    skip_reasons = Counter(
        r.reason or "other"
        for r in decisions.values()
        if r.action == ReviewAction.SKIP
    )

    published_ids: list[str] = []
    published_cands: list[str] = []
    for d in all_drafts:
        fb = feedbacks.get_feedback(d.id)
        if fb is not None and fb.published_url:
            published_ids.append(d.id)
            if d.candidate_id:
                published_cands.append(d.candidate_id)

    return WeeklyReport(
        total_drafts=len(all_drafts),
        approved=approved,
        revised=revised,
        skipped=skipped,
        approval_rate=approved / len(all_drafts) if all_drafts else 0.0,
        skip_reasons=dict(skip_reasons),
        published_draft_ids=published_ids,
        published_candidate_ids=published_cands,
    )


def render_weekly(report: WeeklyReport) -> str:
    """把 WeeklyReport 渲染为 Markdown。"""
    lines = ["# Finch Weekly Review", ""]
    lines.append(f"- 草稿总数: {report.total_drafts}")
    lines.append(f"- 批准: {report.approved} / 修改: {report.revised} / 跳过: {report.skipped}")
    lines.append(f"- 批准率: {report.approval_rate:.0%}")
    if report.skip_reasons:
        lines.append("- 跳过原因:")
        for reason, count in sorted(report.skip_reasons.items()):
            lines.append(f"  - {reason}: {count}")
    if report.published_draft_ids:
        lines.append(f"- 已发布草稿: {len(report.published_draft_ids)}")
        lines.append(f"- 产生对话的候选: {', '.join(report.published_candidate_ids)}")
    return "\n".join(lines)
