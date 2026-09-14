"""有界 peer→人扩展：从讨论参与者一跳发现新人。

``connect expand --from``：读机会来源线程，最多 5 位新作者，仅评估新人。
``connect expand --scope``：先从缓存机会按标签重选；不足时才做有界额外搜索。
"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.codex.runner import CodexRunner
from finch.engagement.models import DiscoverySnapshot, ExternalPost, Opportunity, SuggestedMode
from finch.engagement.opportunity import (
    assign_next_action,
    scored_post_to_opportunity,
    select_opportunity_set,
)
from finch.engagement.scoring import rank_candidates, score_posts
from finch.engagement.search import (
    RedditPostSearchProvider,
    XPostSearchProvider,
    _to_external_post,
    search_engagement_posts,
)
from finch.peers.service import peer_id_for
from finch.reddit.opencli_client import RedditOpenCliClient
from finch.settings import Settings
from finch.storage.repositories import (
    DiscoverySnapshotRepository,
    OpportunityRepository,
    PeerRepository,
)
from finch.storage.workspace import Workspace
from finch.twitter.opencli_client import OpenCliClient

_MAX_NEW_AUTHORS = 5
_THREAD_LIMIT = 30


def _thread_posts(url: str) -> list[ExternalPost]:
    posts: list[ExternalPost] = []
    if "reddit.com" in url:
        # Reddit thread expansion not wired via opencli thread API yet.
        return posts
    client = OpenCliClient()
    try:
        tweets = client.thread(url, limit=_THREAD_LIMIT)
    except Exception:  # noqa: BLE001 — fail-closed
        return posts
    for tweet in tweets:
        external = _to_external_post(tweet, topic="expand")
        if external is not None:
            posts.append(external)
    return posts


def expand_from_opportunity(
    settings: Settings,
    ws: Workspace,
    opportunity_id: str,
    *,
    runner: CodexRunner,
) -> tuple[list[Opportunity], DiscoverySnapshot | None, str]:
    """One-hop expand from an opportunity's source thread.

    Returns (new_opportunities, child_snapshot_or_None, message).
    """
    opp_repo = OpportunityRepository(ws)
    opp = opp_repo.get(opportunity_id)
    if opp is None:
        return [], None, f"opportunity not found: {opportunity_id}"
    url = opp.source_refs[0] if opp.source_refs else ""
    if not url:
        return [], None, "opportunity has no source URL"

    existing_peers = {p.id for p in PeerRepository(ws).list_all()}
    thread_posts = _thread_posts(url)
    # Keep authors not already known; cap at _MAX_NEW_AUTHORS.
    novel: list[ExternalPost] = []
    seen_authors: set[str] = set()
    for post in thread_posts:
        pid = peer_id_for(post.platform, post.author_id)
        if pid in existing_peers or post.author_id in seen_authors:
            continue
        if opp.post and post.author_id == opp.post.author_id:
            continue
        seen_authors.add(post.author_id)
        novel.append(post)
        if len(novel) >= _MAX_NEW_AUTHORS:
            break

    if not novel:
        return [], None, "no new participants found in thread (bounded one-hop)"

    scored = score_posts(runner, novel, settings.engagement.weights)
    ranked = rank_candidates(
        scored, min_candidate_score=settings.engagement.min_candidate_score
    )
    practice = list(settings.interests.practice_refs)
    budget = settings.interests.time_budget_minutes
    raw: list[Opportunity] = []
    for sp in ranked:
        assess = sp.assessment
        mode = SuggestedMode.DISCUSS
        why = "Concrete participant in related discussion"
        opening = "Ask about their concrete claim in the thread"
        shared = ""
        if assess is not None:
            try:
                mode = SuggestedMode(assess.suggested_mode)
            except ValueError:
                mode = SuggestedMode.DISCUSS
            why = assess.why_relevant or why
            opening = assess.opening or opening
            shared = assess.shared_problem or ""
        basis = list(practice) if practice and opening else []
        action, minutes = assign_next_action(
            mode, has_practice=bool(basis), time_budget=budget
        )
        raw.append(
            scored_post_to_opportunity(
                sp,
                why_relevant=why,
                opening=opening,
                suggested_mode=mode,
                shared_problem=shared,
                contribution_basis_refs=basis,
                next_action=action,
                estimated_minutes=minutes,
                discovered_via=f"expand:{opportunity_id}",
            )
        )

    selected = select_opportunity_set(raw, limit=_MAX_NEW_AUTHORS)
    for o in selected:
        opp_repo.upsert(o)

    parent = DiscoverySnapshotRepository(ws).latest()
    child_id = f"expand_{opportunity_id}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    ranked_ids = [o.id for o in selected]
    if parent is not None:
        # Merge into remaining pool: append new ids not already ranked.
        merged = list(dict.fromkeys([*parent.ranked_opportunity_ids, *ranked_ids]))
        snap = parent.model_copy(
            update={
                "id": child_id,
                "created_at": datetime.now(UTC),
                "ranked_opportunity_ids": merged,
                "source_coverage": {
                    **(parent.source_coverage or {}),
                    "expand_from": opportunity_id,
                    "expand_new": len(selected),
                },
            }
        )
    else:
        snap = DiscoverySnapshot(
            id=child_id,
            created_at=datetime.now(UTC),
            context_fingerprint="",
            ranked_opportunity_ids=ranked_ids,
            source_coverage={"expand_from": opportunity_id, "expand_new": len(selected)},
        )
    DiscoverySnapshotRepository(ws).upsert(snap)
    return selected, snap, f"expanded {len(selected)} new author(s) from {opportunity_id}"


def expand_by_scope(
    settings: Settings,
    ws: Workspace,
    scope: str,
    *,
    runner: CodexRunner,
    limit: int = 5,
) -> tuple[list[Opportunity], str]:
    """Re-select cached opportunities by tag/scope text; bounded search only if short."""
    scope_key = scope.strip().casefold()
    if not scope_key:
        return [], "scope text is empty"

    cached = OpportunityRepository(ws).list_all()
    matched = [
        o
        for o in cached
        if scope_key in " ".join(o.topic_tags).casefold()
        or scope_key in o.why_relevant.casefold()
        or scope_key in o.discovered_via.casefold()
        or scope_key in o.shared_problem.casefold()
    ]
    matched = select_opportunity_set(matched, limit=limit)
    if len(matched) >= min(3, limit):
        return matched, f"reselected {len(matched)} from cache for scope={scope!r}"

    # Bounded extra search using scope as a usage query.
    from finch.settings import InterestsSettings

    interests = InterestsSettings(
        current_questions=[scope],
        usage_queries=[scope],
        long_term_interests=list(settings.interests.long_term_interests),
        excluded_content=list(settings.interests.excluded_content),
    )
    providers = [
        XPostSearchProvider(OpenCliClient()),
        RedditPostSearchProvider(RedditOpenCliClient()),
    ]
    # Temporarily shrink scan budget for expand.
    engagement = settings.engagement.model_copy(update={"max_posts_scanned": min(12, settings.engagement.max_posts_scanned)})
    outcome = search_engagement_posts(providers, interests, engagement)
    if not outcome.posts:
        return matched, f"cache had {len(matched)}; extra search returned none for scope={scope!r}"

    scored = score_posts(runner, outcome.posts[:10], engagement.weights)
    ranked = rank_candidates(scored, min_candidate_score=engagement.min_candidate_score)
    practice = list(settings.interests.practice_refs)
    budget = settings.interests.time_budget_minutes
    raw: list[Opportunity] = []
    for sp in ranked:
        action, minutes = assign_next_action(
            SuggestedMode.DISCUSS, has_practice=bool(practice), time_budget=budget
        )
        raw.append(
            scored_post_to_opportunity(
                sp,
                why_relevant=f"Matches scoped search: {scope}",
                opening=f"Ask how they approach {scope}",
                suggested_mode=SuggestedMode.DISCUSS,
                contribution_basis_refs=list(practice) if practice else [],
                next_action=action,
                estimated_minutes=minutes,
                discovered_via=f"expand_scope:{scope}",
            )
        )
    selected = select_opportunity_set([*matched, *raw], limit=limit)
    for o in selected:
        OpportunityRepository(ws).upsert(o)
    return selected, f"scope expand returned {len(selected)} (cache+search)"
