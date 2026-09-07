"""Unit tests for `finch learn`（记录已发布草稿的反馈，供 weekly 汇总）。"""

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.models import Draft, DraftKind
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import DraftRepository, FeedbackRepository


def _settings(tmp_path):
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


def _patch_settings(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


def _seed_draft(store, draft_id="draft_1"):
    DraftRepository(store).upsert_draft(Draft(id=draft_id, kind=DraftKind.ORIGINAL, body="hi"))


def test_learn_records_feedback(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch_settings(monkeypatch, settings)
    _seed_draft(store)

    r = CliRunner().invoke(
        app,
        [
            "learn", "draft_1",
            "--url", "https://x.com/u/1",
            "--metrics", '{"likes":3,"replies":1}',
            "--outcome", '{"job_completed":"yes","reader_understood":true}',
            "--learning", "works",
        ],
    )
    assert r.exit_code == 0, r.output

    fb = FeedbackRepository(store).get_feedback("draft_1")
    assert fb is not None
    assert fb.published_url == "https://x.com/u/1"
    assert fb.interaction_metrics == {"likes": 3, "replies": 1}
    assert fb.outcome is not None
    assert fb.outcome.job_completed == "yes"
    assert fb.outcome.reader_understood is True
    assert fb.learning == "works"


def test_learn_without_optional_flags(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch_settings(monkeypatch, settings)
    _seed_draft(store)

    r = CliRunner().invoke(app, ["learn", "draft_1"])
    assert r.exit_code == 0, r.output

    fb = FeedbackRepository(store).get_feedback("draft_1")
    assert fb is not None
    assert fb.published_url is None
    assert fb.interaction_metrics == {}
    assert fb.outcome is None
    assert fb.learning is None


def test_learn_rejects_missing_draft(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch_settings(monkeypatch, settings)

    r = CliRunner().invoke(app, ["learn", "nope"])
    assert r.exit_code == 1
    assert "draft not found" in r.output


def test_learn_rejects_invalid_metrics_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch_settings(monkeypatch, settings)
    _seed_draft(store)

    r = CliRunner().invoke(app, ["learn", "draft_1", "--metrics", "not-json"])
    assert r.exit_code == 1
    assert "invalid --metrics" in r.output


def test_learn_rejects_invalid_outcome_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch_settings(monkeypatch, settings)
    _seed_draft(store)

    r = CliRunner().invoke(
        app, ["learn", "draft_1", "--outcome", '{"job_completed":"maybe"}']
    )
    assert r.exit_code == 1
    assert "invalid --outcome" in r.output
