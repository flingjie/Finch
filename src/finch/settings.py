"""配置加载：finch.yaml + 环境变量覆盖。"""

from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class Paths(BaseModel):
    var_dir: Path = Field(default_factory=lambda: Path("var"))
    db_path: Path = Field(default_factory=lambda: Path("var/finch.db"))
    outputs_dir: Path = Field(default_factory=lambda: Path("var/outputs"))
    inbox_dir: Path = Field(default_factory=lambda: Path("var/inbox"))
    cache_dir: Path = Field(default_factory=lambda: Path("var/cache"))
    voice_profile_path: Path = Field(default_factory=lambda: Path("voice-profile.yaml"))
    local_repos_dirs: list[Path] = Field(
        default_factory=lambda: [Path.home() / "underway"]
    )

    def ensure(self) -> "Paths":
        dirs = (self.var_dir, self.outputs_dir, self.inbox_dir, self.cache_dir, self.db_path.parent)
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        return self


class LLMSettings(BaseModel):
    """OpenAI 兼容 LLM 配置（base_url + model；api_key 优先读环境变量 LLM_API_KEY）。"""

    base_url: str = ""
    model: str = ""
    api_key: str = ""


class QualityGates(BaseModel):
    max_daily_replies: int = 5
    max_daily_original_posts: int = 1
    min_candidate_score: float = 0.65
    min_evidence_score: float = 0.75
    min_quality_score: float = 0.75
    min_discussability: float = 0.50
    max_rewrite_rounds: int = 2
    match_top_k: int = 10
    timing_default: float = 0.3


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
    """互动评分五维权重（执行计划 5 默认评分权重表）。"""

    relevance: float = 0.25
    novelty: float = 0.25
    discussability: float = 0.20
    practical_evidence: float = 0.20
    relationship_value: float = 0.10


class EngagementSettings(BaseModel):
    """互动轨道配置（执行计划 5 配置设计）。"""

    enabled: bool = True
    schedule: str = "every_run"
    platforms: list[str] = Field(default_factory=lambda: ["x", "reddit"])
    max_posts_scanned: int = 30
    min_candidate_score: float = 0.72
    max_bookmarks: int = 5
    max_reply_drafts: int = 3
    max_public_replies: int = 2
    per_author_daily_limit: int = 1
    public_expression_requires_approval: bool = True
    weights: ScoringWeights = Field(default_factory=ScoringWeights)


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
