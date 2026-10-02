"""repo-discovery service: collect, dedupe, resume, failed semantics."""

from datetime import UTC, datetime

from finch.repos.collect import merge_tweet_records, tweet_to_record
from finch.repos.models import QueryStatus, RunStatus, TweetRecord
from finch.repos.repository import RepoDiscoveryRepository
from finch.repos.service import RepoDiscoveryService
from finch.storage.workspace import Workspace
from finch.twitter.models import Tweet


class FakeSearcher:
    def __init__(self, batches: dict[str, list[Tweet]] | None = None, *, fail: bool = False):
        self.batches = batches or {}
        self.fail = fail
        self.calls: list[tuple[str, str, int]] = []

    def search(self, query: str, *, product: str = "live", limit: int = 20) -> list[Tweet]:
        self.calls.append((query, product, limit))
        if self.fail:
            raise RuntimeError("twitter down")
        return list(self.batches.get(query, []))


def _tweet(
    tid: str,
    text: str,
    *,
    likes: int = 1,
    created: str = "Wed Oct 01 12:00:00 +0000 2026",
) -> Tweet:
    return Tweet(
        id=tid,
        author="alice",
        text=text,
        created_at=created,
        likes=likes,
        views=10,
        url=f"https://x.com/alice/status/{tid}",
    )


def test_tweet_to_record_extracts_repos_and_null_engagement():
    t = _tweet("1", "see https://github.com/acme/widget and https://github.com/acme/other")
    rec = tweet_to_record(t, query_id="q0", expand_shorts=False)
    assert set(rec.repo_refs) == {"acme/widget", "acme/other"}
    assert rec.likes == 1
    assert rec.reposts is None
    assert rec.quotes is None
    assert rec.replies is None


def test_merge_tweet_replaces_metrics_not_sum():
    a = TweetRecord(tweet_id="1", likes=1, query_refs=["q0"], repo_refs=["a/b"])
    b = TweetRecord(tweet_id="1", likes=5, query_refs=["q1"], repo_refs=["a/b"])
    m = merge_tweet_records(a, b)
    assert m.likes == 5
    assert m.query_refs == ["q0", "q1"]


def test_discover_collects_all_repos_no_top_n(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    q = "github.com has:links"
    searcher = FakeSearcher(
        {
            q: [
                _tweet("1", "https://github.com/a/one", likes=2),
                _tweet("2", "https://github.com/b/two", likes=9),
                _tweet("3", "https://github.com/c/three https://github.com/d/four", likes=1),
            ]
        }
    )
    svc = RepoDiscoveryService(
        RepoDiscoveryRepository(ws),
        searcher,
        gh=None,
        clock=lambda: datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
    )
    run = svc.discover(
        queries=[q],
        lookback_hours=48,
        fetch_github=False,
        per_query_limit=100,
        agent_keywords=["agent"],
    )
    assert run.status == RunStatus.COMPLETED
    assert run.repo_count == 4
    repos = RepoDiscoveryRepository(ws).list_repos(run.run_id)
    assert {r.key for r in repos} == {"a/one", "b/two", "c/three", "d/four"}
    # empty description still present
    assert all(r.key for r in repos)


def test_discover_duplicate_tweet_id_once(tmp_path):
    ws = Workspace(tmp_path)
    q1, q2 = "q-alpha", "q-beta"
    tw = _tweet("same", "https://github.com/a/one", likes=4)
    searcher = FakeSearcher({q1: [tw], q2: [tw]})
    svc = RepoDiscoveryService(
        RepoDiscoveryRepository(ws),
        searcher,
        clock=lambda: datetime(2026, 10, 2, tzinfo=UTC),
    )
    run = svc.discover(queries=[q1, q2], lookback_hours=48, fetch_github=False)
    tweets = RepoDiscoveryRepository(ws).list_tweets(run.run_id)
    assert len(tweets) == 1
    assert set(tweets[0].query_refs) == {"q0", "q1"}


def test_discover_all_queries_fail_is_failed_not_empty(tmp_path):
    ws = Workspace(tmp_path)
    svc = RepoDiscoveryService(
        RepoDiscoveryRepository(ws),
        FakeSearcher(fail=True),
        clock=lambda: datetime(2026, 10, 2, tzinfo=UTC),
    )
    run = svc.discover(queries=["anything"], lookback_hours=24, fetch_github=False)
    assert run.status == RunStatus.FAILED
    assert run.repo_count == 0


def test_discover_hit_limit_marks_partial_and_resume(tmp_path):
    ws = Workspace(tmp_path)
    q = "github.com"
    # Exactly at limit → truncated
    batch = [
        _tweet(str(i), f"https://github.com/org/repo{i}", likes=i)
        for i in range(5)
    ]
    searcher = FakeSearcher({q: batch})
    clock_t = {"t": datetime(2026, 10, 2, tzinfo=UTC)}

    def clock():
        return clock_t["t"]

    svc = RepoDiscoveryService(
        RepoDiscoveryRepository(ws), searcher, clock=clock
    )
    run = svc.discover(
        queries=[q], lookback_hours=48, fetch_github=False, per_query_limit=5
    )
    assert run.status == RunStatus.PARTIAL
    assert run.queries[0].status == QueryStatus.TRUNCATED
    # Resume with completed query should stay partial unless we re-mark;
    # mark query completed manually then resume to verify lock/path works.
    run.queries[0].status = QueryStatus.COMPLETED
    RepoDiscoveryRepository(ws).save_run(run)
    # New searcher returns nothing more — resume completes remaining
    searcher2 = FakeSearcher({q: []})
    svc2 = RepoDiscoveryService(
        RepoDiscoveryRepository(ws), searcher2, clock=clock
    )
    run2 = svc2.discover(
        queries=[q], resume_run_id=run.run_id, fetch_github=False, per_query_limit=5
    )
    assert run2.run_id == run.run_id
    assert run2.repo_count == 5


def test_list_page_does_not_truncate_total(tmp_path):
    ws = Workspace(tmp_path)
    q = "github.com"
    batch = [
        _tweet(str(i), f"https://github.com/org/r{i}", likes=i + 1)
        for i in range(12)
    ]
    svc = RepoDiscoveryService(
        RepoDiscoveryRepository(ws),
        FakeSearcher({q: batch}),
        clock=lambda: datetime(2026, 10, 2, tzinfo=UTC),
    )
    run = svc.discover(queries=[q], lookback_hours=48, fetch_github=False)
    snap, page, total = svc.list_page(run.run_id, page=1, page_size=5)
    assert total == 12
    assert len(page) == 5
    assert page[0].rank == 1
    _, page2, _ = svc.list_page(run.run_id, page=3, page_size=5)
    assert len(page2) == 2
    assert page2[0].rank == 11
