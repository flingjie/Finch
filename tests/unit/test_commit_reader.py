# tests/unit/test_commit_reader.py
from pathlib import Path

from finch.github.commit_reader import CommitReader, is_noise
from finch.github.models import CommitDetail, CommitFile


def _detail(files, message="feat: x"):
    return CommitDetail(
        sha="a" * 40, message=message, author_date="2026-09-01T00:00:00Z",
        html_url="u", parents=[], files=files, stats={},
    )


def test_is_noise_lockfile():
    d = _detail([CommitFile(filename="package-lock.json", status="modified", additions=1, deletions=1)])
    assert is_noise(d) is True


def test_is_noise_format_only():
    d = _detail([CommitFile(filename="src/a.ts", status="modified", additions=0, deletions=0, patch=" ")],
                message="chore: format with prettier")
    assert is_noise(d) is True


def test_is_noise_rename():
    d = _detail([CommitFile(filename="src/a.ts", status="renamed", additions=0, deletions=0)])
    assert is_noise(d) is True


def test_not_noise_real_change():
    d = _detail([CommitFile(filename="src/graph/runtime.ts", status="modified", additions=10, deletions=4,
                            patch="+export function run")],
                message="feat: node-ize orchestrator")
    assert is_noise(d) is False


def test_sync_uses_cursor_and_advances(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = {}

    class FakeGh:
        def list_commits(self, repo, since, per_page=100):
            calls["since"] = since
            return []

    r = CommitReader(FakeGh(), repo="flingjie/FDE-Gym")
    # 首次无游标 → 用显式 since
    r.sync(since="2026-09-01T00:00:00Z")
    assert calls["since"] == "2026-09-01T00:00:00Z"
    # 游标已推进，存在 cursor 文件
    assert Path("var/cache/github_sync_state.json").exists()
