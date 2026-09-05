from datetime import UTC, datetime

from finch.github.models import CommitDetail, CommitFile, CommitSummary
from finch.storage.database import Store
from finch.storage.repositories import (
    CommitIngestionRecord,
    CommitIngestionRepository,
    RepoCursorRepository,
)

REPO = "flingjie/FDE-Gym"


def _summary(sha, message="feat: x", when=None):
    return CommitSummary(
        sha=sha, message=message,
        author_date=when or datetime(2026, 9, 1, tzinfo=UTC),
        html_url="u", parents=[],
    )


def _detail(sha):
    return CommitDetail(
        sha=sha, message="feat: x", author_date=datetime(2026, 9, 1, tzinfo=UTC),
        html_url="u", parents=[],
        files=[CommitFile(filename="src/a.py", status="modified", additions=5, deletions=1)],
        stats={},
    )


def test_upsert_pending_and_known_shas(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = CommitIngestionRepository(store)
    repo.upsert_pending(REPO, [_summary("a" * 40), _summary("b" * 40)])
    assert repo.known_shas(REPO) == {"a" * 40, "b" * 40}
    # 幂等：同 sha 再次 upsert 不产生重复行
    repo.upsert_pending(REPO, [_summary("a" * 40)])
    assert repo.known_shas(REPO) == {"a" * 40, "b" * 40}


def test_store_detail_upgrades_to_grouped(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = CommitIngestionRepository(store)
    repo.upsert_pending(REPO, [_summary("a" * 40)])
    repo.store_detail(REPO, _detail("a" * 40))
    grouped = repo.list_grouped(REPO)
    assert [r.sha for r in grouped] == ["a" * 40]
    assert grouped[0].status == "grouped"
    parsed = CommitDetail.model_validate_json(grouped[0].payload_json)
    assert [f.filename for f in parsed.files] == ["src/a.py"]


def test_mark_extracted_and_skipped(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = CommitIngestionRepository(store)
    repo.upsert_pending(REPO, [_summary("a" * 40), _summary("b" * 40)])
    repo.mark_extracted(REPO, ["a" * 40])
    repo.mark_skipped(REPO, "b" * 40)
    assert repo.list_grouped(REPO) == []
    assert repo.list_pending(REPO) == []


def test_mark_failed_retries_then_skips(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = CommitIngestionRepository(store)
    repo.upsert_pending(REPO, [_summary("a" * 40)])
    repo.store_detail(REPO, _detail("a" * 40))
    for _ in range(2):
        repo.mark_failed(REPO, ["a" * 40], max_retries=3)
    failed = repo.list_grouped(REPO)[0]
    assert isinstance(failed, CommitIngestionRecord)
    assert failed.status == "failed"
    repo.mark_failed(REPO, ["a" * 40], max_retries=3)  # 第 3 次 → skipped
    assert repo.list_grouped(REPO) == []


def test_cursor_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = RepoCursorRepository(store)
    assert repo.get_sha(REPO) is None
    repo.advance(REPO, "a" * 40)
    assert repo.get_sha(REPO) == "a" * 40
    repo.advance(REPO, "b" * 40)
    assert repo.get_sha(REPO) == "b" * 40
