from datetime import UTC, datetime, timedelta

from finch.github.gh_client import GhClient
from finch.github.ingestion import Ingestor
from finch.github.models import CommitDetail, CommitFile, CommitSummary
from finch.settings import DailyBudget, Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import CommitIngestionRepository, RepoCursorRepository

REPO = "flingjie/FDE-Gym"
NOW = datetime(2026, 9, 5, tzinfo=UTC)


def _detail(sha, files, message="feat: x"):
    return CommitDetail(
        sha=sha, message=message, author_date=NOW - timedelta(hours=1),
        html_url="u", parents=[], files=files, stats={},
    )


class _FakeGh:
    def __init__(self, summaries, details_by_sha):
        self.summaries = summaries
        self.details_by_sha = details_by_sha

    def list_commits(self, repo, since=None, per_page=100):
        return self.summaries

    def commit_detail(self, repo, sha):
        return self.details_by_sha[sha]


def _settings(tmp_path):
    return Settings(
        repositories=[REPO],
        paths=Paths(db_path=tmp_path / "db.sqlite"),
        daily_budget=DailyBudget(max_detail_fetches=2, max_change_groups=1),
    )


def test_ingest_fetches_budget_and_defers_rest(tmp_path, monkeypatch):
    monkeypatch.setattr("finch.github.ingestion.find_local_clone", lambda *a: None)
    shas = [f"{i:040d}" for i in range(5)]
    summaries = [
        CommitSummary(sha=s, message="fix: thing", author_date=NOW - timedelta(hours=1),
                      html_url="u", parents=[])
        for s in shas
    ]
    gh = _FakeGh(summaries, {
        s: _detail(s, [CommitFile(filename=f"src/{i}.py", status="modified",
                                   additions=1, deletions=0)])
        for i, s in enumerate(shas)
    })
    store = Store(tmp_path / "db.sqlite")
    store.init()
    ing = Ingestor(gh, _settings(tmp_path), CommitIngestionRepository(store),
                   RepoCursorRepository(store))
    groups = ing.ingest([REPO], existing_topics=set())

    # 全部 5 个 commit 都进入 ledger（发现），但只有 max_detail_fetches=2 被 fetch 成 grouped
    repo = CommitIngestionRepository(store)
    assert len(repo.known_shas(REPO)) == 5
    assert len(repo.list_pending(REPO)) == 3
    # 仅 1 个 group 进入本轮提取预算
    assert sum(len(g) for g in groups[REPO]) == 1


def test_ingest_skips_noise(tmp_path, monkeypatch):
    monkeypatch.setattr("finch.github.ingestion.find_local_clone", lambda *a: None)
    gh = _FakeGh(
        [CommitSummary(sha="n" * 40, message="chore: format",
                       author_date=NOW - timedelta(hours=1), html_url="u", parents=[])],
        {"n" * 40: _detail("n" * 40,
                           [CommitFile(filename="package-lock.json", status="modified",
                                       additions=1, deletions=1)],
                           message="chore: format")},
    )
    store = Store(tmp_path / "db.sqlite")
    store.init()
    ing = Ingestor(gh, _settings(tmp_path), CommitIngestionRepository(store),
                   RepoCursorRepository(store))
    ing.ingest([REPO], existing_topics=set())
    assert CommitIngestionRepository(store).list_pending(REPO) == []


def test_ingest_incremental_stops_at_known_sha(tmp_path, monkeypatch):
    monkeypatch.setattr("finch.github.ingestion.find_local_clone", lambda *a: None)
    known_sha = "k" * 40
    new2 = "2" * 40
    new1 = "1" * 40
    old_behind = "0" * 40  # 比 known_sha 更旧，走到 known_sha 即停，不应被摄取
    summaries = [
        CommitSummary(sha=new2, message="fix: newer", author_date=NOW - timedelta(hours=1),
                      html_url="u", parents=[]),
        CommitSummary(sha=new1, message="fix: older", author_date=NOW - timedelta(hours=2),
                      html_url="u", parents=[]),
        CommitSummary(sha=known_sha, message="feat: known", author_date=NOW - timedelta(days=1),
                      html_url="u", parents=[]),
        CommitSummary(sha=old_behind, message="feat: behind", author_date=NOW - timedelta(days=2),
                      html_url="u", parents=[]),
    ]

    class FakeGh:
        def list_commits_newest_first(self, repo, *, per_page=100, max_commits=200):
            return summaries

        def commit_detail(self, repo, sha):
            return _detail(
                sha,
                [CommitFile(filename=f"src/{sha[:2]}.py", status="modified",
                            additions=1, deletions=0)],
            )

    store = Store(tmp_path / "db.sqlite")
    store.init()
    ing_repo = CommitIngestionRepository(store)
    ing_repo.upsert_pending(REPO, [
        CommitSummary(sha=known_sha, message="feat: known",
                      author_date=NOW - timedelta(days=1), html_url="u", parents=[]),
    ])
    ing = Ingestor(FakeGh(), _settings(tmp_path), ing_repo, RepoCursorRepository(store))
    ing.ingest([REPO], existing_topics=set())

    shas = ing_repo.known_shas(REPO)
    assert new1 in shas and new2 in shas
    assert old_behind not in shas  # 走到 known_sha 即停，不摄取更旧 commit


def test_list_commits_newest_first_stops_on_short_page(monkeypatch):
    calls = []

    def fake_gh_json(argv, timeout=None):
        calls.append(argv)
        return [
            {"sha": "1" * 40, "commit": {"message": "m1", "author": {"date": "2026-09-01T00:00:00Z"}},
             "html_url": "u", "parents": []},
            {"sha": "2" * 40, "commit": {"message": "m2", "author": {"date": "2026-09-01T00:00:00Z"}},
             "html_url": "u", "parents": []},
        ]

    gh = GhClient()
    monkeypatch.setattr(gh, "_gh_json", fake_gh_json)
    out = gh.list_commits_newest_first("r", per_page=100, max_commits=200)
    assert [s.sha for s in out] == ["1" * 40, "2" * 40]
    assert len(calls) == 1  # 短页 → 不再请求下一页
