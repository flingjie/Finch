"""Twitter 数据模型（spec 5.3 / 7.3）"""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


class QuotedTweet(BaseModel):
    """嵌套引用 tweet（opencli 输出 schema 子集）."""

    id: str
    author: str
    name: str | None = None
    text: str
    created_at: str | None = None
    url: str
    has_media: bool = False
    media_urls: list[str] = Field(default_factory=list)
    media_posters: list[str] = Field(default_factory=list)


class Tweet(BaseModel):
    """opencli twitter search/thread/timeline 原始 JSON 映射.

    未知字段由 Pydantic 忽略，contract test 捕获字段变化。
    """

    id: str
    author: str
    bio: str | None = None
    text: str
    created_at: str | None = None
    likes: int = 0
    views: int = 0
    url: str
    has_media: bool = False
    media_urls: list[str] = Field(default_factory=list)
    media_posters: list[str] = Field(default_factory=list)
    card: Any | None = None
    quoted_tweet: QuotedTweet | None = None

    @field_validator("views", mode="before")
    @classmethod
    def _coerce_views(cls, v: object) -> int:
        if isinstance(v, str):
            return int(v) if v else 0
        return v or 0  # type: ignore[return-value]

    def published_at(self) -> datetime | None:
        """解析 Twitter created_at；失败返回 None（spec 5.3：禁止伪造时间）."""
        if not self.created_at:
            return None
        try:
            return datetime.strptime(self.created_at, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            return None


class DiscussionCandidate(BaseModel):
    """规范化后的讨论候选（spec 7.3）."""

    id: str
    source: Literal["twitter"] = "twitter"
    author_handle: str
    text: str
    url: str
    published_at: datetime | None = None
    metrics: dict = Field(default_factory=dict)
    query_id: str | None = None
    captured_at: datetime = Field(default_factory=_utcnow)


class TwitterError(RuntimeError):
    """Twitter 调用失败基类."""

    error_code: str

    def __init__(self, message: str, error_code: str = "TWITTER_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code


class TwitterSourceUnavailable(TwitterError):
    """Bridge 离线、未登录或浏览器不可用."""

    def __init__(self, message: str = "Twitter source unavailable") -> None:
        super().__init__(message, "TWITTER_SOURCE_UNAVAILABLE")


class TwitterRateLimited(TwitterError):
    """限流."""

    def __init__(self, message: str = "Twitter rate limited") -> None:
        super().__init__(message, "RATE_LIMITED")


class TwitterCommandBlocked(TwitterError):
    """Denylist 命中（defense in depth）."""

    def __init__(self, message: str = "Twitter write command blocked") -> None:
        super().__init__(message, "COMMAND_BLOCKED")


def to_candidate(tweet: Tweet, *, query_id: str | None = None) -> DiscussionCandidate:
    """将原始 Tweet 转换为内部 DiscussionCandidate."""
    return DiscussionCandidate(
        id=tweet.id,
        author_handle=tweet.author,
        text=tweet.text,
        url=tweet.url,
        published_at=tweet.published_at(),
        metrics={"likes": tweet.likes, "views": tweet.views},
        query_id=query_id,
    )
