"""Review 服务：人工审核 CLI 的 approve/revise/skip 路径（C3）。"""

import difflib
from datetime import UTC, datetime

from finch.content.models import Draft
from finch.review.models import ReviewAction, ReviewDecision, SkipReason
from finch.storage.repositories import DraftRepository, ReviewRepository


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
        """返回尚未有 ReviewRecord 的草稿（已审核的 draft_id 过滤掉）。"""
        reviewed = {r.draft_id for r in self.reviews.list_reviews()}
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
