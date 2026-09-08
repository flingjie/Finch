"""Unit tests for the `finch voice` CLI (approve-example --text / revoke-example)."""

from datetime import UTC, datetime

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.models import Draft, DraftKind
from finch.content.voice import load_voice_profile
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import DecisionRecordRepository, DraftRepository


def _settings(tmp_path) -> Settings:
    return Settings(
        paths=Paths(
            db_path=tmp_path / "finch.db",
            voice_profile_path=tmp_path / "voice.yaml",
        )
    )


def test_voice_approve_example_text(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "approve-example", "--text", "we shipped it fast"])
    assert r.exit_code == 0, r.output

    profile = load_voice_profile(settings.paths.voice_profile_path)
    assert len(profile.approved_examples) == 1
    ex = profile.approved_examples[0]
    assert ex.text == "we shipped it fast"
    assert ex.source == "user_text"


def test_voice_approve_example_from_draft_records_diff(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    DraftRepository(store).upsert_draft(
        Draft(id="draft_1", kind=DraftKind.ORIGINAL, body="model draft")
    )
    DecisionRecordRepository(store).save(
        DecisionRecord(
            id="dec_1", job_id="job_1", draft_id="draft_1",
            action=DecisionAction.ACCEPT, approved_content_hash="h",
            revised_body="human revised draft", decided_at=datetime.now(UTC),
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "approve-example", "draft_1"])
    assert r.exit_code == 0, r.output

    profile = load_voice_profile(settings.paths.voice_profile_path)
    ex = profile.approved_examples[0]
    assert ex.text == "human revised draft"  # 最终人工修订版本
    assert ex.original_draft == "model draft"
    assert ex.diff is not None  # 记录了初稿 → 最终文本的 diff


def test_voice_revoke_example_removes_sample(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    CliRunner().invoke(app, ["voice", "approve-example", "--text", "we shipped it fast"])
    r = CliRunner().invoke(app, ["voice", "revoke-example", "text_00000000"])
    assert r.exit_code == 1  # 不存在的样例
    assert "not found" in r.output

    # 找到实际 id 再撤销。
    profile = load_voice_profile(settings.paths.voice_profile_path)
    ex_id = profile.approved_examples[0].id
    r = CliRunner().invoke(app, ["voice", "revoke-example", ex_id])
    assert r.exit_code == 0, r.output
    assert load_voice_profile(settings.paths.voice_profile_path).approved_examples == []


def test_voice_approve_example_requires_exactly_one_source(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "approve-example"])
    assert r.exit_code == 1
    assert "exactly one of" in r.output
