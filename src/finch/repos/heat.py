"""X 分享热度公式（设计 §5；P0：x_heat_v1_likes_only）。"""

from __future__ import annotations

from datetime import datetime

from finch.repos.models import (
    EFFECTIVE_FORMULA_LIKES_ONLY,
    FORMULA_VERSION_LIKES_ONLY,
    RankedRepoEntry,
    RankingSnapshot,
    RepoRecord,
    SortKey,
    TopicTag,
    TweetRecord,
)


def tweet_heat(
    tweet: TweetRecord,
    *,
    likes_weight: int = 1,
    reposts_weight: int = 2,
    quotes_weight: int = 2,
    replies_weight: int = 1,
    formula_version: str = FORMULA_VERSION_LIKES_ONLY,
) -> int | None:
    """单条推文热度；原生转推壳返回 None（不计入）；全缺失 → None。"""
    if tweet.is_native_retweet:
        return None
    if formula_version == FORMULA_VERSION_LIKES_ONLY:
        if tweet.likes is None:
            return None
        return likes_weight * tweet.likes
    # 预留 v2：全公式；缺项则只加已知项（调用方标 metrics_partial）
    total = 0
    any_known = False
    if tweet.likes is not None:
        total += likes_weight * tweet.likes
        any_known = True
    if tweet.reposts is not None:
        total += reposts_weight * tweet.reposts
        any_known = True
    if tweet.quotes is not None:
        total += quotes_weight * tweet.quotes
        any_known = True
    if tweet.replies is not None:
        total += replies_weight * tweet.replies
        any_known = True
    return total if any_known else None


def build_ranking(
    *,
    run_id: str,
    window_start: datetime,
    window_end: datetime,
    tweets: list[TweetRecord],
    repos: list[RepoRecord],
    sort: SortKey = SortKey.X_HEAT,
    topic_filter: str = "all",
    likes_weight: int = 1,
    formula_version: str = FORMULA_VERSION_LIKES_ONLY,
    effective_formula: str = EFFECTIVE_FORMULA_LIKES_ONLY,
    prior_stars: dict[str, int] | None = None,
) -> RankingSnapshot:
    """由原始记录复算榜单；相同输入 → 相同顺序。"""
    tweets_by_id = {t.tweet_id: t for t in tweets}
    entries: list[RankedRepoEntry] = []
    for repo in repos:
        if topic_filter == "agent" and repo.topic_tag != TopicTag.AGENT:
            continue
        heat_parts: list[int] = []
        partial = False
        any_metric = False
        likes_sum = 0
        likes_known = False
        latest: datetime | None = None
        for tid in repo.tweet_ids:
            tw = tweets_by_id.get(tid)
            if tw is None:
                continue
            if tw.published_at is not None and (
                tw.published_at < window_start or tw.published_at > window_end
            ):
                continue
            h = tweet_heat(tw, likes_weight=likes_weight, formula_version=formula_version)
            if h is None:
                if not tw.is_native_retweet and tw.likes is None:
                    partial = True
                continue
            heat_parts.append(h)
            any_metric = True
            if tw.likes is not None:
                likes_sum += tw.likes
                likes_known = True
            if tw.published_at is not None and (
                latest is None or tw.published_at > latest
            ):
                latest = tw.published_at
        x_heat = sum(heat_parts) if any_metric else None
        if any_metric and partial:
            pass  # keep metrics_partial
        elif not any_metric:
            partial = True
        stars_delta = None
        if prior_stars is not None and repo.stars is not None and repo.key in prior_stars:
            stars_delta = repo.stars - prior_stars[repo.key]
        entries.append(
            RankedRepoEntry(
                rank=0,
                repo_key=repo.key,
                x_heat=x_heat,
                mentions=len({tid for tid in repo.tweet_ids if tid in tweets_by_id}),
                likes_sum=likes_sum if likes_known else None,
                github_stars=repo.stars,
                github_forks=repo.forks,
                stars_delta=stars_delta,
                latest_share_at=latest,
                metrics_partial=partial and x_heat is not None,
                topic_tag=repo.topic_tag,
                description=repo.description,
                url=repo.url or f"https://github.com/{repo.key}",
                source_urls=list(repo.source_share_urls),
            )
        )

    def sort_key(e: RankedRepoEntry) -> tuple:
        # 未知热度置后；同热度：mentions desc → stars desc → key asc
        if sort == SortKey.X_HEAT:
            known = 0 if e.x_heat is not None else 1
            return (known, -(e.x_heat or 0), -e.mentions, -(e.github_stars or -1), e.repo_key)
        if sort == SortKey.MENTIONS:
            return (0, -e.mentions, -(e.github_stars or -1), e.repo_key)
        if sort == SortKey.GITHUB_STARS:
            known = 0 if e.github_stars is not None else 1
            return (known, -(e.github_stars or 0), -e.mentions, e.repo_key)
        if sort == SortKey.STARS_DELTA:
            known = 0 if e.stars_delta is not None else 1
            return (known, -(e.stars_delta or 0), -e.mentions, e.repo_key)
        # latest
        ts = e.latest_share_at.timestamp() if e.latest_share_at else float("-inf")
        known = 0 if e.latest_share_at is not None else 1
        return (known, -ts, -e.mentions, e.repo_key)

    entries.sort(key=sort_key)
    for i, e in enumerate(entries, start=1):
        e.rank = i
    return RankingSnapshot(
        run_id=run_id,
        sort=sort,
        topic_filter=topic_filter,
        formula_version=formula_version,
        effective_formula=effective_formula,
        window_start=window_start,
        window_end=window_end,
        entries=entries,
    )
