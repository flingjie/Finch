"""repo-discovery 领域模型（设计 §8）。

指标缺失保存为 null，不得伪装成真实零值。热度公式版本写在 RankingSnapshot 上。
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class QueryStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    TRUNCATED = "truncated"
    FAILED = "failed"


class TopicTag(StrEnum):
    AGENT = "agent"
    OTHER = "other"
    UNKNOWN = "unknown"


class SortKey(StrEnum):
    X_HEAT = "x_heat"
    MENTIONS = "mentions"
    GITHUB_STARS = "github_stars"
    STARS_DELTA = "stars_delta"
    LATEST = "latest"


# P0 probe: opencli 仅返回 likes/views → v1 只用 likes。
FORMULA_VERSION_LIKES_ONLY = "x_heat_v1_likes_only"
EFFECTIVE_FORMULA_LIKES_ONLY = "tweet_heat = likes; repo_heat = sum(deduped tweet likes)"


class TweetRecord(BaseModel):
    """窗口内采集到的一条推文（唯一键 tweet_id）。"""

    tweet_id: str
    url: str = ""
    author: str = ""
    published_at: datetime | None = None
    text: str = ""
    expanded_urls: list[str] = Field(default_factory=list)
    # 互动快照：null = 适配层未提供；0 = 真实零。
    likes: int | None = None
    reposts: int | None = None
    quotes: int | None = None
    replies: int | None = None
    views: int | None = None
    metrics_fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    query_refs: list[str] = Field(default_factory=list)
    # 原生转推壳：不计独立原创分享热度。
    is_native_retweet: bool = False
    repo_refs: list[str] = Field(default_factory=list)  # owner/repo keys


class RepoRecord(BaseModel):
    """可识别的 GitHub 仓库（临时身份为规范化 owner/repo）。"""

    key: str  # lowercased owner/repo
    owner: str
    name: str
    url: str = ""
    github_id: int | None = None
    description: str | None = None  # None = 未取得；"" = 空简介
    topics: list[str] = Field(default_factory=list)
    stars: int | None = None
    forks: int | None = None
    metadata_fetched_at: datetime | None = None
    metadata_ok: bool = False
    tweet_ids: list[str] = Field(default_factory=list)
    topic_tag: TopicTag = TopicTag.UNKNOWN
    source_share_urls: list[str] = Field(default_factory=list)


class QueryProgress(BaseModel):
    query_id: str
    text: str
    status: QueryStatus = QueryStatus.PENDING
    slice_index: int = 0
    tweets_seen: int = 0
    error: str = ""
    coverage_note: str = ""


class DiscoveryRun(BaseModel):
    """一次固定时间窗口的采集运行。"""

    run_id: str
    window_start: datetime
    window_end: datetime
    timezone: str = "Asia/Shanghai"
    config_fingerprint: str = ""
    status: RunStatus = RunStatus.RUNNING
    queries: list[QueryProgress] = Field(default_factory=list)
    pending_short_urls: list[str] = Field(default_factory=list)
    non_repo_urls: list[str] = Field(default_factory=list)
    tweet_count: int = 0
    repo_count: int = 0
    formula_version: str = FORMULA_VERSION_LIKES_ONLY
    effective_formula: str = EFFECTIVE_FORMULA_LIKES_ONLY
    unavailable_metrics: list[str] = Field(
        default_factory=lambda: ["reposts", "quotes", "replies"]
    )
    errors: list[str] = Field(default_factory=list)
    checkpoint: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class RankedRepoEntry(BaseModel):
    """榜单中的一行（可由原始记录复算）。"""

    rank: int
    repo_key: str
    x_heat: int | None = None
    mentions: int = 0
    likes_sum: int | None = None
    reposts_sum: int | None = None
    quotes_sum: int | None = None
    replies_sum: int | None = None
    github_stars: int | None = None
    github_forks: int | None = None
    stars_delta: int | None = None
    latest_share_at: datetime | None = None
    metrics_partial: bool = False
    topic_tag: TopicTag = TopicTag.UNKNOWN
    description: str | None = None
    url: str = ""
    source_urls: list[str] = Field(default_factory=list)


class RankingSnapshot(BaseModel):
    """一次可复算的排序快照。"""

    run_id: str
    sort: SortKey = SortKey.X_HEAT
    topic_filter: str = "all"  # all | agent
    formula_version: str = FORMULA_VERSION_LIKES_ONLY
    effective_formula: str = EFFECTIVE_FORMULA_LIKES_ONLY
    window_start: datetime
    window_end: datetime
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    entries: list[RankedRepoEntry] = Field(default_factory=list)
