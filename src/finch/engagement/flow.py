"""互动轨道流程胶水：搜索 → 预过滤 → 同行聚合 → 关系评分 → 逐帖评分 → 提案（Phase 0–4）。

只读：本轮输出互动提案（``InteractionProposal``，含草稿类动作的草稿），不做审批/执行、
不持久化互动记录，也不计算指标。单条轨道失败不得抛到调用方；搜索层的部分失败会记录在
``failures`` 中。

连接优先改造（Phase 2）：预过滤后的帖子先按作者聚合为同行，做确定性关系评分，再限
「推荐人数」与「每人帖数」，最后才逐帖四维语义评分并合成互动提案——避免榜单被单一作者
占满，也让 relationship_value 从「单帖印象」变为「基于 PeerProfile 与历史」的确定值。
"""

from typing import Literal

from pydantic import BaseModel, Field

from finch.peers.models import PeerProfile

from ..codex.runner import CodexRunner
from ..reddit.opencli_client import RedditOpenCliClient
from ..settings import Settings
from ..twitter.opencli_client import OpenCliClient
from .models import ExternalPost, InteractionProposal
from .peer_aggregation import aggregate_by_peer
from .proposals import generate_proposals
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

# 过短内容在评分前就被确定性规则丢弃（Phase 3 规则过滤）。
_MIN_CONTENT_LENGTH = 20


class RankedPeer(BaseModel):
    """一个同行及其确定性 peer_value（供 CLI 展示「今天最值得连接的同行」）。"""

    profile: PeerProfile
    value: PeerValue


class EngagementRunResult(BaseModel):
    """互动轨道单轮结果。

    ``candidates`` 持有 ``InteractionProposal``（Pydantic 模型，含 ``post``/``score``/
    ``action``/``draft`` 等）；``peers`` 持有关系评分后的同行榜单（限人限帖后的子集）；
    ``posts_found`` 为搜索层返回（去重/排除/截断后、内容长度预过滤前）的帖子数，便于区分
    「没搜到」与「搜到但无候选」。
    """

    run_id: str
    posts_found: int
    candidates: list[InteractionProposal]
    peers: list[RankedPeer] = Field(default_factory=list)
    failures: list[PostSearchFailure]
    status: Literal["succeeded", "empty", "failed"]
    summary: str


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
    candidates: list[InteractionProposal],
    failures: list[PostSearchFailure],
) -> str:
    lines = [
        f"engagement: {posts_found} post(s) found, {len(candidates)} candidate(s) above threshold"
    ]
    for idx, candidate in enumerate(candidates, start=1):
        lines.append(f"{idx}. [{candidate.action.value}] {_post_title(candidate.post)}")
        lines.append(f"   - total: {candidate.score.total:.3f}")
        if candidate.intent:
            lines.append(f"   - intent: {candidate.intent}")
        if candidate.draft:
            lines.append(f"   - draft: {_snippet(candidate.draft)}")
        if candidate.factual_risks:
            lines.append(f"   - factual risks: {', '.join(candidate.factual_risks)}")
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


def run_discovery_engagement_flow(
    settings: Settings,
    opencli: OpenCliClient,
    runner: CodexRunner,
    *,
    reddit_opencli: RedditOpenCliClient | None = None,
    run_id: str,
    skip_ids: set[str] | None = None,
    history_by_peer: dict[str, PeerHistory] | None = None,
) -> EngagementRunResult:
    """执行互动轨道：搜索 → 预过滤 → 同行聚合 → 关系评分 → 逐帖评分 → 提案。

    空帖子返回 ``status="empty"``（成功空结果，非错误）；顶层异常捕获为 ``status="failed"``，
    不向外抛出。空输入不会调用 LLM。``history_by_peer`` 提供同行的历史互动上下文（供关系
    评分计算 continuity_potential / repetition_penalty），缺省视为首次发现。
    """
    engagement = settings.engagement
    interests = [*settings.interests.stable, *settings.interests.exploring]
    providers = _build_providers(engagement.platforms, opencli, reddit_opencli)
    try:
        outcome = search_engagement_posts(
            providers, settings.interests, engagement, skip_ids=skip_ids
        )
        posts = prefilter_posts(
            outcome.posts, min_length=_MIN_CONTENT_LENGTH, skip_ids=skip_ids
        )

        # 同行聚合 + 关系评分 + 限人限帖（避免榜单被单一作者占满）。
        bundles = aggregate_by_peer(posts)
        ranked_peers = rank_peers(
            bundles,
            interests=interests,
            history_by_peer=history_by_peer,
            weights=engagement.peer_value_weights,
        )[: engagement.max_peers_per_run]

        relationship_by_peer: dict[str, float] = {}
        selected_posts: list[ExternalPost] = []
        for bundle, peer_value in ranked_peers:
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
        candidates = generate_proposals(runner, ranked, engagement)
    except Exception as exc:  # noqa: BLE001 - 顶层防御，调用方仍会二次隔离
        return EngagementRunResult(
            run_id=run_id,
            posts_found=0,
            candidates=[],
            failures=[],
            status="failed",
            summary=_render_failed(exc),
        )

    if not outcome.posts:
        return EngagementRunResult(
            run_id=run_id,
            posts_found=0,
            candidates=[],
            failures=outcome.failures,
            status="empty",
            summary=_render_empty(outcome.failures),
        )

    return EngagementRunResult(
        run_id=run_id,
        posts_found=len(outcome.posts),
        candidates=candidates,
        peers=[RankedPeer(profile=b.profile, value=v) for b, v in ranked_peers],
        failures=outcome.failures,
        status="succeeded",
        summary=_render_summary(
            posts_found=len(outcome.posts),
            candidates=candidates,
            failures=outcome.failures,
        ),
    )
