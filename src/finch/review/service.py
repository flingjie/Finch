"""Review 服务：人工审核 CLI 的 approve/revise/skip 路径（C3）。"""

import difflib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from finch.content.checkers.base import CheckResult
from finch.content.jobs import AuthorPosition, ContentJob
from finch.content.models import Draft
from finch.evidence.models import EvidenceCard
from finch.review.models import Feedback, ReviewAction, ReviewDecision, SkipReason
from finch.storage.repositories import DraftRepository, ReviewRepository
from finch.twitter.models import DiscussionCandidate


def compute_diff(before: str, after: str) -> str:
    """返回 before → after 的 unified diff 文本。"""
    return "\n".join(
        difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
    )


class ReviewService:
    def __init__(self, drafts: DraftRepository, reviews: ReviewRepository) -> None:
        self.drafts = drafts
        self.reviews = reviews

    def list_pending(self) -> list[Draft]:
        """返回尚未有最终决策（approve/revise/skip）的草稿。

        CONFIRM_POSITION 独立于最终发布批准，不计入「已审核」，故不把仅确认过
        立场的草稿从 pending 队列中剔除。
        """
        reviewed = {
            r.draft_id
            for r in self.reviews.list_reviews()
            if r.action != ReviewAction.CONFIRM_POSITION
        }
        return [d for d in self.drafts.list_drafts() if d.id not in reviewed]

    def show(self, draft_id: str) -> Draft | None:
        return self.drafts.get_draft(draft_id)

    def approve(self, draft_id: str) -> ReviewDecision:
        self._require_draft(draft_id)
        decision = ReviewDecision(
            id=f"rev_{draft_id}",
            draft_id=draft_id,
            action=ReviewAction.APPROVE,
            decided_at=datetime.now(UTC),
        )
        self.reviews.save_review(decision)
        self.reviews.append_history(decision)
        return decision

    def revise(self, draft_id: str, revised_body: str) -> ReviewDecision:
        draft = self._require_draft(draft_id)
        decision = ReviewDecision(
            id=f"rev_{draft_id}",
            draft_id=draft_id,
            action=ReviewAction.REVISE,
            revised_body=revised_body,
            diff=compute_diff(draft.body, revised_body),
            decided_at=datetime.now(UTC),
        )
        self.reviews.save_review(decision)
        self.reviews.append_history(decision)
        return decision

    def skip(self, draft_id: str, reason: SkipReason) -> ReviewDecision:
        self._require_draft(draft_id)
        decision = ReviewDecision(
            id=f"rev_{draft_id}",
            draft_id=draft_id,
            action=ReviewAction.SKIP,
            reason=reason.value,
            decided_at=datetime.now(UTC),
        )
        self.reviews.save_review(decision)
        self.reviews.append_history(decision)
        return decision

    def confirm_position(
        self,
        draft_id: str,
        *,
        voice_match: int,
        position_correct: bool | None = None,
        job_clear: bool | None = None,
    ) -> ReviewDecision:
        """记录立场确认（CONFIRM_POSITION），独立于 approve/skip 最终决策。

        使用独立 id（``confirm_<draft_id>``）而非 ``rev_<draft_id>``，
        因此不会覆盖 approve/skip 决策。
        """
        self._require_draft(draft_id)
        decision = ReviewDecision(
            id=f"confirm_{draft_id}",
            draft_id=draft_id,
            action=ReviewAction.CONFIRM_POSITION,
            voice_match=voice_match,
            position_correct=position_correct,
            job_clear=job_clear,
            decided_at=datetime.now(UTC),
        )
        self.reviews.save_review(decision)
        self.reviews.append_history(decision)
        return decision

    def _require_draft(self, draft_id: str) -> Draft:
        draft = self.drafts.get_draft(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        return draft


@dataclass(frozen=True)
class ReviewPackage:
    """一次 ``review show`` 的完整数据视图（仅渲染，不持久化）。

    各字段由 CLI 从对应仓储读取后经 :func:`build_review_package` 组装；
    candidate 原帖在当前数据模型下未持久化（DiscussionCandidate 只存在于 graph
    node output，且 Draft 无 run_id 关联），故通常为 None，渲染时降级为占位。
    """

    draft: Draft
    job: ContentJob | None = None
    evidence_cards: list[EvidenceCard] = field(default_factory=list)
    candidate: DiscussionCandidate | None = None
    critic_reports: list[dict] = field(default_factory=list)
    decision: ReviewDecision | None = None
    feedback: Feedback | None = None

    @property
    def rewrite_count(self) -> int:
        """Critic 判定需要 rewrite 的轮数（与 weekly._rewrite_rounds 同口径）。"""
        return sum(1 for r in self.critic_reports if r.get("outcome") == "rewrite")

    @property
    def final_outcome(self) -> str | None:
        """最后一轮 Critic 的聚合去向（reports 按 round 升序）。"""
        if not self.critic_reports:
            return None
        return self.critic_reports[-1].get("outcome")

    @property
    def failed_checks(self) -> list[CheckResult]:
        """最后一轮 Critic 的失败检查项（passed=False）。"""
        if not self.critic_reports:
            return []
        return [
            CheckResult.model_validate(check)
            for check in self.critic_reports[-1].get("checks", [])
            if not check.get("passed")
        ]

    @property
    def remaining_risk(self) -> str:
        """未通过 Critic 时的剩余风险；通过或无报告返回空串。"""
        if self.final_outcome == "rewrite":
            return f"critique 未通过（重写预算用尽，共 {self.rewrite_count} 轮 rewrite）"
        if self.final_outcome == "reject":
            return "被 Critic hard_fail 拒绝"
        return ""


def build_review_package(
    draft: Draft,
    *,
    job: ContentJob | None = None,
    evidence_cards: list[EvidenceCard] | None = None,
    candidate: DiscussionCandidate | None = None,
    critic_reports: list[dict] | None = None,
    decision: ReviewDecision | None = None,
    feedback: Feedback | None = None,
) -> ReviewPackage:
    """组装 review show 数据视图（纯函数；数据由 CLI 从各仓储读取）。"""
    return ReviewPackage(
        draft=draft,
        job=job,
        evidence_cards=evidence_cards or [],
        candidate=candidate,
        critic_reports=critic_reports or [],
        decision=decision,
        feedback=feedback,
    )


def _render_job(job: ContentJob | None) -> list[str]:
    if job is None:
        return ["(none)"]
    effect = job.intended_effect
    lines = [
        f"id: {job.id}",
        f"status: {job.status.value}",
        f"format: {job.recommended_format.value}",
        f"reader_problem: {job.reader_problem}",
        f"audience: {job.audience}",
        "intended_effect: "
        f"understand={effect.understand}; believe={effect.believe or ''}; "
        f"action={effect.action or ''}",
    ]
    if job.core_message:
        lines.append(f"core_message: {job.core_message}")
    if job.why_now:
        lines.append(f"why_now: {job.why_now}")
    lines.append(f"scope: {job.scope.value}")
    if job.candidate_id:
        lines.append(f"candidate_id: {job.candidate_id}")
    return lines


def _render_position(position: AuthorPosition | None) -> list[str]:
    if position is None:
        return ["(none)"]
    lines = [
        f"claim: {position.claim}",
        f"decision: {position.decision}",
        f"tradeoff: {position.tradeoff}",
    ]
    if position.change_mind_if:
        lines.append(f"change_mind_if: {position.change_mind_if}")
    lines.append(f"confirmed: {position.confirmed}")
    return lines


def _render_evidence(cards: list[EvidenceCard]) -> list[str]:
    if not cards:
        return ["(none)"]
    lines: list[str] = []
    for card in cards:
        lines.append(f"- {card.claim} [{card.confidence.value}]")
        if card.sources:
            for source in card.sources:
                lines.append(f"    {source.type}: {source.url}")
        else:
            lines.append("    (no source link)")
    return lines


def _render_candidate(draft: Draft, candidate: DiscussionCandidate | None) -> list[str]:
    if draft.candidate_id is None:
        return ["(original content — not a reply)"]
    if candidate is None:
        return [
            f"candidate_id: {draft.candidate_id}",
            "(original post not persisted — unavailable in review show)",
        ]
    return [
        f"candidate_id: {candidate.id}",
        f"author: @{candidate.author_handle}",
        f"url: {candidate.url}",
        f"text: {candidate.text}",
    ]


def _render_critic(package: ReviewPackage) -> list[str]:
    lines = [f"rewrite count: {package.rewrite_count}"]
    failed = package.failed_checks
    if not package.critic_reports:
        lines.append("failures: (no critic report)")
    elif not failed:
        lines.append("failures: none")
    else:
        lines.append("failures:")
        for check in failed:
            detail = "; ".join(check.issues) if check.issues else "(no issues)"
            lines.append(f"  - {check.checker} [severity={check.severity}] {detail}")
    lines.append(f"remaining risk: {package.remaining_risk or 'none'}")
    return lines


def next_step(package: ReviewPackage) -> str:
    """返回可复制的下一步命令（无最终决策 → approve；已 approve → feedback）。"""
    draft_id = package.draft.id
    if package.decision is None:
        return f"finch review approve {draft_id}"
    action = package.decision.action
    if action == ReviewAction.APPROVE:
        if package.feedback is None or package.feedback.published_url is None:
            return f"finch review feedback {draft_id} --url <published-url>"
        if package.feedback.outcome is None:
            return f"finch review feedback {draft_id} --outcome '<json>'"
        return "(complete — feedback recorded)"
    if action == ReviewAction.SKIP:
        return "(skipped — no next step)"
    # REVISE / CONFIRM_POSITION：仍需最终批准。
    return f"finch review approve {draft_id}"


def render_review_package(
    package: ReviewPackage,
    *,
    body_only: bool = False,
    evidence_only: bool = False,
    critic_only: bool = False,
) -> str:
    """渲染 ``review show`` 输出。

    - ``body_only``：只输出草稿正文（等价旧行为）。
    - ``evidence_only``：只输出证据卡与链接。
    - ``critic_only``：只输出 Critic 失败项、剩余风险与 rewrite 次数。
    - 均未指定：输出完整 Review Package（Content Job、作者立场、证据、candidate 原帖、
      草稿、Critic、下一步命令）。
    """
    if body_only:
        return package.draft.body
    if evidence_only:
        return "\n".join(["## Evidence", *_render_evidence(package.evidence_cards)])
    if critic_only:
        return "\n".join(["## Critic", *_render_critic(package)])

    position = package.job.author_position if package.job is not None else None
    sections = [
        "## Content Job",
        *_render_job(package.job),
        "## Author Position",
        *_render_position(position),
        "## Evidence",
        *_render_evidence(package.evidence_cards),
        "## Candidate",
        *_render_candidate(package.draft, package.candidate),
        "## Draft",
        package.draft.body,
        "## Critic",
        *_render_critic(package),
        "## Next step",
        next_step(package),
    ]
    return "\n".join(sections)
