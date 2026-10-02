"""repo-discovery 编排：discover / resume / rank / export。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from finch.github.gh_client import GhClient, GhError
from finch.repos.collect import (
    TweetSearcher,
    filter_tweets_in_window,
    merge_tweet_records,
    tweet_to_record,
)
from finch.repos.heat import build_ranking
from finch.repos.models import (
    EFFECTIVE_FORMULA_LIKES_ONLY,
    FORMULA_VERSION_LIKES_ONLY,
    DiscoveryRun,
    QueryProgress,
    QueryStatus,
    RepoRecord,
    RunStatus,
    SortKey,
)
from finch.repos.repository import RepoDiscoveryRepository
from finch.repos.tagging import tag_repo


def _fingerprint(queries: list[str], lookback_hours: int) -> str:
    raw = f"{lookback_hours}|" + "|".join(queries)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _window(
    *, lookback_hours: int, timezone: str, now: datetime | None = None
) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone)
    end_local = (now or datetime.now(tz)).astimezone(tz)
    start_local = end_local - timedelta(hours=lookback_hours)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def make_run_id(window_end: datetime) -> str:
    return f"repos_{window_end.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"


class RepoDiscoveryService:
    """确定性采集 + 排名；不调用 LLM。"""

    def __init__(
        self,
        repo: RepoDiscoveryRepository,
        searcher: TweetSearcher,
        *,
        gh: GhClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = repo
        self.searcher = searcher
        self.gh = gh
        self._clock = clock or (lambda: datetime.now(UTC))

    def discover(
        self,
        *,
        queries: list[str],
        lookback_hours: int = 24,
        timezone: str = "Asia/Shanghai",
        resume_run_id: str | None = None,
        per_query_limit: int = 100,
        per_slice_budget_seconds: int = 600,
        agent_keywords: list[str] | None = None,
        agent_tags_enabled: bool = True,
        likes_weight: int = 1,
        fetch_github: bool = True,
        expand_shorts: bool = False,
    ) -> DiscoveryRun:
        """全量采集窗口内可获取结果；预算耗尽 → partial。"""
        if resume_run_id:
            run = self.repo.get_run(resume_run_id)
            if run is None:
                raise KeyError(resume_run_id)
            window_start, window_end = run.window_start, run.window_end
        else:
            window_start, window_end = _window(
                lookback_hours=lookback_hours, timezone=timezone, now=self._clock()
            )
            run_id = make_run_id(window_end)
            run = DiscoveryRun(
                run_id=run_id,
                window_start=window_start,
                window_end=window_end,
                timezone=timezone,
                config_fingerprint=_fingerprint(queries, lookback_hours),
                status=RunStatus.RUNNING,
                queries=[
                    QueryProgress(query_id=f"q{i}", text=q)
                    for i, q in enumerate(queries)
                ],
                formula_version=FORMULA_VERSION_LIKES_ONLY,
                effective_formula=EFFECTIVE_FORMULA_LIKES_ONLY,
            )
            self.repo.save_run(run)

        if not self.repo.try_acquire_lock(run.run_id):
            run.errors.append("lock held by another process")
            run.status = RunStatus.PARTIAL
            self.repo.save_run(run)
            return run

        started = self._clock()
        try:
            any_success = False
            all_failed = True
            for qp in run.queries:
                if qp.status == QueryStatus.COMPLETED:
                    continue
                if (self._clock() - started).total_seconds() >= per_slice_budget_seconds:
                    run.status = RunStatus.PARTIAL
                    run.errors.append("per_slice_budget_seconds exhausted")
                    break
                qp.status = QueryStatus.RUNNING
                self.repo.save_run(run)
                try:
                    # Prefer live (Latest); opencli maps product=live.
                    raw = self.searcher.search(
                        qp.text, product="live", limit=per_query_limit
                    )
                    all_failed = False
                    any_success = True
                    records = [
                        tweet_to_record(
                            t, query_id=qp.query_id, expand_shorts=expand_shorts
                        )
                        for t in raw
                    ]
                    in_window = filter_tweets_in_window(
                        records, window_start=window_start, window_end=window_end
                    )
                    for rec in in_window:
                        existing = self.repo.get_tweet(run.run_id, rec.tweet_id)
                        if existing is not None:
                            rec = merge_tweet_records(existing, rec)
                        self.repo.save_tweet(run.run_id, rec)
                    qp.tweets_seen += len(in_window)
                    # opencli 无游标：若返回条数触达 limit，记 truncated。
                    if len(raw) >= per_query_limit:
                        qp.status = QueryStatus.TRUNCATED
                        qp.coverage_note = (
                            f"hit per_query_limit={per_query_limit}; "
                            "no cursor — mark partial for resume/time-slice"
                        )
                        run.status = RunStatus.PARTIAL
                    else:
                        qp.status = QueryStatus.COMPLETED
                except Exception as exc:  # noqa: BLE001 — fail-soft per query
                    qp.status = QueryStatus.FAILED
                    qp.error = str(exc)
                    run.errors.append(f"{qp.query_id}: {exc}")

            self._upsert_repos_from_tweets(
                run,
                agent_keywords=agent_keywords or [],
                agent_tags_enabled=agent_tags_enabled,
                fetch_github=fetch_github,
            )
            tweets = self.repo.list_tweets(run.run_id)
            repos = self.repo.list_repos(run.run_id)
            run.tweet_count = len(tweets)
            run.repo_count = len(repos)

            if all_failed and not any_success and run.queries:
                run.status = RunStatus.FAILED
            elif run.status != RunStatus.PARTIAL:
                if any(q.status == QueryStatus.TRUNCATED for q in run.queries):
                    run.status = RunStatus.PARTIAL
                elif any(q.status == QueryStatus.FAILED for q in run.queries) and any(
                    q.status == QueryStatus.COMPLETED for q in run.queries
                ):
                    run.status = RunStatus.PARTIAL
                elif all(q.status == QueryStatus.FAILED for q in run.queries):
                    run.status = RunStatus.FAILED
                else:
                    run.status = RunStatus.COMPLETED
                    run.completed_at = self._clock()

            ranking = build_ranking(
                run_id=run.run_id,
                window_start=window_start,
                window_end=window_end,
                tweets=tweets,
                repos=repos,
                sort=SortKey.X_HEAT,
                likes_weight=likes_weight,
            )
            self.repo.save_ranking(ranking)
            self.repo.save_run(run)
            return run
        finally:
            self.repo.release_lock(run.run_id)

    def _upsert_repos_from_tweets(
        self,
        run: DiscoveryRun,
        *,
        agent_keywords: list[str],
        agent_tags_enabled: bool,
        fetch_github: bool,
    ) -> None:
        tweets = self.repo.list_tweets(run.run_id)
        by_key: dict[str, RepoRecord] = {
            r.key: r for r in self.repo.list_repos(run.run_id)
        }
        for tw in tweets:
            for key in tw.repo_refs:
                owner, _, name = key.partition("/")
                if not owner or not name:
                    continue
                repo = by_key.get(key)
                if repo is None:
                    repo = RepoRecord(
                        key=key,
                        owner=owner,
                        name=name,
                        url=f"https://github.com/{key}",
                    )
                    by_key[key] = repo
                if tw.tweet_id not in repo.tweet_ids:
                    repo.tweet_ids.append(tw.tweet_id)
                if tw.url and tw.url not in repo.source_share_urls:
                    repo.source_share_urls.append(tw.url)

        for repo in by_key.values():
            if fetch_github and self.gh is not None and not repo.metadata_ok:
                try:
                    meta = self.gh.public_repo(f"{repo.owner}/{repo.name}")
                    repo.description = meta.description
                    repo.stars = getattr(meta, "stargazer_count", None)
                    repo.forks = getattr(meta, "fork_count", None)
                    repo.topics = list(getattr(meta, "topics", []) or [])
                    repo.github_id = getattr(meta, "github_id", None)
                    repo.url = meta.url
                    repo.metadata_fetched_at = self._clock()
                    repo.metadata_ok = True
                except (GhError, Exception) as exc:  # noqa: BLE001
                    repo.metadata_ok = False
                    run.errors.append(f"github {repo.key}: {exc}")
            related = [t for t in tweets if t.tweet_id in repo.tweet_ids]
            repo.topic_tag = tag_repo(
                repo,
                related,
                keywords=agent_keywords,
                enabled=agent_tags_enabled,
            )
            self.repo.save_repo(run.run_id, repo)

    def list_page(
        self,
        run_id: str,
        *,
        sort: SortKey = SortKey.X_HEAT,
        topic: str = "all",
        page: int = 1,
        page_size: int = 50,
        likes_weight: int = 1,
    ):
        run = self.repo.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        tweets = self.repo.list_tweets(run_id)
        repos = self.repo.list_repos(run_id)
        snap = build_ranking(
            run_id=run_id,
            window_start=run.window_start,
            window_end=run.window_end,
            tweets=tweets,
            repos=repos,
            sort=sort,
            topic_filter=topic,
            likes_weight=likes_weight,
            formula_version=run.formula_version,
            effective_formula=run.effective_formula,
        )
        self.repo.save_ranking(snap)
        total = len(snap.entries)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        return snap, snap.entries[start:end], total
