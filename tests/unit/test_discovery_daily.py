"""Unit tests for unified daily discovery (no X/Reddit search providers)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from finch.discovery.daily import (
    _gate_by_window,
    run_daily_discovery,
)
from finch.peers.evidence_service import (
    CreatorEvidenceBatchOutput,
    CreatorEvidenceBatchPerson,
    CreatorEvidenceItem,
)
from finch.peers.models import PeerProfile
from finch.peers.person import CreatorEvidenceKind
from finch.peers.scoring import PersonScoreBreakdown
from finch.peers.shortlist import ShortlistCandidate
from finch.settings import Settings, SourcesSettings, SourceTwitterPlan
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import (
    AuthorIdentity,
    RawArtifact,
    ResultKind,
    Source,
    SourceStatus,
)
from finch.sources.opencli_gateway import OpenCliGateway
from finch.sources.orchestrator import SyncResult
from finch.sources.query_plan import build_discovery_plan
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace


def _art(
    sid: str,
    author: str,
    text: str,
    *,
    published_at: datetime | None = None,
) -> RawArtifact:
    url = f"https://x.com/{author}/status/{sid}"
    return RawArtifact(
        artifact_id=artifact_id("twitter", "post", sid),
        source=Source.TWITTER,
        source_type="post",
        source_id=sid,
        canonical_url=url,
        author_identity=AuthorIdentity(
            platform="x", external_id=author, handle=author
        ),
        text=text,
        published_at=published_at,
        retrieved_at=datetime.now(UTC),
        capture_method="adapter",
        content_fingerprint=content_fingerprint(text, url=url),
    )


class _FakeRunner:
    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        if output_model is CreatorEvidenceBatchOutput:
            import json as _json

            persons_data = _json.loads(prompt.split("## Persons\n", 1)[1].strip())
            persons = []
            for p in persons_data:
                ids = [a["artifact_id"] for a in p["artifacts"]]
                items = []
                if ids:
                    items = [
                        CreatorEvidenceItem(
                            artifact_id=ids[0],
                            kind=CreatorEvidenceKind.CREATION,
                            claim="built something",
                            support=[ids[0]],
                            first_hand=True,
                            confidence=0.8,
                        ),
                        CreatorEvidenceItem(
                            artifact_id=ids[1] if len(ids) > 1 else ids[0],
                            kind=CreatorEvidenceKind.KNOWLEDGE_SHARING,
                            claim="shared practice",
                            support=[ids[1] if len(ids) > 1 else ids[0]],
                            confidence=0.7,
                        ),
                    ]
                persons.append(
                    CreatorEvidenceBatchPerson(person_id=p["person_id"], items=items)
                )
            return CreatorEvidenceBatchOutput(persons=persons)
        from finch.opportunities.assess import OpportunityDraft

        if output_model is OpportunityDraft:
            from finch.opportunities.models import ContributionForm, EntryKind

            return OpportunityDraft(
                topic="flaky evals",
                entry_kind=EntryKind.DIFFICULTY,
                why_me="与回归测试探索相关",
                why_continue="作者保存了 trace",
                contribution="做一张最小回放方法卡",
                form=ContributionForm.METHOD_CARD,
                expected_output="一张方法卡",
                scope="一个失败案例",
                recommend=True,
            )
        # CollisionDraft
        from finch.collisions.service import CollisionDraft

        return CollisionDraft(
            their_domain="evals",
            your_domain="agents",
            surface_similarity="both about quality",
            structural_similarity="gate on uncertainty",
            shared_question="when to re-run?",
            transferable_mechanism="risk_gate",
            falsifiable_hypothesis="gates cut flaky retries 20%",
            artifact_ids=["twitter:post:1", "twitter:post:2"],
        )


def test_run_daily_skips_network_with_seeded_artifacts(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    arts = [
        _art("1", "alice", "long enough content about agent harness failures and retries xx"),
        _art("2", "alice", "second long enough post about eval trajectories in production yy"),
    ]
    for a in arts:
        ArtifactRepository(ws).upsert(a)
    from finch.sources.projector import ArtifactProjector

    ArtifactProjector(ws).project(arts)

    settings = Settings(
        paths={"var_dir": tmp_path},  # type: ignore[arg-type]
        sources=SourcesSettings(twitter=SourceTwitterPlan(queries=[])),
        interests={"practice_refs": ["practice:1"], "long_term_interests": ["agents"]},  # type: ignore[arg-type]
    )
    settings.paths.var_dir = tmp_path

    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    result = run_daily_discovery(
        settings,
        runner=_FakeRunner(),
        gateway=OpenCliGateway(run_fn=fake_run),
        skip_sync=True,
    )
    assert result.engagement is not None
    assert result.engagement.status in {"succeeded", "empty"}
    # With evidence from fake runner, priority tier should populate
    assert result.recommendations is not None
    assert len(result.recommendations.priority) >= 1 or result.engagement.posts_found >= 2


def _seed_priority_candidate_settings(tmp_path: Path) -> Settings:
    """Seed alice's artifacts (priority candidate) and return matching Settings."""
    ws = Workspace(tmp_path)
    ws.ensure()
    arts = [
        _art("1", "alice", "long enough content about agent harness failures and retries xx"),
        _art("2", "alice", "second long enough post about eval trajectories in production yy"),
    ]
    for a in arts:
        ArtifactRepository(ws).upsert(a)
    from finch.sources.projector import ArtifactProjector

    ArtifactProjector(ws).project(arts)

    settings = Settings(
        paths={"var_dir": tmp_path},  # type: ignore[arg-type]
        sources=SourcesSettings(twitter=SourceTwitterPlan(queries=[])),
        interests={"practice_refs": ["practice:1"], "long_term_interests": ["agents"]},  # type: ignore[arg-type]
    )
    settings.paths.var_dir = tmp_path
    return settings


class _PromptCapturingRunner(_FakeRunner):
    def __init__(self) -> None:
        self.last_prompt: str | None = None

    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        from finch.opportunities.assess import OpportunityDraft

        if output_model is OpportunityDraft:
            self.last_prompt = prompt
            return OpportunityDraft(recommend=False, skip_reason="t")
        return super().run(prompt, output_model, timeout=timeout)


def test_snapshot_persists_opportunity_assessments(tmp_path: Path):
    """首选评估覆盖写入 DiscoverySnapshot，非刷新可读。"""
    from finch.storage.repositories import DiscoverySnapshotRepository

    ws = Workspace(tmp_path)
    settings = _seed_priority_candidate_settings(tmp_path)

    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    result = run_daily_discovery(
        settings,
        runner=_FakeRunner(),
        gateway=OpenCliGateway(run_fn=fake_run),
        skip_sync=True,
    )
    assert result.preferred_opportunity is not None
    assert result.opportunity_assessments
    assert result.opportunity_assessments[0].outcome == "recommended"

    snap = DiscoverySnapshotRepository(ws).latest()
    assert snap is not None
    assert snap.preferred_opportunity_id == result.preferred_opportunity.id
    assert snap.opportunity_assessments
    assert snap.opportunity_assessments[0].outcome == "recommended"
    assert snap.opportunity_assessments[0].opportunity_id == result.preferred_opportunity.id
    assert snap.opportunity_assessments[0].fingerprint


def test_daily_discovery_passes_confirmed_practices_to_assessor(tmp_path: Path):
    profile_path = tmp_path / "practice-profile.yaml"
    profile_path.write_text(
        "items:\n"
        "  - id: agent-100-days\n"
        "    domain: agent engineering\n"
        "    claim: 100 天路径\n"
        "    evidence_refs: [https://github.com/flingjie/Agent-100-Days]\n"
        "    status: sourced\n"
        "    confirmed: true\n"
    )
    settings = _seed_priority_candidate_settings(tmp_path)
    settings.paths.practice_profile_path = profile_path
    runner = _PromptCapturingRunner()

    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    run_daily_discovery(
        settings,
        runner=runner,
        gateway=OpenCliGateway(run_fn=fake_run),
        skip_sync=True,
    )
    # 模板示例里也含 "[agent-100-days]"，故断言渲染后的完整条目行。
    assert "- [agent-100-days] (sourced) agent engineering: 100 天路径" in (
        runner.last_prompt or ""
    )


def test_opportunity_assess_soft_stops_on_discovery_deadline(tmp_path: Path, monkeypatch):
    """评估时限耗尽时首选评估 soft-stop，不继续调 LLM。"""
    import time

    from finch.opportunities import discover as discover_mod

    ws = Workspace(tmp_path)
    ws.ensure()
    arts = [
        _art("1", "alice", "long enough content about agent harness failures and retries xx"),
        _art("2", "alice", "second long enough post about eval trajectories in production yy"),
        _art("3", "bob", "long enough content about agent reliability from another author xx"),
        _art("4", "bob", "second long enough post about eval trajectories from bob yy"),
    ]
    for a in arts:
        ArtifactRepository(ws).upsert(a)
    from finch.sources.projector import ArtifactProjector

    ArtifactProjector(ws).project(arts)

    settings = Settings(
        paths={"var_dir": tmp_path},  # type: ignore[arg-type]
        sources=SourcesSettings(twitter=SourceTwitterPlan(queries=[])),
        interests={"practice_refs": ["practice:1"], "long_term_interests": ["agents"]},  # type: ignore[arg-type]
    )
    settings.paths.var_dir = tmp_path
    settings.discovery.discovery_deadline_seconds = 1
    settings.discovery.daily_people.opportunity_assess_limit = 5

    # 评估循环内第 1 次 monotonic = assess_started；之后的检查一律超时。
    mono_calls = {"n": 0}

    def fake_monotonic() -> float:
        mono_calls["n"] += 1
        return 0.0 if mono_calls["n"] == 1 else 10.0

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    assess_calls = {"n": 0}

    def boom(*args, **kwargs):
        assess_calls["n"] += 1
        raise AssertionError("should not assess after deadline")

    monkeypatch.setattr(discover_mod, "discover_preferred_opportunity_outcome", boom)

    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    result = run_daily_discovery(
        settings,
        runner=_FakeRunner(),
        gateway=OpenCliGateway(run_fn=fake_run),
        skip_sync=True,
    )
    assert assess_calls["n"] == 0
    assert result.preferred_opportunity is None
    assert any(
        a.reason == "发现运行时限已到，未继续评估"
        for a in result.opportunity_assessments
    )


def test_run_daily_three_slot_cap_and_metrics(tmp_path: Path):
    """Phase 0 回归：短名单仍 ≤3，观测指标被记录且不改变选择。"""
    ws = Workspace(tmp_path)
    ws.ensure()
    arts = []
    for i, author in enumerate(["alice", "bob", "carol", "dave", "erin"]):
        arts.append(
            _art(
                f"{i * 2 + 1}",
                author,
                f"first long enough post from {author} about agent reliability",
            )
        )
        arts.append(
            _art(
                f"{i * 2 + 2}",
                author,
                f"second long enough post from {author} about eval trajectories",
            )
        )
    for a in arts:
        ArtifactRepository(ws).upsert(a)
    from finch.sources.projector import ArtifactProjector

    ArtifactProjector(ws).project(arts)

    settings = Settings(
        paths={"var_dir": tmp_path},  # type: ignore[arg-type]
        sources=SourcesSettings(twitter=SourceTwitterPlan(queries=[])),
        interests={"practice_refs": ["practice:1"], "long_term_interests": ["agents"]},  # type: ignore[arg-type]
    )
    settings.paths.var_dir = tmp_path

    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    result = run_daily_discovery(
        settings,
        runner=_FakeRunner(),
        gateway=OpenCliGateway(run_fn=fake_run),
        skip_sync=True,
    )

    # 观测指标不改变选择：priority 层不超过 priority_count=5。
    assert result.recommendations is not None
    assert len(result.recommendations.priority) <= 5
    assert result.metrics.recommended_count == result.recommendations.total
    assert result.metrics.people_count >= 5
    assert result.metrics.llm_calls >= 1


def _score() -> PersonScoreBreakdown:
    return PersonScoreBreakdown(
        sustained_creation=0.0,
        first_hand=0.0,
        sharing_willingness=0.0,
        cross_domain=0.0,
        joint_practice=0.0,
        connection_opportunity=0.0,
        total=1.0,
        popularity_context={},
    )


def test_gate_by_window_trims_stale_and_keeps_fresh(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    as_of = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    fresh = _art(
        "1",
        "alice",
        "fresh content about agent reliability enough text",
        published_at=as_of - timedelta(hours=1),
    )
    old = _art(
        "2",
        "bob",
        "old content about agent reliability enough text here",
        published_at=as_of - timedelta(days=40),
    )
    repo = ArtifactRepository(ws)
    repo.upsert(fresh)
    repo.upsert(old)

    def cand(person_id: str, ids: list[str]) -> ShortlistCandidate:
        return ShortlistCandidate(
            peer=PeerProfile(id=f"peer_{person_id}", platform_identities=[]),
            person_id=person_id,
            score=_score(),
            artifact_ids=ids,
        )

    mixed = cand("alice", [fresh.artifact_id, old.artifact_id])
    only_old = cand("bob", [old.artifact_id])
    practice = cand("carol", ["practice:1"])
    kept, stale = _gate_by_window(
        [mixed, only_old, practice], ws=ws, lookback_hours=24, as_of=as_of
    )
    assert stale == 1
    by_person = {c.person_id: c.artifact_ids for c in kept}
    assert by_person["alice"] == [fresh.artifact_id]
    assert "bob" not in by_person
    assert by_person["carol"] == ["practice:1"]


def test_run_daily_empty_round_no_history_fallback(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    old = _art(
        "1",
        "alice",
        "long enough old content about agent reliability",
        published_at=datetime.now(UTC) - timedelta(days=40),
    )
    ArtifactRepository(ws).upsert(old)
    from finch.sources.projector import ArtifactProjector

    ArtifactProjector(ws).project([old])
    settings = Settings(
        paths={"var_dir": tmp_path},  # type: ignore[arg-type]
        sources=SourcesSettings(twitter=SourceTwitterPlan(queries=[])),
        interests={"practice_refs": ["practice:1"], "long_term_interests": ["agents"]},  # type: ignore[arg-type]
    )
    settings.paths.var_dir = tmp_path

    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    result = run_daily_discovery(
        settings,
        runner=_FakeRunner(),
        gateway=OpenCliGateway(run_fn=fake_run),
        skip_sync=True,
        lookback_hours=24,
    )
    assert result.engagement is not None
    assert result.engagement.status == "empty"


def test_run_daily_source_failure_coverage_and_fingerprint(tmp_path, monkeypatch):
    from finch.sources.orchestrator import DiscoveryOrchestrator

    ws = Workspace(tmp_path)
    ws.ensure()
    now = datetime.now(UTC)
    fresh = _art("1", "alice", "fresh content about agent reliability enough text")
    old = _art(
        "2",
        "bob",
        "old content about agent reliability enough text here",
        published_at=now - timedelta(days=40),
    )
    ok = SyncResult(
        source=Source.TWITTER,
        status=SourceStatus.READY,
        raw_count=2,
        normalized_count=2,
        projected_count=2,
        kind=ResultKind.SUCCESS,
        artifacts=[fresh, old],
    )
    failed = SyncResult(
        source=Source.REDDIT,
        status=SourceStatus.DEGRADED,
        kind=ResultKind.ERROR,
        error_type="error",
        detail="reddit timeout",
    )

    def fake_sync_all(self, sources=None, context_by_source=None):
        return [ok, failed]

    monkeypatch.setattr(DiscoveryOrchestrator, "sync_all", fake_sync_all)

    settings = Settings(
        paths={"var_dir": tmp_path},  # type: ignore[arg-type]
        sources=SourcesSettings(twitter=SourceTwitterPlan(queries=[])),
        interests={"practice_refs": ["practice:1"], "long_term_interests": ["agents"]},  # type: ignore[arg-type]
    )
    settings.paths.var_dir = tmp_path

    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    result = run_daily_discovery(
        settings,
        runner=None,
        gateway=OpenCliGateway(run_fn=fake_run),
        skip_sync=False,
        lookback_hours=24,
    )
    eng = result.engagement
    assert eng is not None
    sources_cov = eng.source_coverage["sources"]
    assert sources_cov["reddit"]["error_type"] == "error"
    assert sources_cov["reddit"]["detail"] == "reddit timeout"
    assert eng.source_coverage["source_failures"]
    plan = build_discovery_plan(settings, lookback_hours=24)
    assert eng.context_fingerprint == plan.config_fingerprint


def test_snapshot_persists_plan_and_coverage(tmp_path, monkeypatch):
    from finch.sources.orchestrator import DiscoveryOrchestrator
    from finch.storage.repositories import DiscoverySnapshotRepository

    ws = Workspace(tmp_path)
    ws.ensure()
    fresh = _art("1", "alice", "fresh content about agent reliability enough text")
    ok = SyncResult(
        source=Source.TWITTER,
        status=SourceStatus.READY,
        raw_count=1,
        normalized_count=1,
        projected_count=1,
        kind=ResultKind.SUCCESS,
        artifacts=[fresh],
    )
    monkeypatch.setattr(
        DiscoveryOrchestrator,
        "sync_all",
        lambda self, sources=None, context_by_source=None: [ok],
    )
    settings = Settings(
        paths={"var_dir": tmp_path},  # type: ignore[arg-type]
        sources=SourcesSettings(twitter=SourceTwitterPlan(queries=[])),
        interests={"practice_refs": ["practice:1"], "long_term_interests": ["agents"]},  # type: ignore[arg-type]
    )
    settings.paths.var_dir = tmp_path

    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    run_daily_discovery(
        settings,
        runner=None,
        gateway=OpenCliGateway(run_fn=fake_run),
        skip_sync=False,
        lookback_hours=24,
    )
    plan = build_discovery_plan(settings, lookback_hours=24)
    snap = DiscoverySnapshotRepository(ws).latest()
    assert snap is not None
    assert snap.plan_id == plan.plan_id
    assert snap.context_fingerprint == plan.config_fingerprint
    assert snap.plan_summary["lookback_hours"] == 24
    assert snap.plan_summary["config_fingerprint"] == plan.config_fingerprint



def test_gate_by_window_prefers_fresh_retrieved_at(tmp_path):
    """无 published_at 的平台：本轮 fresh 时间戳优先于存量首次 retrieved_at。"""
    ws = Workspace(tmp_path)
    ws.ensure()
    as_of = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    stored = _art("1", "v2exer", "long enough content about agent reliability")
    stored = stored.model_copy(
        update={"published_at": None, "retrieved_at": as_of - timedelta(days=40)}
    )
    ArtifactRepository(ws).upsert(stored)
    fresh = stored.model_copy(update={"retrieved_at": as_of - timedelta(hours=1)})

    def cand() -> ShortlistCandidate:
        return ShortlistCandidate(
            peer=PeerProfile(id="peer_v2exer", platform_identities=[]),
            person_id="v2exer",
            score=_score(),
            artifact_ids=[stored.artifact_id],
        )

    kept, stale = _gate_by_window(
        [cand()],
        ws=ws,
        lookback_hours=24,
        as_of=as_of,
        fresh={fresh.artifact_id: fresh},
    )
    assert stale == 0
    assert kept[0].artifact_ids == [stored.artifact_id]

    # 不传 fresh：存量旧 retrieved_at 会被判过期。
    _, stale2 = _gate_by_window([cand()], ws=ws, lookback_hours=24, as_of=as_of)
    assert stale2 == 1
