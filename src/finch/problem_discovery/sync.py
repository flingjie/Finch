"""问题发现广域同步：合并 research_topics → sources，再走 DiscoveryOrchestrator。

与 ``products.sources`` / 旧 peer ``sources`` 隔离；单源失败不阻塞其他源。
"""

from __future__ import annotations

from finch.settings import ProblemDiscoverySettings, Settings, SourcesSettings
from finch.sources.orchestrator import DiscoveryOrchestrator, SyncResult, aggregate_state
from finch.sources.query_plan import build_context_by_source
from finch.storage.workspace import Workspace


def merge_problem_sources(pd: ProblemDiscoverySettings) -> SourcesSettings:
    """把 research_topics / signals 合并进 problem_discovery.sources 的拷贝。"""
    data = pd.sources.model_dump()
    for topic in pd.research_topics:
        for key, queries in topic.queries_by_source.items():
            qlist = [str(q).strip() for q in queries if str(q).strip()]
            if not qlist:
                continue
            if key == "github_issue_queries":
                cur = list(data.get("github", {}).get("issue_queries") or [])
                data.setdefault("github", {})["issue_queries"] = list(
                    dict.fromkeys([*cur, *qlist])
                )
            elif key == "github_watchlist":
                cur = list(data.get("github", {}).get("watchlist") or [])
                data.setdefault("github", {})["watchlist"] = list(
                    dict.fromkeys([*cur, *qlist])
                )
            elif key in data and isinstance(data[key], dict):
                cur = list(data[key].get("queries") or [])
                data[key]["queries"] = list(dict.fromkeys([*cur, *qlist]))
        signals = [str(s).strip() for s in topic.signals if str(s).strip()]
        if signals:
            for src in ("reddit", "hackernews"):
                cur = list(data.get(src, {}).get("queries") or [])
                data.setdefault(src, {})["queries"] = list(
                    dict.fromkeys([*cur, *signals])
                )

    # 有载荷时自动启用对应源（mode=query），避免 enabled:false 静默跳过。
    gh = data.setdefault("github", {})
    if gh.get("users") or gh.get("watchlist") or gh.get("issue_queries"):
        gh["enabled"] = True
        if gh.get("mode") in (None, "", "disabled"):
            gh["mode"] = "query"
    for src in ("reddit", "hackernews"):
        block = data.setdefault(src, {})
        if block.get("queries") or block.get("urls") or block.get("subreddits"):
            block["enabled"] = True
            if block.get("mode") in (None, "", "disabled"):
                block["mode"] = "query"
            if src == "hackernews" and not block.get("entrances"):
                block["entrances"] = ["search"]
    return SourcesSettings.model_validate(data)


def sync_problem_sources(
    ws: Workspace,
    settings: Settings,
    *,
    orchestrator: DiscoveryOrchestrator | None = None,
    source: str | None = None,
    cli_queries: list[str] | None = None,
) -> list[SyncResult]:
    """按 problem_discovery 配置同步广域来源；无启用源时返回空列表。

    ``source`` / ``cli_queries`` 供影子 Skill 做单源定向补充搜索（仍走 PD 合并配置）。
    """
    from finch.sources.models import Source

    pd = settings.problem_discovery
    if not pd.enabled:
        return []
    merged = merge_problem_sources(pd)
    src_enum: Source | None = None
    if source:
        try:
            src_enum = Source(source)
        except ValueError:
            return []
    contexts = build_context_by_source(
        settings,
        all_sources=src_enum is None,
        source=src_enum,
        cli_queries=cli_queries,
        sources=merged,
        limit=50,
    )
    if not contexts:
        return []
    orch = orchestrator or DiscoveryOrchestrator(ws)
    return orch.sync_all(context_by_source=contexts, project_peers=False)


def format_sync_summary(results: list[SyncResult]) -> str:
    if not results:
        return "problem sources: none configured"
    parts = [f"{r.source.value}:{aggregate_state(r)}" for r in results]
    return "problem sources: " + " ".join(parts)
