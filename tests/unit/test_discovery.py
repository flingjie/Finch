from datetime import UTC, datetime, timedelta

from finch.github.discovery import RepoDiscovery, resolve_repositories
from finch.github.models import RepoSummary
from finch.settings import Settings


class FakeGh:
    def __init__(self, repos):
        self.repos = repos

    def list_user_repos(self):
        return self.repos


def _repo(
    name,
    pushed_at=None,
    *,
    size=1,
    private=False,
    fork=False,
    archived=False,
    disabled=False,
):
    return RepoSummary(
        name_with_owner=name,
        pushed_at=pushed_at,
        size=size,
        is_private=private,
        is_fork=fork,
        archived=archived,
        disabled=disabled,
    )


def test_recently_pushed_filters_and_orders():
    now = datetime.now(UTC)
    old = now - timedelta(hours=48)
    fresh_a = now - timedelta(hours=1)
    fresh_b = now - timedelta(hours=3)
    gh = FakeGh(
        [
            _repo("o/old", old),
            _repo("o/fresh-a", fresh_a),
            _repo("o/fresh-b", fresh_b),
            _repo("o/private", fresh_a, private=True),
            _repo("o/fork", fresh_a, fork=True),
            _repo("o/archived", fresh_a, archived=True),
            _repo("o/empty", fresh_a, size=0),
        ]
    )
    names = RepoDiscovery(gh).recently_pushed(lookback_hours=24, limit=10)
    assert names == ["o/fresh-a", "o/fresh-b"]


def test_recently_pushed_respects_limit():
    now = datetime.now(UTC)
    gh = FakeGh([_repo(f"o/r{i}", now - timedelta(minutes=i)) for i in range(5)])
    names = RepoDiscovery(gh).recently_pushed(lookback_hours=24, limit=2)
    assert names == ["o/r0", "o/r1"]


def test_resolve_repositories_prefers_explicit_list():
    gh = FakeGh([])
    settings = Settings(repositories=["a/b"])
    assert resolve_repositories(settings, gh) == ["a/b"]


def test_resolve_repositories_disables_discovery():
    gh = FakeGh([_repo("o/r", datetime.now(UTC))])
    settings = Settings(
        repository_discovery={"enabled": False, "lookback_hours": 24, "max_repos": 10}
    )
    assert resolve_repositories(settings, gh) == []
