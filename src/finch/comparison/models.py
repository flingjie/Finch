"""选题比较数据模型（finch ideas compare）。

比较记录是选题的准备过程，不是正式内容任务；确认选题后经
``IdeaService.create_candidate`` 复用现有内容任务路径（见 service.materialize）。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finch.ideas.models import SourceRef

Readiness = Literal["ready", "needs_material", "needs_validation", "undecided"]
Decision = Literal["selected", "parked", "discarded"]


class TopicDirection(BaseModel):
    """一张可比较方向卡（文档要求的 5 字段，合计 ≤600 汉字）。

    ``angle_id`` 由代码按序号填（``d1``/``d2``/``d3``），不信任模型输出。
    """

    model_config = ConfigDict(extra="forbid")

    angle_id: str = ""                              # 代码填
    kind: str = ""                                  # 方向类型
    question: str = Field(min_length=1)             # 核心问题
    reader_value: str = Field(min_length=1)         # 读者所得
    available_materials: str = ""                   # 已有材料；无则「暂无」
    material_gaps: str = ""                         # 材料缺口
    tradeoff: str = ""                              # 取舍：价值 + 主要限制


class ValidationAction(BaseModel):
    """最小验证行动：低成本检验关键假设（不做完整实验）。"""

    model_config = ConfigDict(extra="forbid")

    question: str            # 要回答的问题
    action: str              # 具体动作
    observation: str         # 观察记录（记什么）
    when: str                # 何时完成
    cannot_conclude: str     # 不能据此推断什么


class ReadinessCheck(BaseModel):
    """材料检查输出（LLM 判断；程序只校验字段与状态迁移）。"""

    model_config = ConfigDict(extra="forbid")

    readiness: Readiness
    reason: str
    clarifying_question: str = ""                  # needs_material 时
    validation: ValidationAction | None = None     # needs_validation 时


class ComparisonSelection(BaseModel):
    """用户侧取舍：只存用户原话与明确选择，与 AI 推荐/推断分离。"""

    selected_angle_ids: list[str] = Field(default_factory=list)
    final_question: str = ""                       # 合并时收敛为单一核心问题
    user_reason: str = ""                          # 用户一句话理由（可空）
    rejection_reasons: list[str] = Field(default_factory=list)
    decision: Decision = "selected"


class ComparisonRecommendation(BaseModel):
    """AI 侧「帮我选」建议：与用户选择分离存储。"""

    model_config = ConfigDict(extra="forbid")

    angle_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class ComparisonOutput(BaseModel):
    """LLM 结构化输出：比较方向的判断字段（确定性字段由代码覆盖）。"""

    model_config = ConfigDict(extra="forbid")

    directions: list[TopicDirection] = Field(default_factory=list)
    note: str = ""                                 # 信息不足返回 <3 个方向时的解释


class ComparisonRecord(BaseModel):
    """一次选题比较的落盘记录。

    旧记录缺新字段仍可读：新增字段全部带默认值，且不加 ``extra="forbid"``。
    """

    id: str                                        # comparison_id（确定性）
    source_type: Literal["text", "file", "url"] = "text"
    source_ref: str | None = None
    content_hash: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)
    created_at: datetime
    generation_note: str = ""
    angles: list[TopicDirection] = Field(default_factory=list)
    recommendation: ComparisonRecommendation | None = None
    selection: ComparisonSelection | None = None
    readiness: Readiness = "undecided"
    readiness_reason: str = ""
    next_action: str = ""
    content_job_id: str | None = None
