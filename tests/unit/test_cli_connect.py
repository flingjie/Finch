"""Unit tests for the connection-first CLI: `finch connect` / `peers` / `conversations`.

No real LLM / gh / opencli: `connect daily` monkeypatches the discovery flow; repos are
backed by a temp SQLite store.
"""

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.conversations.models import ConversationThread
from finch.conversations.service import ConversationService
from finch.engagement.flow import EngagementRunResult, RankedPeer
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionProposal,
    InteractionStatus,
)
from finch.engagement.relationship import PeerValue
from finch.peers.models import PeerProfile, PlatformIdentity
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import (
    ConversationThreadRepository,
    InteractionRecordRepository,
    InteractionRepository,
    PeerRepository,
)


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


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


def _candidate(candidate_id: str = "x:post_1:draft_reply") -> InteractionProposal:
    return InteractionProposal(
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
        peer_id="peer_abc",
    )


def _seed_candidate(store: Store, candidate_id="x:post_1:draft_reply"):
    InteractionRepository(store).upsert(_candidate(candidate_id), run_id="run_1")


# ---- finch connect approve / reject / edit ----

def test_connect_approve_flips_status(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_candidate(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "approve", "x:post_1:draft_reply"])
    assert r.exit_code == 0, r.output
    assert InteractionRepository(store).get("x:post_1:draft_reply").status == (
        InteractionStatus.APPROVED
    )


def test_connect_reject_records_reason(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_candidate(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app, ["connect", "reject", "x:post_1:draft_reply", "--reason", "not_relevant"]
    )
    assert r.exit_code == 0, r.output
    candidate = InteractionRepository(store).get("x:post_1:draft_reply")
    assert candidate.status == InteractionStatus.REJECTED
    assert candidate.reject_reason == "not_relevant"


def test_connect_edit_saves_revised_draft_without_approving(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_candidate(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    revised_file = tmp_path / "revised.md"
    revised_file.write_text("the human-edited reply")

    r = CliRunner().invoke(
        app, ["connect", "edit", "x:post_1:draft_reply", "--file", str(revised_file)]
    )
    assert r.exit_code == 0, r.output
    candidate = InteractionRepository(store).get("x:post_1:draft_reply")
    assert candidate.revised_draft == "the human-edited reply"
    assert candidate.status == InteractionStatus.PROPOSED


# ---- finch connect record ----

def test_connect_record_requires_approval_and_is_idempotent(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_candidate(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    # 未批准：无法进入执行态。
    r = CliRunner().invoke(
        app, ["connect", "record", "x:post_1:draft_reply", "--url", "https://x.com/1"]
    )
    assert r.exit_code == 1
    assert "not approved" in r.output

    InteractionRepository(store).approve("x:post_1:draft_reply")
    r = CliRunner().invoke(
        app, ["connect", "record", "x:post_1:draft_reply", "--url", "https://x.com/1"]
    )
    assert r.exit_code == 0, r.output
    recs = InteractionRecordRepository(store).list_by_proposal("x:post_1:draft_reply")
    assert len(recs) == 1
    assert recs[0].peer_id == "peer_abc"
    assert recs[0].published_body == "a draft reply"

    # 同一 proposal 重复记录：幂等，不重复计两次。
    r = CliRunner().invoke(
        app, ["connect", "record", "x:post_1:draft_reply", "--url", "https://x.com/1"]
    )
    assert r.exit_code == 0, r.output
    assert len(InteractionRecordRepository(store).list_by_proposal("x:post_1:draft_reply")) == 1


def test_connect_record_unknown_proposal_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "record", "nope", "--url", "https://x.com/1"])
    assert r.exit_code == 1
    assert "not found" in r.output


# ---- finch connect daily ----

def _daily_result() -> EngagementRunResult:
    profile = PeerProfile(
        id="peer_abc",
        platform_identities=[PlatformIdentity(platform="x", author_id="author_1")],
        display_name="Alice",
    )
    value = PeerValue(
        topic_overlap=1.0,
        practical_depth=0.0,
        contribution_space=1.0,
        continuity_potential=0.0,
        repetition_penalty=0.0,
        promotion_risk=0.0,
        total=0.5,
        reasons=[],
    )
    return EngagementRunResult(
        run_id="daily_test",
        posts_found=1,
        candidates=[_candidate()],
        peers=[RankedPeer(profile=profile, value=value)],
        failures=[],
        status="succeeded",
        summary="ok",
    )


def test_connect_daily_persists_peers_and_renders_sections(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "run_discovery_engagement_flow", lambda *a, **k: _daily_result())

    r = CliRunner().invoke(app, ["connect", "daily"])
    assert r.exit_code == 0, r.output
    assert "需要继续的对话" in r.output
    assert "Alice" in r.output
    assert "可贡献的具体内容" in r.output
    assert "观点候选" in r.output
    # 发现结果落库，供 peers show / connect approve 进入。
    assert PeerRepository(store).get("peer_abc") is not None
    assert InteractionRepository(store).get("x:post_1:draft_reply") is not None


def test_connect_daily_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "run_discovery_engagement_flow", lambda *a, **k: _daily_result())

    r = CliRunner().invoke(app, ["connect", "daily", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["run_id"] == "daily_test"
    assert [p["id"] for p in payload["peers"]] == ["peer_abc"]
    assert [c["id"] for c in payload["contributions"]] == ["x:post_1:draft_reply"]


# ---- finch peers ----

def test_peers_list_and_show(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    profile = PeerProfile(
        id="peer_abc",
        platform_identities=[PlatformIdentity(platform="x", author_id="author_1")],
        display_name="Alice",
        shared_topics=["graphs"],
    )
    PeerRepository(store).upsert(profile)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["peers", "list"])
    assert r.exit_code == 0, r.output
    assert "peer_abc" in r.output
    assert "Alice" in r.output

    r = CliRunner().invoke(app, ["peers", "show", "peer_abc"])
    assert r.exit_code == 0, r.output
    assert "Alice" in r.output
    assert "graphs" in r.output

    r = CliRunner().invoke(app, ["peers", "show", "nope"])
    assert r.exit_code == 1
    assert "peer not found" in r.output


# ---- finch conversations ----

def _seed_thread(store: Store, conversation_id="thread_1") -> ConversationThread:
    thread = ConversationService().open_thread(peer_id="peer_abc", topic="agent evals")
    thread = thread.model_copy(update={"id": conversation_id})
    ConversationThreadRepository(store).upsert(thread)
    return thread


def test_conversations_list_and_show(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _seed_thread(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["conversations", "list"])
    assert r.exit_code == 0, r.output
    assert "thread_1" in r.output
    assert "agent evals" in r.output

    r = CliRunner().invoke(app, ["conversations", "show", "thread_1"])
    assert r.exit_code == 0, r.output
    assert "agent evals" in r.output
    assert "peer_abc" in r.output


def test_conversations_needs_follow_up(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    recent = datetime(2026, 9, 7, tzinfo=UTC)
    # 一条带未解问题的线索需要跟进；一条近期活跃且无未解问题的线索不需要。
    with_q = ConversationThread(
        id="t_q", peer_id="p", topic="t", open_questions=["how?"], last_activity_at=recent
    )
    clean = ConversationThread(id="t_clean", peer_id="p", topic="t", last_activity_at=recent)
    ConversationThreadRepository(store).upsert(with_q)
    ConversationThreadRepository(store).upsert(clean)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["conversations", "list", "--needs-follow-up"])
    assert r.exit_code == 0, r.output
    assert "t_q" in r.output
    assert "t_clean" not in r.output


def test_conversations_follow_up_restores_context(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    with_q = ConversationThread(
        id="t_q", peer_id="p", topic="agent evals", open_questions=["how to reproduce?"]
    )
    ConversationThreadRepository(store).upsert(with_q)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["conversations", "follow-up", "t_q"])
    assert r.exit_code == 0, r.output
    assert "how to reproduce?" in r.output
    assert "next_step" in r.output
