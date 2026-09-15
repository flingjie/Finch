# Named-Person Connect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user name a person on X or GitHub and immediately persist a PeerProfile plus one interaction outline, skipping the daily browse list.

**Architecture:** Pure parse/fetch/persist lives in `src/finch/engagement/named.py`. Adapters stay read-only (`OpenCliClient.tweets`, `GhClient.user` / `list_public_repos`). Scoring and outlines reuse `score_posts` / `generate_proposals`. CLI `finch connect with` only validates flags and renders. Results never write `DiscoverySnapshot`.

**Tech Stack:** Python 3.12, Pydantic 2, Typer, pytest, ruff (`E,F,I,B,UP`, line-length 100), mypy.

## Global Constraints

- Python 3.12+; Pydantic 2; Typer CLI; tests via `uv run pytest`; lint `uv run ruff check .`; types `uv run mypy src`.
- Domain services deterministic and single-threaded; subprocess args as arrays; per-call timeouts.
- LLM output never carries `total`; weighted totals stay in Python.
- `gh` and `opencli` remain read-only; no auto-publish; `guard.evaluate_execution` unchanged.
- Same handle on X vs GitHub is two PeerProfiles; never auto-merge.
- GitHub `author_id` is login lowercased inside `peer_id_for` / `from_author`.
- Opportunity + Proposal are written together only when a proposal is produced; identity success always upserts PeerProfile.
- Named results must not write `DiscoverySnapshot`.
- Conventional commits: `feat(...)`, `test(...)`, `docs(...)`.

## File map

- Create: `src/finch/engagement/named.py` — parse target, fetch content, persist peer, prepare one outline.
- Create: `tests/unit/test_named_connect.py` — parse + orchestration.
- Modify: `src/finch/peers/models.py` — `Platform` adds `github`.
- Modify: `src/finch/engagement/models.py` — `Platform` adds `github`.
- Modify: `src/finch/peers/service.py` — github URL + lowercase author_id.
- Modify: `src/finch/twitter/opencli_client.py` — `tweets()`.
- Modify: `src/finch/github/models.py` — `PublicRepo` + parsers.
- Modify: `src/finch/github/gh_client.py` — `user()`, `list_public_repos()`, `public_repo()`.
- Modify: `src/finch/cli.py` — `connect with`.
- Modify: `prompts/propose-engagement-outline.md` — github discussion outline rule.
- Modify: skills + `CLAUDE.md` command list.
- Test: `tests/unit/test_peer_service.py`, `tests/unit/test_opencli_client.py`, `tests/unit/test_gh_client.py`, `tests/unit/test_cli_connect.py`.

---

### Task 1: Platform `github` and identity helpers

**Files:**
- Modify: `src/finch/peers/models.py`
- Modify: `src/finch/engagement/models.py`
- Modify: `src/finch/peers/service.py`
- Test: `tests/unit/test_peer_service.py`

**Interfaces:**
- Consumes: existing `peer_id_for`, `profile_url_for`, `PeerService.from_author`.
- Produces: `Platform = Literal["x", "reddit", "github"]` in both models modules; `peer_id_for("github", login)` lowercases login; `profile_url_for("github", ...)` → `https://github.com/{handle}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_peer_service.py`:

```python
def test_github_author_id_is_case_insensitive():
    assert peer_id_for("github", "iFurySt") == peer_id_for("github", "ifuryst")
    assert peer_id_for("x", "ifuryst") != peer_id_for("github", "ifuryst")


def test_profile_url_for_github():
    assert profile_url_for("github", username="iFurySt") == "https://github.com/iFurySt"
    assert profile_url_for("github", author_id="ifuryst") == "https://github.com/ifuryst"


def test_from_author_github_lowercases_id():
    profile = PeerService().from_author(
        platform="github", author_id="iFurySt", username="iFurySt"
    )
    assert profile.id == peer_id_for("github", "ifuryst")
    assert profile.platform_identities[0].author_id == "ifuryst"
    assert profile.platform_identities[0].url == "https://github.com/iFurySt"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_peer_service.py -k "github or profile_url_for" -v`

Expected: FAIL — `github` is not a valid `Platform`, and `profile_url_for` returns `None`.

- [ ] **Step 3: Implement**

In `src/finch/peers/models.py` and `src/finch/engagement/models.py` change:

```python
Platform = Literal["x", "reddit"]
```

to:

```python
Platform = Literal["x", "reddit", "github"]
```

In `src/finch/peers/service.py` `peer_id_for`:

```python
def peer_id_for(platform: str, author_id: str) -> str:
    """由 ``(platform, author_id)`` 派生稳定 peer id（幂等：同一作者恒同 id）。"""
    ident = author_id.strip()
    if platform == "github":
        ident = ident.lower()
    digest = hashlib.sha256(f"{platform}:{ident}".encode()).hexdigest()
    return f"peer_{digest[:12]}"
```

In `profile_url_for`, after the reddit branch:

```python
    if platform == "github":
        return f"https://github.com/{handle}"
```

In `PeerService.from_author`, before building `PlatformIdentity`:

```python
        ident = author_id.strip()
        if platform == "github":
            ident = ident.lower()
        display = username or author_id
        identity = PlatformIdentity(
            platform=platform,
            author_id=ident,
            username=username,
            url=url or profile_url_for(platform, username=username, author_id=ident),
        )
        return PeerProfile(
            id=peer_id_for(platform, ident),
            platform_identities=[identity],
            display_name=display,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_peer_service.py tests/unit/test_engagement_models.py tests/unit/test_peer_models.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/peers/models.py src/finch/engagement/models.py src/finch/peers/service.py tests/unit/test_peer_service.py
git commit -m "$(cat <<'EOF'
feat(peers): add github platform identity without cross-platform merge

EOF
)"
```

---

### Task 2: Parse `--x` / `--github` targets

**Files:**
- Create: `src/finch/engagement/named.py`
- Test: `tests/unit/test_named_connect.py`

**Interfaces:**
- Consumes: Task 1 `Platform` (`x` | `github` only at this parser).
- Produces: `NamedTarget(platform, handle, content_url, identity_url)` and `parse_named_target(platform, raw) -> NamedTarget` raising `ValueError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_named_connect.py`:

```python
import pytest

from finch.engagement.named import parse_named_target


def test_parse_x_handle_and_profile_url():
    bare = parse_named_target("x", "iFurySt")
    assert bare.platform == "x"
    assert bare.handle == "iFurySt"
    assert bare.content_url is None
    assert bare.identity_url == "https://x.com/iFurySt"

    at = parse_named_target("x", "@iFurySt")
    assert at.handle == "iFurySt"

    profile = parse_named_target("x", "https://x.com/iFurySt")
    assert profile.handle == "iFurySt"
    assert profile.content_url is None

    twitter = parse_named_target("x", "https://twitter.com/iFurySt")
    assert twitter.handle == "iFurySt"


def test_parse_x_status_url():
    t = parse_named_target("x", "https://x.com/iFurySt/status/123456")
    assert t.handle == "iFurySt"
    assert t.content_url == "https://x.com/iFurySt/status/123456"


def test_parse_github_handle_user_and_repo():
    user = parse_named_target("github", "iFurySt")
    assert user.platform == "github"
    assert user.handle == "ifuryst"
    assert user.content_url is None
    assert user.identity_url == "https://github.com/ifuryst"

    profile = parse_named_target("github", "https://github.com/iFurySt")
    assert profile.handle == "ifuryst"
    assert profile.content_url is None

    repo = parse_named_target("github", "https://github.com/iFurySt/Finch")
    assert repo.handle == "ifuryst"
    assert repo.content_url == "https://github.com/iFurySt/Finch"


def test_parse_rejects_wrong_host_and_empty():
    with pytest.raises(ValueError):
        parse_named_target("x", "https://github.com/iFurySt")
    with pytest.raises(ValueError):
        parse_named_target("github", "https://x.com/iFurySt")
    with pytest.raises(ValueError):
        parse_named_target("x", "")
    with pytest.raises(ValueError):
        parse_named_target("github", "https://github.com/iFurySt/Finch/issues/1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_named_connect.py -v`

Expected: FAIL — `finch.engagement.named` does not exist.

- [ ] **Step 3: Implement parser**

Create `src/finch/engagement/named.py`:

```python
"""点名连接：把用户指定的 X / GitHub 身份收成 PeerProfile，并准备一条提纲。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

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
_GH_REPO_RESERVED = frozenset({"issues", "pulls", "actions", "projects", "wiki", "security", "settings"})


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_named_connect.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/engagement/named.py tests/unit/test_named_connect.py
git commit -m "$(cat <<'EOF'
feat(engagement): parse named X and GitHub connect targets

EOF
)"
```

---

### Task 3: GitHub public user/repo adapter

**Files:**
- Modify: `src/finch/github/models.py`
- Modify: `src/finch/github/gh_client.py`
- Modify: `src/finch/engagement/named.py` (add `github_repo_to_post`)
- Test: `tests/unit/test_gh_client.py`, `tests/unit/test_named_connect.py`

**Interfaces:**
- Consumes: existing `GhClient._gh_json`.
- Produces:
  - `PublicRepo(name_with_owner, url, owner_login, description="", pushed_at=None, is_private=False, is_fork=False, archived=False, disabled=False)`
  - `GhClient.user(login: str) -> dict` with keys `login`, `html_url`
  - `GhClient.list_public_repos(login: str, limit: int = 20) -> list[PublicRepo]`
  - `GhClient.public_repo(name_with_owner: str) -> PublicRepo`
  - `github_repo_to_post(repo: PublicRepo) -> ExternalPost | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_gh_client.py`:

```python
def test_user_parses_login(monkeypatch):
    _set(json.dumps({"login": "iFurySt", "html_url": "https://github.com/iFurySt"}), monkeypatch)
    data = GhClient().user("iFurySt")
    assert data["login"] == "iFurySt"
    assert data["html_url"] == "https://github.com/iFurySt"


def test_list_public_repos_filters_and_caps(monkeypatch):
    _set(
        json.dumps(
            [
                {
                    "full_name": "iFurySt/keep",
                    "html_url": "https://github.com/iFurySt/keep",
                    "description": "agent eval harness",
                    "owner": {"login": "iFurySt"},
                    "private": False,
                    "fork": False,
                    "archived": False,
                    "disabled": False,
                    "pushed_at": "2026-09-04T06:05:12Z",
                },
                {
                    "full_name": "iFurySt/forked",
                    "html_url": "https://github.com/iFurySt/forked",
                    "description": "nope",
                    "owner": {"login": "iFurySt"},
                    "private": False,
                    "fork": True,
                    "archived": False,
                    "disabled": False,
                    "pushed_at": "2026-09-04T06:05:12Z",
                },
            ]
        ),
        monkeypatch,
    )
    repos = GhClient().list_public_repos("iFurySt", limit=20)
    assert [r.name_with_owner for r in repos] == ["iFurySt/keep"]
    assert repos[0].description == "agent eval harness"
    assert repos[0].pushed_at is not None
```

Append to `tests/unit/test_named_connect.py`:

```python
from datetime import UTC, datetime

from finch.engagement.named import github_repo_to_post
from finch.github.models import PublicRepo


def test_github_repo_to_post_skips_missing_time():
    assert github_repo_to_post(
        PublicRepo(
            name_with_owner="a/b",
            url="https://github.com/a/b",
            owner_login="a",
            description="hi",
        )
    ) is None


def test_github_repo_to_post_maps_external_post():
    post = github_repo_to_post(
        PublicRepo(
            name_with_owner="iFurySt/keep",
            url="https://github.com/iFurySt/keep",
            owner_login="iFurySt",
            description="agent eval harness",
            pushed_at=datetime(2026, 9, 4, 6, 5, 12, tzinfo=UTC),
        )
    )
    assert post is not None
    assert post.platform == "github"
    assert post.id == "iFurySt/keep"
    assert post.author_id == "ifuryst"
    assert post.content == "agent eval harness"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_gh_client.py::test_user_parses_login tests/unit/test_gh_client.py::test_list_public_repos_filters_and_caps tests/unit/test_named_connect.py::test_github_repo_to_post_skips_missing_time tests/unit/test_named_connect.py::test_github_repo_to_post_maps_external_post -v`

Expected: FAIL — methods / `PublicRepo` missing.

- [ ] **Step 3: Implement**

Add to `src/finch/github/models.py` after `RepoSummary`:

```python
class PublicRepo(BaseModel):
    """Another user's public repository (named-person connect; not the local evidence pipeline)."""

    name_with_owner: str
    url: str
    owner_login: str
    description: str = ""
    pushed_at: datetime | None = None
    is_private: bool = False
    is_fork: bool = False
    archived: bool = False
    disabled: bool = False


def parse_public_repo(data: dict) -> PublicRepo:
    owner = data.get("owner") or {}
    login = owner.get("login") or (data.get("full_name") or "").split("/", 1)[0]
    return PublicRepo(
        name_with_owner=data["full_name"],
        url=data.get("html_url") or f"https://github.com/{data['full_name']}",
        owner_login=login,
        description=data.get("description") or "",
        pushed_at=data.get("pushed_at"),
        is_private=data.get("private", False),
        is_fork=data.get("fork", False),
        archived=data.get("archived", False),
        disabled=data.get("disabled", False),
    )
```

Pydantic will coerce ISO `pushed_at` strings. Do **not** change `list_user_repos`.

In `src/finch/github/gh_client.py` import `PublicRepo, parse_public_repo` and add:

```python
    def user(self, login: str) -> dict:
        data = self._gh_json(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", f"users/{login}"]
        )
        assert isinstance(data, dict)
        return {"login": data["login"], "html_url": data.get("html_url") or ""}

    def list_public_repos(self, login: str, limit: int = 20) -> list[PublicRepo]:
        cap = max(1, min(limit, 100))
        data = self._gh_json(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"users/{login}/repos?type=owner&sort=pushed&direction=desc&per_page={cap}",
            ],
            timeout=60.0,
        )
        assert isinstance(data, list)
        out: list[PublicRepo] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            repo = parse_public_repo(item)
            if repo.is_private or repo.is_fork or repo.archived or repo.disabled:
                continue
            if repo.pushed_at is None:
                continue
            out.append(repo)
            if len(out) >= cap:
                break
        return out

    def public_repo(self, name_with_owner: str) -> PublicRepo:
        data = self._gh_json(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"repos/{name_with_owner}",
            ]
        )
        assert isinstance(data, dict)
        repo = parse_public_repo(data)
        if repo.is_private:
            raise GhError(f"repository is private: {name_with_owner}")
        return repo
```

In `src/finch/engagement/named.py` add:

```python
from datetime import UTC

from finch.engagement.models import ExternalPost
from finch.github.models import PublicRepo


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_gh_client.py tests/unit/test_named_connect.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/github/models.py src/finch/github/gh_client.py src/finch/engagement/named.py tests/unit/test_gh_client.py tests/unit/test_named_connect.py
git commit -m "$(cat <<'EOF'
feat(github): read another user's public profile and repos

EOF
)"
```

---

### Task 4: `OpenCliClient.tweets`

**Files:**
- Modify: `src/finch/twitter/opencli_client.py`
- Test: `tests/unit/test_opencli_client.py`

**Interfaces:**
- Consumes: existing `_call` / allowlist `twitter tweets`.
- Produces: `OpenCliClient.tweets(username: str, *, limit: int = 20) -> list[Tweet]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_opencli_client.py`:

```python
    def test_tweets_passes_username_and_limit(self, monkeypatch):
        captured = {}

        def fake_run(argv, timeout):
            captured["argv"] = argv
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
        OpenCliClient().tweets("iFurySt", limit=20)
        argv = captured["argv"]
        assert argv[1:4] == ["twitter", "tweets", "iFurySt"]
        assert "--limit" in argv
        assert "20" in argv
        assert "-f" in argv
        assert "json" in argv
```

Put this inside a new class `TestOpenCliClientTweets` (same file, after search tests).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_opencli_client.py::TestOpenCliClientTweets -v`

Expected: FAIL — `tweets` attribute missing.

- [ ] **Step 3: Implement**

In `src/finch/twitter/opencli_client.py` after `profile`:

```python
    def tweets(self, username: str, *, limit: int = 20) -> list[Tweet]:
        """读取用户近期推文（只读）。"""
        argv = [
            "opencli", "twitter", "tweets",
            username,
            "--limit", str(limit),
            "-f", "json",
        ]
        return _call(argv, timeout=60.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_opencli_client.py::TestOpenCliClientTweets -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finch/twitter/opencli_client.py tests/unit/test_opencli_client.py
git commit -m "$(cat <<'EOF'
feat(twitter): wrap opencli twitter tweets for named-person fetch

EOF
)"
```

---

### Task 5: `connect_named` orchestration

**Files:**
- Modify: `src/finch/engagement/named.py`
- Modify: `prompts/propose-engagement-outline.md`
- Test: `tests/unit/test_named_connect.py`

**Interfaces:**
- Consumes: `parse_named_target`, `github_repo_to_post`, `PeerService`, `score_posts`, `rank_candidates`, `generate_proposals`, `scored_post_to_opportunity`, `assess_job_contribution`, `ready_gate_blocks`, `fetch_post_by_url`, `_to_external_post`.
- Produces: `NamedConnectResult` and `connect_named(...)` as specified below.

```python
@dataclass
class NamedConnectResult:
    status: Literal["ok", "no_reply", "blocked", "failed"]
    message: str
    peer: PeerProfile | None = None
    opportunity: Opportunity | None = None
    proposal: InteractionProposal | None = None


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
) -> NamedConnectResult: ...
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_named_connect.py` (keep helpers local to this file):

```python
from datetime import UTC, datetime
from types import SimpleNamespace

from finch.codex.runner import CodexRunner
from finch.content.jobs import ContentJob, ContentJobStatus
from finch.engagement.models import ConversationScore, ExternalPost, InteractionProposal, InteractionAction
from finch.engagement.named import NamedTarget, connect_named
from finch.engagement.scoring import ScoredPost
from finch.peers.service import peer_id_for
from finch.settings import Paths, Settings
from finch.storage.repositories import (
    DiscoverySnapshotRepository,
    InteractionRepository,
    OpportunityRepository,
    PeerRepository,
)
from finch.storage.workspace import Workspace
from finch.twitter.models import Tweet


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def _x_post() -> ExternalPost:
    return ExternalPost(
        id="99",
        platform="x",
        url="https://x.com/iFurySt/status/99",
        author_id="iFurySt",
        author_name="iFurySt",
        content="shipping an agent eval harness with replay diffs",
        published_at=datetime.now(UTC),
        matched_topics=["named"],
    )


def _proposal() -> InteractionProposal:
    return InteractionProposal(
        id="x:99:draft_reply",
        post=_x_post(),
        score=ConversationScore(
            relevance=0.8,
            novelty=0.7,
            discussability=0.7,
            practical_evidence=0.6,
            relationship_value=0.4,
            total=0.7,
            reasons=["overlap"],
        ),
        action=InteractionAction.DRAFT_REPLY,
        approval_required=True,
        outline="问 replay 如何验证补偿",
        value_added="对准 eval harness",
        peer_id=peer_id_for("x", "iFurySt"),
        generation_key=f"{peer_id_for('x', 'iFurySt')}:99:draft_reply:2",
    )


class _FakeOpenCli:
    def __init__(self, *, profile=True, tweets=None, search=None, thread=None):
        self._profile = profile
        self._tweets = tweets if tweets is not None else [
            Tweet(
                id="99",
                author="iFurySt",
                text="shipping an agent eval harness with replay diffs",
                created_at="Wed Sep 02 06:05:25 +0000 2026",
                url="https://x.com/iFurySt/status/99",
            )
        ]
        self._search = search if search is not None else []
        self._thread = thread

    def profile(self, username: str):
        if not self._profile:
            return None
        return Tweet(id="u", author=username, text="", url=f"https://x.com/{username}")

    def tweets(self, username: str, *, limit: int = 20):
        return list(self._tweets)

    def search(self, query: str, *, product: str = "top", limit: int = 20):
        return list(self._search)

    def thread(self, url: str, *, limit: int = 50):
        return list(self._thread or [])


def test_connect_named_x_persists_peer_and_proposal(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    candidate = _proposal()
    monkeypatch.setattr(
        "finch.engagement.named.score_posts",
        lambda *a, **k: [ScoredPost(post=_x_post(), score=candidate.score)],
    )
    monkeypatch.setattr(
        "finch.engagement.named.rank_candidates",
        lambda scored, **k: scored,
    )
    monkeypatch.setattr(
        "finch.engagement.named.generate_proposals",
        lambda *a, **k: [candidate],
    )
    target = NamedTarget(
        platform="x",
        handle="iFurySt",
        identity_url="https://x.com/iFurySt",
    )
    result = connect_named(
        target=target,
        ws=ws,
        settings=settings,
        runner=CodexRunner(),
        opencli=_FakeOpenCli(),
    )
    assert result.status == "ok"
    peer = PeerRepository(ws).get(peer_id_for("x", "iFurySt"))
    assert peer is not None
    assert InteractionRepository(ws).get(candidate.id) is not None
    assert OpportunityRepository(ws).get(result.opportunity.id) is not None
    assert DiscoverySnapshotRepository(ws).latest() is None


def test_connect_named_profile_missing_does_not_persist(tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    target = NamedTarget(platform="x", handle="nobody", identity_url="https://x.com/nobody")
    result = connect_named(
        target=target,
        ws=ws,
        settings=settings,
        runner=CodexRunner(),
        opencli=_FakeOpenCli(profile=False, tweets=[]),
    )
    assert result.status == "failed"
    assert PeerRepository(ws).list_all() == []


def test_connect_named_no_posts_persists_peer_without_proposal(tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    target = NamedTarget(platform="x", handle="iFurySt", identity_url="https://x.com/iFurySt")
    result = connect_named(
        target=target,
        ws=ws,
        settings=settings,
        runner=CodexRunner(),
        opencli=_FakeOpenCli(tweets=[], search=[]),
    )
    assert result.status == "no_reply"
    assert "暂不回复" in result.message
    peer = PeerRepository(ws).get(peer_id_for("x", "iFurySt"))
    assert peer is not None
    assert peer.evidence_status.value == "pending_review"
    assert InteractionRepository(ws).list_all() == []
    assert OpportunityRepository(ws).list_all() == []
```

`PeerRepository.list_all`, `InteractionRepository.list_all`, `OpportunityRepository.list_all`, and `DiscoverySnapshotRepository.latest` already exist in `src/finch/storage/repositories.py`. `PeerProfile.evidence_status` is `EvidenceStatus | None`; after upsert it is `EvidenceStatus.PENDING_REVIEW`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_named_connect.py -k connect_named -v`

Expected: FAIL — `connect_named` missing.

- [ ] **Step 3: Implement `connect_named`**

Add these imports and functions to `src/finch/engagement/named.py` (keep parser code). Implementation must:

1. Fetch identity + posts (`_fetch_x` / `_fetch_github`).
2. If identity is missing → `status="failed"`, no writes.
3. Else `PeerService.merge_discovered` + `PeerRepository.upsert`. If no posts, set `evidence_status=PENDING_REVIEW`, return `no_reply` with `暂不回复：没有可讨论的公开内容`.
4. If `job` is not None, `assess_job_contribution`; on fail return `no_reply` (peer already written, no opportunity/proposal).
5. `score_posts` then `rank_candidates`. If ranked empty, synthesize one `ScoredPost` with `ConversationScore(relevance=0.8, novelty=0.8, discussability=0.8, practical_evidence=0.7, relationship_value=0.5, total=0.78, reasons=[reason])` like `connect create`.
6. Take **only the first** ranked item.
7. `generate_proposals(..., full_draft=full_draft, contribution_basis_refs=..., job=job)`. If empty → `no_reply` (no opportunity/proposal).
8. `ready_gate_blocks` on outline/draft; if `secret_detected` → `blocked` (no opportunity/proposal).
9. Idempotent proposal upsert via `generation_key`.
10. `scored_post_to_opportunity(..., discovered_via=f"named:{target.platform}")` then `OpportunityRepository.upsert`.
11. Never touch `DiscoverySnapshotRepository`.

`_fetch_x`:

- If `target.content_url`: `fetch_post_by_url`; success → peer from post author + `[post]`; failure → `(None, [])`.
- Else: `opencli.profile(handle)` None → `(None, [])`. Peer via `from_author(platform="x", author_id=handle, username=handle)`. Tweets via `tweets`; on exception or empty, `search(f"from:{handle}", limit=20)`. Map with `_to_external_post(..., topic="named")`, drop None.

`_fetch_github`:

- `gh.user(handle)` raising `GhError` → `(None, [])`.
- Peer via `from_author(platform="github", author_id=handle)`.
- If `content_url`: parse `owner/repo` from the URL path, `gh.public_repo`, map with `github_repo_to_post`; private/error → `(None, [])` only when user() also failed; if user exists but repo fails, persist peer path: return `(peer, [])`.
- Else: `list_public_repos(handle, limit=20)` mapped through `github_repo_to_post`.

Use `opencli or OpenCliClient()` and `gh or GhClient()`.

Add this line to `prompts/propose-engagement-outline.md` after the existing “Forbidden” bullet:

```
- If a post's platform is "github", write a discussion outline the user can send about that public repository. Do not claim Finch will comment on GitHub or post to X.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_named_connect.py -v`

Expected: PASS.

Also add a GitHub happy-path test with a stub `gh` object exposing `user`, `list_public_repos` returning one `PublicRepo` with `pushed_at`, monkeypatching score/rank/generate like the X test, asserting `post.platform == "github"` on the saved proposal.

- [ ] **Step 5: Commit**

```bash
git add src/finch/engagement/named.py prompts/propose-engagement-outline.md tests/unit/test_named_connect.py
git commit -m "$(cat <<'EOF'
feat(engagement): persist named peers and prepare one outline

EOF
)"
```

---

### Task 6: CLI `finch connect with`

**Files:**
- Modify: `src/finch/cli.py` (add command after `connect_create`; update `connect_app` help)
- Modify: `CLAUDE.md` CLI surface line (add `with`)
- Test: `tests/unit/test_cli_connect.py`

**Interfaces:**
- Consumes: `parse_named_target`, `connect_named`, existing `--note` / `--from-idea` job loading from `connect_create`.
- Produces: `finch connect with --x|--github VALUE` with optional `--note` `--from-idea` `--draft` `--json`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cli_connect.py`:

```python
def test_connect_with_requires_exactly_one_source(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["connect", "with"])
    assert r.exit_code == 1
    assert "--x" in r.output or "github" in r.output
    r = CliRunner().invoke(
        app, ["connect", "with", "--x", "a", "--github", "b"]
    )
    assert r.exit_code == 1


def test_connect_with_x_renders_source_and_saves(monkeypatch, tmp_path):
    from finch.engagement.named import NamedConnectResult
    from finch.peers.service import PeerService

    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    peer = PeerService().from_author(platform="x", author_id="iFurySt", username="iFurySt")
    candidate = _candidate()
    candidate = candidate.model_copy(update={"peer_id": peer.id, "outline": "问 replay"})

    def fake_connect_named(**kwargs):
        PeerRepository(ws).upsert(peer)
        InteractionRepository(ws).upsert(candidate, run_id="with")
        return NamedConnectResult(
            status="ok",
            message="ok",
            peer=peer,
            proposal=candidate,
        )

    monkeypatch.setattr(cli, "connect_named", fake_connect_named)
    r = CliRunner().invoke(app, ["connect", "with", "--x", "iFurySt"])
    assert r.exit_code == 0, r.output
    assert "X" in r.output or "x.com" in r.output
    assert InteractionRepository(ws).get(candidate.id) is not None
```

If `connect_named` is imported inside the command, monkeypatch `finch.engagement.named.connect_named` instead of `cli.connect_named`. Match the import style of `connect_create` (inline vs module-level). Prefer a module-level import in `cli.py`: `from .engagement.named import connect_named, parse_named_target` so the test can patch `cli.connect_named`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli_connect.py -k connect_with -v`

Expected: FAIL — no `with` command.

- [ ] **Step 3: Implement CLI**

Update `connect_app` help to mention `with`.

Add command **before** `connect_approve` (after `connect_create`):

```python
@connect_app.command("with")
def connect_with(
    x: str | None = typer.Option(None, "--x", help="X handle 或 URL"),
    github: str | None = typer.Option(None, "--github", help="GitHub handle 或 URL"),
    from_idea: str | None = typer.Option(None, "--from-idea", help="已有 idea / ContentJob id"),
    note: str | None = typer.Option(None, "--note", help="个人笔记原文"),
    full_draft: bool = typer.Option(False, "--draft", help="生成完整回复草稿"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """点名一个人：落 PeerProfile 并准备一条提纲（跳过今日浏览）。"""
```

Validation:

- If `from_idea and note`: echo `pass only one of --from-idea / --note`, exit 1.
- If bool(x) == bool(github): echo `pass exactly one of --x / --github`, exit 1.

Then copy the `--from-idea` / `--note` job loading block from `connect_create` (FragmentService / IdeaService / ContentJobRepository). Parse target:

```python
    try:
        target = parse_named_target("x" if x else "github", x or github or "")
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
```

Call `connect_named(...)`. Map result:

- `failed` → echo message, exit 1
- `ok` → text: first line `来源：X @{handle}` or `来源：GitHub {handle}` then `_render_proposal_card(proposal)`; JSON: dump status, message, peer_id, opportunity_id, proposal
- `no_reply` / `blocked` → same payload shape as `connect create` (`{"status", "reason"/"message"}`); text prints `result.message`; exit 0

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli_connect.py tests/unit/test_named_connect.py -v`

Expected: PASS.

Update `CLAUDE.md` CLI surface: `connect ... (refresh / today / daily / more / expand / prepare / feedback / approve / reject / edit / record / create / with)`.

- [ ] **Step 5: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_connect.py CLAUDE.md
git commit -m "$(cat <<'EOF'
feat(cli): add finch connect with for named X or GitHub peers

EOF
)"
```

---

### Task 7: Skill presentation — stop inventory dumps

**Files:**
- Modify: `skills/interaction-preparation/SKILL.md`
- Modify: `skills/interaction-preparation/references/presentation.md`
- Modify: `skills/peer-discovery/SKILL.md`
- Modify: `skills/_shared/agent-presentation.md`

**Interfaces:**
- Consumes: Task 6 CLI.
- Produces: agent instructions for named-person path.

- [ ] **Step 1: Edit skills (no pytest; this is the deliverable)**

In `skills/interaction-preparation/SKILL.md` description, add that naming a specific person (handle/URL) is already a selection.

CLI section, insert above prepare:

```markdown
- 点名一个人：`finch connect with --x <handle|url>` 或 `--github <handle|url>`
  （可选 `--note` / `--from-idea` / `--draft`）。跳过浏览，直接落 PeerProfile 并准备 1 条提纲。
- 用户只给裸 handle、没说来源：先问 X 还是 GitHub，再调用本命令。禁止为此翻 `var/peers/` 或跑 `connect today`。
```

Keep existing prepare/create/approve commands.

In `skills/interaction-preparation/references/presentation.md`, add a **点名** shape before the generic shape:

```text
已把 @iFurySt（X）收进连接对象，并准备了 1 条提纲：

**回复 @iFurySt：…** 〔来源：X〕
为什么是这个人：…
提纲：…
回复「批准」「改提纲」或「跳过」。
```

GitHub uses「讨论提纲」and says the user sends it themselves. Do not list today's other candidates.

User mapping: `点名 @handle` / `连接 github.com/user` → `finch connect with --x|--github ...`

In `skills/peer-discovery/SKILL.md` 边界, add:

```markdown
- 用户点名具体人（handle / 主页）时，这不是发现请求 → `interaction-preparation` 的 `connect with`。不要用今日机会名单顶替。
```

In `skills/_shared/agent-presentation.md` 约束, add:

```markdown
- **点名路径**：只呈现这一个人的准备卡（带来源 X / GitHub）。禁止报 Peer 总数、今日其他候选人、已有 proposals、或让用户自己跑 `finch peers` / `connect create`。裸 handle 先问来源平台。
```

- [ ] **Step 2: Sanity-check wording**

Read the four files and confirm they still say Skill 只调用 CLI、不复制业务逻辑；批准 ≠ 已发布。

- [ ] **Step 3: Run unit tests that should still pass**

Run: `uv run pytest tests/unit/test_named_connect.py tests/unit/test_cli_connect.py tests/unit/test_peer_service.py tests/unit/test_gh_client.py tests/unit/test_opencli_client.py -q`

Expected: PASS.

- [ ] **Step 4: Lint / types**

Run: `uv run ruff check src tests && uv run mypy src`

Expected: no new errors.

- [ ] **Step 5: Commit**

```bash
git add skills/interaction-preparation/SKILL.md skills/interaction-preparation/references/presentation.md skills/peer-discovery/SKILL.md skills/_shared/agent-presentation.md
git commit -m "$(cat <<'EOF'
docs(skills): route named people through connect with, not inventory dumps

EOF
)"
```

---

## Self-review

1. **Spec coverage**
   - Command / mutually exclusive flags → Task 6
   - Parse handle/URL → Task 2
   - Platform github + no auto-merge + lowercase login → Task 1
   - X tweets + fallback search → Tasks 4–5
   - GitHub public user/repos → Task 3
   - Persist peer, one outline, no snapshot → Task 5
   - 暂不回复 / failed / blocked table → Task 5
   - `--note` / `--from-idea` / `--draft` → Task 6
   - Skill presentation → Task 7
   - Outline prompt github rule → Task 5

2. **Placeholders:** none remaining; repository `list_all` / `latest` APIs are named in Task 5.

3. **Types:** `NamedTarget`, `NamedConnectResult`, `connect_named`, `parse_named_target`, `PublicRepo`, `github_repo_to_post` are used consistently across later tasks.
