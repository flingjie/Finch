"""统一每日发现：sources sync → 投影 → CreatorEvidence → shortlist → 连接建议。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal

from finch.codex.runner import CodexRunner
from finch.discovery.candidate_pool import build_pool, match_interests
from finch.engagement.flow import EngagementRunResult, RankedPeer
from finch.engagement.models import (
    ActionFeedbackValue,
    DiscoverySnapshot,
    InterestFeedbackValue,
    OpportunityAssessmentEntry,
    OutcomeFeedbackValue,
    RecommendationEntry,
    RecommendationFeedback,
)
from finch.engagement.relationship import PeerValue
from finch.llm.base import StructuredInferenceRunner
from finch.opportunities.discover import discover_preferred_opportunity_outcome
from finch.opportunities.models import Opportunity as OppAggregate
from finch.opportunities.repository import OpportunityRepository as OppAggregateRepository
from finch.opportunities.repository import SkipAssessmentRepository
from finch.opportunities.service import OpportunityService
from finch.peers.evidence_repo import CreatorEvidenceRepository
from finch.peers.evidence_service import CreatorEvidenceService
from finch.peers.person import CreatorEvidence, CreatorEvidenceKind
from finch.peers.person_service import PersonRepository, PersonService
from finch.peers.presentation import PersonPresentationRepository
from finch.peers.recommendations import (
    DailyRecommendationSet,
    select_daily_recommendations,
    select_home_items,
)
from finch.peers.scoring import score_person
from finch.peers.shortlist import ShortlistCandidate
from finch.problems.render import render_active_problems
from finch.profile.models import load_practice_profile
from finch.profile.render import NONE_MARKER, render_user_practices
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
    PeerRepository,
    ProblemRepository,
    RecommendationFeedbackRepository,
)
from finch.storage.workspace import Workspace

_TEXT_LIMIT = 600
_VALID_PLATFORMS = frozenset(
    {"x", "reddit", "github", "v2ex", "weixin", "xiaohongshu"}
)


@dataclass
class DailyDiscoveryResult:
    """connect daily --refresh 的统一结果。"""

    run_id: str
    sync_results: list[SyncResult] = field(default_factory=list)
    recommendations: DailyRecommendationSet | None = None
    preferred_opportunity: OppAggregate | None = None
    opportunity_assessments: list[OpportunityAssessment] = field(default_factory=list)
    engagement: EngagementRunResult | None = None
    collision_id: str = ""
    detail: str = ""
    metrics: RunMetrics = field(default_factory=lambda: RunMetrics())


@dataclass
class OpportunityAssessment:
    """首选机会评估单条结果：推荐 / 跳过 / 评估失败。"""

    person_id: str
    outcome: Literal["recommended", "skipped", "eval_failed"]
    reason: str = ""
    opportunity: OppAggregate | None = None
    fingerprint: str = ""
    opportunity_id: str = ""


def _assessment_entries(
    assessments: list[OpportunityAssessment],
) -> list[OpportunityAssessmentEntry]:
    """把本轮评估结果序列化为可持久化的快照条目。"""
    return [
        OpportunityAssessmentEntry(
            person_id=a.person_id,
            outcome=a.outcome,
            reason=a.reason,
            opportunity_id=(
                a.opportunity.id
                if a.opportunity is not None
                else (a.opportunity_id or None)
            ),
            fingerprint=a.fingerprint,
        )
        for a in assessments
    ]


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
def _truncate(text: str, n: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _cross_domain_hook(evs: list[CreatorEvidence], fallback: str = "") -> str:
    """探索位 hook：优先跨领域桥接证据的 claim（拼首个 support），否则回退 why_relevant。

    只为意外位提供「具体吸引点」，避免出现「只是领域不同」的空泛说明；两者都空则留空。
    """
    for e in evs:
        if e.kind != CreatorEvidenceKind.CROSS_DOMAIN_BRIDGE:
            continue
        claim = (e.claim or "").strip()
        if not claim:
            continue
        detail = (e.support[0] if e.support else "").strip()
        return claim + (f"：{detail}" if detail else "")
    return (fallback or "").strip()


_REPLY_OPENING_SIGNALS = (
    "?",
    "？",
    "如何",
    "为什么",
    "怎样",
    "怎么",
    "workaround",
    "失败",
    "踩坑",
    "bug",
    "disagreement",
    "分歧",
    "卡点",
    "难题",
)


def _has_reply_opening(evs: list[CreatorEvidence]) -> bool:
    """从证据判断是否有可回复的开放问题/失败/workaround。

    这是与 ``_role_hint`` 同级的 best-effort 启发式；命中后通过 ``score_person`` 的
    ``has_reply_opening`` 激活 joint_practice / connection_opportunity 两个维度。
    """
    for e in evs:
        if e.kind == CreatorEvidenceKind.CONVERSATION_BEHAVIOR:
            return True
        blob = (e.claim or "").lower()
        if any(signal in blob for signal in _REPLY_OPENING_SIGNALS):
            return True
    return False


def _feedback_boost_map(
    feedback: list[RecommendationFeedback],
    opportunities: list[OppAggregate],
) -> dict[str, float]:
    """把推荐反馈折叠成 person_id -> 排序调整量。

    正向反馈（值得了解 / 准备 / 采用回复 / 再次交流）小幅上浮；长期排斥（不合适 /
    无切入点）小幅下浮。瞬态信号 no_time_today 不参与调节。
    """
    person_by_opp = {o.id: o.person_ref for o in opportunities if o.person_ref}
    boost: dict[str, float] = {}
    for fb in feedback:
        person_id = person_by_opp.get(fb.opportunity_id)
        if not person_id:
            continue
        delta = 0.0
        if fb.value == InterestFeedbackValue.WORTH_FOLLOWING.value:
            delta = 0.03
        elif fb.value == ActionFeedbackValue.PREPARE.value:
            delta = 0.05
        elif fb.value in {
            OutcomeFeedbackValue.ADOPTED_REPLIED.value,
            OutcomeFeedbackValue.REENGAGED.value,
        }:
            delta = 0.08
        elif fb.value in {
            InterestFeedbackValue.UNSUITABLE.value,
            ActionFeedbackValue.NO_OPENING.value,
        }:
            delta = -0.06
        if delta:
            boost[person_id] = boost.get(person_id, 0.0) + delta
    return boost
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
        score = score_person(evs, has_reply_opening=_has_reply_opening(evs))
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
                hook=_cross_domain_hook(evs, peer.why_relevant),
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
                hook=r.hook,
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
    user_practices = render_user_practices(
        load_practice_profile(settings.paths.practice_profile_path)
    )
    active_problems = render_active_problems(ProblemRepository(ws).list_all())
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
        question_peer_ids = sorted(
            {
                peer.id
                for peer in PeerRepository(ws).list_all()
                if any(
                    hit.category == "question"
                    for hit in match_interests(peer, settings=settings)
                )
            }
        )
        ev_started = time.perf_counter()
        ev_result = evidence_svc.assess(prefer_peer_ids=question_peer_ids)
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
    feedback = RecommendationFeedbackRepository(ws).list_all()
    feedback_boost = _feedback_boost_map(
        feedback, OppAggregateRepository(ws).list_all()
    )
    recs = select_daily_recommendations(
        pool.candidates, settings=settings, feedback_boost=feedback_boost
    )
    result.recommendations = recs

    metrics.people_count = len(candidates)
    metrics.eligible_count = sum(
        1 for c in candidates if len(c.artifact_ids) >= 1 and c.score.total > 0
    )
    metrics.recommended_count = recs.total

    # 首选机会（新聚合）：在评估时限内评估少量 priority 候选。
    # 时限从评估循环起算（不含 sync/证据 LLM），避免抓取耗尽预算后永远评不到首选。
    result.preferred_opportunity = None
    if runner is not None and recs.priority:
        assess_limit = max(
            1, settings.discovery.daily_people.opportunity_assess_limit
        )
        skip_repo = SkipAssessmentRepository(ws)
        opp_service = OpportunityService(OppAggregateRepository(ws))
        if user_practices == NONE_MARKER:
            # 无已确认实践画像时，机会评估必然没有贡献锚点；不浪费 LLM，直接确定性地跳过。
            for rec in recs.priority[:assess_limit]:
                result.opportunity_assessments.append(
                    OpportunityAssessment(
                        person_id=rec.candidate.person_id,
                        outcome="skipped",
                        reason="缺少已确认实践画像，无法判断可贡献点",
                    )
                )
            result.detail = (
                (result.detail + "; " if result.detail else "")
                + "skipped opportunity assessment: no confirmed practice profile"
            )
        else:
            assess_started = time.monotonic()
            deadline_seconds = max(1, settings.discovery.discovery_deadline_seconds)
            for rec in recs.priority[:assess_limit]:
                if time.monotonic() - assess_started >= deadline_seconds:
                    result.opportunity_assessments.append(
                        OpportunityAssessment(
                            person_id=rec.candidate.person_id,
                            outcome="skipped",
                            reason="发现运行时限已到，未继续评估",
                        )
                    )
                    result.detail = (
                        (result.detail + "; " if result.detail else "")
                        + f"opportunity assess soft-stopped at {deadline_seconds}s"
                    )
                    break
                top = rec.candidate
                arts = ArtifactRepository(ws).list_by_ids(top.artifact_ids)
                outcome = discover_preferred_opportunity_outcome(
                    runner=runner,
                    peer_id=top.peer.id,
                    display_name=top.peer.display_name,
                    platform=top.platform,
                    current_work=top.peer.current_work,
                    why_relevant=top.peer.why_relevant,
                    person_ref=top.person_id,
                    artifacts=arts,
                    service=opp_service,
                    user_context=question or plan.ranking_question or "",
                    user_practices=user_practices,
                    active_problems=active_problems,
                    skips=skip_repo,
                )
                result.opportunity_assessments.append(
                    OpportunityAssessment(
                        person_id=top.person_id,
                        outcome=outcome.outcome,
                        reason=outcome.reason,
                        opportunity=outcome.opportunity,
                        fingerprint=outcome.fingerprint,
                        opportunity_id=outcome.opportunity_id,
                    )
                )
                if outcome.opportunity is not None:
                    result.preferred_opportunity = outcome.opportunity
                    break

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

    status = "succeeded" if recs.priority else "empty"
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
        peers=peers_out,
        status=status,  # type: ignore[arg-type]
        summary=f"sources→people priority={len(recs.priority)}",
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
        ranked_opportunity_ids=[],
        ranking_version="people-first-1",
        plan_id=plan.plan_id,
        plan_summary=plan_summary,
        recommendations=_recommendation_entries(recs),
        recommendation_shortfall=dict(recs.shortfall),
        home_person_ids=[r.person_id for r in select_home_items(recs)],
        preferred_opportunity_id=(
            result.preferred_opportunity.id if result.preferred_opportunity else ""
        ),
        opportunity_assessments=_assessment_entries(result.opportunity_assessments),
    )
    DiscoverySnapshotRepository(ws).upsert(snapshot)

    result.metrics = metrics
    return result
