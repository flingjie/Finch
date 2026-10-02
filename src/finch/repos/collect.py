"""从 Twitter 搜索采集推文并提取 GitHub 仓库链接。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from finch.repos.models import TweetRecord
from finch.repos.urls import expand_short_url, extract_github_links
from finch.twitter.models import Tweet


class TweetSearcher(Protocol):
    def search(
        self, query: str, *, product: str = "live", limit: int = 20
    ) -> list[Tweet]: ...


def tweet_to_record(
    tweet: Tweet,
    *,
    query_id: str,
    expand_shorts: bool = True,
) -> TweetRecord:
    """Tweet → TweetRecord；likes 有值则保留，reposts/quotes/replies 恒为 None（v1）。"""
    published = tweet.published_at()
    entity_urls: list[str] = []
    # opencli 无 entities；从 text 抽。card 偶发含 url。
    if isinstance(tweet.card, dict):
        for key in ("url", "expanded_url", "vanity_url"):
            val = tweet.card.get(key)
            if isinstance(val, str) and val:
                entity_urls.append(val)
    links = extract_github_links(tweet.text, entity_urls=entity_urls)
    expanded: list[str] = []
    repo_refs: list[str] = []
    for link in links:
        if link.kind == "short" and expand_shorts:
            resolved = expand_short_url(link.raw_url)
            if resolved:
                from finch.repos.urls import classify_github_url

                link = classify_github_url(resolved)
        if link.kind == "repo":
            repo_refs.append(link.repo_key)
            expanded.append(link.canonical_url)
        else:
            expanded.append(link.raw_url)
    # 去重 repo_refs 保序
    seen: set[str] = set()
    uniq_repos: list[str] = []
    for r in repo_refs:
        if r not in seen:
            seen.add(r)
            uniq_repos.append(r)
    text_l = (tweet.text or "").lower()
    is_rt = text_l.startswith("rt @") or " retweeted " in text_l
    return TweetRecord(
        tweet_id=str(tweet.id),
        url=tweet.url or "",
        author=tweet.author or "",
        published_at=published,
        text=tweet.text or "",
        expanded_urls=expanded,
        likes=int(tweet.likes) if tweet.likes is not None else None,
        reposts=None,
        quotes=None,
        replies=None,
        views=int(tweet.views) if tweet.views is not None else None,
        metrics_fetched_at=datetime.now(UTC),
        query_refs=[query_id],
        is_native_retweet=is_rt,
        repo_refs=uniq_repos,
    )


def merge_tweet_records(existing: TweetRecord, incoming: TweetRecord) -> TweetRecord:
    """同 tweet_id：合并 query_refs / repo_refs；指标取最新快照（替换，不累加）。"""
    queries = list(dict.fromkeys([*existing.query_refs, *incoming.query_refs]))
    repos = list(dict.fromkeys([*existing.repo_refs, *incoming.repo_refs]))
    urls = list(dict.fromkeys([*existing.expanded_urls, *incoming.expanded_urls]))
    return incoming.model_copy(
        update={
            "query_refs": queries,
            "repo_refs": repos,
            "expanded_urls": urls,
            # 指标以 incoming 为准（刷新快照）
        }
    )


def filter_tweets_in_window(
    tweets: list[TweetRecord],
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[TweetRecord]:
    """保留发布时间在窗口内的推文；无时间戳的推文保留（宁可多收录）。"""
    out: list[TweetRecord] = []
    for t in tweets:
        if t.published_at is None:
            out.append(t)
            continue
        ts = t.published_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if window_start <= ts <= window_end:
            out.append(t)
    return out
