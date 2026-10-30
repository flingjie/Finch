"""产品发现领域模型：Product / Founder / Solution。

与旧 ``peers`` / ``opportunities`` 领域隔离：不 import 其模型，只共享原始工件与存储层。
``score_total`` / ``score_dimensions`` 只由 Python 确定性计算，LLM 永不写总分
（遵守确定性 totals 铁律）。
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from finch.sources.models import Source

Platform = Literal["x", "reddit", "github", "v2ex", "weixin", "xiaohongshu", "web"]


class EvidenceStatus(StrEnum):
    """证据分层：来源明确说出 / 由材料推导 / 未经验证的假设。"""

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    ASSUMPTION = "assumption"


class FeedbackType(StrEnum):
    """一条用户反馈的类型（证据，不是投票）。"""

    PRAISE = "praise"
    COMPLAINT = "complaint"
    REQUEST = "request"
    COMPARISON = "comparison"
    QUESTION = "question"


class ExperienceLevel(StrEnum):
    """使用经历层级：第一手 / 间接转述 / 未知。"""

    FIRST_HAND = "first_hand"
    SECOND_HAND = "second_hand"
    UNKNOWN = "unknown"


class FounderHandle(BaseModel):
    """创业者在某个平台上的身份。"""

    platform: Platform
    external_id: str = ""
    handle: str = ""
    url: str | None = None


class Founder(BaseModel):
    """构建该产品的创业者（轻量身份；连接关系管理留到后续轮）。"""

    founder_id: str
    display_name: str = ""
    handles: list[FounderHandle] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProductEvidence(BaseModel):
    """一条支撑产品的证据（来源工件或仓库分享链接）。"""

    source_ref: str  # artifact_id 或仓库分享 URL
    url: str = ""
    quote: str = ""
    claim: str = ""
    status: EvidenceStatus = EvidenceStatus.INFERRED


class ProductStatus(StrEnum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    ARCHIVED = "archived"


class Product(BaseModel):
    """一个待关注的产品（发现主对象）。"""

    id: str  # prod_<sha256[:12]>，由 key 决定
    key: str  # 规范化产品身份（如 github.com/owner/repo）
    name: str = ""
    tagline: str = ""
    product_url: str = ""
    founder: Founder | None = None
    target_user: str = ""
    problem_solved: str = ""
    core_solution: str = ""
    differentiation: str = ""
    progress: str = ""
    why_worth_attention: str = ""
    evidence: list[ProductEvidence] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    score_total: float = 0.0
    score_dimensions: dict[str, float] = Field(default_factory=dict)
    status: ProductStatus = ProductStatus.CANDIDATE
    card_version: str = ""
    fingerprint: str = ""
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SolutionVerdict(StrEnum):
    """选中产品后的下一步判断（8 点理解之八）。"""

    WORTH_TRIAL = "worth_trial"
    WORTH_OUTREACH = "worth_outreach"
    WATCH = "watch"
    SKIP = "skip"


class SolutionEvidence(BaseModel):
    """一条价值判断的证据/假设（区分显式证据与未验证假设）。

    ``origin`` 标记证据来源：founder（创业者主张，引用产品 source_ref）/ feedback
    （用户反馈，引用 feedback_id）/ inference（模型推断，无引用）。
    """

    claim: str
    source_refs: list[str] = Field(default_factory=list)
    feedback_refs: list[str] = Field(default_factory=list)
    origin: Literal["founder", "feedback", "inference"] = "founder"
    status: EvidenceStatus = EvidenceStatus.ASSUMPTION


class FeedbackOpportunityKind(StrEnum):
    """反馈揭示的机会类型。"""

    PAIN_POINT = "pain_point"
    UNMET_NEED = "unmet_need"
    CONTRADICTION = "contradiction"
    HYPOTHESIS = "hypothesis"
    FOUNDER_QUESTION = "founder_question"


class FeedbackOpportunity(BaseModel):
    """Solution 中由用户反馈揭示的机会/待验证假设（可追溯到 feedback_id）。"""

    kind: FeedbackOpportunityKind
    claim: str
    feedback_refs: list[str] = Field(default_factory=list)
    status: EvidenceStatus = EvidenceStatus.ASSUMPTION


class Solution(BaseModel):
    """选中一个产品后的 8 点理解（Solution Explorer 输出）。"""

    id: str  # sol_<sha256[:12]>
    product_id: str
    version: str = "product-solution-v1"
    who_problem: str = ""  # (1) 为谁解决什么问题
    prior_approach: str = ""  # (2) 原来如何解决
    new_solution: str = ""  # (3) 采用什么新方案
    why_more_valuable: str = ""  # (4) 为什么可能更有价值
    value_evidence: list[SolutionEvidence] = Field(default_factory=list)  # (5) 证据 vs 假设
    design_thinking: str = ""  # (6) 值得学习的产品设计思路
    builders: str = ""  # (7) 谁在构建
    verdict: SolutionVerdict = SolutionVerdict.WATCH  # (8) 是否值得深入试用/交流
    feedback_opportunities: list[FeedbackOpportunity] = Field(
        default_factory=list
    )  # 用户反馈揭示的机会
    feedback_report_id: str = ""  # 该 Solution 基于哪份反馈报告（失效审计）
    sources: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Feedback(BaseModel):
    """一条结构化用户反馈（研究主对象）。摘录与链接必须可追溯到候选讨论。"""

    feedback_id: str  # fb_<sha256[:12]>，由 product/source/url/excerpt 内容派生
    product_id: str
    source: Source
    source_url: str = ""
    author: str = ""
    published_at: datetime | None = None
    feedback_type: FeedbackType = FeedbackType.QUESTION
    user_scenario: str = ""  # 使用场景
    original_excerpt: str = ""  # 逐字原文摘录
    user_problem: str = ""  # 用户遇到的问题
    requested_outcome: str = ""  # 期望结果 / 功能请求
    experience_level: ExperienceLevel = ExperienceLevel.UNKNOWN
    evidence_status: EvidenceStatus = EvidenceStatus.ASSUMPTION
    confidence: Literal["low", "medium", "high"] = "medium"


class FeedbackPoint(BaseModel):
    """反馈报告里的一条可追溯结论（LLM 提语义，代码算 count + 校验 refs）。"""

    claim: str
    feedback_refs: list[str] = Field(default_factory=list)
    count: int = 0  # 去重后的独立反馈数（代码算，LLM 不写）
    status: EvidenceStatus = EvidenceStatus.ASSUMPTION


class FeedbackReport(BaseModel):
    """一个产品的用户反馈研究报告（综合 + 对照前的输入）。"""

    report_id: str  # fbr_<sha256[:12]>，由 product/version/feedback_ids 内容派生
    product_id: str
    version: str = "product-feedback-v1"
    feedback: list[Feedback] = Field(default_factory=list)
    recurring_problems: list[FeedbackPoint] = Field(default_factory=list)
    unmet_needs: list[FeedbackPoint] = Field(default_factory=list)
    contradictions: list[FeedbackPoint] = Field(default_factory=list)
    questions_for_founder: list[FeedbackPoint] = Field(default_factory=list)
    info_gaps: str = ""
    insufficient_feedback: bool = False
    search_fingerprint: str = ""  # 查询/来源/配置指纹（缓存 TTL 用，非失效键）
    sources: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProductSnapshot(BaseModel):
    """一次产品发现运行的持久化投影。"""

    id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    plan_id: str = ""
    context_fingerprint: str = ""
    product_ids: list[str] = Field(default_factory=list)  # 有序每日列表
    shortfall: dict[str, int] = Field(default_factory=dict)
    source_coverage: dict[str, object] = Field(default_factory=dict)
    source_states: dict[str, str] = Field(default_factory=dict)
    card_fill_success: int = 0
    card_fill_failed: int = 0
    elapsed_seconds: float = 0.0
