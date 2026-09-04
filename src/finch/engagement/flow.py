"""互动轨道流程胶水：搜索 → 预过滤 → 评分 → 排序 → 互动提案 → 结构化结果（执行计划 Phase 0–4）。

只读：本轮输出互动提案（``InteractionCandidate``，含草稿类动作的草稿），不做审批/执行、
不持久化互动记录，也不计算指标。单条轨道失败不得抛到调用方；搜索层的部分失败会记录在
``failures`` 中。
"""

from typing import Literal

from pydantic import BaseModel

from ..codex.runner import CodexRunner
from ..settings import Settings
from ..twitter.opencli_client import OpenCliClient
from .models import ExternalPost, InteractionCandidate
from .proposals import generate_proposals
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


class EngagementRunResult(BaseModel):
    """互动轨道单轮结果。

    ``candidates`` 持有 ``InteractionCandidate``（Pydantic 模型，含 ``post``/``score``/
    ``action``/``draft`` 等）；``posts_found`` 为搜索层返回（去重/排除/截断后、内容长度预过滤前）
    的帖子数，便于区分「没搜到」与「搜到但无候选」。
    """

    run_id: str
    posts_found: int
    candidates: list[InteractionCandidate]
    failures: list[PostSearchFailure]
    status: Literal["succeeded", "empty", "failed"]
    summary: str


def _build_providers(platforms: list[str], opencli: OpenCliClient) -> list[PostSearchProvider]:
    """由 ``settings.engagement.platforms`` 构造搜索适配器；未知平台忽略。"""
    providers: list[PostSearchProvider] = []
    for platform in platforms:
        if platform == "x":
            providers.append(XPostSearchProvider(opencli))
        elif platform == "reddit":
            providers.append(RedditPostSearchProvider())
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
    candidates: list[InteractionCandidate],
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
    run_id: str,
    skip_ids: set[str] | None = None,
) -> EngagementRunResult:
    """执行互动轨道：搜索 → 预过滤 → 评分 → 排序 → 互动提案，返回结构化结果。

    空帖子返回 ``status="empty"``（成功空结果，非错误）；顶层异常捕获为 ``status="failed"``，
    不向外抛出。空输入不会调用 LLM（``score_posts`` 已短路，这里亦不传空列表）。
    """
    engagement = settings.engagement
    providers = _build_providers(engagement.platforms, opencli)
    try:
        outcome = search_engagement_posts(
            providers, settings.interests, engagement, skip_ids=skip_ids
        )
        posts = prefilter_posts(
            outcome.posts, min_length=_MIN_CONTENT_LENGTH, skip_ids=skip_ids
        )
        scored = score_posts(runner, posts, engagement.weights) if posts else []
        ranked = rank_candidates(
            scored, min_candidate_score=engagement.min_candidate_score
        )
        candidates = generate_proposals(runner, ranked, engagement)
    except Exception as exc:  # noqa: BLE001 - 顶层防御，双轨调度侧仍会二次隔离
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
        failures=outcome.failures,
        status="succeeded",
        summary=_render_summary(
            posts_found=len(outcome.posts),
            candidates=candidates,
            failures=outcome.failures,
        ),
    )
