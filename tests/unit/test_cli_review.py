"""Unit tests for finch review CLI commands."""
import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.models import Draft, DraftKind
from finch.review.models import Feedback
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import FeedbackRepository


def test_review_subcommands_exist():
    r = CliRunner()
    for cmd in ["list", "show", "approve", "revise", "skip", "feedback"]:
        res = r.invoke(app, ["review", cmd, "--help"])
        assert res.exit_code == 0, cmd


def _settings(tmp_path):
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


def test_review_feedback_records_outcome(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    from finch.storage.repositories import DraftRepository

    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.REPLY, candidate_id="t", body="hi", claims=[])
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    outcome_json = json.dumps(
        {"job_completed": "partly", "reader_understood": True, "useful_reply_count": 3}
    )
    r = CliRunner().invoke(
        app, ["review", "feedback", "d1", "--url", "https://x.com/1", "--outcome", outcome_json]
    )
    assert r.exit_code == 0, r.output

    fb = FeedbackRepository(store).get_feedback("d1")
    assert isinstance(fb, Feedback)
    assert fb.outcome is not None
    assert fb.outcome.job_completed == "partly"
    assert fb.outcome.useful_reply_count == 3


def test_review_feedback_rejects_invalid_outcome(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app, ["review", "feedback", "d1", "--outcome", json.dumps({"job_completed": "maybe"})]
    )
    assert r.exit_code != 0


def test_review_feedback_outcome_preserves_url_and_metrics(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    from finch.storage.repositories import DraftRepository

    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.REPLY, candidate_id="t", body="hi", claims=[])
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r1 = CliRunner().invoke(
        app,
        ["review", "feedback", "d1", "--url", "https://x.com/1",
         "--metrics", json.dumps({"likes": 5})],
    )
    assert r1.exit_code == 0, r1.output
    outcome_json = json.dumps({"job_completed": "yes"})
    r2 = CliRunner().invoke(app, ["review", "feedback", "d1", "--outcome", outcome_json])
    assert r2.exit_code == 0, r2.output

    fb = FeedbackRepository(store).get_feedback("d1")
    assert fb is not None
    assert fb.published_url == "https://x.com/1"
    assert fb.interaction_metrics == {"likes": 5}
    assert fb.outcome is not None
    assert fb.outcome.job_completed == "yes"
