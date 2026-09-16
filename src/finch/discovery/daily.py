"""统一每日发现：sources sync → 投影 → CreatorEvidence → shortlist → 连接建议。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from finch.codex.runner import CodexRunner
from finch.collisions.service import CollisionService
from finch.connections.service import (
    ConnectionDecision,
    ConnectionOpportunity,
    build_connection_opportunity,
)
from finch.connections.store import ConnectionOpportunityRepository
from finch.engagement.flow import EngagementRunResult, RankedPeer
from finch.engagement.models import (
    ConversationScore,
    DiscoverySnapshot,
    ExternalPost,
    Opportunity,
)
from finch.engagement.opportunity import scored_post_to_opportunity, select_opportunity_set
from finch.engagement.relationship import PeerValue
from finch.engagement.scoring import ScoredPost
from finch.llm.base import StructuredInferenceRunner
from finch.peers.evidence_repo import CreatorEvidenceRepository
from finch.peers.evidence_service import CreatorEvidenceService
from finch.peers.person_service import PersonRepository, PersonService
from finch.peers.presentation import PersonPresentationRepository
from finch.peers.scoring import score_person
from finch.peers.shortlist import ShortlistCandidate, ShortlistItem, select_daily_shortlist
from finch.settings import Settings
from finch.sources.models import RawArtifact
from finch.sources.opencli_gateway import OpenCliGateway
from finch.sources.orchestrator import DiscoveryOrchestrator, SyncResult
from finch.sources.query_plan import build_context_by_source
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
    shortlist: list[ShortlistItem] = field(default_factory=list)
    connections: list[ConnectionOpportunity] = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)
    engagement: EngagementRunResult | None = None
    collision_id: str = ""
    detail: str = ""


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


def assess_connection_for_slot(
    *,
    runner: StructuredInferenceRunner | CodexRunner,
    item: ShortlistItem,
    artifacts: list[RawArtifact],
    user_evidence_refs: list[str],
    user_contribution_hint: str = "",
) -> ConnectionOpportunity:
    allowed = {a.artifact_id for a in artifacts}
    prompt = _CONN_PROMPT.read_text().format(
        peer_id=item.candidate.peer.id,
        person_id=item.candidate.person_id,
        display_name=item.candidate.peer.display_name,
        platform=item.candidate.platform,
        current_work=item.candidate.peer.current_work,
        why_relevant=item.candidate.why,
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
            peer=item.candidate.peer,
            person_id=item.candidate.person_id,
            their_artifacts=list(allowed)[:8],
            their_summary=item.candidate.peer.current_work or item.candidate.why,
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
            person_id=item.candidate.person_id,
            peer_id=item.candidate.peer.id,
            their_problem=draft.their_problem,
            user_contribution="",
            why_now="",
            why_not=draft.why_not or "没有可追溯的用户真实经验可贡献",
            decision=ConnectionDecision.SKIP,
            their_artifact_ids=their_ids,
        )
    return ConnectionOpportunity(
        opportunity_id=f"conn_{uuid4().hex[:12]}",
        person_id=item.candidate.person_id,
        peer_id=item.candidate.peer.id,
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


def run_daily_discovery(
    settings: Settings,
    *,
    runner: StructuredInferenceRunner | CodexRunner | None = None,
    gateway: OpenCliGateway | None = None,
    skip_sync: bool = False,
    generate_collision: bool = True,
) -> DailyDiscoveryResult:
    """跨平台抓取 → 标准化 → 人物与证据投影 → 三槽位 → 连接建议。"""
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    run_id = f"daily_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    result = DailyDiscoveryResult(run_id=run_id)

    gw = gateway or OpenCliGateway(profile=settings.opencli.profile)
    orch = DiscoveryOrchestrator(ws, gateway=gw)
    contexts = build_context_by_source(settings, all_sources=True, limit=20)

    if not skip_sync:
        result.sync_results = orch.sync_all(context_by_source=contexts)

    # Collect artifacts from this sync (and fall back to store)
    artifacts: list[RawArtifact] = []
    for sr in result.sync_results:
        artifacts.extend(sr.artifacts)
    if not artifacts:
        from finch.sources.store import ArtifactRepository

        artifacts = ArtifactRepository(ws).list_all()

    # Creator evidence via Codex (fail-soft per person)
    evidence_svc = CreatorEvidenceService(ws, runner=runner)
    if runner is not None:
        ev_result = evidence_svc.assess()
        if ev_result.failures:
            result.detail = "; ".join(ev_result.failures[:3])

    candidates, recent = build_shortlist_candidates(ws)
    items = select_daily_shortlist(candidates, recent_platforms=recent)
    presentations = PersonPresentationRepository(ws)
    for item in items:
        presentations.record_shown(
            person_id=item.candidate.person_id,
            peer_id=item.candidate.peer.id,
            platform=item.candidate.platform,
            slot=item.slot.value,
        )
    result.shortlist = items

    # Connection opportunities for shortlist slots
    conn_repo = ConnectionOpportunityRepository(ws)
    user_refs = list(settings.interests.practice_refs)
    if runner is not None:
        from finch.sources.store import ArtifactRepository

        art_repo = ArtifactRepository(ws)
        for item in items:
            arts = art_repo.list_by_ids(item.candidate.artifact_ids)
            conn = assess_connection_for_slot(
                runner=runner,
                item=item,
                artifacts=arts,
                user_evidence_refs=user_refs,
            )
            conn_repo.save(conn)
            result.connections.append(conn)

    # Browse opportunities from artifacts (no second X/Reddit search)
    opps = opportunities_from_artifacts(
        artifacts, limit=settings.engagement.max_display_opportunities
    )
    result.opportunities = opps
    opp_repo = OpportunityRepository(ws)
    for opp in opps:
        opp_repo.upsert(opp)

    peers_out: list[RankedPeer] = []
    for item in items:
        peers_out.append(
            RankedPeer(
                profile=item.candidate.peer,
                value=PeerValue(
                    topic_overlap=0.5,
                    practical_depth=0.5,
                    contribution_space=0.5,
                    continuity_potential=0.5,
                    repetition_penalty=0.0,
                    promotion_risk=0.0,
                    total=item.candidate.score.total,
                    reasons=["people-first shortlist"],
                ),
            )
        )

    status = "succeeded" if (items or opps) else "empty"
    engagement = EngagementRunResult(
        run_id=run_id,
        posts_found=len(artifacts),
        opportunities=opps,
        peers=peers_out,
        failures=[],
        status=status,  # type: ignore[arg-type]
        summary=f"sources→people shortlist={len(items)} opps={len(opps)}",
        context_fingerprint=run_id,
        source_coverage={
            "sources": {
                r.source.value: {
                    "status": r.status.value,
                    "raw": r.raw_count,
                    "norm": r.normalized_count,
                    "projected": r.projected_count,
                }
                for r in result.sync_results
            },
            "shortlist": len(items),
            "connections": len(result.connections),
        },
    )
    result.engagement = engagement

    snapshot = DiscoverySnapshot(
        id=run_id,
        created_at=datetime.now(UTC),
        context_fingerprint=run_id,
        source_coverage=engagement.source_coverage,
        failures=[],
        ranked_opportunity_ids=[o.id for o in opps],
        ranking_version="people-first-1",
    )
    DiscoverySnapshotRepository(ws).upsert(snapshot)

    if generate_collision and runner is not None and items:
        col = CollisionService(ws, runner=runner).generate_from_shortlist(
            items,
            your_domains=list(settings.interests.long_term_interests),
        )
        if col.saved is not None:
            result.collision_id = col.saved.collision_id

    return result
