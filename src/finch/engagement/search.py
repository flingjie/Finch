"""外部帖子搜索适配器（执行计划 Phase 2）。

提供统一的同步 ``PostSearchProvider`` 接口，接入 X/Twitter 与 Reddit（均复用 OpenCLI 只读封装）。
查询由兴趣配置生成，排除词与去重在搜索完成后本地执行，最终按 ``max_posts_scanned`` 截断。
"""

from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal, Protocol

from finch.reddit.models import RedditPost
from finch.reddit.opencli_client import RedditOpenCliClient
from finch.twitter.models import Tweet
from finch.twitter.opencli_client import OpenCliClient

from ..settings import EngagementSettings, InterestsSettings
from .models import ExternalPost, Platform

QueryClass = Literal["peer", "usage", "adjacent"]


class PostSearchError(RuntimeError):
    """帖子搜索失败基类；单个 provider 失败应可被调用方捕获，而非中断整轮。"""

    error_code: str

    def __init__(self, message: str, error_code: str = "POST_SEARCH_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code


class ProviderUnavailableError(PostSearchError):
    """搜索提供方未启用或不可用（如 Reddit 占位实现）。"""

    def __init__(self, message: str = "Post search provider unavailable") -> None:
        super().__init__(message, "PROVIDER_UNAVAILABLE")


@dataclass(frozen=True)
class PostSearchFailure:
    """单次搜索失败记录，不会阻断其他 provider 或查询。"""

    platform: Platform
    query: str | None
    reason: str
    query_class: QueryClass | None = None


@dataclass(frozen=True)
class TaggedQuery:
    """带类别的查询词：peer / usage / adjacent。"""

    text: str
    query_class: QueryClass


@dataclass
class QueryClassCoverage:
    """单类查询覆盖统计；空类如实报告，不用低质量结果凑数。"""

    query_class: QueryClass
    queries: int = 0
    returned: int = 0
    kept: int = 0
    allocated: int = 0
    failure_reasons: list[str] = field(default_factory=list)


# Default recall slot shares: peer 50% / adjacent 30% / usage 20% of max_posts_scanned.
_QUERY_BUDGET_SHARES: dict[QueryClass, float] = {
    "peer": 0.50,
    "adjacent": 0.30,
    "usage": 0.20,
}


def allocate_query_budgets(max_posts: int) -> dict[QueryClass, int]:
    """Split max_posts into peer/adjacent/usage slots (50/30/20); remainder to peer."""
    cap = max(0, max_posts)
    if cap == 0:
        return {"peer": 0, "adjacent": 0, "usage": 0}
    peer = int(cap * _QUERY_BUDGET_SHARES["peer"])
    adjacent = int(cap * _QUERY_BUDGET_SHARES["adjacent"])
    usage = int(cap * _QUERY_BUDGET_SHARES["usage"])
    # Fix rounding so sum == cap.
    assigned = peer + adjacent + usage
    peer += cap - assigned
    return {"peer": peer, "adjacent": adjacent, "usage": usage}


@dataclass
class EngagementSearchOutcome:
    """一次搜索汇总：有效帖子 + 部分失败记录 + 按类覆盖。"""

    posts: list[ExternalPost]
    failures: list[PostSearchFailure]
    coverage: list[QueryClassCoverage] = field(default_factory=list)


class PostSearchProvider(Protocol):
    """统一帖子搜索接口（同步，匹配仓库现有约定）。

    失败约定：``search`` 失败时抛出 ``PostSearchError``（或其子类），由调用方决定是否
    继续其他 provider；``available`` 返回 ``False`` 表示该 provider 当前不可运行。
    """

    platform: Platform

    def search(self, query: str, *, limit: int) -> list[ExternalPost]: ...

    def available(self) -> bool: ...


class XPostSearchProvider:
    """X/Twitter 搜索适配器：包装 ``OpenCliClient.search`` 并规范化为 ``ExternalPost``。

    注意：``Tweet`` 没有单独的 ``name``/``id`` 拆分，因此 ``author_id`` 与 ``author_name``
    都使用 ``tweet.author``；``created_at`` 无法解析的推文会被跳过（不伪造时间）。
    """

    platform: Platform = "x"

    def __init__(self, client: OpenCliClient | None = None) -> None:
        self._client = client or OpenCliClient()

    def available(self) -> bool:
        # 适配器已接入；运行时健康问题（未登录/桥不可用/限流）在 search 中以类型化异常暴露。
        return True

    def search(self, query: str, *, limit: int) -> list[ExternalPost]:
        tweets = self._client.search(query, product="top", limit=limit)
        posts: list[ExternalPost] = []
        for tweet in tweets:
            post = _to_external_post(tweet, topic=query)
            if post is not None:
                posts.append(post)
        return posts


class RedditPostSearchProvider:
    """Reddit 搜索适配器：包装 ``RedditOpenCliClient.search`` 并规范化为 ``ExternalPost``。

    ``created_utc`` 无法解析的帖子会被跳过（不伪造时间）；正文为标题 + 截断 selftext，
    ``metrics`` 使用 Reddit 语义键（upvotes / comments）。
    """

    platform: Platform = "reddit"

    def __init__(self, client: RedditOpenCliClient | None = None) -> None:
        self._client = client or RedditOpenCliClient()

    def available(self) -> bool:
        return True

    def search(self, query: str, *, limit: int) -> list[ExternalPost]:
        posts = self._client.search(query, limit=limit)
        result: list[ExternalPost] = []
        for post in posts:
            external = _reddit_to_external_post(post, topic=query)
            if external is not None:
                result.append(external)
        return result


def _reddit_to_external_post(post: RedditPost, *, topic: str) -> ExternalPost | None:
    """将 RedditPost 映射为 ExternalPost；时间无法解析时返回 None（禁止伪造时间）。"""
    published_at = post.published_at()
    if published_at is None:
        return None
    return ExternalPost(
        id=post.id,
        platform="reddit",
        url=post.url,
        author_id=post.author,
        author_name=post.author,
        content=post.content(),
        published_at=published_at,
        metrics={"upvotes": post.score, "comments": post.comments},
        matched_topics=[topic],
    )


def _to_external_post(tweet: Tweet, *, topic: str) -> ExternalPost | None:
    """将 Tweet 映射为 ExternalPost；时间无法解析时返回 None（禁止伪造时间）。"""
    published_at = tweet.published_at()
    if published_at is None:
        return None
    return ExternalPost(
        id=tweet.id,
        platform="x",
        url=tweet.url,
        author_id=tweet.author,
        author_name=tweet.author,
        content=tweet.text,
        published_at=published_at,
        metrics={"likes": tweet.likes, "views": tweet.views},
        matched_topics=[topic],
    )


def fetch_post_by_url(
    url: str,
    *,
    opencli: OpenCliClient,
    reddit_opencli: RedditOpenCliClient | None = None,
    topic: str = "",
) -> ExternalPost | None:
    """按 URL 抓取单条帖子并映射为 ExternalPost（X 取 thread 首条；Reddit 取 post）。

    抓取失败或时间无法解析返回 None（禁止伪造时间）。topic 为空时 ``matched_topics`` 为空。
    """
    if "reddit.com" in url:
        if reddit_opencli is None:
            return None
        reddit_post = reddit_opencli.post(url)
        if reddit_post is None:
            return None
        external = _reddit_to_external_post(reddit_post, topic=topic)
    else:
        tweets = opencli.thread(url)
        if not tweets:
            return None
        external = _to_external_post(tweets[0], topic=topic)
    if external is None:
        return None
    return external.model_copy(update={"matched_topics": [topic] if topic else []})


def build_tagged_queries(
    interests: InterestsSettings,
    *,
    material_terms: Sequence[str] | None = None,
) -> list[TaggedQuery]:
    """同行 / 使用情境 / 相邻领域分类去重；跨类同一词只保留先出现的类别。

    ``material_terms``：来自个人笔记 facts / 近期 ContentJob 的窄查询（Build-in-Public），
    归入 usage 类，与 current_questions 一起优先于相邻领域。
    """
    out: list[TaggedQuery] = []
    seen: set[str] = set()

    def _add(terms: Sequence[str], query_class: QueryClass) -> None:
        for term in terms:
            query = term.strip()
            key = query.casefold()
            if query and key not in seen:
                seen.add(key)
                out.append(TaggedQuery(text=query, query_class=query_class))

    _add([*interests.long_term_interests, *interests.explore_directions], "peer")
    _add(
        [
            *interests.current_questions,
            *interests.usage_queries,
            *(material_terms or []),
            *interests.practice_refs,
        ],
        "usage",
    )
    _add(interests.adjacent_queries, "adjacent")
    return out


def build_queries(interests: InterestsSettings) -> list[str]:
    """扁平查询列表（兼容旧调用）；顺序为 peer → usage → adjacent。"""
    return [q.text for q in build_tagged_queries(interests)]


def is_excluded(post: ExternalPost, excluded: Sequence[str]) -> bool:
    """本地排除过滤：正文或匹配主题包含排除词（大小写不敏感的子串匹配）。

    有真实实践信号的发布帖不能仅因「发布」等歧义词被删除。
    """
    from finch.engagement.opportunity import (
        _AMBIGUOUS_EXCLUDE_TOKENS,
        looks_like_practice_release,
    )

    content = post.content.casefold()
    practice = looks_like_practice_release(post.content)
    for term in excluded:
        token = term.strip().casefold()
        if not token:
            continue
        hit_content = token in content
        hit_topic = any(token in topic.casefold() for topic in post.matched_topics)
        if not hit_content and not hit_topic:
            continue
        if token in _AMBIGUOUS_EXCLUDE_TOKENS and practice:
            continue
        return True
    return False


def dedupe(
    posts: Iterable[ExternalPost],
    *,
    skip_ids: set[str] | None = None,
) -> list[ExternalPost]:
    """按 ``(platform, id)`` 去重，并可跳过调用方提供的已处理 id。"""
    skip = skip_ids or set()
    seen: set[tuple[str, str]] = set()
    out: list[ExternalPost] = []
    for post in posts:
        key = (post.platform, post.id)
        if key in seen or post.id in skip:
            continue
        seen.add(key)
        out.append(post)
    return out


def search_engagement_posts(
    providers: Sequence[PostSearchProvider],
    interests: InterestsSettings,
    engagement: EngagementSettings,
    *,
    skip_ids: set[str] | None = None,
) -> EngagementSearchOutcome:
    """统一搜索入口：遍历 provider × 分类查询词，本地过滤、去重并按上限截断。

    覆盖 peer / usage / adjacent（有配置时）；部分失败不中断其余查询；空类写入 coverage 缺口。
    """
    tagged = build_tagged_queries(interests)
    availability = [(provider, provider.available()) for provider in providers]

    tasks: list[tuple[PostSearchProvider, TaggedQuery]] = []
    for provider, available in availability:
        if available:
            for query in tagged:
                tasks.append((provider, query))

    def _search(
        task: tuple[PostSearchProvider, TaggedQuery],
    ) -> list[ExternalPost] | PostSearchFailure:
        provider, query = task
        try:
            return provider.search(query.text, limit=engagement.max_posts_scanned)
        except Exception as exc:  # noqa: BLE001 - 单条失败不得中断整轮
            return PostSearchFailure(
                platform=provider.platform,
                query=query.text,
                reason=str(exc),
                query_class=query.query_class,
            )

    if len(tasks) <= 1:
        results = [_search(task) for task in tasks]
    else:
        with ThreadPoolExecutor(max_workers=max(1, engagement.search_concurrency)) as pool:
            results = list(pool.map(_search, tasks))

    raw: list[ExternalPost] = []
    failures: list[PostSearchFailure] = []
    returned_by_class: dict[QueryClass, int] = {"peer": 0, "usage": 0, "adjacent": 0}
    failure_by_class: dict[QueryClass, list[str]] = {
        "peer": [],
        "usage": [],
        "adjacent": [],
    }
    query_count_by_class: dict[QueryClass, int] = {"peer": 0, "usage": 0, "adjacent": 0}
    for q in tagged:
        query_count_by_class[q.query_class] += 1

    task_iter = iter(results)
    for provider, available in availability:
        if not available:
            failures.append(
                PostSearchFailure(
                    platform=provider.platform, query=None, reason="provider not enabled"
                )
            )
            continue
        for query in tagged:
            result = next(task_iter)
            if isinstance(result, PostSearchFailure):
                failures.append(result)
                failure_by_class[query.query_class].append(result.reason)
            else:
                returned_by_class[query.query_class] += len(result)
                raw.extend(result)

    cap = max(0, engagement.max_posts_scanned)
    budgets = allocate_query_budgets(cap)
    kept = [post for post in raw if not is_excluded(post, interests.excluded_content)]
    deduped = dedupe(kept, skip_ids=skip_ids)

    texts: dict[QueryClass, set[str]] = {
        "peer": {q.text.casefold() for q in tagged if q.query_class == "peer"},
        "usage": {q.text.casefold() for q in tagged if q.query_class == "usage"},
        "adjacent": {q.text.casefold() for q in tagged if q.query_class == "adjacent"},
    }

    def _classes_for(post: ExternalPost) -> list[QueryClass]:
        matched: list[QueryClass] = []
        for qclass, keys in texts.items():
            if any(t.casefold() in keys for t in post.matched_topics):
                matched.append(qclass)
        return matched or ["peer"]

    # First pass: fill per-class slots. Empty class releases unused budget afterward.
    by_class: dict[QueryClass, list[ExternalPost]] = {
        "peer": [],
        "adjacent": [],
        "usage": [],
    }
    used_ids: set[tuple[str, str]] = set()
    for post in deduped:
        for qclass in _classes_for(post):
            if len(by_class[qclass]) >= budgets[qclass]:
                continue
            key = (post.platform, post.id)
            if key in used_ids:
                continue
            by_class[qclass].append(post)
            used_ids.add(key)
            break

    # Release unused slots from empty/underfilled classes to remaining posts.
    unused = sum(budgets[c] - len(by_class[c]) for c in budgets)
    overflow: list[ExternalPost] = []
    if unused > 0:
        for post in deduped:
            key = (post.platform, post.id)
            if key in used_ids:
                continue
            overflow.append(post)
            used_ids.add(key)
            if len(overflow) >= unused:
                break

    posts: list[ExternalPost] = []
    for qclass in ("peer", "adjacent", "usage"):
        posts.extend(by_class[qclass])
    posts.extend(overflow)
    posts = posts[:cap]

    kept_by: dict[QueryClass, int] = {
        "peer": len(by_class["peer"]),
        "adjacent": len(by_class["adjacent"]),
        "usage": len(by_class["usage"]),
    }

    coverage = [
        QueryClassCoverage(
            query_class=qclass,
            queries=query_count_by_class[qclass],
            returned=returned_by_class[qclass],
            kept=kept_by[qclass],
            allocated=budgets[qclass],
            failure_reasons=list(failure_by_class[qclass]),
        )
        for qclass in ("peer", "usage", "adjacent")
    ]
    return EngagementSearchOutcome(posts=posts, failures=failures, coverage=coverage)
