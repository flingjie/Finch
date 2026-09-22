"""分源查询计划：``--all`` / daily 用 yaml；单源 CLI 覆盖。

P1 起引入 ``DiscoveryPlan``：一次刷新唯一的输入契约（稳定 ``plan_id``），
``plan_id`` 与 ``config_fingerprint`` 均由规范化计划内容计算，不含运行时间。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from finch.settings import ExplorationTopic, Settings, SourceDiscoverySettings
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


def select_exploration_topic(
    settings: Settings, *, now: datetime | None = None
) -> ExplorationTopic | None:
    """按天轮换选择一个主题组（D9）。

    确定性（同一天同一组）、幂等（重试复用同组）、纯读安全（不推进状态）。
    """
    topics = settings.sources.exploration_topics
    if not topics:
        return None
    clock = now or datetime.now(UTC)
    return topics[clock.timetuple().tm_yday % len(topics)]


def build_context_by_source(
    settings: Settings,
    *,
    cli_queries: list[str] | None = None,
    cli_urls: list[str] | None = None,
    source: Source | None = None,
    limit: int = 20,
    all_sources: bool = False,
    topic: ExplorationTopic | None = None,
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
        # D9：合并当前主题组的查询（去重；用户固定查询优先，GitHub 用登录名）。
        if topic is not None:
            if src == Source.GITHUB:
                q = list(dict.fromkeys([*q, *topic.github_users]))
            else:
                q = list(dict.fromkeys([*q, *topic.queries_by_source.get(src.value, [])]))
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


_DEFAULT_INTENT = "探索相邻实践者"
_DEFAULT_RANKING_QUESTION = "谁最近做了具体、可信、与我互补的事情？"


def _fingerprint(obj: object) -> str:
    """规范化 JSON 的 sha256 前 16 位（键排序、列表保序但不含运行时间）。"""
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class DiscoveryPlan(BaseModel):
    """一次刷新唯一的输入契约（P1）：稳定 ``plan_id``，与 ``run_id`` 分离。

    - ``plan_id``：规范化后的真实计划内容哈希，同一计划重试得到同一 ID。
    - ``config_fingerprint``：只覆盖来源/时间窗/预算/排除项，回答"配置是否变了"。
    """

    schema_version: str = "1"
    plan_id: str = ""
    intent: str = ""
    ranking_question: str = ""
    lookback_hours: int = 720
    freshness_boost_hours: int = 72
    source_queries: dict[str, list[str]] = Field(default_factory=dict)
    source_limits: dict[str, int] = Field(default_factory=dict)
    candidate_limit: int = 50
    nominate_limit: int = 10
    enrich_limit: int = 3
    excluded_content: list[str] = Field(default_factory=list)
    config_fingerprint: str = ""


def build_discovery_plan(
    settings: Settings,
    *,
    lookback_hours: int | None = None,
    intent: str | None = None,
) -> DiscoveryPlan:
    """构造一次刷新唯一的输入契约；CLI 覆盖优先，默认走配置与当前问题。"""
    dp = settings.discovery.daily_people
    source_queries: dict[str, list[str]] = {}
    source_limits: dict[str, int] = {}
    for src in Source:
        plan = getattr(settings.sources, src.value, None)
        if plan is None or not plan.enabled or plan.mode == "disabled":
            continue
        q, u = _payload(settings, src)
        inputs = list(dict.fromkeys([*q, *u]))
        if inputs:
            source_queries[src.value] = inputs
        source_limits[src.value] = plan.fetch_limit

    lookback = lookback_hours if lookback_hours is not None else settings.discovery.lookback_hours
    intent_text = (intent or "").strip()
    if not intent_text:
        intent_text = (
            next((q for q in settings.interests.current_questions if q.strip()), "")
            or (
                settings.interests.long_term_interests[0]
                if settings.interests.long_term_interests
                else ""
            )
            or _DEFAULT_INTENT
        )

    plan = DiscoveryPlan(
        intent=intent_text,
        ranking_question=_DEFAULT_RANKING_QUESTION,
        lookback_hours=lookback,
        freshness_boost_hours=settings.discovery.freshness_boost_hours,
        source_queries=source_queries,
        source_limits=source_limits,
        candidate_limit=dp.candidate_pool_size,
        nominate_limit=settings.discovery.nominate_limit,
        enrich_limit=dp.semantic_assess_limit,
        excluded_content=list(settings.interests.excluded_content),
    )
    plan.config_fingerprint = _fingerprint(
        {
            "lookback_hours": plan.lookback_hours,
            "freshness_boost_hours": plan.freshness_boost_hours,
            "source_queries": plan.source_queries,
            "source_limits": plan.source_limits,
            "candidate_limit": plan.candidate_limit,
            "nominate_limit": plan.nominate_limit,
            "enrich_limit": plan.enrich_limit,
            "excluded_content": plan.excluded_content,
        }
    )
    plan.plan_id = f"plan_{_fingerprint({
        'schema_version': plan.schema_version,
        'intent': plan.intent,
        'ranking_question': plan.ranking_question,
        'config_fingerprint': plan.config_fingerprint,
    })}"
    return plan
