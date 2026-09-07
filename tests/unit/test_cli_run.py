"""Unit tests for finch weekly/decide/next/voice/author CLI commands."""
import json
from datetime import UTC, datetime

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
from finch.content.models import Draft, DraftKind
from finch.content.voice import (
    ApprovedExample,
    RejectedExample,
    load_voice_profile,
    save_voice_profile,
)
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.settings import AuthorAccountConfig, Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import (
    ContentJobRepository,
    DecisionRecordRepository,
    DraftRepository,
    PublicationIntentRepository,
)


def test_weekly_renders(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["weekly"])
    assert r.exit_code == 0, r.output
    assert "Finch Weekly Review" in r.output
    assert "建议" in r.output


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
        status=ContentJobStatus.PROPOSED,
    )
    kw.update(overrides)
    return ContentJob(**kw)


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


def _seed_accept_decision(store, draft_id, *, revised_body=None):
    DecisionRecordRepository(store).save(
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
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d1", "original ai draft body")
    _seed_accept_decision(store, "d1", revised_body="human revised body")
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
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d2", "original ai draft body")
    _seed_accept_decision(store, "d2")
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


def test_voice_approve_example_requires_decision(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d3", "body")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["voice", "approve-example", "d3"])
    assert r.exit_code == 1
    assert "not accepted" in r.output


def test_voice_approve_example_removes_from_rejected(monkeypatch, tmp_path):
    settings = _voice_settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_draft(store, "d7", "body")
    _seed_accept_decision(store, "d7")
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


def test_author_sync_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    settings.author_accounts = [AuthorAccountConfig(handle="test", enabled=True)]
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "verify_account", lambda cfg, client, store: object())
    monkeypatch.setattr(cli, "sync_posts", lambda acc, client, store, **kw: 3)
    monkeypatch.setattr(cli, "reconcile", lambda store: type("R", (), {
        "linked": [], "needs_manual": [], "awaiting": [],
    })())
    r = CliRunner().invoke(app, ["author", "sync", "--json"])
    assert r.exit_code == 0, r.output
    assert '"synced": 3' in r.output


def test_author_sync_error_is_clean(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    settings.author_accounts = [AuthorAccountConfig(handle="test", enabled=True)]
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def fail_verify(cfg, client, store):
        raise ValueError("mismatch")

    monkeypatch.setattr(cli, "verify_account", fail_verify)
    r = CliRunner().invoke(app, ["author", "sync"])
    assert r.exit_code == 1
    assert isinstance(r.exception, SystemExit), repr(r.exception)
    assert "mismatch" in r.output
    assert "Traceback" not in r.output
    assert r.output.count("\n") <= 1


def test_decide_accept(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    ContentJobRepository(store).upsert_job(
        _job(job_id="j1", author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"))
    )
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="b", content_job_id="j1")
    )
    r = CliRunner().invoke(app, ["decide", "j1", "--action", "accept", "--json"])
    assert r.exit_code == 0, r.output
    assert '"action": "accept"' in r.output
    record = DecisionRecordRepository(store).get("j1")
    assert record is not None and record.action.value == "accept"
    assert PublicationIntentRepository(store).get("d1") is not None


def test_decide_skip(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    ContentJobRepository(store).upsert_job(
        _job(job_id="j1", author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"))
    )
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="b", content_job_id="j1")
    )
    r = CliRunner().invoke(
        app, ["decide", "j1", "--action", "skip", "--reason", "not_now", "--json"]
    )
    assert r.exit_code == 0, r.output
    assert ContentJobRepository(store).get_job("j1").status.value == "skipped"
    assert DecisionRecordRepository(store).get("j1").action.value == "skip"


def test_decide_revise(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    ContentJobRepository(store).upsert_job(
        _job(job_id="j1", author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"))
    )
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="b", content_job_id="j1")
    )

    class _FakeInboxDecisionService:
        def __init__(self, **kwargs):
            pass

        def revise(self, item_id, instruction, *, runner, cards_by_id):
            return {"new_body": "v2", "diff": "", "critic": {}}

    monkeypatch.setattr(cli, "InboxDecisionService", lambda **kw: _FakeInboxDecisionService())
    r = CliRunner().invoke(
        app, ["decide", "j1", "--action", "revise", "--instruction", "语气弱一点", "--json"]
    )
    assert r.exit_code == 0, r.output
    assert "v2" in r.output


def test_decide_revise_codex_error_emits_structured_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    ContentJobRepository(store).upsert_job(
        _job(job_id="j1", author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"))
    )
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="b", content_job_id="j1")
    )

    class _FailingInboxDecisionService:
        def __init__(self, **kwargs):
            pass

        def revise(self, item_id, instruction, *, runner, cards_by_id):
            raise RuntimeError("codex exec failed")

    monkeypatch.setattr(cli, "InboxDecisionService", lambda **kw: _FailingInboxDecisionService())
    r = CliRunner().invoke(
        app, ["decide", "j1", "--action", "revise", "--instruction", "语气弱一点", "--json"]
    )
    assert r.exit_code == 1, r.output
    assert isinstance(r.exception, SystemExit), repr(r.exception)
    assert '"status": "error"' in r.output
    assert "codex exec failed" in r.output


def test_next_json_returns_card(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    ContentJobRepository(store).upsert_job(
        _job(
            job_id="j1",
            author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
            core_message="topic here", why_now="why now",
        )
    )
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="body", content_job_id="j1")
    )
    r = CliRunner().invoke(app, ["next", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["status"] == "review_required"
    assert payload["job_id"] == "j1"
    assert payload["track"] == "original"
    assert payload["draft"] == "body"
    assert payload["must_ask"] is False
    assert payload["ask_reasons"] == []


def test_next_json_none(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["next", "--json"])
    assert r.exit_code == 0, r.output
    assert '"status": "none"' in r.output
