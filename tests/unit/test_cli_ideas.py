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
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import ContentJobRepository

CORE_POINT = "make the orchestrator a deterministic graph"
COMMIT_URL = "https://github.com/acme/proj/commit/" + "a" * 40


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
