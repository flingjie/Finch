"""Unit tests for finch review CLI commands."""
import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.checkers.base import CheckResult
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    ContentScope,
    IntendedEffect,
    SuccessCriterion,
)
from finch.content.models import Draft, DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard, Source
from finch.review.models import Feedback
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import (
    ContentJobRepository,
    CriticReportRepository,
    DraftRepository,
    EvidenceRepository,
    FeedbackRepository,
    ReviewRepository,
)


def test_review_subcommands_exist():
    r = CliRunner()
    for cmd in ["list", "show", "approve", "revise", "skip", "feedback", "confirm-position"]:
        res = r.invoke(app, ["review", cmd, "--help"])
        assert res.exit_code == 0, cmd


def test_review_confirm_position_records_voice_match(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.REPLY, candidate_id="t", body="hi", claims=[])
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app,
        ["review", "confirm-position", "d1", "--voice-match", "4",
         "--position-correct", "--job-clear"],
    )
    assert r.exit_code == 0, r.output

    repo = ReviewRepository(store)
    got = repo.get_position_review("d1")
    assert got is not None
    assert got.voice_match == 4
    assert got.position_correct is True
    assert got.job_clear is True


def test_review_confirm_position_rejects_out_of_range(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.REPLY, candidate_id="t", body="hi", claims=[])
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "confirm-position", "d1", "--voice-match", "6"])
    assert r.exit_code == 1
    assert "0-5" in r.output


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


def test_review_feedback_preserves_recorded_at(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.REPLY, candidate_id="t", body="hi", claims=[])
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r1 = CliRunner().invoke(app, ["review", "feedback", "d1", "--url", "https://x.com/1"])
    assert r1.exit_code == 0, r1.output
    first = FeedbackRepository(store).get_feedback("d1")
    assert first is not None

    outcome_json = json.dumps({"job_completed": "yes"})
    r2 = CliRunner().invoke(app, ["review", "feedback", "d1", "--outcome", outcome_json])
    assert r2.exit_code == 0, r2.output

    second = FeedbackRepository(store).get_feedback("d1")
    assert second is not None
    assert second.recorded_at == first.recorded_at


def _reply_draft(body="hello world") -> Draft:
    return Draft(
        id="d1",
        kind=DraftKind.REPLY,
        candidate_id="cand_1",
        body=body,
        claims=[],
        content_job_id="j1",
    )


def _job(**overrides) -> ContentJob:
    data = dict(
        id="j1",
        source_card_ids=["ev_1"],
        candidate_id="cand_1",
        reader_problem="how to test agents",
        audience="agent builders",
        intended_effect=IntendedEffect(understand="replay makes failures testable"),
        author_position=AuthorPosition(
            claim="replay is the key",
            decision="share the replay pattern",
            tradeoff="adds infra complexity",
            confirmed=True,
        ),
        success_criteria=[SuccessCriterion(id="s1", description="d", measurement="human")],
        recommended_format=DraftKind.REPLY,
        status=ContentJobStatus.READY,
        core_message="replay",
        why_now="agent testing",
        scope=ContentScope.BOUNDED_LESSON,
    )
    data.update(overrides)
    return ContentJob(**data)


def _card(**overrides) -> EvidenceCard:
    data = dict(
        id="ev_1",
        event_id="evt",
        claim="replay is testable",
        sources=[Source(type="commit", url="https://github.com/a/b/commit/1")],
        confidence=ClaimConfidence.VERIFIED,
        publishable=True,
        topics=[],
    )
    data.update(overrides)
    return EvidenceCard(**data)


def _seed_review_package(store):
    DraftRepository(store).upsert_draft(_reply_draft())
    ContentJobRepository(store).upsert_job(_job())
    EvidenceRepository(store).upsert_card(_card())
    CriticReportRepository(store).upsert_report(
        "d1",
        0,
        [
            CheckResult(
                checker="specificity",
                passed=False,
                severity="high",
                locations=["s[0]"],
                issues=["vague"],
                rewrite_instructions=["be specific"],
            )
        ],
        "rewrite",
    )


def test_review_show_default_package(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_review_package(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "show", "d1"])
    assert r.exit_code == 0, r.output
    assert "## Content Job" in r.output
    assert "reader_problem: how to test agents" in r.output
    assert "## Author Position" in r.output
    assert "decision: share the replay pattern" in r.output
    assert "## Evidence" in r.output
    assert "replay is testable" in r.output
    assert "https://github.com/a/b/commit/1" in r.output
    assert "## Candidate" in r.output
    assert "candidate_id: cand_1" in r.output
    assert "## Draft" in r.output
    assert "hello world" in r.output
    assert "## Critic" in r.output
    assert "specificity" in r.output
    assert "rewrite count: 1" in r.output
    assert "remaining risk:" in r.output
    assert "## Next step" in r.output
    assert "finch review approve d1" in r.output


def test_review_show_body_only(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_review_package(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "show", "d1", "--body-only"])
    assert r.exit_code == 0, r.output
    assert r.output == "hello world\n"
    assert "## Content Job" not in r.output


def test_review_show_evidence(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_review_package(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "show", "d1", "--evidence"])
    assert r.exit_code == 0, r.output
    assert "## Evidence" in r.output
    assert "replay is testable" in r.output
    assert "https://github.com/a/b/commit/1" in r.output
    assert "## Content Job" not in r.output
    assert "hello world" not in r.output


def test_review_show_critic(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_review_package(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "show", "d1", "--critic"])
    assert r.exit_code == 0, r.output
    assert "## Critic" in r.output
    assert "rewrite count: 1" in r.output
    assert "specificity" in r.output
    assert "## Content Job" not in r.output


def test_review_show_missing_draft(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "show", "missing"])
    assert r.exit_code == 1
    assert "draft not found" in r.output


def test_review_feedback_learning_preserved_on_rerecord(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    DraftRepository(store).upsert_draft(_reply_draft())
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r1 = CliRunner().invoke(
        app, ["review", "feedback", "d1", "--learning", "首稿缺 decision 时 Critic 会补写"]
    )
    assert r1.exit_code == 0, r1.output
    r2 = CliRunner().invoke(
        app, ["review", "feedback", "d1", "--outcome", json.dumps({"job_completed": "yes"})]
    )
    assert r2.exit_code == 0, r2.output

    fb = FeedbackRepository(store).get_feedback("d1")
    assert fb is not None
    assert fb.learning == "首稿缺 decision 时 Critic 会补写"
    assert fb.outcome is not None
    assert fb.outcome.job_completed == "yes"
