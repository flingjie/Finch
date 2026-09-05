"""Reddit 数据模型（opencli reddit search 输出映射）。"""

from datetime import UTC, datetime

from pydantic import BaseModel, field_validator

_SELFTEXT_MAX_CHARS = 500


class RedditPost(BaseModel):
    """opencli reddit search 原始 JSON 映射。

    未知字段由 Pydantic 忽略，contract test 捕获字段变化。
    """

    id: str
    title: str
    subreddit: str | None = None
    author: str
    score: int = 0
    comments: int = 0
    url: str
    created_utc: int | str | float | None = None
    selftext: str = ""

    @field_validator("score", "comments", mode="before")
    @classmethod
    def _coerce_int(cls, v: object) -> int:
        if isinstance(v, str):
            return int(v) if v else 0
        return v or 0  # type: ignore[return-value]

    @field_validator("selftext", mode="before")
    @classmethod
    def _coerce_selftext(cls, v: object) -> str:
        if isinstance(v, str):
            return v
        return ""

    def published_at(self) -> datetime | None:
        """解析 created_utc（Unix epoch 秒）为 UTC datetime；失败返回 None（禁止伪造时间）。"""
        if self.created_utc is None:
            return None
        try:
            return datetime.fromtimestamp(float(self.created_utc), tz=UTC)
        except (ValueError, TypeError, OSError):
            return None

    def content(self) -> str:
        """标题 + 截断正文；空 selftext 退化为仅标题（链接/图片帖常见）。"""
        body = self.selftext.strip()
        if not body:
            return self.title
        return f"{self.title}\n\n{body[:_SELFTEXT_MAX_CHARS]}"


class RedditError(RuntimeError):
    """Reddit 调用失败基类."""

    error_code: str

    def __init__(self, message: str, error_code: str = "REDDIT_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code


class RedditSourceUnavailable(RedditError):
    """Bridge 离线、未登录或浏览器不可用."""

    def __init__(self, message: str = "Reddit source unavailable") -> None:
        super().__init__(message, "REDDIT_SOURCE_UNAVAILABLE")


class RedditRateLimited(RedditError):
    """限流."""

    def __init__(self, message: str = "Reddit rate limited") -> None:
        super().__init__(message, "RATE_LIMITED")


class RedditCommandBlocked(RedditError):
    """Denylist 命中（defense in depth）."""

    def __init__(self, message: str = "Reddit write command blocked") -> None:
        super().__init__(message, "COMMAND_BLOCKED")
