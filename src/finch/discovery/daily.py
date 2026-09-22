"""统一每日发现：sources sync → 投影 → CreatorEvidence → shortlist → 连接建议。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from finch.codex.runner import CodexRunner
from finch.connections.service import (
    ConnectionDecision,
    ConnectionOpportunity,
    build_connection_opportunity,
)
from finch.discovery.candidate_pool import PersonCandidate, build_pool
from finch.engagement.flow import EngagementRunResult, RankedPeer
from finch.engagement.models import (
    ConversationScore,
    DiscoverySnapshot,
    ExternalPost,
    Opportunity,
    RecommendationEntry,
)
from finch.engagement.opportunity import scored_post_to_opportunity, select_opportunity_set
from finch.engagement.relationship import PeerValue
from finch.engagement.scoring import ScoredPost
from finch.llm.base import StructuredInferenceRunner
from finch.peers.evidence_repo import CreatorEvidenceRepository
from finch.peers.evidence_service import CreatorEvidenceService
from finch.peers.person_service import PersonRepository, PersonService
from finch.peers.presentation import PersonPresentationRepository
from finch.peers.recommendations import (
    DailyRecommendationSet,
    select_daily_recommendations,
    select_home_items,
)
from finch.peers.scoring import score_person
from finch.peers.shortlist import ShortlistCandidate
from finch.settings import Settings
from finch.sources.models import RawArtifact
from finch.sources.opencli_gateway import OpenCliGateway
from finch.sources.orchestrator import DiscoveryOrchestrator, SyncResult
from finch.sources.query_plan import (
    build_context_by_source,
    build_discovery_plan,
    select_exploration_topic,
)
from finch.sources.store import ArtifactRepository
from finch.storage.repositories import (
    DiscoverySnapshotRepository,
    OpportunityRepository,
    PeerRepository,
)
from finch.storage.workspace import Workspace

_CONN_PROMPT = Path("prompts/connection-opportunity.md")
_TEXT_LIMIT = 600
_VALID_PLATFORMS = frozenset(
    {"x", "reddit", "github", "v2ex", "weixin", "xiaohongshu"}
)


class ConnectionOpportunityDraft(BaseModel):
    their_problem: str = ""
    user_contribution: str = ""
    why_now: str = ""
    why_not: str = ""
    min_action: str = ""
    decision: ConnectionDecision = ConnectionDecision.CONNECT
    user_evidence_refs: list[str] = Field(default_factory=list)
    their_artifact_ids: list[str] = Field(default_factory=list)


@dataclass
class DailyDiscoveryResult:
    """connect daily --refresh 的统一结果。"""

    run_id: str
    sync_results: list[SyncResult] = field(default_factory=list)
    recommendations: DailyRecommendationSet | None = None
    connections: list[ConnectionOpportunity] = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)
    engagement: EngagementRunResult | None = None
    collision_id: str = ""
    detail: str = ""
    metrics: RunMetrics = field(default_factory=lambda: RunMetrics())


@dataclass
class SourceRunMetric:
    """单源观测：数量 + 耗时 + 错误类型（不改变选择逻辑）。"""

    raw_count: int = 0
    normalized_count: int = 0
    projected_count: int = 0
    elapsed_seconds: float = 0.0
    error_type: str = ""


@dataclass
class RunMetrics:
    """每轮发现管线的计数观测，仅用于观测，不参与任何选择/排序。"""

    raw_count: int = 0
    normalized_count: int = 0
    people_count: int = 0
    eligible_count: int = 0
    recommended_count: int = 0
    llm_calls: int = 0
    llm_input_persons: int = 0
    llm_failures: int = 0
    llm_elapsed_seconds: float = 0.0
    filtered: dict[str, int] = field(default_factory=dict)
    sources: dict[str, SourceRunMetric] = field(default_factory=dict)


def artifact_to_external_post(art: RawArtifact) -> ExternalPost | None:
    platform = (art.author_identity.platform or "").strip()
    if platform not in _VALID_PLATFORMS:
        return None
    author_id = (art.author_identity.external_id or art.author_identity.handle or "").strip()
    if not author_id:
        return None
    content = "\n".join(p for p in [art.title or "", art.text] if p).strip()
    if len(content) < 20:
        return None
    metrics: dict[str, int | float] = {}
    for k, v in (art.metrics or {}).items():
        if isinstance(v, (int, float)):
            metrics[k] = v
    return ExternalPost(
        id=art.source_id or art.artifact_id,
        platform=platform,  # type: ignore[arg-type]
        url=art.canonical_url or "",
        author_id=author_id,
        author_name=art.author_identity.handle or author_id,
        content=content,
        published_at=art.published_at or art.retrieved_at,
        metrics=metrics,
        matched_topics=[],
    )


def _deterministic_score(art: RawArtifact) -> ConversationScore:
    """No LLM total: lightweight heuristic for browse cards from sources."""
    text = art.text or ""
    practical = 0.7 if art.source_type in {"commit", "repo", "release", "pull_request"} else 0.45
    discuss = 0.55 if "?" in text or "怎么" in text or "如何" in text else 0.4
    dims = {
        "relevance": 0.55,
        "novelty": 0.45,
        "discussability": discuss,
        "practical_evidence": practical,
        "relationship_value": 0.25,
    }
    total = (
        dims["relevance"] * 0.25
        + dims["novelty"] * 0.25
        + dims["discussability"] * 0.20
        + dims["practical_evidence"] * 0.20
        + dims["relationship_value"] * 0.10
    )
    return ConversationScore(
        **dims,
        total=round(min(1.0, total), 4),
        reasons=["projected from RawArtifact"],
    )


def opportunities_from_artifacts(
    artifacts: list[RawArtifact],
    *,
    limit: int = 10,
) -> list[Opportunity]:
    scored: list[ScoredPost] = []
    for art in artifacts:
        post = artifact_to_external_post(art)
        if post is None:
            continue
        scored.append(ScoredPost(post=post, score=_deterministic_score(art)))
    opps = [
        scored_post_to_opportunity(s, discovered_via="sources_sync")
        for s in scored
    ]
    return select_opportunity_set(opps, limit=limit)


def _truncate(text: str, n: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def build_shortlist_candidates(ws: Workspace) -> tuple[list[ShortlistCandidate], set[str]]:
    peers = PeerRepository(ws).list_all()
    person_svc = PersonService(PersonRepository(ws))
    evidence_repo = CreatorEvidenceRepository(ws)
    presentations = PersonPresentationRepository(ws)
    recent = presentations.recent_platforms(within_days=14)
    candidates: list[ShortlistCandidate] = []
    for peer in peers:
        person = person_svc.ensure_from_peer(peer)
        if peer.person_id != person.person_id:
            peer = peer.model_copy(update={"person_id": person.person_id})
            PeerRepository(ws).upsert(peer)
        evs = evidence_repo.list_for_person(person.person_id)
        score = score_person(evs)
        platform = (
            peer.platform_identities[0].platform if peer.platform_identities else ""
        )
        artifact_ids = [e.artifact_id for e in evs]
        if len(artifact_ids) < 2:
            artifact_ids = list(
                dict.fromkeys([*artifact_ids, *peer.source_refs, *peer.practice_evidence_refs])
            )
        last_shown = presentations.last_shown_at(person.person_id)
        has_new = bool(
            person.last_evidence_at
            and last_shown
            and person.last_evidence_at > last_shown
        )
        candidates.append(
            ShortlistCandidate(
                peer=peer,
                person_id=person.person_id,
                score=score,
                artifact_ids=artifact_ids[:8],
                why=peer.why_relevant,
                platform=platform,
                last_shown_at=last_shown,
                has_new_work=has_new,
            )
        )
    return candidates, recent


def _gate_by_window(
    candidates: list[ShortlistCandidate],
    *,
    ws: Workspace,
    lookback_hours: int | None,
    as_of: datetime,
    fresh: dict[str, RawArtifact] | None = None,
) -> tuple[list[ShortlistCandidate], int]:
    """按时间窗裁剪候选证据（P1）：仅保留窗口内 artifact，无窗口内证据者剔除。

    - ``lookback_hours=None`` 时不做时间门控（原样返回）。
    - ``fresh`` 是本轮同步到的 artifact（时间戳最新）；优先用它，避免无 ``published_at``
      的平台（v2ex/小红书）被存量首次 ``retrieved_at`` 误判为过期。
    - 未在 ``ArtifactRepository`` 命中的引用（如用户自身 ``practice_evidence_refs``）
      不是"发现的证据"，不做时间门控，原样保留。
    - 未来时间戳钳制为 ``as_of``。
    """
    if lookback_hours is None:
        return candidates, 0
    from datetime import timedelta

    cutoff = as_of - timedelta(hours=lookback_hours)
    repo = ArtifactRepository(ws)
    fresh_map = fresh or {}
    kept: list[ShortlistCandidate] = []
    stale = 0
    for c in candidates:
        ids = list(c.artifact_ids)
        if not ids:
            kept.append(c)
            continue
        by_id = {a.artifact_id: a for a in repo.list_by_ids(ids)}
        in_window: list[str] = []
        for aid in ids:
            art = fresh_map.get(aid) or by_id.get(aid)
            if art is None:
                in_window.append(aid)
                continue
            published = art.published_at or art.retrieved_at
            effective = published if published <= as_of else as_of
            if effective >= cutoff:
                in_window.append(aid)
        if not in_window:
            stale += 1
            continue
        kept.append(replace(c, artifact_ids=in_window))
    return kept, stale


def assess_connection(
    *,
    runner: StructuredInferenceRunner | CodexRunner,
    candidate: PersonCandidate,
    artifacts: list[RawArtifact],
    user_evidence_refs: list[str],
    user_contribution_hint: str = "",
) -> ConnectionOpportunity:
    allowed = {a.artifact_id for a in artifacts}
    prompt = _CONN_PROMPT.read_text().format(
        peer_id=candidate.peer.id,
        person_id=candidate.person_id,
        display_name=candidate.peer.display_name,
        platform=candidate.platform,
        current_work=candidate.peer.current_work,
        why_relevant=candidate.peer.why_relevant,
        their_artifacts=json.dumps(
            [
                {
                    "artifact_id": a.artifact_id,
                    "title": a.title or "",
                    "text": _truncate(a.text),
                    "url": a.canonical_url,
                }
                for a in artifacts
            ],
            ensure_ascii=False,
            indent=2,
        ),
        user_evidence_refs=json.dumps(user_evidence_refs, ensure_ascii=False),
        user_contribution_hint=user_contribution_hint or "(none)",
    )
    try:
        draft = runner.run(prompt, ConnectionOpportunityDraft)
        assert isinstance(draft, ConnectionOpportunityDraft)
    except Exception:
        return build_connection_opportunity(
            peer=candidate.peer,
            person_id=candidate.person_id,
            their_artifacts=list(allowed)[:8],
            their_summary=candidate.peer.current_work or candidate.peer.why_relevant,
            user_evidence_refs=user_evidence_refs,
            user_contribution=user_contribution_hint,
        )

    their_ids = [a for a in draft.their_artifact_ids if a in allowed] or list(allowed)[:4]
    user_refs = [r for r in draft.user_evidence_refs if r in set(user_evidence_refs)] or list(
        user_evidence_refs
    )
    skip = (
        draft.decision == ConnectionDecision.SKIP
        or not user_refs
        or not draft.user_contribution.strip()
    )
    if skip:
        return ConnectionOpportunity(
            opportunity_id=f"conn_{uuid4().hex[:12]}",
            person_id=candidate.person_id,
            peer_id=candidate.peer.id,
            their_problem=draft.their_problem,
            user_contribution="",
            why_now="",
            why_not=draft.why_not or "没有可追溯的用户真实经验可贡献",
            decision=ConnectionDecision.SKIP,
            their_artifact_ids=their_ids,
        )
    return ConnectionOpportunity(
        opportunity_id=f"conn_{uuid4().hex[:12]}",
        person_id=candidate.person_id,
        peer_id=candidate.peer.id,
        their_problem=draft.their_problem,
        user_contribution=draft.user_contribution.strip(),
        why_now=draft.why_now,
        why_not=draft.why_not,
        min_action=draft.min_action
        or "公开回复并补充自己的真实经验",
        decision=draft.decision,
        user_evidence_refs=user_refs,
        their_artifact_ids=their_ids,
    )


def _recommendation_entries(recs: DailyRecommendationSet) -> list[RecommendationEntry]:
    """把 DailyRecommendationSet 序列化为可持久化的快照条目（F1）。"""
    out: list[RecommendationEntry] = []
    for r in recs.all:
        c = r.candidate
        out.append(
            RecommendationEntry(
                person_id=r.person_id,
                peer_id=c.peer.id,
                display_name=c.peer.display_name or c.peer.id,
                tier=r.tier,
                rank=r.rank,
                direction=r.direction,
                platform=c.platform,
                score=c.score.total,
                artifact_ids=list(c.artifact_ids),
                hit_labels=list(c.hit_labels),
            )
        )
    return out


def run_daily_discovery(
    settings: Settings,
    *,
    runner: StructuredInferenceRunner | CodexRunner | None = None,
    gateway: OpenCliGateway | None = None,
    skip_sync: bool = False,
    lookback_hours: int | None = None,
    question: str | None = None,
) -> DailyDiscoveryResult:
    """跨平台抓取 → 标准化 → 人物与证据投影 → 分层推荐 → 快照。

    时间窗以 ``build_discovery_plan`` 生成的计划为准；过滤结果才是后续阶段的唯一输入。
    """
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    now = datetime.now(UTC)
    run_id = f"daily_{now.strftime('%Y%m%d%H%M%S')}"
    plan = build_discovery_plan(settings, lookback_hours=lookback_hours, intent=question)
    result = DailyDiscoveryResult(run_id=run_id)
    metrics = RunMetrics()

    gw = gateway or OpenCliGateway(profile=settings.opencli.profile)
    orch = DiscoveryOrchestrator(ws, gateway=gw)
    contexts = build_context_by_source(
        settings, all_sources=True, limit=20, topic=select_exploration_topic(settings)
    )

    if not skip_sync:
        result.sync_results = orch.sync_all(context_by_source=contexts)

    # Collect artifacts from this sync only — an empty round stays empty (no history).
    artifacts: list[RawArtifact] = []
    for sr in result.sync_results:
        artifacts.extend(sr.artifacts)

    # Observability only — never feeds selection/ranking.
    for sr in result.sync_results:
        metrics.raw_count += sr.raw_count
        metrics.normalized_count += sr.normalized_count
        metrics.sources[sr.source.value] = SourceRunMetric(
            raw_count=sr.raw_count,
            normalized_count=sr.normalized_count,
            projected_count=sr.projected_count,
            elapsed_seconds=sr.elapsed_seconds,
            error_type=sr.error_type,
        )
    from finch.discovery.filtering import filter_artifacts

    filtered, metrics.filtered = filter_artifacts(
        artifacts,
        excluded_content=list(plan.excluded_content),
        lookback_hours=plan.lookback_hours,
        as_of=now,
    )

    # Creator evidence via Codex (fail-soft per person)
    evidence_svc = CreatorEvidenceService(
        ws,
        runner=runner,
        max_persons=settings.discovery.daily_people.semantic_assess_limit,
    )
    if runner is not None:
        ev_started = time.perf_counter()
        ev_result = evidence_svc.assess()
        metrics.llm_elapsed_seconds = round(time.perf_counter() - ev_started, 4)
        metrics.llm_calls += ev_result.assessed_persons + ev_result.skipped_persons
        metrics.llm_input_persons += ev_result.assessed_persons
        metrics.llm_failures += len(ev_result.failures)
        if ev_result.failures:
            result.detail = "; ".join(ev_result.failures[:3])

    candidates, _ = build_shortlist_candidates(ws)
    candidates, stale_count = _gate_by_window(
        candidates,
        ws=ws,
        lookback_hours=plan.lookback_hours,
        as_of=now,
        fresh={a.artifact_id: a for a in artifacts},
    )
    if stale_count:
        metrics.filtered["stale"] = stale_count
    pool = build_pool(
        candidates,
        settings=settings,
        max_size=settings.discovery.daily_people.candidate_pool_size,
    )
    recs = select_daily_recommendations(pool.candidates, settings=settings)
    result.recommendations = recs

    metrics.people_count = len(candidates)
    metrics.eligible_count = sum(
        1 for c in candidates if len(c.artifact_ids) >= 1 and c.score.total > 0
    )
    metrics.recommended_count = recs.total

    # Browse opportunities from filtered artifacts (no second X/Reddit search)
    opps = opportunities_from_artifacts(
        filtered, limit=settings.engagement.max_display_opportunities
    )
    result.opportunities = opps
    opp_repo = OpportunityRepository(ws)
    for opp in opps:
        opp_repo.upsert(opp)

    peers_out: list[RankedPeer] = []
    for rec in recs.priority:
        peers_out.append(
            RankedPeer(
                profile=rec.candidate.peer,
                value=PeerValue(
                    topic_overlap=0.5,
                    practical_depth=0.5,
                    contribution_space=0.5,
                    continuity_potential=0.5,
                    repetition_penalty=0.0,
                    promotion_risk=0.0,
                    total=rec.candidate.score.total,
                    reasons=["people-first priority"],
                ),
            )
        )

    status = "succeeded" if (recs.priority or opps) else "empty"
    source_failures = [
        {
            "source": r.source.value,
            "error_type": r.error_type,
            "detail": r.detail[:300],
        }
        for r in result.sync_results
        if r.error_type
    ]
    plan_summary = {
        "schema_version": plan.schema_version,
        "intent": plan.intent,
        "ranking_question": plan.ranking_question,
        "lookback_hours": plan.lookback_hours,
        "freshness_boost_hours": plan.freshness_boost_hours,
        "config_fingerprint": plan.config_fingerprint,
        "source_queries": plan.source_queries,
    }
    engagement = EngagementRunResult(
        run_id=run_id,
        posts_found=len(artifacts),
        opportunities=opps,
        peers=peers_out,
        failures=[],
        status=status,  # type: ignore[arg-type]
        summary=f"sources→people priority={len(recs.priority)} opps={len(opps)}",
        context_fingerprint=plan.config_fingerprint,
        source_coverage={
            "sources": {
                r.source.value: {
                    "status": r.status.value,
                    "kind": r.kind.value if r.kind else "",
                    "error_type": r.error_type,
                    "detail": r.detail[:300],
                    "raw": r.raw_count,
                    "norm": r.normalized_count,
                    "projected": r.projected_count,
                }
                for r in result.sync_results
            },
            "priority": len(recs.priority),
            "connections": len(result.connections),
            "lookback_hours": plan.lookback_hours,
            "as_of": now.isoformat(),
            "source_failures": source_failures,
        },
    )
    result.engagement = engagement

    snapshot = DiscoverySnapshot(
        id=run_id,
        created_at=now,
        context_fingerprint=plan.config_fingerprint,
        source_coverage=engagement.source_coverage,
        failures=source_failures,
        ranked_opportunity_ids=[o.id for o in opps],
        ranking_version="people-first-1",
        plan_id=plan.plan_id,
        plan_summary=plan_summary,
        recommendations=_recommendation_entries(recs),
        recommendation_shortfall=dict(recs.shortfall),
        home_person_ids=[r.person_id for r in select_home_items(recs)],
    )
    DiscoverySnapshotRepository(ws).upsert(snapshot)

    result.metrics = metrics
    return result
