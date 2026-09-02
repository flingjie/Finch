"""Twitter 查询构建（spec 8）"""

from pydantic import BaseModel, Field, field_validator


class QueryConfig(BaseModel):
    """单个查询配置."""

    id: str
    text: str
    filter: str = "top"  # top, live, photos, videos
    priority: int = Field(default=5, ge=1, le=10)

    @field_validator("filter")
    @classmethod
    def _valid_filter(cls, v: str) -> str:
        allowed = {"top", "live", "photos", "videos"}
        if v not in allowed:
            raise ValueError(f"filter must be one of {allowed}")
        return v

    def build_argv(self, per_query_limit: int = 20) -> list[str]:
        """生成 opencli argv 数组（spec 5.3：数组传参，不拼接 shell 字符串）."""
        argv = [
            "opencli", "twitter", "search",
            self.text,
            "--product", self.filter,
            "--limit", str(per_query_limit),
            "-f", "json",
        ]
        return argv


class QueryBuilder:
    """从 finch.yaml 加载查询集并管理版本."""

    def __init__(self, configs: list[dict], per_query_limit: int = 20):
        self.configs = [QueryConfig(**c) for c in configs]
        self.per_query_limit = per_query_limit
        # 版本由查询文本哈希构成，用于可复现性
        self.version = self._compute_version()

    def _compute_version(self) -> str:
        import hashlib
        raw = "|".join(f"{q.id}:{q.text}:{q.filter}" for q in self.configs)
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def __iter__(self):
        yield from self.configs

    def __len__(self) -> int:
        return len(self.configs)

    def build_all(self) -> list[tuple[QueryConfig, list[str]]]:
        """返回 (config, argv) 对列表."""
        return [(cfg, cfg.build_argv(self.per_query_limit)) for cfg in self.configs]
