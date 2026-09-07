"""Unit tests for finch drafts create CLI command（Skill 架构 Step 4 Task 3）。"""

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.models import Draft, DraftKind
from finch.content.voice import VoiceProfile
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import DraftRepository

IDEA_ID = "idea_abc12345"


def _settings(tmp_path):
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


class _FakeDraftService:
    """记录构造/调用参数并返回固定 Draft 的替身（monkeypatch cli.DraftService）。"""

    created: list[dict] = []

    def __init__(self, drafts, critic_reports, jobs, runner, *, max_rewrite_rounds=None,
                 voice_profile=None):
        self.drafts = drafts
        self.critic_reports = critic_reports
        self.jobs = jobs
        self.runner = runner
        self.max_rewrite_rounds = max_rewrite_rounds
        self.voice_profile = voice_profile

    def create(self, idea_id, *, version, format, voice_version):
        type(self).created.append(
            {"idea_id": idea_id, "version": version, "format": format,
             "voice_version": voice_version}
        )
        return Draft(
            id="draft_fake1234",
            kind=DraftKind.ORIGINAL,
            language="zh",
            body="把编排器改成确定性图后，失败可以重放。",
            claims=[],
            content_job_id=idea_id,
            position_statement="用可恢复性评价 Graph",
            run_id="idea",
        )


class _RaisingDraftService:
    def __init__(self, *args, **kwargs):
        pass

    def create(self, idea_id, *, version, format, voice_version):
        raise ValueError(f"idea {idea_id} needs_confirmation (status=proposed)")


def _patch(monkeypatch, settings, draft_service_cls):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_voice_profile", lambda _p: VoiceProfile())
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: None)
    monkeypatch.setattr(cli, "CodexRunner", lambda: object())
    monkeypatch.setattr(cli, "DraftService", draft_service_cls)


def test_drafts_create_json_output(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _FakeDraftService.created = []
    _patch(monkeypatch, settings, _FakeDraftService)

    r = CliRunner().invoke(app, ["drafts", "create", IDEA_ID, "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["draft_id"] == "draft_fake1234"
    assert payload["status"] == "drafted"
    assert payload["body"] == "把编排器改成确定性图后，失败可以重放。"

    assert _FakeDraftService.created == [
        {"idea_id": IDEA_ID, "version": "1.0.0", "format": "original", "voice_version": "1.0.0"}
    ]


def test_drafts_create_non_json_output(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _FakeDraftService.created = []
    _patch(monkeypatch, settings, _FakeDraftService)

    r = CliRunner().invoke(app, ["drafts", "create", IDEA_ID])
    assert r.exit_code == 0, r.output
    assert "draft_fake1234" in r.output
    assert "drafted" in r.output


def test_drafts_create_unconfirmed_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch(monkeypatch, settings, _RaisingDraftService)

    r = CliRunner().invoke(app, ["drafts", "create", IDEA_ID, "--json"])
    assert r.exit_code == 1
    assert "needs_confirmation" in r.output


# ---- finch drafts show / revise ----

BODY = "把编排器改成确定性图后，失败可以重放。"


def _draft(draft_id="draft_fake1234", body=BODY, content_job_id=None) -> Draft:
    return Draft(
        id=draft_id,
        kind=DraftKind.ORIGINAL,
        language="zh",
        body=body,
        claims=[],
        content_job_id=content_job_id,
        position_statement="用可恢复性评价 Graph",
        run_id="idea",
    )


def _seed_draft(store, draft_id="draft_fake1234", body=BODY, content_job_id=None) -> Draft:
    draft = _draft(draft_id=draft_id, body=body, content_job_id=content_job_id)
    DraftRepository(store).upsert_draft(draft)
    return draft


def test_drafts_show_json_output(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    _seed_draft(store)

    r = CliRunner().invoke(app, ["drafts", "show", "draft_fake1234", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["id"] == "draft_fake1234"
    assert payload["body"] == BODY


def test_drafts_show_non_json_output(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    _seed_draft(store)

    r = CliRunner().invoke(app, ["drafts", "show", "draft_fake1234"])
    assert r.exit_code == 0, r.output
    assert "draft_fake1234" in r.output
    assert BODY in r.output


def test_drafts_show_unknown_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["drafts", "show", "draft_nope"])
    assert r.exit_code == 1
    assert "not found" in r.output


class _FakeRewrite:
    called: list[dict] = []

    @staticmethod
    def rewrite(runner, draft, instruction, cards_by_id, job):
        _FakeRewrite.called.append(
            {"instruction": instruction, "job_id": job.id if job else None}
        )
        return draft.model_copy(update={"body": "REVISED: " + draft.body})


def _raising_rewrite(runner, draft, instruction, cards_by_id, job):
    raise RuntimeError("boom")


def _patch_revise(monkeypatch, settings, rewrite=_FakeRewrite.rewrite):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: None)
    monkeypatch.setattr(cli, "CodexRunner", lambda: object())
    monkeypatch.setattr(cli, "rewrite_with_instruction", rewrite)


def test_drafts_revise_updates_body(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _FakeRewrite.called = []
    _patch_revise(monkeypatch, settings)
    _seed_draft(store)

    r = CliRunner().invoke(
        app, ["drafts", "revise", "draft_fake1234", "--instruction", "make it shorter", "--json"]
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["draft_id"] == "draft_fake1234"
    assert payload["body"] == "REVISED: " + BODY
    assert _FakeRewrite.called == [{"instruction": "make it shorter", "job_id": None}]
    assert DraftRepository(store).get_draft("draft_fake1234").body == "REVISED: " + BODY


def test_drafts_revise_unknown_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch_revise(monkeypatch, settings)

    r = CliRunner().invoke(
        app, ["drafts", "revise", "draft_nope", "--instruction", "make it shorter"]
    )
    assert r.exit_code == 1
    assert "not found" in r.output


def test_drafts_revise_rewrite_error_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch_revise(monkeypatch, settings, _raising_rewrite)
    _seed_draft(store)

    r = CliRunner().invoke(
        app, ["drafts", "revise", "draft_fake1234", "--instruction", "make it shorter", "--json"]
    )
    assert r.exit_code == 1
    assert "boom" in r.output
