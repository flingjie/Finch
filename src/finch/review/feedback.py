"""Feedback 服务：发布链接与互动数据的手动登记（C3）。"""

from datetime import UTC, datetime

from finch.review.models import Feedback
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
    ) -> Feedback:
        feedback = Feedback(
            draft_id=draft_id,
            published_url=published_url,
            interaction_metrics=metrics or {},
            recorded_at=datetime.now(UTC),
        )
        self.feedbacks.save_feedback(feedback)
        return feedback
