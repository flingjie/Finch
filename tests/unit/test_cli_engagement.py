"""Unit tests for finch engagement CLI commands (Phase 5, human-in-the-loop)."""

from datetime import datetime

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionCandidate,
    InteractionStatus,
)
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import InteractionRepository


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


def _candidate(pid: str = "p1") -> InteractionCandidate:
    post = ExternalPost(
        id=pid,
        platform="x",
        url=f"https://x.com/alice/status/{pid}",
        author_id="alice",
        author_name="Alice",
        content="How do you test agent reliability in production?",
        published_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    score = ConversationScore(
        relevance=0.8,
        novelty=0.8,
        discussability=0.8,
        practical_evidence=0.8,
        relationship_value=0.8,
        total=0.8,
        reasons=["on topic", "debatable"],
    )
    return InteractionCandidate(
        id=f"x:{pid}:draft_reply",
        post=post,
        score=score,
        action=InteractionAction.DRAFT_REPLY,
        draft="Have you tried recording a failure replay?",
        intent="propose a verification method",
        source_summary="the claim about prod reliability",
        factual_risks=["assumes failure replay is available"],
        approval_required=True,
    )


def _seed(tmp_path, monkeypatch) -> tuple[Settings, InteractionRepository]:
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    repo = InteractionRepository(store)
    repo.upsert(_candidate(), run_id="run-1")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    return settings, repo


def test_engagement_subcommands_exist():
    r = CliRunner()
    for cmd in ["list", "show", "approve", "reject", "edit"]:
        res = r.invoke(app, ["engagement", cmd, "--help"])
        assert res.exit_code == 0, cmd


def test_list_prints_pending_candidates(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["engagement", "list"])
    assert r.exit_code == 0, r.output
    assert "x:p1:draft_reply" in r.output
    assert "draft_reply" in r.output


def test_list_empty(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    Store(settings.paths.db_path).init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["engagement", "list"])
    assert r.exit_code == 0, r.output
    assert "no pending candidates" in r.output


def test_show_prints_full_candidate(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["engagement", "show", "x:p1:draft_reply"])
    assert r.exit_code == 0, r.output
    assert "author: @Alice (alice)" in r.output
    assert "post_content:" in r.output
    assert "relevance=" in r.output
    assert "score_reasons: on topic, debatable" in r.output
    assert "draft: Have you tried recording a failure replay?" in r.output
    assert "intent: propose a verification method" in r.output
    assert "factual_risks:" in r.output


def test_show_missing_candidate_exits_1(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    Store(settings.paths.db_path).init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["engagement", "show", "x:missing:draft_reply"])
    assert r.exit_code == 1
    assert "not found" in r.output


def test_approve_transitions_status(monkeypatch, tmp_path):
    _, repo = _seed(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["engagement", "approve", "x:p1:draft_reply"])
    assert r.exit_code == 0, r.output
    assert repo.get("x:p1:draft_reply").status is InteractionStatus.APPROVED


def test_reject_records_reason(monkeypatch, tmp_path):
    _, repo = _seed(tmp_path, monkeypatch)
    r = CliRunner().invoke(
        app, ["engagement", "reject", "x:p1:draft_reply", "--reason", "fact_error"]
    )
    assert r.exit_code == 0, r.output
    got = repo.get("x:p1:draft_reply")
    assert got.status is InteractionStatus.REJECTED
    assert got.reject_reason == "fact_error"


def test_edit_saves_revision_without_approving(monkeypatch, tmp_path):
    _, repo = _seed(tmp_path, monkeypatch)
    revised = tmp_path / "revised.txt"
    revised.write_text("revised draft v2", encoding="utf-8")

    r = CliRunner().invoke(
        app, ["engagement", "edit", "x:p1:draft_reply", "--file", str(revised)]
    )
    assert r.exit_code == 0, r.output
    got = repo.get("x:p1:draft_reply")
    assert got.revised_draft == "revised draft v2"
    assert got.status is InteractionStatus.PROPOSED  # 保存版本不自动批准


def test_approve_missing_candidate_exits_1(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    Store(settings.paths.db_path).init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["engagement", "approve", "x:missing:draft_reply"])
    assert r.exit_code == 1
    assert "not found" in r.output
