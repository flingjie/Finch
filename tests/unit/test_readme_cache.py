"""Unit tests for GitHub README cache + repo normalization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from finch.github.gh_client import GhClient, GhError
from finch.github.readme_cache import ReadmeCache, load_personal_repo_context, normalize_repo
from finch.settings import Settings
from finch.storage.workspace import Workspace


class _FakeGh(GhClient):
    def __init__(self, readmes: dict[str, str] | None = None, fail: tuple[str, ...] = ()):
        self._readmes = dict(readmes or {})
        self._fail = set(fail)
        self.calls: list[str] = []

    def readme(self, repo: str) -> str:
        self.calls.append(repo)
        if repo in self._fail:
            raise GhError(f"no README for {repo}")
        return self._readmes.get(repo, "")


def test_normalize_repo_variants():
    assert normalize_repo("flingjie/Finch") == "flingjie/finch"
    assert normalize_repo("https://github.com/flingjie/Finch") == "flingjie/finch"
    assert normalize_repo("github.com/FlingJie/Finch.git") == "flingjie/finch"
    assert normalize_repo("https://github.com/owner/repo/tree/main/README.md") == "owner/repo"
    assert normalize_repo("  Owner/Repo  ") == "owner/repo"
    assert normalize_repo("") == ""
    assert normalize_repo("justonename") == ""  # 无 owner/name → 空


def test_readme_cache_miss_fetches_and_hits(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    gh = _FakeGh(readmes={"owner/repo": "# Hello"})
    cache = ReadmeCache(ws, gh)

    first = cache.read("https://github.com/owner/repo")
    assert first is not None
    assert first.repo == "owner/repo"
    assert first.content == "# Hello"
    assert gh.calls == ["owner/repo"]

    # 再次 read：命中缓存，不再网络读取。
    second = cache.read("owner/REPO")
    assert second is not None
    assert second.content == "# Hello"
    assert gh.calls == ["owner/repo"]


def test_readme_cache_force_refresh(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    gh = _FakeGh(readmes={"owner/repo": "# v1"})
    cache = ReadmeCache(ws, gh)
    cache.read("owner/repo")

    gh._readmes["owner/repo"] = "# v2"
    fresh = cache.read("owner/repo", force=True)
    assert fresh is not None
    assert fresh.content == "# v2"
    assert gh.calls == ["owner/repo", "owner/repo"]


def test_readme_cache_failure_returns_none(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    gh = _FakeGh(fail=("owner/repo",))
    cache = ReadmeCache(ws, gh)
    assert cache.read("owner/repo") is None


def test_readme_cache_expiry_refetches(tmp_path: Path):
    from finch.github.readme_cache import _ReadmeMeta

    ws = Workspace(tmp_path)
    ws.ensure()
    gh = _FakeGh(readmes={"owner/repo": "# v1"})
    cache = ReadmeCache(ws, gh, ttl_hours=1)
    cache.read("owner/repo")

    # 手动把 meta 的 fetched_at 改旧，模拟 TTL 过期。
    meta_path = ws.dir("cache/readmes") / "owner_repo.meta.yaml"
    stale = _ReadmeMeta(
        repo="owner/repo",
        fetched_at=datetime.now(UTC) - timedelta(hours=2),
        version="x",
    )
    ws.write_yaml(meta_path, stale)

    gh._readmes["owner/repo"] = "# v2"
    fresh = cache.read("owner/repo")
    assert fresh is not None
    assert fresh.content == "# v2"
    assert gh.calls == ["owner/repo", "owner/repo"]


def test_load_personal_repo_context_uses_repositories_not_discovery_limit(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    gh = _FakeGh(readmes={"flingjie/finch": "# Finch", "flingjie/fde-gym": "# Gym"})
    settings = Settings(repositories=["flingjie/Finch", "flingjie/FDE-Gym"])

    out = load_personal_repo_context(settings, ws, gh)
    assert set(out.keys()) == {"flingjie/finch", "flingjie/fde-gym"}
    assert out["flingjie/finch"].content == "# Finch"

    # 二次加载命中缓存，不再网络读取。
    load_personal_repo_context(settings, ws, gh)
    assert sorted(gh.calls) == ["flingjie/fde-gym", "flingjie/finch"]
