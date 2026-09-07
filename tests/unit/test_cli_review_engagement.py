"""Unit tests for restored `finch review` and `finch engagement` CLI surfaces.

No real LLM / gh / opencli: repos are backed by a temp SQLite store and the only
LLM path (`review revise`) is monkeypatched at the service-module boundary.
"""

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus, IntendedEffect
from finch.content.models import Draft, DraftKind
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionCandidate,
    InteractionStatus,
)
from finch.inbox.models import DecisionAction
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import (
    ContentJobRepository,
    DecisionRecordRepository,
    DraftRepository,
    InteractionRepository,
)


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


def _job(job_id: str = "job_1") -> ContentJob:
    return ContentJob(
        id=job_id,
        source_card_ids=[],
        reader_problem="orchestrator was hard to rerun",
        audience="backend engineers",
        intended_effect=IntendedEffect(understand="failures can be replayed"),
        author_position=AuthorPosition(
            claim="failures can be replayed",
            decision="deterministic graphs win",
            tradeoff="orchestrator was hard to rerun",
        ),
        success_criteria=[],
        recommended_format=DraftKind.ORIGINAL,
        status=ContentJobStatus.DRAFTED,
        core_message="deterministic graphs",
        why_now="failures can now be replayed",
        origin="user",
    )


def _draft(job_id: str = "job_1", draft_id: str = "draft_1", body: str = "hello world") -> Draft:
    return Draft(
        id=draft_id,
        kind=DraftKind.ORIGINAL,
        body=body,
        content_job_id=job_id,
    )


def _seed_job_and_draft(store: Store, job_id="job_1", draft_id="draft_1", body="hello world"):
    ContentJobRepository(store).upsert_job(_job(job_id))
    DraftRepository(store).upsert_draft(_draft(job_id, draft_id, body))


def _post() -> ExternalPost:
    return ExternalPost(
        id="post_1",
        platform="x",
        url="https://x.com/alice/status/1",
        author_id="author_1",
        author_name="alice",
        content="interesting engineering take on deterministic graphs",
        published_at=datetime.now(UTC),
        matched_topics=["graphs"],
    )


def _candidate(candidate_id: str = "x:post_1:draft_reply") -> InteractionCandidate:
    return InteractionCandidate(
        id=candidate_id,
        post=_post(),
        score=ConversationScore(
            relevance=0.8,
            novelty=0.7,
            discussability=0.6,
            practical_evidence=0.5,
            relationship_value=0.4,
            total=0.62,
            reasons=["relevant"],
        ),
        action=InteractionAction.DRAFT_REPLY,
        draft="a draft reply",
        approval_required=True,
    )


def _seed_candidate(store: Store, candidate_id="x:post_1:draft_reply"):
    InteractionRepository(store).upsert(_candidate(candidate_id), run_id="run_1")


# ---- finch review list ----

def test_review_list_json_lists_pending_original_drafts(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_job_and_draft(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "list", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert [p["draft_id"] for p in payload] == ["draft_1"]
    assert payload[0]["job_id"] == "job_1"
    assert payload[0]["content_type"] == "original"
    assert payload[0]["draft"] == "hello world"


def test_review_list_non_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_job_and_draft(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "list"])
    assert r.exit_code == 0, r.output
    assert "draft_1" in r.output
    assert "hello world" in r.output


def test_review_list_excludes_decided_drafts(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_job_and_draft(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "approve", "draft_1", "--json"])
    assert r.exit_code == 0, r.output

    r = CliRunner().invoke(app, ["review", "list", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == []


# ---- finch review show ----

def test_review_show_prints_body(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_job_and_draft(store, body="full draft body here")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "show", "draft_1"])
    assert r.exit_code == 0, r.output
    assert "full draft body here" in r.output


def test_review_show_unknown_draft_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "show", "draft_nope"])
    assert r.exit_code == 1
    assert "draft not found" in r.output


# ---- finch review approve / skip ----

def test_review_approve_writes_decision_and_intent(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_job_and_draft(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "approve", "draft_1", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["action"] == "accept"
    assert payload["job_id"] == "job_1"
    assert payload["draft_id"] == "draft_1"

    records = DecisionRecordRepository(store).list()
    assert len(records) == 1
    assert records[0].action == DecisionAction.ACCEPT


def test_review_approve_unknown_draft_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "approve", "draft_nope"])
    assert r.exit_code == 1
    assert "draft not found" in r.output


def test_review_skip_records_reason_and_skips_job(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_job_and_draft(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["review", "skip", "draft_1", "--reason", "not_now", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["action"] == "skip"
    assert payload["job_id"] == "job_1"

    records = DecisionRecordRepository(store).list()
    assert len(records) == 1
    assert records[0].action == DecisionAction.SKIP
    assert ContentJobRepository(store).get_job("job_1").status == ContentJobStatus.SKIPPED
    assert ContentJobRepository(store).get_job("job_1").reject_reason == "not_now"


# ---- finch review revise ----

def test_review_revise_rewrites_via_service(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_job_and_draft(store, body="original body")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: object())

    import finch.inbox.service as inbox_service

    def _fake_rewrite(runner, draft, instruction, cards_by_id, job):
        return draft.model_copy(update={"body": f"revised {instruction}"})

    class _FakeCritic:
        def model_dump(self, mode="json"):
            return {"checks": [], "outcome": "pass"}

    monkeypatch.setattr(inbox_service, "rewrite_with_instruction", _fake_rewrite)
    monkeypatch.setattr(inbox_service, "critique", lambda *a, **k: _FakeCritic())

    r = CliRunner().invoke(
        app, ["review", "revise", "draft_1", "--instruction", "make it shorter"]
    )
    assert r.exit_code == 0, r.output
    assert "revised make it shorter" in r.output


# ---- finch engagement list / show ----

def test_engagement_list_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_candidate(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["engagement", "list", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert [c["id"] for c in payload] == ["x:post_1:draft_reply"]


def test_engagement_list_non_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_candidate(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["engagement", "list"])
    assert r.exit_code == 0, r.output
    assert "x:post_1:draft_reply" in r.output
    assert "draft_reply" in r.output


def test_engagement_show(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_candidate(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["engagement", "show", "x:post_1:draft_reply"])
    assert r.exit_code == 0, r.output
    assert "x:post_1:draft_reply" in r.output
    assert "a draft reply" in r.output
    assert "total=0.620" in r.output


def test_engagement_show_unknown_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["engagement", "show", "nope"])
    assert r.exit_code == 1
    assert "candidate not found" in r.output


# ---- finch engagement approve / reject / edit ----

def test_engagement_approve_flips_status(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_candidate(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["engagement", "approve", "x:post_1:draft_reply"])
    assert r.exit_code == 0, r.output
    assert InteractionRepository(store).get("x:post_1:draft_reply").status == (
        InteractionStatus.APPROVED
    )


def test_engagement_reject_records_reason(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_candidate(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app, ["engagement", "reject", "x:post_1:draft_reply", "--reason", "not_relevant"]
    )
    assert r.exit_code == 0, r.output
    candidate = InteractionRepository(store).get("x:post_1:draft_reply")
    assert candidate.status == InteractionStatus.REJECTED
    assert candidate.reject_reason == "not_relevant"


def test_engagement_edit_saves_revised_draft(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_candidate(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    revised_file = tmp_path / "revised.md"
    revised_file.write_text("the human-edited reply")

    r = CliRunner().invoke(
        app, ["engagement", "edit", "x:post_1:draft_reply", "--file", str(revised_file)]
    )
    assert r.exit_code == 0, r.output
    candidate = InteractionRepository(store).get("x:post_1:draft_reply")
    assert candidate.revised_draft == "the human-edited reply"
    assert candidate.status == InteractionStatus.PROPOSED  # edit does not approve


def test_engagement_edit_unknown_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    revised_file = tmp_path / "revised.md"
    revised_file.write_text("body")

    r = CliRunner().invoke(
        app, ["engagement", "edit", "nope", "--file", str(revised_file)]
    )
    assert r.exit_code == 1
    assert "candidate not found" in r.output


# ---- finch engagement metrics ----

def test_engagement_metrics_renders(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_candidate(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["engagement", "metrics"])
    assert r.exit_code == 0, r.output
    assert "Finch Engagement Metrics" in r.output
