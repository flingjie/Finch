"""Unit tests for the connection-first CLI: `finch connect` / `peers` / `conversations`.

No real LLM / gh / opencli: `connect daily` monkeypatches the discovery flow; repos are
backed by a temp file workspace.
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
from finch.peers.models import PeerProfile, PlatformIdentity, RelationshipStage
from finch.settings import Paths, Settings
from finch.storage.repositories import (
    ConversationThreadRepository,
    InteractionRecordRepository,
    InteractionRepository,
    PeerRepository,
)
from finch.storage.workspace import Workspace


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


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
        why_this_person="writes about deterministic graphs",
        why_now="thread is still active",
        expected_conversation_opening="ask how they replay failures",
    )


def _seed_candidate(ws: Workspace, candidate_id="x:post_1:draft_reply"):
    InteractionRepository(ws).upsert(_candidate(candidate_id), run_id="run_1")


# ---- finch connect approve / reject / edit ----

def test_connect_approve_flips_status(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _seed_candidate(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "approve", "x:post_1:draft_reply"])
    assert r.exit_code == 0, r.output
    assert InteractionRepository(ws).get("x:post_1:draft_reply").status == (
        InteractionStatus.APPROVED
    )


def test_connect_reject_records_reason(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _seed_candidate(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app, ["connect", "reject", "x:post_1:draft_reply", "--reason", "not_relevant"]
    )
    assert r.exit_code == 0, r.output
    candidate = InteractionRepository(ws).get("x:post_1:draft_reply")
    assert candidate.status == InteractionStatus.REJECTED
    assert candidate.reject_reason == "not_relevant"


def test_connect_edit_saves_revised_draft_without_approving(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _seed_candidate(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    revised_file = tmp_path / "revised.md"
    revised_file.write_text("the human-edited reply")

    r = CliRunner().invoke(
        app, ["connect", "edit", "x:post_1:draft_reply", "--file", str(revised_file)]
    )
    assert r.exit_code == 0, r.output
    candidate = InteractionRepository(ws).get("x:post_1:draft_reply")
    assert candidate.revised_draft == "the human-edited reply"
    assert candidate.status == InteractionStatus.PROPOSED


# ---- finch connect record ----

def test_connect_record_requires_approval_and_is_idempotent(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _seed_candidate(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    # 未批准：无法进入执行态。
    r = CliRunner().invoke(
        app, ["connect", "record", "x:post_1:draft_reply", "--url", "https://x.com/1"]
    )
    assert r.exit_code == 1
    assert "not approved" in r.output

    InteractionRepository(ws).approve("x:post_1:draft_reply")
    r = CliRunner().invoke(
        app, ["connect", "record", "x:post_1:draft_reply", "--url", "https://x.com/1"]
    )
    assert r.exit_code == 0, r.output
    recs = InteractionRecordRepository(ws).list_by_proposal("x:post_1:draft_reply")
    assert len(recs) == 1
    assert recs[0].peer_id == "peer_abc"
    assert recs[0].published_body == "a draft reply"

    # 记录互动同时串进 (peer, topic) 的 ConversationThread。
    threads = ConversationThreadRepository(ws).list_all()
    assert len(threads) == 1
    assert threads[0].peer_id == "peer_abc"
    assert threads[0].topic == "graphs"
    assert threads[0].interaction_ids == ["rec_x:post_1:draft_reply"]

    # 同一 proposal 重复记录：幂等，不重复计两次。
    r = CliRunner().invoke(
        app, ["connect", "record", "x:post_1:draft_reply", "--url", "https://x.com/1"]
    )
    assert r.exit_code == 0, r.output
    assert len(InteractionRecordRepository(ws).list_by_proposal("x:post_1:draft_reply")) == 1
    assert len(ConversationThreadRepository(ws).list_all()[0].interaction_ids) == 1


def test_connect_record_unknown_proposal_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "record", "nope", "--url", "https://x.com/1"])
    assert r.exit_code == 1
    assert "not found" in r.output


# ---- finch connect create ----

def test_connect_create_fetch_failure(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "fetch_post_by_url", lambda *a, **k: None)

    r = CliRunner().invoke(app, ["connect", "create", "--input", "https://x.com/1"])
    assert r.exit_code == 1
    assert "could not fetch post" in r.output


def test_connect_create_saves_proposal(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    candidate = _candidate()
    monkeypatch.setattr(cli, "fetch_post_by_url", lambda *a, **k: _post())
    monkeypatch.setattr(cli, "score_posts", lambda *a, **k: [])
    monkeypatch.setattr(cli, "generate_proposals", lambda *a, **k: [candidate])

    r = CliRunner().invoke(
        app, ["connect", "create", "--input", "https://x.com/alice/status/1"]
    )
    assert r.exit_code == 0, r.output
    assert InteractionRepository(ws).get(candidate.id) is not None


# ---- finch connect daily ----

def _daily_result() -> EngagementRunResult:
    profile = PeerProfile(
        id="peer_abc",
        platform_identities=[PlatformIdentity(platform="x", author_id="author_1")],
        display_name="Alice",
        shared_topics=["graphs"],
        why_relevant="writes concretely about agent memory",
        next_context="ask about relationship facts",
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
    ws = Workspace(settings.paths.var_dir)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "run_discovery_engagement_flow", lambda *a, **k: _daily_result())

    r = CliRunner().invoke(app, ["connect", "daily"])
    assert r.exit_code == 0, r.output
    assert "需要继续的对话" in r.output
    assert "谁: Alice" in r.output
    assert "为什么值得连: writes concretely about agent memory" in r.output
    assert "下一步上下文: ask about relationship facts" in r.output
    assert "uv run finch peers show peer_abc" in r.output
    assert "peer_value=" not in r.output
    assert "动作: 回复" in r.output
    assert "草稿预览: a draft reply" in r.output
    assert "uv run finch connect approve x:post_1:draft_reply" in r.output
    assert "可贡献的具体内容" in r.output
    assert "观点候选" in r.output
    # 发现结果落库，供 peers show / connect approve 进入。
    assert PeerRepository(ws).get("peer_abc") is not None
    assert InteractionRepository(ws).get("x:post_1:draft_reply") is not None


def test_connect_prepare_renders_decision_cards(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "run_discovery_engagement_flow", lambda *a, **k: _daily_result())

    r = CliRunner().invoke(app, ["connect", "prepare"])
    assert r.exit_code == 0, r.output
    assert "动作: 回复" in r.output
    assert "为什么是这个人: writes about deterministic graphs" in r.output
    assert "为什么现在: thread is still active" in r.output
    assert "草稿预览: a draft reply" in r.output
    assert "uv run finch connect approve x:post_1:draft_reply" in r.output
    assert "\tdraft_reply\t" not in r.output
    assert "peer_value=" not in r.output


def test_connect_prepare_caps_cards(monkeypatch, tmp_path):
    settings = _settings(tmp_path)

    def _many():
        result = _daily_result()
        result.candidates = [
            _candidate(f"x:post_{i}:draft_reply") for i in range(8)
        ]
        return result

    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "run_discovery_engagement_flow", lambda *a, **k: _many())

    r = CliRunner().invoke(app, ["connect", "prepare"])
    assert r.exit_code == 0, r.output
    assert r.output.count("动作:") == 6
    assert "共 8 个候选，以上 6 个。" in r.output
    assert "uv run finch connect approve x:post_0:draft_reply" in r.output
    assert "uv run finch connect reject x:post_0:draft_reply --reason ..." in r.output
    assert "x:post_6:draft_reply" not in r.output


def test_connect_daily_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "run_discovery_engagement_flow", lambda *a, **k: _daily_result())

    r = CliRunner().invoke(app, ["connect", "daily", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["run_id"] == "daily_test"
    assert [p["id"] for p in payload["peers"]] == ["peer_abc"]
    assert [c["id"] for c in payload["contributions"]] == ["x:post_1:draft_reply"]


def test_connect_daily_preserves_accumulated_peer_fields(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    existing = PeerProfile(
        id="peer_abc",
        platform_identities=[PlatformIdentity(platform="x", author_id="author_1")],
        display_name="Alice",
        shared_topics=["graphs"],
        why_relevant="writes concretely",
        relationship_stage=RelationshipStage.CONVERSING,
        next_context="ask about replay",
    )
    PeerRepository(ws).upsert(existing)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "run_discovery_engagement_flow", lambda *a, **k: _daily_result())

    r = CliRunner().invoke(app, ["connect", "daily"])
    assert r.exit_code == 0, r.output

    persisted = PeerRepository(ws).get("peer_abc")
    assert persisted is not None
    assert persisted.shared_topics == ["graphs"]
    assert persisted.relationship_stage == RelationshipStage.CONVERSING
    assert persisted.why_relevant == "writes concretely"
    assert persisted.next_context == "ask about replay"


# ---- finch peers ----

def _seed_thread(ws: Workspace, conversation_id="thread_1") -> ConversationThread:
    thread = ConversationService().open_thread(peer_id="peer_abc", topic="agent evals")
    thread = thread.model_copy(update={"id": conversation_id})
    ConversationThreadRepository(ws).upsert(thread)
    return thread


def test_peers_list_and_show(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    profile = PeerProfile(
        id="peer_abc",
        platform_identities=[
            PlatformIdentity(
                platform="x",
                author_id="author_1",
                username="alice",
                url="https://x.com/alice",
            )
        ],
        display_name="Alice",
        shared_topics=["graphs"],
        why_relevant="writes concretely",
        next_context="ask about replay",
        source_refs=["https://x.com/alice/status/1"],
    )
    PeerRepository(ws).upsert(profile)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["peers", "list"])
    assert r.exit_code == 0, r.output
    assert "谁: Alice" in r.output
    assert "为什么值得连: writes concretely" in r.output
    assert "共同话题: graphs" in r.output
    assert "主页: https://x.com/alice" in r.output
    assert "代表帖: https://x.com/alice/status/1" in r.output
    assert "uv run finch peers show peer_abc" in r.output
    assert "id\tdisplay_name" not in r.output

    r = CliRunner().invoke(app, ["peers", "show", "peer_abc"])
    assert r.exit_code == 0, r.output
    assert "谁: Alice" in r.output
    assert "为什么值得连: writes concretely" in r.output
    assert "下一步上下文: ask about replay" in r.output
    assert "主页: https://x.com/alice" in r.output
    assert "代表帖: https://x.com/alice/status/1" in r.output
    assert "uv run finch connect prepare" in r.output

    r = CliRunner().invoke(app, ["peers", "show", "nope"])
    assert r.exit_code == 1
    assert "peer not found" in r.output


def test_conversations_list_and_show(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _seed_thread(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["conversations", "list"])
    assert r.exit_code == 0, r.output
    assert "话题: agent evals" in r.output
    assert "uv run finch conversations show thread_1" in r.output
    assert "id\tpeer_id\ttopic" not in r.output

    r = CliRunner().invoke(app, ["conversations", "show", "thread_1"])
    assert r.exit_code == 0, r.output
    assert "话题: agent evals" in r.output
    assert "同行: peer_abc" in r.output


def test_conversations_needs_follow_up(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    recent = datetime(2026, 9, 7, tzinfo=UTC)
    # 一条带未解问题的线索需要跟进；一条近期活跃且无未解问题的线索不需要。
    with_q = ConversationThread(
        id="t_q", peer_id="p", topic="t", open_questions=["how?"], last_activity_at=recent
    )
    clean = ConversationThread(id="t_clean", peer_id="p", topic="t", last_activity_at=recent)
    ConversationThreadRepository(ws).upsert(with_q)
    ConversationThreadRepository(ws).upsert(clean)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["conversations", "list", "--needs-follow-up"])
    assert r.exit_code == 0, r.output
    assert "话题: t" in r.output
    assert "未解问题: how?" in r.output
    assert "建议下一步: 回答未解问题或提出实验" in r.output
    assert "uv run finch conversations follow-up t_q" in r.output
    assert "t_clean" not in r.output


def test_conversations_follow_up_restores_context(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    with_q = ConversationThread(
        id="t_q", peer_id="p", topic="agent evals", open_questions=["how to reproduce?"]
    )
    ConversationThreadRepository(ws).upsert(with_q)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["conversations", "follow-up", "t_q"])
    assert r.exit_code == 0, r.output
    assert "话题: agent evals" in r.output
    assert "未解问题: how to reproduce?" in r.output
    assert "建议下一步: 回答未解问题或提出实验" in r.output
    assert "uv run finch conversations show t_q" in r.output
    assert "next_step:" not in r.output
    assert "conversation:" not in r.output