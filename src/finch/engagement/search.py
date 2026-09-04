"""外部帖子搜索适配器（执行计划 Phase 2）。

提供统一的同步 ``PostSearchProvider`` 接口，当前接入 X/Twitter（复用现有 OpenCLI 只读封装），
并为 Reddit 保留占位实现（返回明确的未启用状态）。查询由兴趣配置生成，排除词与去重在
搜索完成后本地执行，最终按 ``max_posts_scanned`` 截断。
"""

from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

from finch.twitter.models import Tweet
from finch.twitter.opencli_client import OpenCliClient

from ..settings import EngagementSettings, InterestsSettings
from .models import ExternalPost, Platform


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


@dataclass
class EngagementSearchOutcome:
    """一次搜索汇总：有效帖子 + 部分失败记录。"""

    posts: list[ExternalPost]
    failures: list[PostSearchFailure]


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
    """Reddit 搜索适配器占位：尚无稳定访问方式，保留接口并返回明确的未启用状态。"""

    platform: Platform = "reddit"

    def available(self) -> bool:
        return False

    def search(self, query: str, *, limit: int) -> list[ExternalPost]:
        raise ProviderUnavailableError("Reddit search provider is not enabled")


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


def build_queries(interests: InterestsSettings) -> list[str]:
    """由稳定 + 探索兴趣生成查询词列表（每词一条查询，直接作为查询字符串）。"""
    queries: list[str] = []
    seen: set[str] = set()
    for term in [*interests.stable, *interests.exploring]:
        query = term.strip()
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            queries.append(query)
    return queries


def is_excluded(post: ExternalPost, excluded: Sequence[str]) -> bool:
    """本地排除过滤：正文或匹配主题包含排除词（大小写不敏感的子串匹配）。"""
    content = post.content.casefold()
    for term in excluded:
        token = term.strip().casefold()
        if not token:
            continue
        if token in content:
            return True
        if any(token in topic.casefold() for topic in post.matched_topics):
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
    """统一搜索入口：遍历 provider × 查询词，本地过滤、去重并按上限截断。

    部分失败处理：单个 provider 或单条查询失败不会中断其余 provider；失败会记录到返回的
    ``EngagementSearchOutcome.failures`` 中，而非静默吞掉。不可用（``available()`` 为 False）
    的 provider 也会记录一条 ``query=None`` 的失败，便于调用方感知未启用状态。
    """
    queries = build_queries(interests)

    # available() 只评估一次（它可能反映运行期健康状态，两次调用会错位回放）。
    availability = [(provider, provider.available()) for provider in providers]

    # 拍平 (provider, query) 搜索任务，保持 provider 主序 + query 次序；available()
    # 检查与失败注入保持串行，只有真正的 search 调用并行化。
    tasks: list[tuple[PostSearchProvider, str]] = []
    for provider, available in availability:
        if available:
            for query in queries:
                tasks.append((provider, query))

    def _search(task: tuple[PostSearchProvider, str]) -> list[ExternalPost] | PostSearchFailure:
        provider, query = task
        try:
            return provider.search(query, limit=engagement.max_posts_scanned)
        except Exception as exc:  # noqa: BLE001 - 单条失败不得中断整轮
            return PostSearchFailure(
                platform=provider.platform, query=query, reason=str(exc)
            )

    if len(tasks) <= 1:
        results = [_search(task) for task in tasks]
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(_search, tasks))

    # 按任务顺序回放结果，重新注入 unavailable provider 的失败。
    raw: list[ExternalPost] = []
    failures: list[PostSearchFailure] = []
    task_iter = iter(results)
    for provider, available in availability:
        if not available:
            failures.append(
                PostSearchFailure(
                    platform=provider.platform, query=None, reason="provider not enabled"
                )
            )
            continue
        for _query in queries:
            result = next(task_iter)
            if isinstance(result, PostSearchFailure):
                failures.append(result)
            else:
                raw.extend(result)

    cap = max(0, engagement.max_posts_scanned)
    kept = [post for post in raw if not is_excluded(post, interests.excluded)]
    posts = dedupe(kept, skip_ids=skip_ids)[:cap]
    return EngagementSearchOutcome(posts=posts, failures=failures)
