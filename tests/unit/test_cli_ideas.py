"""Unit tests for finch ideas commit CLI command（Skill 架构 Step 2 Task 3）。"""

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.ideas.models import (
    IdeaBoundaries,
    IdeaCandidate,
    IdeaGenerator,
    IdeaPosition,
    SourceRef,
)
from finch.settings import Paths, Settings, TwitterSettings
from finch.storage.database import Store
from finch.storage.repositories import ContentJobRepository

CORE_POINT = "make the orchestrator a deterministic graph"
COMMIT_URL = "https://github.com/acme/proj/commit/" + "a" * 40
SEARCH_TOPIC = "agent evals"
SEARCH_POST_URL = "https://x.com/acme/status/9876543210"


def _settings(tmp_path, repositories):
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"), repositories=repositories)


def _candidate() -> IdeaCandidate:
    return IdeaCandidate(
        id="idea_abc12345",
        origin="commit",
        core_point=CORE_POINT,
        reader_problem="orchestrator was hard to rerun",
        why_worth_saying="failures can now be replayed",
        author_position=IdeaPosition(
            claim="failures can now be replayed",
            decision=CORE_POINT,
            tradeoff="orchestrator was hard to rerun",
            status="proposed",
        ),
        source_refs=[
            SourceRef(type="commit", ref=COMMIT_URL, summary="feat: node-ize orchestrator")
        ],
        boundaries=IdeaBoundaries(),
        recommended_format="original",
        generator=IdeaGenerator(skill="commit-to-idea", version="1.0.0"),
    )


class _FakeCommitReader:
    def __init__(self, gh, repo):
        self.gh = gh
        self.repo = repo


class _FakeExtractor:
    def __init__(self, runner, *, settings=None, cache_path=None):
        self.runner = runner


class _FakeCommitService:
    def __init__(self, reader, extractor):
        self.reader = reader
        self.extractor = extractor

    def to_ideas(self, commits, *, repo, repo_is_private=False):
        return [_candidate()]


def _patch_cli(monkeypatch, settings, record):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "GhClient", lambda: object())
    monkeypatch.setattr(cli, "CommitReader", _FakeCommitReader)
    monkeypatch.setattr(cli, "Extractor", _FakeExtractor)
    monkeypatch.setattr(cli, "CommitService", _FakeCommitService)

    def _load_commit_details(repo, gh, *, local_dirs, since=None, workers=6):
        record.append((repo, since))
        return []

    monkeypatch.setattr(cli, "load_commit_details", _load_commit_details)


def test_ideas_commit_persists_and_outputs_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path, ["acme/proj"])
    store = Store(settings.paths.db_path)
    store.init()
    _patch_cli(monkeypatch, settings, [])

    r = CliRunner().invoke(app, ["ideas", "commit", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["id"].startswith("idea_")
    assert payload[0]["origin"] == "commit"
    assert payload[0]["core_point"] == CORE_POINT
    assert payload[0]["status"] == "proposed"
    assert payload[0]["generation_key"]

    jobs = ContentJobRepository(store).list_jobs()
    assert len(jobs) == 1
    assert jobs[0].origin == "commit"
    assert jobs[0].status.value == "proposed"
    assert jobs[0].core_message == CORE_POINT


def test_ideas_commit_non_json_output(monkeypatch, tmp_path):
    settings = _settings(tmp_path, ["acme/proj"])
    store = Store(settings.paths.db_path)
    store.init()
    _patch_cli(monkeypatch, settings, [])

    r = CliRunner().invoke(app, ["ideas", "commit"])
    assert r.exit_code == 0, r.output
    lines = [ln for ln in r.output.strip().splitlines() if ln]
    assert len(lines) == 1
    idea_id, status, core = lines[0].split("\t")
    assert idea_id.startswith("idea_")
    assert status == "proposed"
    assert core == CORE_POINT


def test_ideas_commit_defaults_repo_from_settings(monkeypatch, tmp_path):
    settings = _settings(tmp_path, ["acme/proj"])
    store = Store(settings.paths.db_path)
    store.init()
    record = []
    _patch_cli(monkeypatch, settings, record)

    r = CliRunner().invoke(app, ["ideas", "commit", "--json"])
    assert r.exit_code == 0, r.output
    assert len(record) == 1
    assert record[0][0] == "acme/proj"
    assert record[0][1] is not None  # since 被 _since_iso 转成 ISO 时间


def test_ideas_commit_requires_repo_when_unconfigured(monkeypatch, tmp_path):
    settings = _settings(tmp_path, [])
    store = Store(settings.paths.db_path)
    store.init()
    _patch_cli(monkeypatch, settings, [])

    r = CliRunner().invoke(app, ["ideas", "commit", "--json"])
    assert r.exit_code == 1
    assert "--repo" in r.output
    assert ContentJobRepository(store).list_jobs() == []


# ---- finch ideas search ----

def _search_settings(tmp_path, queries):
    return Settings(
        paths=Paths(db_path=tmp_path / "finch.db"),
        twitter=TwitterSettings(queries=queries),
    )


def _search_candidate(topic: str = SEARCH_TOPIC) -> IdeaCandidate:
    return IdeaCandidate(
        id="idea_abc12345",
        origin="search",
        core_point=f"{topic} 相关公开讨论暴露工程缺口：agent keeps failing",
        reader_problem="agent keeps failing on long context",
        why_worth_saying="公开讨论中出现的真实问题/缺口，值得写",
        author_position=IdeaPosition(
            claim=f"{topic} 相关公开讨论暴露真实工程缺口",
            decision="值得调研或回应这个缺口",
            tradeoff="不写则错失这个公共信号",
            status="proposed",
        ),
        source_refs=[
            SourceRef(type="post", ref=SEARCH_POST_URL, summary="agent keeps failing")
        ],
        boundaries=IdeaBoundaries(inferred=["agent keeps failing"]),
        recommended_format="original",
        generator=IdeaGenerator(skill="search-to-idea", version="1.0.0"),
    )


class _FakeQueryConfig:
    def __init__(self, cfg: dict) -> None:
        self.text = cfg.get("text", "")
        self.filter = cfg.get("filter", "top")


class _FakeQueryBuilder:
    def __init__(self, configs, per_query_limit=20):
        self.configs = [_FakeQueryConfig(c) for c in configs]
        self.per_query_limit = per_query_limit


class _FakeSearchService:
    def __init__(self, opencli, builder):
        self.opencli = opencli
        self.builder = builder

    def to_ideas(self, posts, *, topic):
        return [_search_candidate(topic)]


def _patch_search_cli(monkeypatch, settings, record):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "QueryBuilder", _FakeQueryBuilder)
    monkeypatch.setattr(cli, "SearchService", _FakeSearchService)

    class _FakeOpenCliClient:
        def search(self, query, *, product="top", limit=20):
            record.append((query, product, limit))
            return []

    monkeypatch.setattr(cli, "OpenCliClient", _FakeOpenCliClient)


def test_ideas_search_persists_and_outputs_json(monkeypatch, tmp_path):
    settings = _search_settings(tmp_path, [{"text": SEARCH_TOPIC, "filter": "top"}])
    store = Store(settings.paths.db_path)
    store.init()
    _patch_search_cli(monkeypatch, settings, [])

    r = CliRunner().invoke(app, ["ideas", "search", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["id"].startswith("idea_")
    assert payload[0]["origin"] == "search"
    assert SEARCH_TOPIC in payload[0]["core_point"]
    assert payload[0]["status"] == "proposed"
    assert payload[0]["generation_key"]

    jobs = ContentJobRepository(store).list_jobs()
    assert len(jobs) == 1
    assert jobs[0].origin == "search"
    assert jobs[0].status.value == "proposed"
    assert SEARCH_TOPIC in jobs[0].core_message


def test_ideas_search_non_json_output(monkeypatch, tmp_path):
    settings = _search_settings(tmp_path, [{"text": SEARCH_TOPIC, "filter": "top"}])
    store = Store(settings.paths.db_path)
    store.init()
    _patch_search_cli(monkeypatch, settings, [])

    r = CliRunner().invoke(app, ["ideas", "search"])
    assert r.exit_code == 0, r.output
    lines = [ln for ln in r.output.strip().splitlines() if ln]
    assert len(lines) == 1
    idea_id, status, core = lines[0].split("\t")
    assert idea_id.startswith("idea_")
    assert status == "proposed"
    assert core


def test_ideas_search_defaults_topic_from_settings(monkeypatch, tmp_path):
    settings = _search_settings(tmp_path, [{"text": SEARCH_TOPIC, "filter": "top"}])
    store = Store(settings.paths.db_path)
    store.init()
    record = []
    _patch_search_cli(monkeypatch, settings, record)

    r = CliRunner().invoke(app, ["ideas", "search", "--json"])
    assert r.exit_code == 0, r.output
    assert len(record) == 1
    assert record[0][0] == SEARCH_TOPIC
    assert record[0][2] == settings.twitter.per_query_limit


def test_ideas_search_explicit_topic_used(monkeypatch, tmp_path):
    settings = _search_settings(tmp_path, [{"text": SEARCH_TOPIC, "filter": "top"}])
    store = Store(settings.paths.db_path)
    store.init()
    record = []
    _patch_search_cli(monkeypatch, settings, record)

    r = CliRunner().invoke(app, ["ideas", "search", "--topic", "vector search", "--json"])
    assert r.exit_code == 0, r.output
    assert record[0][0] == "vector search"


def test_ideas_search_requires_topic_when_unconfigured(monkeypatch, tmp_path):
    settings = _search_settings(tmp_path, [])
    store = Store(settings.paths.db_path)
    store.init()
    record = []
    _patch_search_cli(monkeypatch, settings, record)

    r = CliRunner().invoke(app, ["ideas", "search", "--json"])
    assert r.exit_code == 1
    assert "--topic" in r.output
    assert record == []
    assert ContentJobRepository(store).list_jobs() == []
