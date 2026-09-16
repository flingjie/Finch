"""配置加载：finch.yaml + 环境变量覆盖。"""

from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator


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


class OpenCliSettings(BaseModel):
    """OpenCLI 网关配置（跨平台只读采集）。"""

    profile: str | None = None
    format: str = "json"
    command_timeout_seconds: int = 60
    browser_connect_timeout_seconds: int = 45
    max_parallel_local: int = 3
    max_parallel_browser: int = 1
    allow_write_commands: bool = False


class SourceDiscoverySettings(BaseModel):
    """每个采集源：是否启用、模式、抓取上限与推荐配额（显式可见，不再隐式回退）。"""

    enabled: bool = False
    mode: Literal["query", "hot", "url", "disabled"] = "disabled"
    fetch_limit: int = 50
    recommendation_cap: int = 20

    @model_validator(mode="before")
    @classmethod
    def _legacy_infer(cls, data: Any) -> Any:
        """兼容旧 YAML：只有 queries/urls/users 而无 enabled/mode 时，推断为启用。"""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        has_queries = bool(out.get("queries"))
        has_urls = bool(out.get("urls"))
        has_users = bool(out.get("users"))
        has_payload = has_queries or has_urls or has_users
        if "mode" not in out:
            if has_users or has_queries:
                out["mode"] = "query"
            elif has_urls:
                out["mode"] = "url"
        if "enabled" not in out:
            out["enabled"] = has_payload
        return out


class SourceTwitterPlan(SourceDiscoverySettings):
    queries: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class SourceRedditPlan(SourceDiscoverySettings):
    queries: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class SourceGithubPlan(SourceDiscoverySettings):
    """GitHub 查询是登录名，不是主题词。"""

    users: list[str] = Field(default_factory=list)


class SourceV2exPlan(SourceDiscoverySettings):
    queries: list[str] = Field(default_factory=list)


class SourceWeixinPlan(SourceDiscoverySettings):
    queries: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class SourceXiaohongshuPlan(SourceDiscoverySettings):
    queries: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class SourcesSettings(BaseModel):
    """跨平台采集的分源查询（``connect daily`` / ``sources sync --all``）。"""

    twitter: SourceTwitterPlan = Field(default_factory=SourceTwitterPlan)
    reddit: SourceRedditPlan = Field(default_factory=SourceRedditPlan)
    github: SourceGithubPlan = Field(default_factory=SourceGithubPlan)
    v2ex: SourceV2exPlan = Field(default_factory=SourceV2exPlan)
    weixin: SourceWeixinPlan = Field(default_factory=SourceWeixinPlan)
    xiaohongshu: SourceXiaohongshuPlan = Field(default_factory=SourceXiaohongshuPlan)


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
    """互动轨道配置（分层预算：轻量发现 → 语义筛选 → 展示 → 深度准备）。

    建议初始预算（未实测性能承诺，见 Build-in-Public 计划 §6）：
    - 每次最多取 ``max_posts_scanned`` 条新候选（默认 30）；
    - 最多深评 ``max_deep_assess`` 条（默认 5；语义作者上限仍用 max_semantic_authors）；
    - 展示最多 ``max_display_interactions`` 条互动机会 + ``max_display_follow_ups`` 条跟进。
    """

    enabled: bool = True
    schedule: str = "every_run"
    platforms: list[str] = Field(default_factory=lambda: ["x", "reddit"])
    max_posts_scanned: int = 30
    search_concurrency: int = Field(default=4, ge=1)
    min_candidate_score: float = 0.72
    max_bookmarks: int = 5
    max_reply_drafts: int = 10
    max_public_replies: int = 2
    per_author_daily_limit: int = 1
    # Layered budgets (problem-led connection A).
    max_discovery_authors: int = 50
    max_semantic_authors: int = 20
    max_display_opportunities: int = 10
    # Build-in-Public display budgets (config; not yet measured).
    max_deep_assess: int = 5
    max_display_interactions: int = 3
    max_display_follow_ups: int = 2
    # Legacy alias for max_semantic_authors (kept for old YAML / callers).
    max_peers_per_run: int = 20
    max_posts_per_peer: int = 3
    snapshot_ttl_hours: int = 24
    public_expression_requires_approval: bool = True
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    peer_value_weights: PeerValueWeights = Field(default_factory=PeerValueWeights)

    @model_validator(mode="after")
    def _sync_semantic_cap(self) -> "EngagementSettings":
        # If only the legacy field was set in older configs, prefer it when the
        # new field still has its default and legacy differs.
        if self.max_peers_per_run != 20 and self.max_semantic_authors == 20:
            self.max_semantic_authors = self.max_peers_per_run
        else:
            self.max_peers_per_run = self.max_semantic_authors
        return self


class InterestsSettings(BaseModel):
    """兴趣上下文：长期兴趣、当前问题、探索方向、排除项（单一真相源）。

    兼容旧 YAML 键 ``stable`` / ``exploring`` / ``excluded``。
    """

    long_term_interests: list[str] = Field(default_factory=list)
    current_questions: list[str] = Field(default_factory=list)
    explore_directions: list[str] = Field(default_factory=list)
    excluded_content: list[str] = Field(default_factory=list)
    adjacent_queries: list[str] = Field(default_factory=list)
    usage_queries: list[str] = Field(default_factory=list)
    practice_refs: list[str] = Field(default_factory=list)
    time_budget_minutes: int = Field(default=20, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _legacy_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if "long_term_interests" not in out and "stable" in out:
            out["long_term_interests"] = out.pop("stable")
        elif "stable" in out:
            out.pop("stable")
        if "explore_directions" not in out and "exploring" in out:
            out["explore_directions"] = out.pop("exploring")
        elif "exploring" in out:
            out.pop("exploring")
        if "excluded_content" not in out and "excluded" in out:
            out["excluded_content"] = out.pop("excluded")
        elif "excluded" in out:
            out.pop("excluded")
        return out

    # ---- read-compat properties for call sites still using old names ----
    @property
    def stable(self) -> list[str]:
        return self.long_term_interests

    @property
    def exploring(self) -> list[str]:
        return self.explore_directions

    @property
    def excluded(self) -> list[str]:
        return self.excluded_content


class DailyPeopleSettings(BaseModel):
    """每日 50 人分层推荐预算：5 重点 / 15 摘要 / 30 浏览。

    LLM 预算固定：``semantic_assess_limit`` 人进入语义评估（批量），
    ``deep_prepare_limit`` 人进入连接分析；候选池从 100 增至 1000 时上限不变。
    """

    target: int = 50
    priority_count: int = 5
    summary_count: int = 15
    browse_count: int = 30
    candidate_pool_size: int = 100
    semantic_assess_limit: int = 20
    deep_prepare_limit: int = 5
    max_per_platform: int = 20
    min_chinese_platforms_total: int = 15
    min_artifacts_priority: int = 2
    cooldown_days: int = 7


class DiscoverySettings(BaseModel):
    """发现轨道配置（每日 50 人）。"""

    daily_people: DailyPeopleSettings = Field(default_factory=DailyPeopleSettings)


class Settings(BaseModel):
    repositories: list[str] = Field(default_factory=list)
    repository_discovery: RepositoryDiscovery = Field(default_factory=RepositoryDiscovery)
    twitter: TwitterSettings = Field(default_factory=TwitterSettings)
    opencli: OpenCliSettings = Field(default_factory=OpenCliSettings)
    sources: SourcesSettings = Field(default_factory=SourcesSettings)
    quality_gates: QualityGates = Field(default_factory=QualityGates)
    paths: Paths = Field(default_factory=Paths)
    engagement: EngagementSettings = Field(default_factory=EngagementSettings)
    interests: InterestsSettings = Field(default_factory=InterestsSettings)
    discovery: DiscoverySettings = Field(default_factory=DiscoverySettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings)


def _warn_legacy_sources(data: dict) -> None:
    """输出旧 YAML 迁移提示：``sources.*`` 现在需要显式 enabled/mode。"""
    sources_raw = data.get("sources")
    sources = sources_raw if isinstance(sources_raw, dict) else {}
    legacy = [
        name
        for name, plan in sources.items()
        if isinstance(plan, dict)
        and ("enabled" not in plan or "mode" not in plan)
        and (plan.get("queries") or plan.get("urls") or plan.get("users"))
    ]
    if legacy:
        import sys

        joined = ", ".join(sorted(legacy))
        print(
            f"[finch] 迁移提示：sources.{joined} 未显式配置 enabled/mode，"
            "已按旧 YAML 自动推断；请显式写入 enabled/mode 以消除歧义。",
            file=sys.stderr,
        )


def load_settings(path: Path | None = None) -> Settings:
    """从 finch.yaml 读取配置；缺省时使用内置默认值。"""
    load_dotenv()
    target = path if path is not None else Path("finch.yaml")
    data: dict = {}
    if target.exists():
        data = yaml.safe_load(target.read_text()) or {}
    _warn_legacy_sources(data)
    settings = Settings(**data)
    settings.paths.ensure()
    return settings
