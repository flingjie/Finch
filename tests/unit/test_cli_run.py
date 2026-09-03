"""Unit tests for finch run/jobs CLI commands."""
import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.checkers.base import CheckResult
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    IntendedEffect,
    SuccessCriterion,
)
from finch.content.models import Draft, DraftKind
from finch.content.voice import (
    ApprovedExample,
    RejectedExample,
    load_voice_profile,
    save_voice_profile,
)
from finch.review.models import ReviewAction, ReviewDecision
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import (
    ContentJobRepository,
    CriticReportRepository,
    DraftRepository,
    DraftVersionRepository,
    ReviewRepository,
)


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


def test_jobs_answer_idempotent_single_record(monkeypatch, tmp_path):
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
    for _ in range(2):
        r = CliRunner().invoke(app, ["jobs", "answer", "job1", "--file", str(answers)])
        assert r.exit_code == 0, r.output
    jobs = repo.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].author_position is not None
    assert jobs[0].author_position.decision == "use token bucket"


def test_jobs_answer_rejects_empty_yaml(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    repo = ContentJobRepository(store)
    repo.upsert_job(_job(job_id="job1"))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    answers = tmp_path / "answers.yaml"
    answers.write_text("")
    r = CliRunner().invoke(app, ["jobs", "answer", "job1", "--file", str(answers)])
    assert r.exit_code == 1
    assert "empty" in r.output
    # No default empty position is silently created.
    job = repo.get_job("job1")
    assert job is not None and job.author_position is None


def test_jobs_answer_rejects_missing_required_fields(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    repo = ContentJobRepository(store)
    repo.upsert_job(_job(job_id="job1"))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    answers = tmp_path / "answers.yaml"
    answers.write_text("claim: use token bucket\n")
    r = CliRunner().invoke(app, ["jobs", "answer", "job1", "--file", str(answers)])
    assert r.exit_code == 1
    assert "missing required" in r.output
    job = repo.get_job("job1")
    assert job is not None and job.author_position is None


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


def _voice_settings(tmp_path):
    return Settings(
        paths=Paths(
            db_path=tmp_path / "finch.db",
            voice_profile_path=tmp_path / "voice-profile.yaml",
        )
    )


def _seed_draft(store: Store, draft_id: str, body: str) -> None:
    DraftRepository(store).upsert_draft(
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
    r = CliRunner().invoke(app, ["voice", "show"])
    assert r.exit_code == 0, r.output
    assert "preferred_patterns" in r.output
    assert "avoid_phrases" in r.output


def _approve_decision(draft_id, voice_match=4, revised_body=None):
    return ReviewDecision(
        id=f"rev_{draft_id}",
        draft_id=draft_id,
        action=ReviewAction.APPROVE,
        revised_body=revised_body,
        voice_match=voice_match,
        decided_at=datetime.now(UTC),
    )


def test_voice_approve_example_uses_revised_body_and_dedupes(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d1", "original ai draft body")
    ReviewRepository(store).save_review(
        _approve_decision("d1", voice_match=4, revised_body="human revised body")
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "approve-example", "d1"])
    assert r.exit_code == 0, r.output
    profile = load_voice_profile(settings.paths.voice_profile_path)
    assert len(profile.approved_examples) == 1
    assert profile.approved_examples[0].id == "d1"
    # 人工修改文本优先于原始 AI 草稿
    assert profile.approved_examples[0].text == "human revised body"

    r = CliRunner().invoke(app, ["voice", "approve-example", "d1"])
    assert r.exit_code == 0, r.output
    assert "already approved" in r.output
    assert len(load_voice_profile(settings.paths.voice_profile_path).approved_examples) == 1


def test_voice_approve_example_falls_back_to_draft_body(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d2", "original ai draft body")
    ReviewRepository(store).save_review(_approve_decision("d2", voice_match=5))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "approve-example", "d2"])
    assert r.exit_code == 0, r.output
    profile = load_voice_profile(settings.paths.voice_profile_path)
    assert profile.approved_examples[0].text == "original ai draft body"


def test_voice_approve_example_missing_draft(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["voice", "approve-example", "nope"])
    assert r.exit_code == 1
    assert "not found" in r.output


def test_voice_approve_example_requires_review_decision(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d3", "body")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["voice", "approve-example", "d3"])
    assert r.exit_code == 1
    assert "not approved" in r.output


def test_voice_approve_example_rejects_non_approve(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d4", "body")
    ReviewRepository(store).save_review(
        ReviewDecision(
            id="rev_d4",
            draft_id="d4",
            action=ReviewAction.REVISE,
            revised_body="revised",
            voice_match=5,
            decided_at=datetime.now(UTC),
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["voice", "approve-example", "d4"])
    assert r.exit_code == 1
    assert "not approved" in r.output


def test_voice_approve_example_rejects_missing_voice_match(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d5", "body")
    ReviewRepository(store).save_review(_approve_decision("d5", voice_match=None))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["voice", "approve-example", "d5"])
    assert r.exit_code == 1
    assert "voice_match not recorded" in r.output


def test_voice_approve_example_rejects_voice_match_below_threshold(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d6", "body")
    ReviewRepository(store).save_review(_approve_decision("d6", voice_match=3))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["voice", "approve-example", "d6"])
    assert r.exit_code == 1
    assert "below threshold" in r.output


def test_voice_approve_example_removes_from_rejected(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d7", "body")
    ReviewRepository(store).save_review(_approve_decision("d7", voice_match=4))
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
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d1", "body")
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
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["voice", "reject-example", "nope", "--reason", "x"])
    assert r.exit_code == 1
    assert "not found" in r.output


def test_voice_reject_example_removes_from_approved(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d8", "body")
    profile = load_voice_profile(settings.paths.voice_profile_path)
    profile.approved_examples.append(ApprovedExample(id="d8", text="body"))
    save_voice_profile(profile, settings.paths.voice_profile_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["voice", "reject-example", "d8", "--reason", "no"])
    assert r.exit_code == 0, r.output
    profile = load_voice_profile(settings.paths.voice_profile_path)
    assert any(ex.id == "d8" for ex in profile.rejected_examples)
    assert not any(ex.id == "d8" for ex in profile.approved_examples)



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


def test_persist_critique_reports_helper(tmp_path):
    from finch.cli import persist_critique_reports

    store = Store(tmp_path / "finch.db")
    store.init()
    draft = Draft(id="d1", kind=DraftKind.REPLY, candidate_id="t", body="v0", claims=[])
    payload = json.dumps(
        {
            "reports": [
                {
                    "draft_id": "d1",
                    "round": 0,
                    "version": draft.model_dump(mode="json"),
                    "checks": [
                        CheckResult(
                            checker="specificity", passed=True, severity="low"
                        ).model_dump(mode="json")
                    ],
                    "outcome": "pass",
                }
            ]
        }
    )
    persist_critique_reports(store, payload)

    versions = DraftVersionRepository(store).list_versions("d1")
    assert [v.body for v in versions] == ["v0"]
    reports = CriticReportRepository(store).list_reports("d1")
    assert len(reports) == 1
    assert reports[0]["outcome"] == "pass"
    assert reports[0]["checks"][0]["checker"] == "specificity"


def test_run_daily_persists_versions_and_reports(monkeypatch, tmp_path):
    from finch.codex.runner import CodexRunner
    from finch.graph.content_nodes import make_critique_node
    from finch.graph.context import items_payload
    from finch.graph.events import NodeResult
    from finch.graph.nodes import Node
    from finch.settings import QualityGates

    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    class Seed(Node):
        model_config = {"extra": "allow"}

        def run(self, ctx):
            return NodeResult(status="succeeded", output=self.seed)

    class PassChecker:
        name = "pass"

        def check(self, ctx):
            return CheckResult(checker="pass", passed=True, severity="low")

    draft = Draft(id="d1", kind=DraftKind.ORIGINAL, candidate_id=None, body="hi", claims=[])

    def build_nodes(**kw):
        return [
            Seed(name="draft", writes="drafts", seed=items_payload([draft])),
            Seed(name="match_evidence", writes="match_results", seed=items_payload([])),
            Seed(name="extract_events", writes="evidence_cards", seed=items_payload([])),
            Seed(name="define_jobs", writes="content_jobs", seed=items_payload([])),
            Seed(name="position_gate", writes="ready_jobs", seed=items_payload([])),
            make_critique_node(
                CodexRunner(), lambda *a, **k: draft, QualityGates(), checkers=[PassChecker()]
            ),
        ]

    monkeypatch.setattr(cli, "daily_nodes", build_nodes)
    r = CliRunner().invoke(app, ["run", "daily"])
    assert r.exit_code == 0, r.output

    versions = DraftVersionRepository(store).list_versions("d1")
    assert len(versions) == 1
    reports = CriticReportRepository(store).list_reports("d1")
    assert len(reports) == 1
    assert reports[0]["outcome"] == "pass"
