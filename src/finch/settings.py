"""配置加载：finch.yaml + 环境变量覆盖。"""

from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class Paths(BaseModel):
    var_dir: Path = Field(default_factory=lambda: Path("var"))
    outputs_dir: Path = Field(default_factory=lambda: Path("var/outputs"))
    inbox_dir: Path = Field(default_factory=lambda: Path("var/inbox"))
    cache_dir: Path = Field(default_factory=lambda: Path("var/cache"))
    voice_profile_path: Path = Field(default_factory=lambda: Path("voice-profile.yaml"))
    local_repos_dirs: list[Path] = Field(
        default_factory=lambda: [Path.home() / "underway"]
    )

    def ensure(self) -> "Paths":
        dirs = (self.var_dir, self.outputs_dir, self.inbox_dir, self.cache_dir)
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        return self


class LLMNodeSettings(BaseModel):
    """单个 LLM 节点的模型/超时/输出上限/并发覆盖。"""

    model: str = ""
    timeout_seconds: float = 90.0
    max_output_tokens: int | None = None
    max_concurrency: int = Field(default=1, ge=1)


class LLMSettings(BaseModel):
    """OpenAI 兼容 LLM 配置（base_url + model；api_key 优先读环境变量 LLM_API_KEY）。"""

    base_url: str = ""
    model: str = ""
    api_key: str = ""
    nodes: dict[str, LLMNodeSettings] = Field(default_factory=dict)

    def for_node(self, name: str) -> LLMNodeSettings:
        """按节点名解析合并后的节点配置；未配置时回退到默认模型。"""
        node = self.nodes.get(name)
        if node is None:
            return LLMNodeSettings(model=self.model)
        return LLMNodeSettings(
            model=node.model or self.model,
            timeout_seconds=node.timeout_seconds,
            max_output_tokens=node.max_output_tokens,
            max_concurrency=node.max_concurrency,
        )


class ExtractionSettings(BaseModel):
    """commit 提取配置（批量提取 + 按 prompt 字节自适应拆批 + 全局 LLM 并发上限）。"""

    max_prompt_bytes: int = 50000
    max_groups_per_batch: int = 12
    max_concurrent_batches: int = 2
    global_max_concurrency: int = Field(default=4, ge=1)
    timeout_seconds: int = 180
    max_commits_per_group_prompt: int = Field(default=15, ge=1)
    max_group_prompt_bytes: int = Field(default=25000, ge=1)


class QualityGates(BaseModel):
    """内容质量门禁：Critic 通过阈值 + 有限重写轮数。

    ``min_quality_score`` 是 Critic 汇总分通过线；``max_rewrite_rounds`` 是
    DraftService 定向重写的上限。
    """

    min_quality_score: float = 0.75
    max_rewrite_rounds: int = 1
    llm_critique_mode: Literal["on_fail_or_gate", "always"] = "on_fail_or_gate"


class RepositoryDiscovery(BaseModel):
    enabled: bool = False
    lookback_hours: int = 24
    max_repos: int = 10


class TwitterSettings(BaseModel):
    daily_limit: int = 100
    per_query_limit: int = 20
    queries: list[dict] = Field(default_factory=list)
    high_value_authors: list[str] = Field(default_factory=list)
    blocked_authors: list[str] = Field(default_factory=list)


class ScoringWeights(BaseModel):
    """互动评分五维权重（执行计划 5 默认评分权重表）。

    ``relationship_value`` 不再是 LLM 评分维度，而由关系评分确定性计算；权重仍保留，
    以便 ``weighted_total`` 汇总时与四维 LLM 分合成总分。
    """

    relevance: float = 0.25
    novelty: float = 0.25
    discussability: float = 0.20
    practical_evidence: float = 0.20
    relationship_value: float = 0.10


class PeerValueWeights(BaseModel):
    """同行价值六维权重（连接优先改造 Phase 2）。

    默认四个正向维度等权（各 0.25），惩罚项权重 1.0：``peer_value`` 基础分落在 [0,1]，
    惩罚项在基础分上扣减后再钳制到 [0,1]。权重可配置，但汇总只在代码里算（LLM 不参与）。
    """

    topic_overlap: float = 0.25
    practical_depth: float = 0.25
    contribution_space: float = 0.25
    continuity_potential: float = 0.25
    repetition_penalty: float = 1.0
    promotion_risk: float = 1.0


class EngagementSettings(BaseModel):
    """互动轨道配置（执行计划 5 配置设计）。"""

    enabled: bool = True
    schedule: str = "every_run"
    platforms: list[str] = Field(default_factory=lambda: ["x", "reddit"])
    max_posts_scanned: int = 30
    search_concurrency: int = Field(default=4, ge=1)
    min_candidate_score: float = 0.72
    max_bookmarks: int = 5
    max_reply_drafts: int = 3
    max_public_replies: int = 2
    per_author_daily_limit: int = 1
    max_peers_per_run: int = 5
    max_posts_per_peer: int = 3
    public_expression_requires_approval: bool = True
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    peer_value_weights: PeerValueWeights = Field(default_factory=PeerValueWeights)


class InterestsSettings(BaseModel):
    """兴趣主题配置（稳定 / 探索 / 排除）。"""

    stable: list[str] = Field(default_factory=list)
    exploring: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    repositories: list[str] = Field(default_factory=list)
    repository_discovery: RepositoryDiscovery = Field(default_factory=RepositoryDiscovery)
    twitter: TwitterSettings = Field(default_factory=TwitterSettings)
    quality_gates: QualityGates = Field(default_factory=QualityGates)
    paths: Paths = Field(default_factory=Paths)
    engagement: EngagementSettings = Field(default_factory=EngagementSettings)
    interests: InterestsSettings = Field(default_factory=InterestsSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings)


def load_settings(path: Path | None = None) -> Settings:
    """从 finch.yaml 读取配置；缺省时使用内置默认值。"""
    load_dotenv()
    target = path if path is not None else Path("finch.yaml")
    data: dict = {}
    if target.exists():
        data = yaml.safe_load(target.read_text()) or {}
    settings = Settings(**data)
    settings.paths.ensure()
    return settings
