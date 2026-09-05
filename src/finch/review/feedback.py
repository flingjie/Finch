"""Feedback 服务：发布链接与互动数据的手动登记（C3）。"""

from datetime import UTC, datetime

from finch.review.models import Feedback, OutcomeAssessment
from finch.storage.repositories import FeedbackRepository


class FeedbackService:
    def __init__(self, feedbacks: FeedbackRepository) -> None:
        self.feedbacks = feedbacks

    def record(
        self,
        draft_id: str,
        *,
        published_url: str | None = None,
        metrics: dict | None = None,
        outcome: OutcomeAssessment | None = None,
        learning: str | None = None,
    ) -> Feedback:
        # 读-改-写：后续 --outcome 不应覆盖先前登记的 url / metrics / learning。
        existing = self.feedbacks.get_feedback(draft_id)
        if existing is not None:
            feedback = Feedback(
                draft_id=draft_id,
                published_url=(
                    existing.published_url if published_url is None else published_url
                ),
                interaction_metrics={
                    **existing.interaction_metrics,
                    **(metrics or {}),
                },
                # 保留首次登记的 recorded_at：后续 --outcome/--metrics 不应把旧发布
                # 挪进当前周窗口（F4）。
                recorded_at=existing.recorded_at,
                outcome=existing.outcome if outcome is None else outcome,
                learning=existing.learning if learning is None else learning,
            )
        else:
            feedback = Feedback(
                draft_id=draft_id,
                published_url=published_url,
                interaction_metrics=metrics or {},
                recorded_at=datetime.now(UTC),
                outcome=outcome,
                learning=learning,
            )
        self.feedbacks.save_feedback(feedback)
        return feedback
