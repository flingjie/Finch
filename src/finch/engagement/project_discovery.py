"""有界项目→人发现：从 GitHub issue 搜索补充候选人（fail-closed）。

复用 GhClient；认证或搜索失败只记录缺口，不中断 X/Reddit 结果。
跨平台身份不合并：GitHub login 以 ``github:<login>`` 作为 author_id。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from finch.engagement.models import ExternalPost
from finch.github.gh_client import GhClient, GhError
from finch.settings import InterestsSettings

_GITHUB_URL = re.compile(
    r"https?://github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull)/(\d+)",
    re.IGNORECASE,
)
_MAX_QUERIES = 3
_MAX_ISSUES_PER_QUERY = 5
_MAX_AUTHORS_PER_SEED = 5


@dataclass
class ProjectDiscoveryOutcome:
    """项目路径结果：合成 ExternalPost + 失败原因（不抛出）。"""

    posts: list[ExternalPost] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)


def _terms_from_interests(interests: InterestsSettings) -> list[str]:
    terms: list[str] = []
    for t in [*interests.current_questions, *interests.practice_refs, *interests.usage_queries]:
        text = t.strip()
        if text and text not in terms:
            terms.append(text)
    return terms[:_MAX_QUERIES]


def github_urls_in_posts(posts: list[ExternalPost]) -> list[tuple[str, str, int]]:
    """Extract (owner, repo, number) from post content/urls."""
    found: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str, int]] = set()
    for p in posts:
        for blob in (p.url, p.content):
            for m in _GITHUB_URL.finditer(blob or ""):
                key = (m.group(1), m.group(2), int(m.group(3)))
                if key not in seen:
                    seen.add(key)
                    found.append(key)
    return found[:_MAX_QUERIES]


def _synthetic_post(
    *,
    login: str,
    body: str,
    url: str,
    topic: str,
) -> ExternalPost:
    return ExternalPost(
        id=f"gh_{login}_{abs(hash(url + login)) % 10_000_000}",
        platform="x",
        url=url,
        author_id=f"github:{login}",
        author_name=login,
        content=body[:2000] or f"GitHub participant @{login}",
        published_at=datetime.now(UTC),
        metrics={},
        matched_topics=[topic] if topic else ["usage"],
    )


def discover_project_participants(
    interests: InterestsSettings,
    *,
    seed_posts: list[ExternalPost] | None = None,
    client: GhClient | None = None,
    max_authors: int = 15,
) -> ProjectDiscoveryOutcome:
    """Search issues / read commenters; return synthetic posts for aggregation.

    Fail-closed: any GhError becomes a failure dict; never raises to caller.
    """
    outcome = ProjectDiscoveryOutcome()
    gh = client or GhClient()
    authors: list[ExternalPost] = []
    seen_logins: set[str] = set()

    try:
        auth = gh.auth_status()
        if not auth.get("ok"):
            outcome.failures.append(
                {
                    "path": "project",
                    "reason": f"gh auth unavailable: {auth.get('detail', '')}",
                }
            )
            return outcome
    except Exception as exc:  # noqa: BLE001
        outcome.failures.append({"path": "project", "reason": str(exc)})
        return outcome

    for term in _terms_from_interests(interests):
        if len(authors) >= max_authors:
            break
        try:
            data = gh._gh_json(  # noqa: SLF001 — bounded read-only search
                [
                    "gh",
                    "search",
                    "issues",
                    term,
                    "--limit",
                    str(_MAX_ISSUES_PER_QUERY),
                    "--json",
                    "url,title,author,body",
                ],
                timeout=45.0,
            )
        except GhError as exc:
            outcome.failures.append({"path": "project", "query": term, "reason": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001
            outcome.failures.append({"path": "project", "query": term, "reason": str(exc)})
            continue
        if not isinstance(data, list):
            continue
        per_seed = 0
        for item in data:
            if per_seed >= _MAX_AUTHORS_PER_SEED or len(authors) >= max_authors:
                break
            author = item.get("author") or {}
            login = (author.get("login") if isinstance(author, dict) else None) or ""
            if not login or login in seen_logins:
                continue
            seen_logins.add(login)
            url = str(item.get("url") or "")
            title = str(item.get("title") or "")
            body = str(item.get("body") or "")
            authors.append(
                _synthetic_post(
                    login=login,
                    body=f"{title}\n{body}".strip(),
                    url=url or f"https://github.com/search?q={term}",
                    topic=term,
                )
            )
            per_seed += 1

    # One-hop: commenters on GitHub URLs already present in social posts.
    for owner, repo, number in github_urls_in_posts(seed_posts or []):
        if len(authors) >= max_authors:
            break
        try:
            data = gh._gh_json(  # noqa: SLF001
                [
                    "gh",
                    "api",
                    f"repos/{owner}/{repo}/issues/{number}/comments",
                    "--paginate",
                ],
                timeout=45.0,
            )
        except Exception as exc:  # noqa: BLE001
            outcome.failures.append(
                {
                    "path": "project",
                    "query": f"{owner}/{repo}#{number}",
                    "reason": str(exc),
                }
            )
            continue
        if not isinstance(data, list):
            continue
        per_seed = 0
        for item in data:
            if per_seed >= _MAX_AUTHORS_PER_SEED or len(authors) >= max_authors:
                break
            user = item.get("user") or {}
            login = user.get("login") if isinstance(user, dict) else ""
            if not login or login in seen_logins:
                continue
            seen_logins.add(login)
            url = str(item.get("html_url") or f"https://github.com/{owner}/{repo}/issues/{number}")
            body = str(item.get("body") or "")
            authors.append(
                _synthetic_post(
                    login=login,
                    body=body or f"Commented on {owner}/{repo}#{number}",
                    url=url,
                    topic="project_discussion",
                )
            )
            per_seed += 1

    outcome.posts = authors
    return outcome
