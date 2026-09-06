"""Unit tests for finch idea CLI command."""

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.checkers.aggregate import AggregateOutcome
from finch.content.voice import VoiceProfile
from finch.idea.models import AssessIdeaOutput
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import ContentJobRepository, DraftRepository


def _settings(tmp_path):
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


def _ready_assessment():
    return AssessIdeaOutput(
        status="ready",
        core_point="Graph 的价值是恢复与重放",
        matched_evidence_ids=[],
        reader_problem="很多人把 Graph 理解成流程可视化",
        audience="构建生产 Agent 的工程师",
        understand="Graph 的核心价值是恢复与重放",
        claim="Graph 的主要价值是恢复与重放",
        decision="用可恢复性评价 Graph",
        tradeoff="需要持久化状态",
    )


def _fake_pass_critic(runner, draft, job, cards, max_rounds, **kw):
    return (AggregateOutcome.PASS, [], draft)


def test_idea_ready_persists_and_outputs_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_voice_profile", lambda _p: VoiceProfile())
    monkeypatch.setattr(cli, "assess_idea", lambda *a, **k: _ready_assessment())
    monkeypatch.setattr(cli, "write_idea", lambda *a, **k: "样稿正文")
    monkeypatch.setattr(cli, "run_idea_critic", _fake_pass_critic)

    r = CliRunner().invoke(app, ["idea", "我觉得 Agent Graph 的价值是恢复", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["status"] == "ready"
    assert payload["draft_id"].startswith("draft_idea_")
    assert payload["sample"] == "样稿正文"

    draft = DraftRepository(store).get_draft(payload["draft_id"])
    assert draft is not None
    assert draft.run_id == "idea"
    jobs = ContentJobRepository(store).list_jobs()
    assert len(jobs) == 1
    assert jobs[0].author_position is not None
    assert jobs[0].author_position.confirmed is True


def test_idea_not_ready_no_persist(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_voice_profile", lambda _p: VoiceProfile())
    monkeypatch.setattr(
        cli, "assess_idea",
        lambda *a, **k: AssessIdeaOutput(status="not_ready", reason_code="TOO_BROAD",
                                         reason="没有具体问题"),
    )

    r = CliRunner().invoke(app, ["idea", "Agent 很重要", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["status"] == "not_ready"
    assert payload["reason_code"] == "TOO_BROAD"

    assert DraftRepository(store).list_drafts() == []
    assert ContentJobRepository(store).list_jobs() == []


def test_idea_critic_failure_maps_unsafe(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_voice_profile", lambda _p: VoiceProfile())
    monkeypatch.setattr(cli, "assess_idea", lambda *a, **k: _ready_assessment())
    monkeypatch.setattr(cli, "write_idea", lambda *a, **k: "样稿正文")

    def _fake_unsafe(runner, draft, job, cards, max_rounds, **kw):
        from finch.content.checkers.base import CheckResult
        return (
            AggregateOutcome.REJECT,
            [CheckResult(checker="safety", passed=False, severity="hard_fail",
                         issues=["unsafe"])],
            draft,
        )

    monkeypatch.setattr(cli, "run_idea_critic", _fake_unsafe)

    r = CliRunner().invoke(app, ["idea", "想法", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["status"] == "not_ready"
    assert payload["reason_code"] == "UNSAFE_TO_PUBLISH"
    assert DraftRepository(store).list_drafts() == []


def test_idea_empty_text_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["idea", "   "])
    assert r.exit_code == 1
    assert "empty" in r.output
    assert DraftRepository(store).list_drafts() == []
