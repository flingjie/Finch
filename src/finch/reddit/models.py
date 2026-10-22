"""Reddit 数据模型（opencli reddit search 输出映射）。"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator


class RedditPost(BaseModel):
    """opencli reddit 原始 JSON 映射（``search`` 行直接校验，``read`` 行由客户端转换）。

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
    # ``reddit read`` 的评论行原文（含缩进）；``search`` 不提供，保持空。
    comment_rows: list[str] = Field(default_factory=list)
    # opencli 按 --max-length 截断正文时置位，供上层报告覆盖缺口。
    truncated: bool = False

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
        """标题 + 完整正文 + 评论行；空 selftext 退化为仅标题（链接/图片帖常见）。"""
        body = self.selftext.strip()
        head = self.title if not body else f"{self.title}\n\n{body}"
        comments = "\n\n".join(row.strip() for row in self.comment_rows if row.strip())
        if not comments:
            return head
        return f"{head}\n\n[评论]\n{comments}"


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
