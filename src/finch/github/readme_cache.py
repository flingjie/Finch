"""GitHub README 缓存与仓库规范化（去重 + TTL 兜底）。

个人背景仓库与外部项目 README 共享同一缓存：按规范化 ``owner/name`` 去重，
内容落 ``cache/readmes/<owner>__<name>.md``，元数据 sidecar 记录抓取时间与内容版本。
读取失败保留来源与缺失提示，不编造当前效果；单个仓库失败不阻塞其它。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from finch.github.gh_client import GhClient, GhError
from finch.storage.workspace import Workspace

if TYPE_CHECKING:
    from finch.settings import Settings


def normalize_repo(full_name: str) -> str:
    """GitHub 链接 / 仓库名 → 规范化 ``owner/name``（小写、去 ``.git`` 与 URL/路径片段）。

    只处理 HTTP(S) 与裸 ``github.com/`` 主机前缀；SSH 形式由上游 ``repos/urls`` 处理。
    """
    s = (full_name or "").strip()
    if not s:
        return ""
    s = re.sub(r"^(https?://)?(www\.)?github\.com/", "", s, flags=re.IGNORECASE)
    s = s.strip().strip("/")
    if s.endswith(".git"):
        s = s[:-4]
    parts = [p for p in s.split("/") if p]
    if len(parts) < 2:
        return ""
    return f"{parts[0]}/{parts[1]}".lower()


@dataclass(frozen=True)
class CachedReadme:
    """缓存里的仓库 README。"""

    repo: str  # normalized owner/name
    content: str
    fetched_at: datetime
    version: str = ""  # 内容 sha256 前 12 位（默认分支不可得时用内容版本兜底）


class _ReadmeMeta(BaseModel):
    """README 缓存元数据 sidecar。"""

    repo: str
    fetched_at: datetime
    version: str = ""


class ReadmeCache:
    """按仓库缓存 README；TTL 兜底，``force`` 刷新。单仓库失败返回 None，不抛异常。"""

    def __init__(
        self,
        workspace: Workspace,
        gh: GhClient | None = None,
        *,
        ttl_hours: int = 168,
    ) -> None:
        self.ws = workspace
        self.gh = gh or GhClient()
        self.ttl_hours = ttl_hours

    def _paths(self, repo: str) -> tuple[Path, Path]:
        d = self.ws.dir("cache/readmes")
        base = self.ws.safe_filename(repo)
        return d / f"{base}.md", d / f"{base}.meta.yaml"

    def _expired(self, fetched_at: datetime) -> bool:
        if self.ttl_hours <= 0:
            return False
        age = datetime.now(UTC) - fetched_at
        return age.total_seconds() > self.ttl_hours * 3600

    def get(self, repo: str) -> CachedReadme | None:
        """读缓存；未命中或 TTL 过期返回 None（不触发网络）。"""
        repo = normalize_repo(repo)
        if not repo:
            return None
        content_path, meta_path = self._paths(repo)
        if not content_path.exists() or not meta_path.exists():
            return None
        meta = self.ws.read_yaml(meta_path, _ReadmeMeta)
        if meta is None or self._expired(meta.fetched_at):
            return None
        try:
            content = content_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return CachedReadme(
            repo=repo, content=content, fetched_at=meta.fetched_at, version=meta.version
        )

    def read(self, repo: str, *, force: bool = False) -> CachedReadme | None:
        """命中缓存即返回（``force`` 跳过缓存）；否则拉取、落盘、返回。失败返回 None。"""
        repo = normalize_repo(repo)
        if not repo:
            return None
        if not force:
            cached = self.get(repo)
            if cached is not None:
                return cached
        try:
            content = self.gh.readme(repo)
        except GhError:
            return None
        return self.store(repo, content)

    def store(self, repo: str, content: str, *, version: str = "") -> CachedReadme:
        repo = normalize_repo(repo)
        content_path, meta_path = self._paths(repo)
        self.ws.atomic_write(content_path, content)
        fetched_at = datetime.now(UTC)
        ver = version or hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        self.ws.write_yaml(meta_path, _ReadmeMeta(repo=repo, fetched_at=fetched_at, version=ver))
        return CachedReadme(repo=repo, content=content, fetched_at=fetched_at, version=ver)


def load_personal_repo_context(
    settings: Settings,
    workspace: Workspace,
    gh: GhClient | None = None,
    *,
    refresh: bool = False,
) -> dict[str, CachedReadme | None]:
    """读个人背景仓库 README（``settings.repositories``，不受 ``max_repos`` 上限截断）。

    首次加载 / TTL 过期 / ``refresh=True`` 才真的读；其余命中缓存。
    单个仓库失败不阻塞其它（值为 None，调用方保留缺失提示）。
    """
    cache = ReadmeCache(workspace, gh, ttl_hours=settings.discovery.readme_cache_ttl_hours)
    out: dict[str, CachedReadme | None] = {}
    for repo in settings.repositories:
        norm = normalize_repo(repo)
        if not norm:
            continue
        out[norm] = cache.read(norm, force=refresh)
    return out
