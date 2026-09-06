"""idea 数据模型（finch idea 轻量入口）。"""

from typing import Literal

from pydantic import BaseModel, Field


class IdeaAssessment(BaseModel):
    """公开 --json 输出：判断结论 +（ready 时）样稿与 draft_id。"""

    status: Literal["ready", "not_ready"]
    reason_code: str | None = None
    reason: str = ""
    core_point: str | None = None
    matched_evidence_ids: list[str] = Field(default_factory=list)
    duplicate_post_url: str | None = None
    draft_id: str | None = None
    sample: str | None = None


class AssessIdeaOutput(IdeaAssessment):
    """assess-idea 调用①内部输出：ready 时携带构建 ContentJob 的语境。"""

    reader_problem: str | None = None
    audience: str | None = None
    understand: str | None = None
    believe: str | None = None
    action: str | None = None
    claim: str | None = None
    decision: str | None = None
    tradeoff: str | None = None
    change_mind_if: str | None = None


class WriteIdeaOutput(BaseModel):
    """write-idea 调用②内部输出：样稿正文。"""

    body: str


class RewriteIdeaOutput(BaseModel):
    """rewrite 内部输出：只回传正文（idea 草稿 claims 恒为空）。"""

    body: str
