"""Unit tests for finch weekly/voice CLI commands."""
from datetime import UTC, datetime

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.models import Draft, DraftKind
from finch.content.voice import (
    ApprovedExample,
    RejectedExample,
    load_voice_profile,
    save_voice_profile,
)
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.learn.reflection import WeeklyReflection
from finch.settings import Paths, Settings
from finch.storage.repositories import (
    DecisionRecordRepository,
    DraftRepository,
)
from finch.storage.workspace import Workspace


class _FakeReflectionService:
    def __init__(self, runner):
        self.runner = runner

    def reflect(self, report, **kwargs):
        return WeeklyReflection(
            insight="i", strongest_expression="s", meaningful_connection="m",
            next_practice="n", stop_doing="x", voice_update_candidate="v",
        )


def test_weekly_renders(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "WeeklyReflectionService", _FakeReflectionService)

    r = CliRunner().invoke(app, ["weekly"])
    assert r.exit_code == 0, r.output
    assert "下周只练：" in r.output
    assert r.output.index("下周只练：") < r.output.index("本周真正有来回")
    assert "Finch Weekly Reflection" not in r.output
    assert "uv run finch practice start" in r.output or "uv run finch connect daily" in r.output


def test_voice_show_prints_summary_not_full_dump(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    profile = load_voice_profile(settings.paths.voice_profile_path)
    profile.preferred_patterns = ["先讲场景", "少用本质"]
    profile.avoid_phrases = ["本质上", "赋能"]
    profile.approved_examples.append(ApprovedExample(id="d1", text="body"))
    save_voice_profile(profile, settings.paths.voice_profile_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "show"])
    assert r.exit_code == 0, r.output
    assert "画像摘要" in r.output
    assert "已批准样例: 1" in r.output
    assert "先讲场景" in r.output
    assert "uv run finch voice propose" in r.output
    assert "uv run finch voice show --json" in r.output
    assert "preferred_patterns:" not in r.output


def test_voice_propose_caps_and_commands(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    profile = load_voice_profile(settings.paths.voice_profile_path)
    profile.approved_examples.append(
        ApprovedExample(
            id="d1",
            text="after",
            diff=(
                "--- a\n+++ b\n"
                "-本质上很重要\n-赋能团队\n-闭环一下\n-抓住本质\n"
                "+先讲场景\n+少用术语\n+给反例\n+再下判断\n"
            ),
        )
    )
    save_voice_profile(profile, settings.paths.voice_profile_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "propose"])
    assert r.exit_code == 0, r.output
    assert "更新候选" in r.output
    assert r.output.count("避免：") <= 3
    assert r.output.count("偏好：") <= 3
    assert "uv run finch voice show --json" in r.output
    assert "不会自动写入" in r.output


def _settings(tmp_path):
    return Settings(paths=Paths(var_dir=tmp_path))


def _voice_settings(tmp_path):
    return Settings(
        paths=Paths(
            var_dir=tmp_path,
            voice_profile_path=tmp_path / "voice-profile.yaml",
        )
    )


def _seed_draft(ws: Workspace, draft_id: str, body: str) -> None:
    DraftRepository(ws).upsert_draft(
        Draft(id=draft_id, kind=DraftKind.REPLY, candidate_id="t", body=body, claims=[])
    )


def test_voice_subcommands_exist():
    r = CliRunner()
    for cmd in ["show", "approve-example", "reject-example"]:
        res = r.invoke(app, ["voice", cmd, "--help"])
        assert res.exit_code == 0, cmd


def test_voice_show_prints_profile(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["voice", "show", "--json"])
    assert r.exit_code == 0, r.output
    assert "preferred_patterns" in r.output
    assert "avoid_phrases" in r.output


def _seed_accept_decision(ws, draft_id, *, revised_body=None):
    DecisionRecordRepository(ws).save(
        DecisionRecord(
            id=f"dec_job_{draft_id}",
            job_id=f"job_{draft_id}",
            draft_id=draft_id,
            action=DecisionAction.ACCEPT,
            approved_content_hash="h",
            revised_body=revised_body,
            decided_at=datetime.now(UTC),
        )
    )


def test_voice_approve_example_uses_revised_body_and_dedupes(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _seed_draft(ws, "d1", "original ai draft body")
    _seed_accept_decision(ws, "d1", revised_body="human revised body")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "approve-example", "d1"])
    assert r.exit_code == 0, r.output
    profile = load_voice_profile(settings.paths.voice_profile_path)
    assert len(profile.approved_examples) == 1
    assert profile.approved_examples[0].id == "d1"
    assert profile.approved_examples[0].text == "human revised body"

    r = CliRunner().invoke(app, ["voice", "approve-example", "d1"])
    assert r.exit_code == 0, r.output
    assert "already approved" in r.output
    assert len(load_voice_profile(settings.paths.voice_profile_path).approved_examples) == 1


def test_voice_approve_example_falls_back_to_draft_body(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _seed_draft(ws, "d2", "original ai draft body")
    _seed_accept_decision(ws, "d2")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "approve-example", "d2"])
    assert r.exit_code == 0, r.output
    profile = load_voice_profile(settings.paths.voice_profile_path)
    assert profile.approved_examples[0].text == "original ai draft body"


def test_voice_approve_example_missing_draft(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["voice", "approve-example", "nope"])
    assert r.exit_code == 1
    assert "not found" in r.output


def test_voice_approve_example_requires_decision(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _seed_draft(ws, "d3", "body")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["voice", "approve-example", "d3"])
    assert r.exit_code == 1
    assert "not accepted" in r.output


def test_voice_approve_example_removes_from_rejected(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _seed_draft(ws, "d7", "body")
    _seed_accept_decision(ws, "d7")
    profile = load_voice_profile(settings.paths.voice_profile_path)
    profile.rejected_examples.append(RejectedExample(id="d7", reason="old"))
    save_voice_profile(profile, settings.paths.voice_profile_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "approve-example", "d7"])
    assert r.exit_code == 0, r.output
    profile = load_voice_profile(settings.paths.voice_profile_path)
    assert any(ex.id == "d7" for ex in profile.approved_examples)
    assert not any(ex.id == "d7" for ex in profile.rejected_examples)


def test_voice_reject_example_and_dedupe(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _seed_draft(ws, "d1", "body")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "reject-example", "d1", "--reason", "too generic"])
    assert r.exit_code == 0, r.output
    profile = load_voice_profile(settings.paths.voice_profile_path)
    assert len(profile.rejected_examples) == 1
    assert profile.rejected_examples[0].id == "d1"
    assert profile.rejected_examples[0].reason == "too generic"

    r = CliRunner().invoke(app, ["voice", "reject-example", "d1", "--reason", "again"])
    assert r.exit_code == 0, r.output
    assert "already rejected" in r.output
    assert len(load_voice_profile(settings.paths.voice_profile_path).rejected_examples) == 1


def test_voice_reject_example_missing_draft(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["voice", "reject-example", "nope", "--reason", "x"])
    assert r.exit_code == 1
    assert "not found" in r.output


def test_voice_reject_example_removes_from_approved(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _seed_draft(ws, "d8", "body")
    profile = load_voice_profile(settings.paths.voice_profile_path)
    profile.approved_examples.append(ApprovedExample(id="d8", text="body"))
    save_voice_profile(profile, settings.paths.voice_profile_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "reject-example", "d8", "--reason", "no"])
    assert r.exit_code == 0, r.output
    profile = load_voice_profile(settings.paths.voice_profile_path)
    assert any(ex.id == "d8" for ex in profile.rejected_examples)
    assert not any(ex.id == "d8" for ex in profile.approved_examples)
