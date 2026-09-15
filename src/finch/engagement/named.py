"""点名连接：把用户指定的 X / GitHub 身份收成 PeerProfile，并准备一条提纲。"""

from __future__ import annotations

import re
from datetime import UTC
from typing import Literal

from pydantic import BaseModel

from finch.engagement.models import ExternalPost
from finch.github.models import PublicRepo

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
