"""Unit tests for finch run/jobs CLI commands."""
from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    IntendedEffect,
    SuccessCriterion,
)
from finch.content.models import DraftKind
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import ContentJobRepository


def test_run_daily_help():
    r = CliRunner().invoke(app, ["run", "daily", "--help"])
    assert r.exit_code == 0


def _settings(tmp_path):
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


def _job(job_id="job1", candidate_id=None, **overrides):
    kw = dict(
        id=job_id,
        source_card_ids=["ev1"],
        candidate_id=candidate_id,
        reader_problem="readers don't know how to rate limit",
        audience="backend engineers",
        intended_effect=IntendedEffect(understand="token bucket rate limiting"),
        author_position=None,
        success_criteria=[
            SuccessCriterion(id="c1", description="critic passes", measurement="critic")
        ],
        recommended_format=DraftKind.REPLY,
        status=ContentJobStatus.NEEDS_INPUT,
    )
    kw.update(overrides)
    return ContentJob(**kw)


def test_jobs_subcommands_exist():
    r = CliRunner()
    for cmd in ["list", "show", "answer", "confirm-position", "reject"]:
        res = r.invoke(app, ["jobs", cmd, "--help"])
        assert res.exit_code == 0, cmd


def test_jobs_list_and_filter(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    repo = ContentJobRepository(store)
    repo.upsert_job(_job(job_id="job1"))
    repo.upsert_job(_job(job_id="job2", status=ContentJobStatus.DO_NOT_WRITE))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["jobs", "list"])
    assert r.exit_code == 0, r.output
    assert "job1" in r.output and "job2" in r.output

    r = CliRunner().invoke(app, ["jobs", "list", "--status", "do_not_write"])
    assert r.exit_code == 0, r.output
    assert "job2" in r.output and "job1" not in r.output


def test_jobs_show(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    repo = ContentJobRepository(store)
    repo.upsert_job(_job(job_id="job1"))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["jobs", "show", "job1"])
    assert r.exit_code == 0, r.output
    assert "readers don't know how to rate limit" in r.output
    assert "backend engineers" in r.output


def test_jobs_answer_sets_position_unconfirmed(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    repo = ContentJobRepository(store)
    repo.upsert_job(_job(job_id="job1"))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    answers = tmp_path / "answers.yaml"
    answers.write_text(
        "claim: use token bucket\n"
        "decision: use token bucket\n"
        "tradeoff: more memory\n"
    )
    r = CliRunner().invoke(app, ["jobs", "answer", "job1", "--file", str(answers)])
    assert r.exit_code == 0, r.output
    job = repo.get_job("job1")
    assert job is not None and job.author_position is not None
    assert job.author_position.decision == "use token bucket"
    assert job.author_position.tradeoff == "more memory"
    assert job.author_position.confirmed is False


def test_jobs_confirm_position_sets_confirmed(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    repo = ContentJobRepository(store)
    repo.upsert_job(
        _job(
            job_id="job1",
            author_position=AuthorPosition(
                claim="use token bucket", decision="use token bucket", tradeoff="more memory"
            ),
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["jobs", "confirm-position", "job1"])
    assert r.exit_code == 0, r.output
    job = repo.get_job("job1")
    assert job is not None and job.author_position is not None
    assert job.author_position.confirmed is True


def test_jobs_reject_sets_do_not_write_and_reason(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    repo = ContentJobRepository(store)
    repo.upsert_job(_job(job_id="job1"))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["jobs", "reject", "job1", "--reason", "not useful right now"])
    assert r.exit_code == 0, r.output
    job = repo.get_job("job1")
    assert job is not None
    assert job.status == ContentJobStatus.DO_NOT_WRITE
    assert job.reject_reason == "not useful right now"


def test_run_resume_help():
    r = CliRunner().invoke(app, ["run", "resume", "--help"])
    assert r.exit_code == 0


def test_run_resume_echoes_state_and_brief(monkeypatch, tmp_path):
    from finch.content.models import Draft
    from finch.graph.content_nodes import make_brief_node
    from finch.graph.context import items_payload
    from finch.graph.events import NodeResult
    from finch.graph.nodes import Node
    from finch.graph.runtime import GraphRuntime
    from finch.settings import QualityGates

    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    class Seed(Node):
        model_config = {"extra": "allow"}

        def run(self, ctx):
            return NodeResult(status="succeeded", output=self.seed)

    def build_nodes(**kw):
        return [
            Seed(
                name="draft",
                writes="drafts",
                seed=items_payload(
                    [Draft(id="d1", kind=DraftKind.REPLY, candidate_id="t", body="hi", claims=[])]
                ),
            ),
            Seed(name="match_evidence", writes="match_results", seed=items_payload([])),
            Seed(name="define_jobs", writes="content_jobs", seed=items_payload([])),
            Seed(name="extract_events", writes="evidence_cards", seed=items_payload([])),
            make_brief_node(QualityGates()),
        ]

    monkeypatch.setattr(cli, "daily_nodes", build_nodes)
    run = GraphRuntime(store, build_nodes()).run()

    r = CliRunner().invoke(app, ["run", "resume", run.id])
    assert r.exit_code == 0, r.output
    assert "WAITING_FOR_REVIEW" in r.output
    assert "草稿正文：hi" in r.output
