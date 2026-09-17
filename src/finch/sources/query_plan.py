"""分源查询计划：``--all`` / daily 用 yaml；单源 CLI 覆盖。"""

from __future__ import annotations

from finch.settings import Settings, SourceDiscoverySettings
from finch.sources.connectors import DiscoveryContext
from finch.sources.models import Source


def _plan(settings: Settings, src: Source) -> SourceDiscoverySettings:
    return getattr(settings.sources, src.value)


def _payload(settings: Settings, src: Source) -> tuple[list[str], list[str]]:
    """返回 (queries, urls)；GitHub 用 users 当登录名，weixin 同时支持 query 与 url。"""
    if src == Source.TWITTER:
        return list(settings.sources.twitter.queries), list(settings.sources.twitter.urls)
    if src == Source.REDDIT:
        return list(settings.sources.reddit.queries), list(settings.sources.reddit.urls)
    if src == Source.GITHUB:
        return list(settings.sources.github.users), []
    if src == Source.V2EX:
        return list(settings.sources.v2ex.queries), []
    if src == Source.WEIXIN:
        return list(settings.sources.weixin.queries), list(settings.sources.weixin.urls)
    if src == Source.XIAOHONGSHU:
        return (
            list(settings.sources.xiaohongshu.queries),
            list(settings.sources.xiaohongshu.urls),
        )
    return [], []


def build_context_by_source(
    settings: Settings,
    *,
    cli_queries: list[str] | None = None,
    cli_urls: list[str] | None = None,
    source: Source | None = None,
    limit: int = 20,
    all_sources: bool = False,
) -> dict[Source, DiscoveryContext]:
    """Build per-source DiscoveryContext.

    - Single ``source`` with CLI queries/urls → CLI only for that source (bypasses enabled/mode).
    - ``all_sources`` / daily (no CLI override) → yaml ``sources.*`` fields only.
    - Disabled sources (``enabled=false`` or ``mode=disabled``) are skipped.
    - Enabled source without a valid query/URL carries ``config_error`` (→ CONFIG_ERROR).
    - GitHub context uses ``github.users`` as queries (logins), never topic words.
    """
    queries = list(cli_queries or [])
    urls = list(cli_urls or [])
    use_cli = bool(queries or urls) and source is not None and not all_sources

    out: dict[Source, DiscoveryContext] = {}
    targets = [source] if source is not None and not all_sources else list(Source)

    for src in targets:
        if use_cli and src == source:
            if src == Source.GITHUB:
                # GitHub queries are always logins; CLI --query treated as users.
                out[src] = DiscoveryContext(queries=queries, urls=[], limit=limit)
            else:
                # Weixin 与其它源一致：--query 走搜索，--url 走文章导入。
                out[src] = DiscoveryContext(queries=queries, urls=urls, limit=limit)
            continue

        plan = _plan(settings, src)
        if not plan.enabled or plan.mode == "disabled":
            continue
        q, u = _payload(settings, src)
        if plan.mode == "query" and not q:
            out[src] = DiscoveryContext(config_error=f"{src.value}: enabled but no queries")
            continue
        if plan.mode == "url" and not u:
            out[src] = DiscoveryContext(config_error=f"{src.value}: enabled but no urls")
            continue
        out[src] = DiscoveryContext(
            queries=q,
            urls=u,
            limit=plan.fetch_limit,
            mode=plan.mode,
        )
    return out
