"""点名连接：把用户指定的 X / GitHub 身份收成 PeerProfile，并准备一条提纲。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC
from typing import Literal

from pydantic import BaseModel

from finch.codex.runner import CodexRunner
from finch.content.jobs import ContentJob
from finch.engagement.contribution import assess_job_contribution
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionProposal,
    Opportunity,
)
from finch.engagement.opportunity import scored_post_to_opportunity
from finch.engagement.proposals import generate_proposals, ready_gate_blocks
from finch.engagement.scoring import ScoredPost, rank_candidates, score_posts
from finch.engagement.search import _to_external_post, fetch_post_by_url
from finch.github.gh_client import GhClient, GhError
from finch.github.models import PublicRepo
from finch.peers.models import EvidenceStatus, PeerProfile
from finch.peers.service import PeerService
from finch.settings import Settings
from finch.storage.repositories import (
    InteractionRepository,
    OpportunityRepository,
    PeerRepository,
)
from finch.storage.workspace import Workspace
from finch.twitter.opencli_client import OpenCliClient

NamedPlatform = Literal["x", "github"]

_X_STATUS = re.compile(
    r"^https?://(?:www\.)?(?:x\.com|twitter\.com)/([^/?#]+)/status/(\d+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_X_PROFILE = re.compile(
    r"^https?://(?:www\.)?(?:x\.com|twitter\.com)/([^/?#]+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_GH_REPO = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/?#]+)/([^/?#]+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_GH_USER = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/?#]+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_HANDLE = re.compile(r"^@?[A-Za-z0-9](?:[A-Za-z0-9_-]{0,38})$")
_GH_RESERVED = frozenset(
    {
        "settings",
        "pulls",
        "issues",
        "marketplace",
        "topics",
        "orgs",
        "login",
        "features",
        "pricing",
        "about",
        "new",
        "notifications",
        "codespaces",
        "sponsors",
        "stars",
        "account",
        "enterprise",
        "security",
        "team",
        "readme",
        "events",
        "collections",
        "customer-stories",
        "gist",
    }
)
_GH_REPO_RESERVED = frozenset(
    {"issues", "pulls", "actions", "projects", "wiki", "security", "settings"}
)


class NamedTarget(BaseModel):
    platform: NamedPlatform
    handle: str
    content_url: str | None = None
    identity_url: str


def parse_named_target(platform: NamedPlatform, raw: str) -> NamedTarget:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty handle or URL")
    if platform == "x":
        return _parse_x(text)
    return _parse_github(text)


def _parse_x(text: str) -> NamedTarget:
    status = _X_STATUS.match(text)
    if status:
        handle = status.group(1)
        url = f"https://x.com/{handle}/status/{status.group(2)}"
        return NamedTarget(
            platform="x",
            handle=handle,
            content_url=url,
            identity_url=f"https://x.com/{handle}",
        )
    profile = _X_PROFILE.match(text)
    if profile:
        handle = profile.group(1)
        if handle.lower() in {"home", "explore", "search", "i", "intent", "share", "settings"}:
            raise ValueError(f"not an X profile URL: {text}")
        return NamedTarget(
            platform="x",
            handle=handle,
            identity_url=f"https://x.com/{handle}",
        )
    handle = _bare_handle(text)
    return NamedTarget(platform="x", handle=handle, identity_url=f"https://x.com/{handle}")


def _parse_github(text: str) -> NamedTarget:
    repo = _GH_REPO.match(text)
    if repo:
        owner, name = repo.group(1), repo.group(2)
        if name.lower() in _GH_REPO_RESERVED:
            raise ValueError(f"not a GitHub repository URL: {text}")
        handle = owner.lower()
        return NamedTarget(
            platform="github",
            handle=handle,
            content_url=f"https://github.com/{owner}/{name}",
            identity_url=f"https://github.com/{handle}",
        )
    user = _GH_USER.match(text)
    if user:
        owner = user.group(1)
        if owner.lower() in _GH_RESERVED:
            raise ValueError(f"not a GitHub user URL: {text}")
        handle = owner.lower()
        return NamedTarget(
            platform="github",
            handle=handle,
            identity_url=f"https://github.com/{handle}",
        )
    handle = _bare_handle(text).lower()
    return NamedTarget(
        platform="github",
        handle=handle,
        identity_url=f"https://github.com/{handle}",
    )


def _bare_handle(text: str) -> str:
    if text.startswith("http://") or text.startswith("https://"):
        raise ValueError(f"URL host does not match selected platform: {text}")
    if not _HANDLE.match(text):
        raise ValueError(f"invalid handle: {text}")
    return text.lstrip("@")


def github_repo_to_post(repo: PublicRepo) -> ExternalPost | None:
    if repo.pushed_at is None:
        return None
    published = repo.pushed_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    owner = repo.owner_login.strip().lower()
    content = repo.description.strip() or repo.name_with_owner
    return ExternalPost(
        id=repo.name_with_owner,
        platform="github",
        url=repo.url,
        author_id=owner,
        author_name=repo.owner_login,
        content=content,
        published_at=published,
        matched_topics=["named"],
    )


@dataclass
class NamedConnectResult:
    """点名连接结果：状态 + 消息 + 落库的 peer / opportunity / proposal（可空）。"""

    status: Literal["ok", "no_reply", "blocked", "failed"]
    message: str
    peer: PeerProfile | None = None
    opportunity: Opportunity | None = None
    proposal: InteractionProposal | None = None


def _fetch_x(
    target: NamedTarget, opencli: OpenCliClient
) -> tuple[PeerProfile | None, list[ExternalPost]]:
    """抓取 X 身份与公开内容；身份缺失返回 ``(None, [])``（不落库）。"""
    service = PeerService()
    if target.content_url:
        post = fetch_post_by_url(target.content_url, opencli=opencli, topic="named")
        if post is None:
            return None, []
        peer = service.from_author(
            platform="x",
            author_id=post.author_id,
            username=post.author_name or post.author_id,
        )
        return peer, [post]

    if opencli.profile(target.handle) is None:
        return None, []
    peer = service.from_author(platform="x", author_id=target.handle, username=target.handle)
    try:
        tweets = opencli.tweets(target.handle, limit=20)
    except Exception:  # noqa: BLE001 — fail-closed，回退到 search
        tweets = []
    if not tweets:
        tweets = opencli.search(f"from:{target.handle}", limit=20)
    posts: list[ExternalPost] = []
    for tweet in tweets:
        external = _to_external_post(tweet, topic="named")
        if external is not None:
            posts.append(external)
    return peer, posts


def _fetch_github(
    target: NamedTarget, gh: GhClient
) -> tuple[PeerProfile | None, list[ExternalPost]]:
    """抓取 GitHub 身份与公开仓库；用户不存在返回 ``(None, [])``（不落库）。

    用户存在但仓库私有/出错时仍走「已落 peer」路径，返回 ``(peer, [])``。
    """
    service = PeerService()
    try:
        gh.user(target.handle)
    except GhError:
        return None, []
    peer = service.from_author(platform="github", author_id=target.handle)

    if target.content_url:
        repo_match = _GH_REPO.match(target.content_url)
        if repo_match is None:
            return peer, []
        name_with_owner = f"{repo_match.group(1)}/{repo_match.group(2)}"
        try:
            repo = gh.public_repo(name_with_owner)
        except GhError:
            return peer, []
        post = github_repo_to_post(repo)
        return peer, [post] if post is not None else []

    posts: list[ExternalPost] = []
    for repo in gh.list_public_repos(target.handle, limit=20):
        external = github_repo_to_post(repo)
        if external is not None:
            posts.append(external)
    return peer, posts


def connect_named(
    *,
    target: NamedTarget,
    ws: Workspace,
    settings: Settings,
    runner: CodexRunner,
    opencli: OpenCliClient | None = None,
    gh: GhClient | None = None,
    job: ContentJob | None = None,
    full_draft: bool = False,
) -> NamedConnectResult:
    """点名连接：把指定身份收成 PeerProfile 并准备至多一条提纲（绝不写发现快照）。

    - 身份缺失 → ``failed``，不落库。
    - 有身份无公开内容 → 落 peer（``evidence_status=pending_review``），返回 ``no_reply``。
    - 有个人素材（job）但贡献检查失败 → ``no_reply``（peer 已落，不产出机会/提案）。
    - 命中敏感内容 → ``blocked``（不产出机会/提案）。
    - 正常 → ``ok``，落 peer + 幂等提案 + 机会。
    """
    opencli = opencli or OpenCliClient()
    gh = gh or GhClient()

    if target.platform == "x":
        peer, posts = _fetch_x(target, opencli)
    else:
        peer, posts = _fetch_github(target, gh)

    if peer is None:
        return NamedConnectResult(
            status="failed",
            message=f"未找到公开身份：{target.identity_url}",
        )

    peer_repo = PeerRepository(ws)
    merged = PeerService().merge_discovered(peer_repo.get(peer.id), peer)

    if not posts:
        merged = merged.model_copy(update={"evidence_status": EvidenceStatus.PENDING_REVIEW})
        peer_repo.upsert(merged)
        return NamedConnectResult(
            status="no_reply",
            message="暂不回复：没有可讨论的公开内容",
            peer=merged,
        )

    peer_repo.upsert(merged)

    if job is not None:
        ok, reason = assess_job_contribution(posts[0], job)
        if not ok:
            return NamedConnectResult(
                status="no_reply",
                message=f"暂不回复：{reason}",
                peer=merged,
            )

    scored = score_posts(runner, posts, settings.engagement.weights)
    ranked = rank_candidates(
        scored, min_candidate_score=settings.engagement.min_candidate_score
    )
    if not ranked:
        reason = "selected named target"
        ranked = [
            ScoredPost(
                post=posts[0],
                score=ConversationScore(
                    relevance=0.8,
                    novelty=0.8,
                    discussability=0.8,
                    practical_evidence=0.7,
                    relationship_value=0.5,
                    total=0.78,
                    reasons=[reason],
                ),
            )
        ]

    first = ranked[0]
    basis = [job.id] if job is not None else list(settings.interests.practice_refs)
    proposals = generate_proposals(
        runner,
        [first],
        settings.engagement,
        contribution_basis_refs=basis,
        full_draft=full_draft,
        job=job,
    )
    if not proposals:
        return NamedConnectResult(
            status="no_reply",
            message="暂不回复：模型未产出可用提纲，且缺少可贡献增量",
            peer=merged,
        )

    candidate = proposals[0]
    body = candidate.draft or candidate.outline or ""
    blocks = ready_gate_blocks(body=body, job=job)
    if blocks and "secret_detected" in blocks:
        return NamedConnectResult(
            status="blocked",
            message=f"暂不回复：敏感内容不得进入可发布状态（{', '.join(blocks)}）",
            peer=merged,
        )

    repo = InteractionRepository(ws)
    if candidate.generation_key:
        existing = repo.find_by_generation_key(candidate.generation_key)
        if existing is not None:
            candidate = existing
        else:
            repo.upsert(candidate, run_id="named")
    else:
        repo.upsert(candidate, run_id="named")

    opportunity = scored_post_to_opportunity(
        first,
        contribution_basis_refs=basis or None,
        discovered_via=f"named:{target.platform}",
    )
    OpportunityRepository(ws).upsert(opportunity)

    return NamedConnectResult(
        status="ok",
        message=f"已记录 {target.handle} 并准备 1 条提纲",
        peer=merged,
        opportunity=opportunity,
        proposal=candidate,
    )
