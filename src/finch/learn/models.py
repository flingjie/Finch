"""learn 数据模型（发布后学习：反馈 + 结果评估）。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class OutcomeAssessment(BaseModel):
    """发布后的结果评估：任务是否完成 + 可选的阅读/行动/回复/点击计数。

    各计数均为 None 表示「未记录」，而非 0；`job_completed` 为必填枚举。
    """

    job_completed: Literal["yes", "partly", "no", "unknown"]
    reader_understood: bool | None = None
    desired_action_count: int | None = None
    useful_reply_count: int | None = None
    github_clicks: int | None = None
    notes: str | None = None


class Feedback(BaseModel):
    draft_id: str
    published_url: str | None = None
    interaction_metrics: dict = Field(default_factory=dict)
    recorded_at: datetime
    outcome: OutcomeAssessment | None = None
    learning: str | None = None  # 自由文本：这次实际学到了什么
