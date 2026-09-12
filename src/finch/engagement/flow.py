"""互动轨道流程胶水：搜索 → 预过滤 → 同行聚合 → 关系评分 → 语义评估 → 机会选择。

只读发现：本轮输出 ``Opportunity`` 列表（轻量交流机会），**不**生成回复草稿。
草稿仅在用户选中机会后由 ``generate_proposals`` / ``connect prepare`` 产生。
单条轨道失败不得抛到调用方；搜索层的部分失败会记录在 ``failures`` 中。
"""

from typing import Literal

from pydantic import BaseModel, Field

from finch.peers.models import PeerProfile

from ..codex.runner import CodexRunner
from ..reddit.opencli_client import RedditOpenCliClient
from ..settings import Settings
from ..twitter.opencli_client import OpenCliClient
from .models import ExternalPost, InteractionProposal, Opportunity, SuggestedMode
from .opportunity import scored_post_to_opportunity, select_opportunity_set
from .peer_aggregation import aggregate_by_peer
from .relationship import (
    PeerHistory,
    PeerValue,
    compute_relationship_value,
    rank_peers,
)
from .scoring import prefilter_posts, rank_candidates, score_posts
from .search import (
    PostSearchFailure,
    PostSearchProvider,
    RedditPostSearchProvider,
    XPostSearchProvider,
    search_engagement_posts,
)

# 过短内容在评分前就被确定性规则丢弃。
_MIN_CONTENT_LENGTH = 20


class RankedPeer(BaseModel):
    """一个同行及其确定性 peer_value。"""

    profile: PeerProfile
    value: PeerValue


class EngagementRunResult(BaseModel):
    """互动轨道单轮结果。

    ``opportunities`` 是轻量推荐条目（浏览列表）；``candidates`` 保留字段以兼容旧调用方，
    发现路径固定为空列表（草稿仅在 prepare 时生成）。``peers`` 为粗筛后的同行子集。
    """

    run_id: str
    posts_found: int
    candidates: list[InteractionProposal] = Field(default_factory=list)
    opportunities: list[Opportunity] = Field(default_factory=list)
    peers: list[RankedPeer] = Field(default_factory=list)
    failures: list[PostSearchFailure]
    status: Literal["succeeded", "empty", "failed"]
    summary: str
    context_fingerprint: str = ""


def _build_providers(
    platforms: list[str],
    opencli: OpenCliClient,
    reddit_opencli: RedditOpenCliClient | None = None,
) -> list[PostSearchProvider]:
    """由 ``settings.engagement.platforms`` 构造搜索适配器；未知平台忽略。"""
    providers: list[PostSearchProvider] = []
    for platform in platforms:
        if platform == "x":
            providers.append(XPostSearchProvider(opencli))
        elif platform == "reddit":
            providers.append(RedditPostSearchProvider(reddit_opencli))
    return providers


def _post_title(post: ExternalPost) -> str:
    snippet = " ".join(post.content.split())[:80]
    return f"{post.url} — {snippet}"


def _snippet(text: str, limit: int = 80) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 3] + "..."


def _render_failures(failures: list[PostSearchFailure]) -> list[str]:
    lines = [f"search failures: {len(failures)}"]
    for failure in failures:
        query = failure.query or "n/a"
        lines.append(f"  - [{failure.platform}] {query}: {failure.reason}")
    return lines


def _render_summary(
    *,
    posts_found: int,
    opportunities: list[Opportunity],
    failures: list[PostSearchFailure],
) -> str:
    lines = [
        f"engagement: {posts_found} post(s) found, {len(opportunities)} opportunity(ies)"
    ]
    for idx, opp in enumerate(opportunities, start=1):
        ref = opp.source_refs[0] if opp.source_refs else opp.id
        lines.append(f"{idx}. [{opp.suggested_mode.value}] {ref}")
        lines.append(f"   - why: {_snippet(opp.why_relevant)}")
        if opp.opening:
            lines.append(f"   - opening: {_snippet(opp.opening)}")
    if failures:
        lines.extend(_render_failures(failures))
    return "\n".join(lines)


def _render_empty(failures: list[PostSearchFailure]) -> str:
    lines = ["engagement: no posts found"]
    if failures:
        lines.extend(_render_failures(failures))
    return "\n".join(lines)


def _render_failed(exc: Exception) -> str:
    return f"engagement: failed ({type(exc).__name__}: {exc})"


def _mode_from_assessment(raw: str) -> SuggestedMode:
    try:
        return SuggestedMode(raw)
    except ValueError:
        return SuggestedMode.DISCUSS


def run_discovery_engagement_flow(
    settings: Settings,
    opencli: OpenCliClient,
    runner: CodexRunner,
    *,
    reddit_opencli: RedditOpenCliClient | None = None,
    run_id: str,
    skip_ids: set[str] | None = None,
    history_by_peer: dict[str, PeerHistory] | None = None,
    seen_fingerprints: set[str] | None = None,
    familiar_peer_ids: set[str] | None = None,
) -> EngagementRunResult:
    """执行互动轨道：搜索 → 预过滤 → 同行聚合 → 粗筛 → 语义评估 → 机会选择。

    空帖子返回 ``status="empty"``；顶层异常捕获为 ``status="failed"``。
    不调用 ``generate_proposals``——浏览列表不含完整回复草稿。
    """
    from finch.engagement.opportunity import context_fingerprint

    engagement = settings.engagement
    interests = settings.interests
    interest_terms = [
        *interests.long_term_interests,
        *interests.current_questions,
        *interests.explore_directions,
    ]
    ctx_fp = context_fingerprint(
        long_term=interests.long_term_interests,
        questions=interests.current_questions,
        explore=interests.explore_directions,
        excluded=interests.excluded_content,
    )
    providers = _build_providers(engagement.platforms, opencli, reddit_opencli)
    outcome = None
    try:
        outcome = search_engagement_posts(
            providers, interests, engagement, skip_ids=skip_ids
        )
        posts = prefilter_posts(
            outcome.posts, min_length=_MIN_CONTENT_LENGTH, skip_ids=skip_ids
        )

        bundles = aggregate_by_peer(posts)
        # Coarse person filter — light discovery author cap.
        ranked_peers = rank_peers(
            bundles,
            interests=interest_terms,
            history_by_peer=history_by_peer,
            weights=engagement.peer_value_weights,
        )[: engagement.max_discovery_authors]

        # Semantic assess at most max_semantic_authors.
        semantic_peers = ranked_peers[: engagement.max_semantic_authors]
        relationship_by_peer: dict[str, float] = {}
        selected_posts: list[ExternalPost] = []
        for bundle, peer_value in semantic_peers:
            relationship_by_peer[bundle.profile.id] = compute_relationship_value(peer_value)
            selected_posts.extend(bundle.posts[: engagement.max_posts_per_peer])

        scored = (
            score_posts(
                runner,
                selected_posts,
                engagement.weights,
                relationship_by_peer=relationship_by_peer,
            )
            if selected_posts
            else []
        )
        ranked = rank_candidates(
            scored, min_candidate_score=engagement.min_candidate_score
        )

        raw_opps: list[Opportunity] = []
        for sp in ranked:
            assess = sp.assessment
            if assess is None:
                raw_opps.append(scored_post_to_opportunity(sp))
                continue
            raw_opps.append(
                scored_post_to_opportunity(
                    sp,
                    why_relevant=assess.why_relevant
                    or (assess.reasons[0] if assess.reasons else "concrete overlap"),
                    opening=assess.opening,
                    suggested_mode=_mode_from_assessment(assess.suggested_mode),
                    topic_tags=assess.topic_tags or list(sp.post.matched_topics),
                    role_tags=assess.role_tags,
                    novelty_reason=assess.novelty_reason,
                    uncertainty=assess.uncertainty,
                    complementarity=assess.complementarity,
                )
            )

        opportunities = select_opportunity_set(
            raw_opps,
            limit=engagement.max_display_opportunities,
            seen_fingerprints=seen_fingerprints,
            familiar_peer_ids=familiar_peer_ids,
        )
        # Ensure why_relevant is concrete enough for quality gate survivors / near-misses
        # that still have good scores: if LLM omitted why, fall back to reasons.
        for i, opp in enumerate(opportunities):
            if not opp.why_relevant.strip() and opp.post is not None:
                opportunities[i] = opp.model_copy(
                    update={"why_relevant": "matches current practice topic with concrete detail"}
                )
    except Exception as exc:  # noqa: BLE001 - 顶层防御，调用方仍会二次隔离
        return EngagementRunResult(
            run_id=run_id,
            posts_found=0,
            candidates=[],
            opportunities=[],
            failures=[],
            status="failed",
            summary=_render_failed(exc),
            context_fingerprint=ctx_fp,
        )

    if not outcome.posts:
        return EngagementRunResult(
            run_id=run_id,
            posts_found=0,
            candidates=[],
            opportunities=[],
            failures=outcome.failures,
            status="empty",
            summary=_render_empty(outcome.failures),
            context_fingerprint=ctx_fp,
        )

    return EngagementRunResult(
        run_id=run_id,
        posts_found=len(outcome.posts),
        candidates=[],  # discovery path: no drafts
        opportunities=opportunities,
        peers=[RankedPeer(profile=b.profile, value=v) for b, v in semantic_peers],
        failures=outcome.failures,
        status="succeeded",
        summary=_render_summary(
            posts_found=len(outcome.posts),
            opportunities=opportunities,
            failures=outcome.failures,
        ),
        context_fingerprint=ctx_fp,
    )
