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

    def ensure(self) -> "Paths":
        dirs = (self.var_dir, self.outputs_dir, self.inbox_dir, self.cache_dir, self.db_path.parent)
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        return self


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


class TwitterSettings(BaseModel):
    daily_limit: int = 100
    per_query_limit: int = 20
    queries: list[dict] = Field(default_factory=list)
    high_value_authors: list[str] = Field(default_factory=list)
    blocked_authors: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    repositories: list[str] = Field(default_factory=list)
    twitter: TwitterSettings = Field(default_factory=TwitterSettings)
    quality_gates: QualityGates = Field(default_factory=QualityGates)
    paths: Paths = Field(default_factory=Paths)


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
