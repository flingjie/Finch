"""Unit tests for the connection-first CLI: `finch connect` / `peers` / `conversations`.

No real LLM / gh / opencli: `connect daily` monkeypatches the discovery flow; repos are
backed by a temp file workspace.
"""

import json
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.conversations.models import ConversationThread
from finch.conversations.service import ConversationService
from finch.engagement.flow import EngagementRunResult, RankedPeer
from finch.engagement.relationship import PeerValue
from finch.peers.models import PeerProfile, PlatformIdentity, RelationshipStage
from finch.settings import Paths, Settings
from finch.storage.repositories import (
    ConversationThreadRepository,
    PeerRepository,
)
from finch.storage.workspace import Workspace


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))

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
        candidates=[],
        opportunities=[],
        peers=[RankedPeer(profile=profile, value=value)],
        failures=[],
        status="succeeded",
        summary="ok",
        context_fingerprint="ctx",
    )


def _daily_full():
    from finch.discovery.daily import DailyDiscoveryResult

    eng = _daily_result()
    return DailyDiscoveryResult(run_id=eng.run_id, engagement=eng)


def test_connect_daily_persists_peers_and_renders_sections(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_run_daily_full", lambda settings, **kwargs: _daily_full())

    r = CliRunner().invoke(app, ["connect", "daily", "--refresh"])
    assert r.exit_code == 0, r.output
    assert "需要继续的对话" in r.output
    assert "草稿预览:" not in r.output
    assert "观点候选" in r.output
    assert PeerRepository(ws).get("peer_abc") is not None


def _seed_new_opportunity(ws, opportunity_id: str = "opp_person_1"):
    from finch.opportunities.models import (
        ContributionForm,
        EntryKind,
        Proposal,
    )
    from finch.opportunities.models import (
        Opportunity as NewOpp,
    )
    from finch.opportunities.repository import OpportunityRepository as NewOppRepo

    NewOppRepo(ws).save(
        NewOpp(
            id=opportunity_id,
            person_ref="person_1",
            topic="失败回放",
            entry_kind=EntryKind.DIFFICULTY,
            why_me="与回归测试探索直接相关",
            why_continue="作者已保存 trace",
            proposal=Proposal(
                contribution="做一张最小回放方法卡",
                form=ContributionForm.METHOD_CARD,
                expected_output="一张方法卡",
                scope="一个失败案例",
            ),
        )
    )


def test_connect_prepare_with_opportunity(monkeypatch, tmp_path):
    from finch.opportunities.models import OpportunityStatus
    from finch.opportunities.prepare import PreparedContribution

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_new_opportunity(ws, "opp_person_1")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def _fake_prepare(*, opportunity, runner, service, **_kw):
        return PreparedContribution(
            opportunity=opportunity.model_copy(
                update={"status": OpportunityStatus.READY}
            ),
            artifacts=[],
        )

    monkeypatch.setattr(cli, "prepare_contribution", _fake_prepare)

    r = CliRunner().invoke(app, ["connect", "prepare", "--opportunity", "opp_person_1"])
    assert r.exit_code == 0, r.output
    assert "成果待审阅" in r.output
    assert "状态：ready" in r.output
    assert "失败回放" in r.output


def test_connect_prepare_json_returns_reviewable_body(monkeypatch, tmp_path):
    from finch.opportunities.models import (
        ArtifactKind,
        ExecutionStatus,
        OpportunityStatus,
    )
    from finch.opportunities.prepare import PreparedArtifact, PreparedContribution

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_new_opportunity(ws, "opp_person_1")
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def _fake_prepare(*, opportunity, runner, service, **_kw):
        return PreparedContribution(
            opportunity=opportunity.model_copy(
                update={"status": OpportunityStatus.READY}
            ),
            artifacts=[
                PreparedArtifact(
                    id="art_opp_person_1_method_card",
                    kind=ArtifactKind.METHOD_CARD,
                    body="适用处境：…",
                    source_refs=["https://x.com/alice/status/1"],
                    execution_status=ExecutionStatus.NOT_RUN,
                )
            ],
        )

    monkeypatch.setattr(cli, "prepare_contribution", _fake_prepare)
    r = CliRunner().invoke(
        app, ["connect", "prepare", "--opportunity", "opp_person_1", "--json"]
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    opp = payload["opportunities"][0]
    assert opp["opportunity_id"] == "opp_person_1"
    assert opp["status"] == "ready"
    assert opp["artifacts"][0]["body"] == "适用处境：…"
    assert opp["artifacts"][0]["source_refs"] == ["https://x.com/alice/status/1"]
    assert opp["artifacts"][0]["execution_status"] == "not_run"


def test_connect_prepare_caps_at_deep_prepare_limit(monkeypatch, tmp_path):
    from finch.opportunities.models import OpportunityStatus
    from finch.opportunities.prepare import PreparedContribution

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()

    ids = []
    for i in range(12):
        oid = f"opp_person_{i}"
        ids.append(oid)
        _seed_new_opportunity(ws, oid)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    calls = {"n": 0}

    def _fake_prepare(*, opportunity, runner, service, **_kw):
        calls["n"] += 1
        return PreparedContribution(
            opportunity=opportunity.model_copy(
                update={"status": OpportunityStatus.READY}
            ),
            artifacts=[],
        )

    monkeypatch.setattr(cli, "prepare_contribution", _fake_prepare)

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
    assert "preferred_opportunity" in payload
    assert payload["preferred_opportunity"] is None
    assert "opportunity_assessments" in payload
    # 阶段 3：移除旧三槽位 shortlist 兼容字段。
    assert "shortlist" not in payload


def test_connect_daily_json_includes_freshness(monkeypatch, tmp_path):
    from finch import cli

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))
    # 无快照时 snapshot_created_at 为 None、stale 为 False、refresh_status 为 missing
    r = CliRunner().invoke(app, ["connect", "daily", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["snapshot_created_at"] is None
    assert payload["stale"] is False
    assert payload["refresh_status"] == "missing"
    assert payload["snapshot_id"] is None


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


def test_connect_daily_home_shows_no_preferred_opportunity_message(monkeypatch, tmp_path):
    from finch.peers.presentation import PersonPresentationRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_daily_snapshot(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "daily"])
    assert r.exit_code == 0, r.output
    # 无首选机会时首页给出明确提示，不再展示 3 重点人物。
    assert "今天没有值得优先投入的讨论" in r.output
    # 首页不再展示人物，因此不记录 person exposure（人物曝光只发生在浏览视图）。
    assert PersonPresentationRepository(ws).list_all() == []


def test_connect_daily_home_renders_preferred_opportunity(monkeypatch, tmp_path):
    from finch.engagement.models import DiscoverySnapshot
    from finch.opportunities.models import (
        ContributionForm,
        EntryKind,
        Proposal,
    )
    from finch.opportunities.models import (
        Opportunity as OppAggregate,
    )
    from finch.opportunities.repository import OpportunityRepository as OppRepo
    from finch.storage.repositories import (
        DiscoverySnapshotRepository,
        PresentationRecordRepository,
    )

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    OppRepo(ws).save(
        OppAggregate(
            id="opp_person_1",
            person_ref="person_1",
            topic="失败回放",
            entry_kind=EntryKind.DIFFICULTY,
            why_me="与回归测试探索直接相关",
            why_continue="作者已保存 trace",
            proposal=Proposal(
                contribution="做一张最小回放方法卡",
                form=ContributionForm.METHOD_CARD,
                expected_output="一张方法卡",
                scope="一个失败案例",
            ),
        )
    )
    DiscoverySnapshotRepository(ws).upsert(
        DiscoverySnapshot(
            id="snap_1",
            created_at=datetime.now(UTC),
            context_fingerprint="ctx",
            preferred_opportunity_id="opp_person_1",
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "daily"])
    assert r.exit_code == 0, r.output
    assert "首选机会" in r.output
    assert "状态：proposed" in r.output
    assert "失败回放" in r.output
    assert "为什么值得参与" in r.output
    assert "最小贡献" in r.output
    # 文本前台实际展示首选 → PresentationRecord（漏斗口径）
    presented = PresentationRecordRepository(ws).list_all()
    assert len(presented) == 1
    assert presented[0].opportunity_id == "opp_person_1"


def test_connect_daily_replays_assessment_coverage_from_snapshot(monkeypatch, tmp_path):
    from finch.engagement.models import (
        DiscoverySnapshot,
        OpportunityAssessmentEntry,
    )
    from finch.storage.repositories import DiscoverySnapshotRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    DiscoverySnapshotRepository(ws).upsert(
        DiscoverySnapshot(
            id="snap_assess",
            created_at=datetime.now(UTC),
            context_fingerprint="ctx",
            opportunity_assessments=[
                OpportunityAssessmentEntry(
                    person_id="person_a",
                    outcome="skipped",
                    reason="已解决",
                    fingerprint="fp1",
                ),
                OpportunityAssessmentEntry(
                    person_id="person_b",
                    outcome="skipped",
                    reason="与现有回复重复",
                    fingerprint="fp2",
                ),
            ],
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "daily"])
    assert r.exit_code == 0, r.output
    assert "今天没有值得优先投入的讨论" in r.output
    assert "本次评估 2 位候选人：2 跳过" in r.output
    assert "为何未首选：已解决；与现有回复重复" in r.output

    r_json = CliRunner().invoke(app, ["connect", "daily", "--json"])
    assert r_json.exit_code == 0, r_json.output
    payload = json.loads(r_json.output)
    assert len(payload["opportunity_assessments"]) == 2
    assert payload["opportunity_assessments"][0]["reason"] == "已解决"


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


def test_persist_discovery_preserves_opportunity_assessments(tmp_path):
    """_persist_discovery 不得清空 run_daily_discovery 已写入的评估覆盖。"""
    from finch.engagement.models import (
        DiscoverySnapshot,
        OpportunityAssessmentEntry,
    )
    from finch.storage.repositories import DiscoverySnapshotRepository

    ws = Workspace(tmp_path)
    ws.ensure()
    assessments = [
        OpportunityAssessmentEntry(
            person_id="person_a",
            outcome="skipped",
            reason="发现运行时限已到，未继续评估",
            fingerprint="fp1",
        ),
        OpportunityAssessmentEntry(
            person_id="person_b",
            outcome="recommended",
            reason="",
            opportunity_id="opp_1",
            fingerprint="fp2",
        ),
    ]
    DiscoverySnapshotRepository(ws).upsert(
        DiscoverySnapshot(
            id="daily_test",
            created_at=datetime.now(UTC),
            context_fingerprint="cfg",
            preferred_opportunity_id="opp_1",
            opportunity_assessments=assessments,
        )
    )
    out = cli._persist_discovery(ws, _daily_result())
    assert out is not None
    assert out.preferred_opportunity_id == "opp_1"
    assert len(out.opportunity_assessments) == 2
    assert out.opportunity_assessments[0].reason == "发现运行时限已到，未继续评估"
    assert out.opportunity_assessments[1].opportunity_id == "opp_1"


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


def test_connect_daily_json_aged_snapshot_is_stale(monkeypatch, tmp_path):
    from finch.storage.repositories import DiscoverySnapshotRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_daily_snapshot(ws, snapshot_id="snap_old")
    repo = DiscoverySnapshotRepository(ws)
    snap = repo.latest()
    assert snap is not None
    repo.upsert(snap.model_copy(update={"created_at": datetime.now(UTC) - timedelta(hours=48)}))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connect", "daily", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["stale"] is True
    assert payload["refresh_status"] == "stale"
    assert payload["snapshot_id"] == "snap_old"
    assert payload["snapshot_created_at"] is not None


def test_connect_daily_json_empty_refresh_keeps_previous_snapshot(monkeypatch, tmp_path):
    from finch.discovery.daily import DailyDiscoveryResult

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_daily_snapshot(ws, snapshot_id="snap_old")
    empty_eng = _daily_result().model_copy(
        update={
            "run_id": "daily_empty",
            "opportunities": [],
            "peers": [],
            "posts_found": 0,
            "status": "empty",
        }
    )
    empty = DailyDiscoveryResult(run_id="daily_empty", engagement=empty_eng)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_run_daily_full", lambda settings, **kwargs: empty)

    r = CliRunner().invoke(app, ["connect", "daily", "--refresh", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["refresh_status"] == "empty"
    assert payload["snapshot_id"] == "snap_old"


def test_connect_record_presented_records_snapshot_members(monkeypatch, tmp_path):
    from finch.peers.presentation import PersonPresentationRepository
    from finch.storage.repositories import PresentationRecordRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_daily_snapshot(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app,
        [
            "connect",
            "record-presented",
            "--snapshot-id",
            "snap_1",
            "--person-id",
            "p1",
            "--opportunity-id",
            "opp_test_1",
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == {"ok": True, "persons": 1, "opportunities": 1}
    presented = [
        x for x in PersonPresentationRepository(ws).list_all() if x.event == "presented"
    ]
    assert {x.person_id for x in presented} == {"p1"}
    records = PresentationRecordRepository(ws).list_all()
    assert [rec.opportunity_id for rec in records] == ["opp_test_1"]


def test_connect_record_presented_rejects_unknown_ids_without_writing(monkeypatch, tmp_path):
    from finch.peers.presentation import PersonPresentationRepository
    from finch.storage.repositories import PresentationRecordRepository

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_daily_snapshot(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app,
        [
            "connect",
            "record-presented",
            "--snapshot-id",
            "snap_1",
            "--person-id",
            "p1",
            "--person-id",
            "ghost",
            "--opportunity-id",
            "opp_test_1",
            "--opportunity-id",
            "ghost_opp",
            "--json",
        ],
    )
    assert r.exit_code == 1
    payload = json.loads(r.output)
    assert payload["ok"] is False
    assert payload["unknown_persons"] == ["ghost"]
    assert payload["unknown_opportunities"] == ["ghost_opp"]
    assert PresentationRecordRepository(ws).list_all() == []
    assert PersonPresentationRepository(ws).list_all() == []


def test_connect_record_presented_rejects_nonlatest_snapshot(monkeypatch, tmp_path):
    from finch import cli

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))
    r = CliRunner().invoke(
        app,
        ["connect", "record-presented", "--snapshot-id", "snap_nonexistent", "--json"],
    )
    assert r.exit_code == 1
    assert json.loads(r.output)["ok"] is False


def test_connect_record_presented_rejects_bad_surface(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))
    r = CliRunner().invoke(
        app,
        [
            "connect",
            "record-presented",
            "--snapshot-id",
            "snap_1",
            "--surface",
            "bogus",
            "--json",
        ],
    )
    assert r.exit_code == 1
    assert "surface" in json.loads(r.output)["error"]
