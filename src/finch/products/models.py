"""产品发现领域模型：Product / Founder / Solution。

与旧 ``peers`` / ``opportunities`` 领域隔离：不 import 其模型，只共享原始工件与存储层。
``score_total`` / ``score_dimensions`` 只由 Python 确定性计算，LLM 永不写总分
（遵守确定性 totals 铁律）。
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

Platform = Literal["x", "reddit", "github", "v2ex", "weixin", "xiaohongshu", "web"]


class EvidenceStatus(StrEnum):
    """证据分层：来源明确说出 / 由材料推导 / 未经验证的假设。"""

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    ASSUMPTION = "assumption"


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
    """一条价值判断的证据/假设（区分显式证据与未验证假设）。"""

    claim: str
    source_refs: list[str] = Field(default_factory=list)
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
