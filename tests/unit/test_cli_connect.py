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
    Opportunity,
    SuggestedMode,
)
from finch.engagement.relationship import PeerValue
from finch.peers.models import PeerProfile, PlatformIdentity, RelationshipStage
from finch.settings import Paths, Settings
from finch.storage.repositories import (
    ConversationThreadRepository,
    InteractionRecordRepository,
    InteractionRepository,
    OpportunityRepository,
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

def _opportunity() -> Opportunity:
    return Opportunity(
        id="opp_test_1",
        peer_id="peer_abc",
        source_refs=["https://x.com/alice/status/1"],
        source_excerpt="interesting engineering take on deterministic graphs",
        content_fingerprint="abc",
        why_relevant="Concrete overlap with deterministic graph practice",
        opening="Ask how they replay failures across graph nodes",
        suggested_mode=SuggestedMode.DISCUSS,
        novelty_reason="active thread",
        score_total=0.82,
        post=_post(),
    )


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
        candidates=[],
        opportunities=[_opportunity()],
        peers=[RankedPeer(profile=profile, value=value)],
        failures=[],
        status="succeeded",
        summary="ok",
        context_fingerprint="ctx",
    )


def _daily_full():
    from finch.discovery.daily import DailyDiscoveryResult

    eng = _daily_result()
    return DailyDiscoveryResult(run_id=eng.run_id, engagement=eng, connections=[])


def test_connect_daily_persists_peers_and_renders_sections(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_run_daily_full", lambda settings, **kwargs: _daily_full())

    r = CliRunner().invoke(app, ["connect", "daily", "--refresh"])
    assert r.exit_code == 0, r.output
    assert "需要继续的对话" in r.output
    assert "新发现的交流机会" in r.output
    assert "为何相关: Concrete overlap with deterministic graph practice" in r.output
    assert "Ask how they replay failures across graph nodes" in r.output
    assert "草稿预览:" not in r.output
    assert "观点候选" in r.output
    assert PeerRepository(ws).get("peer_abc") is not None
    assert OpportunityRepository(ws).get("opp_test_1") is not None


def test_connect_prepare_with_opportunity(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    OpportunityRepository(ws).upsert(_opportunity())
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "fetch_post_by_url", lambda *a, **k: _post())
    monkeypatch.setattr(
        cli,
        "score_posts",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(cli, "generate_proposals", lambda *a, **k: [_candidate()])

    r = CliRunner().invoke(app, ["connect", "prepare", "--opportunity", "opp_test_1"])
    assert r.exit_code == 0, r.output
    assert "动作: 回复" in r.output
    assert "草稿预览: a draft reply" in r.output
    assert InteractionRepository(ws).get("x:post_1:draft_reply") is not None


def test_connect_prepare_caps_at_deep_prepare_limit(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()

    ids = []
    for i in range(12):
        oid = f"opp_{i}"
        ids.append(oid)
        OpportunityRepository(ws).upsert(
            _opportunity().model_copy(
                update={"id": oid, "source_refs": [f"https://x.com/a/status/{i}"]}
            )
        )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "fetch_post_by_url", lambda *a, **k: _post())
    calls = {"n": 0}

    def _gen(*a, **k):
        calls["n"] += 1
        return [_candidate(f"x:post_{calls['n']}:draft_reply")]

    monkeypatch.setattr(cli, "generate_proposals", _gen)
    monkeypatch.setattr(cli, "score_posts", lambda *a, **k: [])

    args = ["connect", "prepare"]
    for oid in ids:
        args.extend(["--opportunity", oid])
    r = CliRunner().invoke(app, args)
    assert r.exit_code == 1, r.output
    assert calls["n"] == 5
    assert "batch limit is 5" in r.output


def test_connect_prepare_requires_selection(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["connect", "prepare"])
    assert r.exit_code == 1
    assert "selection required" in r.output


def test_connect_daily_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_run_daily_full", lambda settings, **kwargs: _daily_full())

    r = CliRunner().invoke(app, ["connect", "daily", "--refresh", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["schema_version"] == 2
    assert payload["snapshot_id"] == "daily_test"
    assert [p["id"] for p in payload["peers"]] == ["peer_abc"]
    assert [o["id"] for o in payload["opportunities"]] == ["opp_test_1"]
    # 阶段 3：移除旧三槽位 shortlist 兼容字段。
    assert "shortlist" not in payload


def test_connect_daily_json_includes_freshness(monkeypatch, tmp_path):
    from finch import cli

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))
    # 无快照时 snapshot_created_at 为 None、stale 为 False、refresh_status 为 fresh
    r = CliRunner().invoke(app, ["connect", "daily", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["snapshot_created_at"] is None
    assert payload["stale"] is False
    assert payload["refresh_status"] == "fresh"


def test_persist_discovery_preserves_recommendations(tmp_path):
    """F1：_persist_discovery 不得覆盖 run_daily_discovery 已写入的完整 50 人推荐。"""
    from finch.engagement.models import DiscoverySnapshot, RecommendationEntry
    from finch.storage.repositories import DiscoverySnapshotRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()

    # 模拟 run_daily_discovery 已写入带完整推荐的快照。
    DiscoverySnapshotRepository(ws).upsert(
        DiscoverySnapshot(
            id="daily_test",
            created_at=datetime.now(UTC),
            context_fingerprint="ctx",
            ranked_opportunity_ids=["opp_test_1"],
            recommendations=[
                RecommendationEntry(
                    person_id="p1",
                    peer_id="peer_abc",
                    display_name="Alice",
                    tier="priority",
                    rank=0,
                    direction="peer",
                    platform="x",
                    score=0.5,
                    artifact_ids=["a0", "a1"],
                    hit_labels=["graphs"],
                )
            ],
            recommendation_shortfall={"insufficient_eligible": 2},
        )
    )

    snap = cli._persist_discovery(ws, _daily_result())
    assert snap is not None
    assert [r.person_id for r in snap.recommendations] == ["p1"]
    assert snap.recommendation_shortfall == {"insufficient_eligible": 2}


def test_connect_person_not_found(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["connect", "person", "does_not_exist"])
    assert r.exit_code == 1
    assert "not found" in r.output


def test_connect_today_is_pure_read(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    result = _daily_result()
    cli._persist_discovery(ws, result)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def boom(*a, **k):
        raise AssertionError("today must not call discovery")

    monkeypatch.setattr(cli, "_run_discovery", boom)
    monkeypatch.setattr(cli, "_run_daily_full", boom)
    r = CliRunner().invoke(app, ["connect", "today", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["snapshot_id"] == "daily_test"
    assert len(payload["opportunities"]) == 1


def test_connect_more_no_network(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    result = _daily_result()
    extra = _opportunity().model_copy(update={"id": "opp_test_2", "peer_id": "peer_abc"})
    result = result.model_copy(
        update={"opportunities": [*result.opportunities, extra]}
    )
    # Fix ranked ids via persist
    snap = cli._persist_discovery(ws, result)
    assert snap is not None
    # Present first only
    from finch.engagement.models import PresentationRecord
    from finch.storage.repositories import PresentationRecordRepository

    PresentationRecordRepository(ws).upsert(
        PresentationRecord(
            id=f"{snap.id}:opp_test_1",
            snapshot_id=snap.id,
            opportunity_id="opp_test_1",
            presented_at=datetime.now(UTC),
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "_run_discovery",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no discovery")),
    )
    r = CliRunner().invoke(
        app, ["connect", "more", "--snapshot", snap.id, "--limit", "5", "--json"]
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert [o["id"] for o in payload["opportunities"]] == ["opp_test_2"]


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
    monkeypatch.setattr(cli, "_run_daily_full", lambda settings, **kwargs: _daily_full())

    r = CliRunner().invoke(app, ["connect", "daily", "--refresh"])
    assert r.exit_code == 0, r.output

    persisted = PeerRepository(ws).get("peer_abc")
    assert persisted is not None
    assert persisted.shared_topics == ["graphs"]
    assert persisted.relationship_stage == RelationshipStage.RECURRING
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


def test_connect_with_requires_exactly_one_source(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["connect", "with"])
    assert r.exit_code == 1
    assert "--x" in r.output or "github" in r.output
    r = CliRunner().invoke(
        app, ["connect", "with", "--x", "a", "--github", "b"]
    )
    assert r.exit_code == 1


def test_connect_with_x_renders_source_and_saves(monkeypatch, tmp_path):
    from finch.engagement.named import NamedConnectResult
    from finch.peers.service import PeerService

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    peer = PeerService().from_author(platform="x", author_id="iFurySt", username="iFurySt")
    candidate = _candidate()
    candidate = candidate.model_copy(update={"peer_id": peer.id, "outline": "问 replay"})

    def fake_connect_named(**kwargs):
        PeerRepository(ws).upsert(peer)
        InteractionRepository(ws).upsert(candidate, run_id="with")
        return NamedConnectResult(
            status="ok",
            message="ok",
            peer=peer,
            proposal=candidate,
        )

    monkeypatch.setattr(cli, "connect_named", fake_connect_named)
    r = CliRunner().invoke(app, ["connect", "with", "--x", "iFurySt"])
    assert r.exit_code == 0, r.output
    assert "X" in r.output or "x.com" in r.output
    assert InteractionRepository(ws).get(candidate.id) is not None



# ---- D10 展示语义（--json 无曝光副作用；文本前台才记曝光） ----


def _seed_daily_snapshot(ws: Workspace, snapshot_id: str = "snap_1") -> None:
    from finch.engagement.models import DiscoverySnapshot, RecommendationEntry
    from finch.storage.repositories import DiscoverySnapshotRepository

    DiscoverySnapshotRepository(ws).upsert(
        DiscoverySnapshot(
            id=snapshot_id,
            created_at=datetime.now(UTC),
            context_fingerprint="ctx",
            ranked_opportunity_ids=["opp_test_1"],
            recommendations=[
                RecommendationEntry(
                    person_id="p1",
                    peer_id="peer_abc",
                    display_name="Alice",
                    tier="priority",
                    rank=0,
                    direction="peer",
                    platform="x",
                    score=0.5,
                    artifact_ids=["a0"],
                    hit_labels=["graphs"],
                )
            ],
            recommendation_shortfall={},
            home_person_ids=["p1"],
        )
    )
    OpportunityRepository(ws).upsert(_opportunity())


def test_connect_daily_json_does_not_record_exposure(monkeypatch, tmp_path):
    from finch.peers.presentation import PersonPresentationRepository
    from finch.storage.repositories import PresentationRecordRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_daily_snapshot(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "daily", "--json"])
    assert r.exit_code == 0, r.output
    assert PresentationRecordRepository(ws).list_all() == []
    assert PersonPresentationRepository(ws).list_all() == []


def test_connect_daily_text_records_person_exposure_idempotent(monkeypatch, tmp_path):
    from finch.peers.presentation import PersonPresentationRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_daily_snapshot(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "daily"])
    assert r.exit_code == 0, r.output
    repo = PersonPresentationRepository(ws)
    presented = [x for x in repo.list_all() if x.event == "presented"]
    assert {x.person_id for x in presented} == {"p1"}

    # 重复打开同一快照：记录总数不变，冷却不被反复延长。
    before = len(repo.list_all())
    CliRunner().invoke(app, ["connect", "daily"])
    assert len(repo.list_all()) == before


def test_connect_today_json_does_not_record_exposure(monkeypatch, tmp_path):
    from finch.storage.repositories import PresentationRecordRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    result = _daily_result()
    cli._persist_discovery(ws, result)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "today", "--json"])
    assert r.exit_code == 0, r.output
    assert PresentationRecordRepository(ws).list_all() == []


def test_connect_more_json_does_not_record_exposure(monkeypatch, tmp_path):
    from finch.storage.repositories import PresentationRecordRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    result = _daily_result()
    extra = _opportunity().model_copy(update={"id": "opp_test_2", "peer_id": "peer_abc"})
    result = result.model_copy(update={"opportunities": [*result.opportunities, extra]})
    snap = cli._persist_discovery(ws, result)
    assert snap is not None
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app, ["connect", "more", "--snapshot", snap.id, "--limit", "5", "--json"]
    )
    assert r.exit_code == 0, r.output
    assert PresentationRecordRepository(ws).list_all() == []


def test_connect_person_records_selected_text_only(monkeypatch, tmp_path):
    from finch.peers.presentation import PersonPresentationRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    PeerRepository(ws).upsert(
        PeerProfile(
            id="peer_abc",
            platform_identities=[PlatformIdentity(platform="x", author_id="a")],
            display_name="Alice",
            person_id="p1",
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    # --json 机器读取不记录 selected。
    CliRunner().invoke(app, ["connect", "person", "peer_abc", "--json"])
    assert PersonPresentationRepository(ws).list_all() == []

    # 文本前台明确查看 → 记录一次 selected（幂等）。
    r = CliRunner().invoke(app, ["connect", "person", "peer_abc"])
    assert r.exit_code == 0, r.output
    selected = [x for x in PersonPresentationRepository(ws).list_all() if x.event == "selected"]
    assert [x.person_id for x in selected] == ["p1"]
    CliRunner().invoke(app, ["connect", "person", "peer_abc"])
    assert len(PersonPresentationRepository(ws).list_all()) == 1


# ---- 关系承诺面：people shortlist / connections today 代理到关系域 ----


def _seed_commitment_thread(ws: Workspace, thread_id: str, *, pending: bool) -> None:
    from finch.conversations.models import FollowUpTrigger
    from finch.storage.repositories import ConversationThreadRepository

    ConversationThreadRepository(ws).upsert(
        ConversationThread(
            id=thread_id,
            peer_id="peer_abc",
            topic="graphs",
            pending_triggers=[FollowUpTrigger.OWN_COMMITMENT] if pending else [],
        )
    )


def test_people_shortlist_today_lists_commitments(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_commitment_thread(ws, "t_pending", pending=True)
    _seed_commitment_thread(ws, "t_idle", pending=False)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["people", "shortlist", "--json"])
    assert r.exit_code == 0, r.output
    assert [t["id"] for t in json.loads(r.output)] == ["t_pending"]


def test_connections_today_delegates_to_commitments(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_commitment_thread(ws, "t_pending", pending=True)
    _seed_commitment_thread(ws, "t_idle", pending=False)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connections", "today", "--json"])
    assert r.exit_code == 0, r.output
    assert [t["id"] for t in json.loads(r.output)] == ["t_pending"]


def test_people_shortlist_all_lists_every_thread(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_commitment_thread(ws, "t_pending", pending=True)
    _seed_commitment_thread(ws, "t_idle", pending=False)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["people", "shortlist", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert {t["id"] for t in json.loads(r.output)} == {"t_pending", "t_idle"}


# ---- 轻量反馈（P3）：内联记录 + no_time_today 非长期排斥 ----


def test_connect_feedback_inline_records_no_time_today(monkeypatch, tmp_path):
    from finch.storage.repositories import RecommendationFeedbackRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, [
        "connect", "feedback",
        "--snapshot", "s1",
        "--opportunity", "o1",
        "--dimension", "action",
        "--value", "no_time_today",
        "--reason", "今天没时间",
    ])
    assert r.exit_code == 0, r.output
    fbs = RecommendationFeedbackRepository(ws).list_all()
    assert len(fbs) == 1
    assert fbs[0].dimension == "action"
    assert fbs[0].value == "no_time_today"


def test_connect_feedback_requires_source(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "feedback"])
    assert r.exit_code == 1
    assert "--file" in r.output or "--opportunity" in r.output


def test_connect_feedback_inline_requires_snapshot(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, [
        "connect", "feedback",
        "--opportunity", "o1",
        "--dimension", "action",
        "--value", "no_opening",
    ])
    assert r.exit_code == 1
    assert "snapshot" in r.output


def test_connect_feedback_file_and_inline_conflict(monkeypatch, tmp_path):
    import json as _json

    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    f = tmp_path / "fb.json"
    f.write_text(_json.dumps([]))

    r = CliRunner().invoke(app, [
        "connect", "feedback",
        "--file", str(f),
        "--opportunity", "o1",
        "--dimension", "action",
        "--value", "prepare",
    ])
    assert r.exit_code == 1
    assert "not both" in r.output


def test_connect_feedback_inline_idempotent(monkeypatch, tmp_path):
    from finch.storage.repositories import RecommendationFeedbackRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    args = [
        "connect", "feedback",
        "--snapshot", "s1",
        "--opportunity", "o1",
        "--dimension", "action",
        "--value", "no_opening",
    ]
    CliRunner().invoke(app, args)
    CliRunner().invoke(app, args)

    # 稳定幂等键：同一次反馈重复提交覆盖而非追加。
    assert len(RecommendationFeedbackRepository(ws).list_all()) == 1


def test_persist_discovery_preserves_plan_fields(tmp_path):
    """_persist_discovery 不得覆盖 run_daily_discovery 已写入的 plan_id/plan_summary。"""
    from finch.engagement.models import DiscoverySnapshot
    from finch.storage.repositories import DiscoverySnapshotRepository

    ws = Workspace(tmp_path)
    ws.ensure()
    DiscoverySnapshotRepository(ws).upsert(
        DiscoverySnapshot(
            id="daily_test",
            created_at=datetime.now(UTC),
            context_fingerprint="cfg123",
            plan_id="plan_abc",
            plan_summary={"lookback_hours": 24},
            ranking_version="people-first-1",
        )
    )
    result = EngagementRunResult(
        run_id="daily_test",
        posts_found=0,
        failures=[],
        status="succeeded",
        summary="",
    )
    out = cli._persist_discovery(ws, result)
    assert out is not None
    assert out.plan_id == "plan_abc"
    assert out.plan_summary == {"lookback_hours": 24}
    assert out.ranking_version == "people-first-1"


def test_lookback_hours_parses_and_rejects():
    import typer

    from finch.cli import _lookback_hours

    assert _lookback_hours("24h") == 24
    assert _lookback_hours("30d") == 720
    assert _lookback_hours("720") == 720
    assert _lookback_hours(None) is None
    try:
        _lookback_hours("abc")
        raise AssertionError("expected BadParameter")
    except typer.BadParameter:
        pass


def test_connect_record_presented_rejects_nonlatest_snapshot(monkeypatch, tmp_path):
    from finch import cli

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))
    r = CliRunner().invoke(
        app,
        ["connect", "record-presented", "--snapshot-id", "snap_nonexistent", "--json"],
    )
    assert r.exit_code == 1
    assert json.loads(r.output)["ok"] is False
