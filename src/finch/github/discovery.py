"""自动发现最近有推送的公开仓库（不读取私有仓库内容）。"""

from datetime import UTC, datetime, timedelta

from ..settings import Settings
from .gh_client import GhClient


class RepoDiscovery:
    def __init__(self, gh: GhClient):
        self.gh = gh

    def recently_pushed(self, lookback_hours: int, limit: int) -> list[str]:
        """返回 `limit` 个在最近 `lookback_hours` 小时内被推送的公开仓库名。"""
        since = datetime.now(UTC) - timedelta(hours=lookback_hours)
        repos = self.gh.list_user_repos()
        active = [
            r
            for r in repos
            if r.pushed_at is not None
            and r.pushed_at >= since
            and r.size > 0
            and not r.is_private
            and not r.is_fork
            and not r.archived
            and not r.disabled
        ]
        active.sort(key=lambda r: r.pushed_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return [r.name_with_owner for r in active[:limit]]


def resolve_repositories(settings: Settings, gh: GhClient) -> list[str]:
    """优先使用显式配置；否则按 repository_discovery 自动发现。"""
    if settings.repositories:
        return settings.repositories
    if settings.repository_discovery.enabled:
        return RepoDiscovery(gh).recently_pushed(
            settings.repository_discovery.lookback_hours,
            settings.repository_discovery.max_repos,
        )
    return []
