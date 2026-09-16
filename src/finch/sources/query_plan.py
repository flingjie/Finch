"""分源查询计划：``--all`` / daily 用 yaml；单源 CLI 覆盖。"""

from __future__ import annotations

from finch.settings import Settings
from finch.sources.connectors import DiscoveryContext
from finch.sources.models import Source


def _twitter_queries_from_settings(settings: Settings) -> list[str]:
    src = settings.sources.twitter.queries
    if src:
        return list(src)
    # Fall back to legacy twitter.queries[].text then interests.
    texts: list[str] = []
    for q in settings.twitter.queries:
        if isinstance(q, dict) and q.get("text"):
            texts.append(str(q["text"]))
    if texts:
        return texts
    return list(settings.interests.long_term_interests[:5])


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

    - Single ``source`` with CLI queries/urls → CLI only for that source.
    - ``all_sources`` / daily (no CLI override) → yaml ``sources.*`` fields only.
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
            elif src == Source.WEIXIN:
                out[src] = DiscoveryContext(queries=[], urls=urls or queries, limit=limit)
            else:
                out[src] = DiscoveryContext(queries=queries, urls=urls, limit=limit)
            continue

        # YAML / settings-driven plan (never share one topic query across platforms).
        if src == Source.TWITTER:
            out[src] = DiscoveryContext(
                queries=_twitter_queries_from_settings(settings),
                urls=list(settings.sources.twitter.urls),
                limit=limit,
            )
        elif src == Source.REDDIT:
            out[src] = DiscoveryContext(
                queries=list(settings.sources.reddit.queries),
                urls=list(settings.sources.reddit.urls),
                limit=limit,
            )
        elif src == Source.GITHUB:
            out[src] = DiscoveryContext(
                queries=list(settings.sources.github.users),
                urls=[],
                limit=limit,
            )
        elif src == Source.V2EX:
            out[src] = DiscoveryContext(
                queries=list(settings.sources.v2ex.queries),
                urls=[],
                limit=limit,
            )
        elif src == Source.WEIXIN:
            out[src] = DiscoveryContext(
                queries=[],
                urls=list(settings.sources.weixin.urls),
                limit=limit,
            )
        elif src == Source.XIAOHONGSHU:
            out[src] = DiscoveryContext(
                queries=list(settings.sources.xiaohongshu.queries),
                urls=list(settings.sources.xiaohongshu.urls),
                limit=limit,
            )
    return out
