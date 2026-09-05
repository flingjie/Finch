# Reddit 搜索接入 opencli 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把互动轨道的 `RedditPostSearchProvider` 占位实现替换为真实 opencli 搜索，使 Reddit 帖子与 X/Twitter 一样流入通用评分 → 提案 → 审批队列。

**Architecture:** 新建 `src/finch/reddit/` 模块镜像 `twitter/`（`models.py` + `opencli_client.py`），提供 `RedditPost` 模型与 `RedditOpenCliClient`（只读 allowlist / 写命令 denylist）。`RedditPostSearchProvider` 包装该客户端并把 `RedditPost` 映射为 `ExternalPost`；DI 通过 `run_discovery_engagement_flow` 新增的 `reddit_opencli` 参数贯通 `flow.py` 与 `cli.py`。

**Tech Stack:** Python 3.12+，Pydantic 2，opencli 1.8.7（`reddit` adapter），pytest，uv。

## Global Constraints

- 只读不变量：opencli Reddit 写命令（comment / reply / save / upvote / subscribe / login）必须 denylist，`_check_allowlist` 双层校验（denylist 命中优先于 allowlist 放行）。
- 禁止伪造时间：`published_at()` 解析失败返回 `None`，调用方跳过该帖。
- `selftext` 截断上限 `_SELFTEXT_MAX_CHARS = 500`；空 `selftext` 退化为仅标题。
- Reddit metrics 键固定为 `{"upvotes": score, "comments": comments}`；`scoring._POPULARITY_KEYS` 需新增 `"upvotes"`。
- 外部 ≠ 证据：Reddit 帖子只映射为 `ExternalPost`，绝不升级为 personal 证据。
- 确定性总分：`total` 仍由 `scoring.weighted_total` 在代码中计算。
- 子进程纪律：参数用数组（不拼接 shell 字符串），`_run` 复用 `finch.github.gh_client._run`。
- 遵循仓库约定：`uv run pytest`、`uv run ruff check .`、`uv run mypy src`。

---

## 文件结构

- 创建 `src/finch/reddit/__init__.py` — 模块说明。
- 创建 `src/finch/reddit/models.py` — `RedditPost` 模型 + 类型化错误 + `published_at()` + `content()`。
- 创建 `src/finch/reddit/opencli_client.py` — `RedditOpenCliClient`（allowlist/denylist/`_call`/`search`）。
- 修改 `src/finch/engagement/search.py` — 替换 `RedditPostSearchProvider` 占位 + 新增 `_reddit_to_external_post`。
- 修改 `src/finch/engagement/scoring.py` — `_POPULARITY_KEYS` 增加 `"upvotes"`。
- 修改 `src/finch/engagement/flow.py` — `_build_providers` / `run_discovery_engagement_flow` 增加 `reddit_opencli`。
- 修改 `src/finch/cli.py` — 构造并传入 `RedditOpenCliClient()`。
- 测试：`tests/unit/test_reddit_models.py`、`tests/unit/test_reddit_client.py`、`tests/contract/test_reddit_models.py`；更新 `tests/unit/test_engagement_search.py`、`tests/unit/test_engagement_flow.py`。
- 创建 fixture `tests/fixtures/opencli/reddit-search.json`。

---

## Task 1: RedditPost 模型与类型化错误

**Files:**
- Create: `src/finch/reddit/__init__.py`
- Create: `src/finch/reddit/models.py`
- Test: `tests/unit/test_reddit_models.py`

**Interfaces:**
- Produces: `RedditPost`（字段 `id: str`, `title: str`, `subreddit: str | None`, `author: str`, `score: int`, `comments: int`, `url: str`, `created_utc: int | str | float | None`, `selftext: str`）、`RedditPost.published_at() -> datetime | None`、`RedditPost.content() -> str`；异常类 `RedditError`, `RedditSourceUnavailable`, `RedditRateLimited`, `RedditCommandBlocked`（均含 `error_code: str`）。

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_reddit_models.py`：

```python
"""Reddit 数据模型单元测试。"""

from datetime import UTC, datetime

from finch.reddit.models import RedditCommandBlocked, RedditPost


def _post(**overrides) -> RedditPost:
    data = dict(
        id="1abc23",
        title="How do you test agent reliability?",
        subreddit="LocalLLaMA",
        author="alice",
        score=128,
        comments=34,
        url="https://www.reddit.com/r/LocalLLaMA/comments/1abc23/title/",
        created_utc=1756627200,
        selftext="We run a failure replay harness.",
    )
    data.update(overrides)
    return RedditPost(**data)


def test_parses_minimal_post():
    post = RedditPost(id="1", title="t", author="a", url="u")
    assert post.id == "1"
    assert post.score == 0
    assert post.comments == 0
    assert post.selftext == ""
    assert post.subreddit is None


def test_coerces_string_counts_and_none_selftext():
    post = _post(score="128", comments="34", selftext=None)
    assert post.score == 128
    assert post.comments == 34
    assert post.selftext == ""


def test_published_at_parses_unix_seconds():
    assert _post(created_utc=1756627200).published_at() == datetime(2025, 8, 31, 8, 0, tzinfo=UTC)


def test_published_at_parses_string_and_float():
    assert _post(created_utc="1756627200").published_at() is not None
    assert _post(created_utc=1756627200.0).published_at() is not None


def test_published_at_none_when_missing_or_invalid():
    assert _post(created_utc=None).published_at() is None
    assert _post(created_utc="not-a-number").published_at() is None


def test_content_title_only_when_selftext_empty():
    assert _post(title="A question", selftext="").content() == "A question"


def test_content_title_plus_truncated_selftext():
    post = _post(title="A question", selftext="x" * 1000)
    content = post.content()
    assert content.startswith("A question\n\n")
    body = content.split("\n\n", 1)[1]
    assert len(body) == 500


def test_command_blocked_error_code():
    assert RedditCommandBlocked().error_code == "COMMAND_BLOCKED"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_reddit_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finch.reddit'`

- [ ] **Step 3: 实现模型**

创建 `src/finch/reddit/__init__.py`：

```python
"""Reddit 读取 adapter（通过 opencli）。"""
```

创建 `src/finch/reddit/models.py`：

```python
"""Reddit 数据模型（opencli reddit search 输出映射）。"""

from datetime import UTC, datetime

from pydantic import BaseModel, field_validator

_SELFTEXT_MAX_CHARS = 500


class RedditPost(BaseModel):
    """opencli reddit search 原始 JSON 映射。

    未知字段由 Pydantic 忽略，contract test 捕获字段变化。
    """

    id: str
    title: str
    subreddit: str | None = None
    author: str
    score: int = 0
    comments: int = 0
    url: str
    created_utc: int | str | float | None = None
    selftext: str = ""

    @field_validator("score", "comments", mode="before")
    @classmethod
    def _coerce_int(cls, v: object) -> int:
        if isinstance(v, str):
            return int(v) if v else 0
        return v or 0  # type: ignore[return-value]

    @field_validator("selftext", mode="before")
    @classmethod
    def _coerce_selftext(cls, v: object) -> str:
        if isinstance(v, str):
            return v
        return ""

    def published_at(self) -> datetime | None:
        """解析 created_utc（Unix epoch 秒）为 UTC datetime；失败返回 None（禁止伪造时间）。"""
        if self.created_utc is None:
            return None
        try:
            return datetime.fromtimestamp(float(self.created_utc), tz=UTC)
        except (ValueError, TypeError, OSError):
            return None

    def content(self) -> str:
        """标题 + 截断正文；空 selftext 退化为仅标题（链接/图片帖常见）。"""
        body = self.selftext.strip()
        if not body:
            return self.title
        return f"{self.title}\n\n{body[:_SELFTEXT_MAX_CHARS]}"


class RedditError(RuntimeError):
    """Reddit 调用失败基类."""

    error_code: str

    def __init__(self, message: str, error_code: str = "REDDIT_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code


class RedditSourceUnavailable(RedditError):
    """Bridge 离线、未登录或浏览器不可用."""

    def __init__(self, message: str = "Reddit source unavailable") -> None:
        super().__init__(message, "REDDIT_SOURCE_UNAVAILABLE")


class RedditRateLimited(RedditError):
    """限流."""

    def __init__(self, message: str = "Reddit rate limited") -> None:
        super().__init__(message, "RATE_LIMITED")


class RedditCommandBlocked(RedditError):
    """Denylist 命中（defense in depth）."""

    def __init__(self, message: str = "Reddit write command blocked") -> None:
        super().__init__(message, "COMMAND_BLOCKED")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_reddit_models.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 提交**

```bash
git add src/finch/reddit/__init__.py src/finch/reddit/models.py tests/unit/test_reddit_models.py
git commit -m "feat(reddit): add RedditPost model and typed errors"
```

---

## Task 2: RedditOpenCliClient 只读封装

**Files:**
- Create: `src/finch/reddit/opencli_client.py`
- Test: `tests/unit/test_reddit_client.py`

**Interfaces:**
- Consumes: `RedditPost`, `RedditError`, `RedditSourceUnavailable`, `RedditRateLimited`, `RedditCommandBlocked`（来自 Task 1）。
- Produces: `RedditOpenCliClient.search(query: str, *, sort: str = "relevance", limit: int = 20) -> list[RedditPost]`；模块级 `_check_allowlist(argv: list[str]) -> None`、`_parse_posts(stdout: str) -> list[RedditPost]`。

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_reddit_client.py`：

```python
"""Reddit opencli 客户端单元测试。"""
import json

import pytest

from finch.reddit.models import RedditCommandBlocked, RedditSourceUnavailable
from finch.reddit.opencli_client import RedditOpenCliClient, _check_allowlist, _parse_posts


class TestCheckAllowlist:
    def test_allows_search(self):
        _check_allowlist(["opencli", "reddit", "search", "query"])

    def test_allows_hot(self):
        _check_allowlist(["opencli", "reddit", "hot"])

    def test_blocks_comment(self):
        with pytest.raises(RedditCommandBlocked):
            _check_allowlist(["opencli", "reddit", "comment", "post-id", "text"])

    def test_blocks_reply(self):
        with pytest.raises(RedditCommandBlocked):
            _check_allowlist(["opencli", "reddit", "reply", "comment-id", "text"])

    def test_blocks_upvote(self):
        with pytest.raises(RedditCommandBlocked):
            _check_allowlist(["opencli", "reddit", "upvote", "post-id"])

    def test_blocks_unknown_command(self):
        with pytest.raises(RedditCommandBlocked):
            _check_allowlist(["opencli", "reddit", "hack"])


class TestParsePosts:
    def test_parses_list(self):
        data = [
            {"id": "1", "title": "a", "author": "u", "url": "https://reddit.com/1"},
            {"id": "2", "title": "b", "author": "v", "url": "https://reddit.com/2"},
        ]
        posts = _parse_posts(json.dumps(data))
        assert len(posts) == 2
        assert posts[0].id == "1"

    def test_parses_single_dict(self):
        data = {"id": "1", "title": "a", "author": "u", "url": "u"}
        posts = _parse_posts(json.dumps(data))
        assert len(posts) == 1

    def test_skips_invalid_items(self):
        data = [
            {"id": "1", "title": "a", "author": "u", "url": "u"},
            {"not": "a", "post": "object"},
        ]
        posts = _parse_posts(json.dumps(data))
        assert len(posts) == 1

    def test_empty_list(self):
        assert _parse_posts(json.dumps([])) == []

    def test_invalid_json_raises(self):
        from finch.reddit.models import RedditError

        with pytest.raises(RedditError):
            _parse_posts("not json")


class TestRedditOpenCliClientSearch:
    def test_search_returns_posts(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps([{"id": "1", "title": "a", "author": "u", "url": "u"}]),
                "stderr": "",
            }

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        client = RedditOpenCliClient()
        posts = client.search("agent reliability")
        assert len(posts) == 1
        assert posts[0].id == "1"

    def test_search_passes_correct_argv(self, monkeypatch):
        captured = {}

        def fake_run(argv, timeout):
            captured["argv"] = argv
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        client = RedditOpenCliClient()
        client.search("agent evals", sort="hot", limit=10)
        assert captured["argv"][3] == "agent evals"
        assert "--sort" in captured["argv"]
        assert "hot" in captured["argv"]
        assert "--limit" in captured["argv"]
        assert "10" in captured["argv"]
        assert "-f" in captured["argv"]
        assert "json" in captured["argv"]

    def test_search_uses_background_persistent_flags(self, monkeypatch):
        captured = {}

        def fake_run(argv, timeout):
            captured["argv"] = argv
            return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        RedditOpenCliClient().search("hello")
        argv = captured["argv"]
        assert "--window" in argv
        assert argv[argv.index("--window") + 1] == "background"
        assert "--site-session" in argv
        assert argv[argv.index("--site-session") + 1] == "persistent"

    def test_search_not_logged_in_raises(self, monkeypatch):
        def fake_run(argv, timeout):
            return {
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "Error: not logged in to Reddit",
            }

        monkeypatch.setattr("finch.reddit.opencli_client._run", fake_run)
        with pytest.raises(RedditSourceUnavailable):
            RedditOpenCliClient().search("hello")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_reddit_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finch.reddit.opencli_client'`

- [ ] **Step 3: 实现客户端**

创建 `src/finch/reddit/opencli_client.py`：

```python
"""opencli Reddit 只读封装（spec 2026-09-05）。"""

import json
import os

from finch.github.gh_client import _run

from .models import (
    RedditCommandBlocked,
    RedditError,
    RedditPost,
    RedditRateLimited,
    RedditSourceUnavailable,
)

# 允许的只读命令前缀
_ALLOWLIST: set[str] = {
    "reddit search",
    "reddit hot",
    "reddit subreddit",
    "reddit frontpage",
    "reddit home",
    "reddit popular",
    "reddit user",
    "reddit read",
    "reddit saved",
    "reddit upvoted",
    "reddit subscribed",
    "reddit subreddit-info",
    "reddit whoami",
    "reddit user-posts",
    "reddit user-comments",
}

# 明确阻断的写命令
_DENYLIST: set[str] = {
    "reddit comment",
    "reddit reply",
    "reddit save",
    "reddit upvote",
    "reddit subscribe",
    "reddit login",
}


def _check_allowlist(argv: list[str]) -> None:
    """Defense in depth：检查命令前缀是否在 allowlist 中且不在 denylist 中."""
    reddit_cmd = " ".join(argv[1:3])
    if reddit_cmd in _DENYLIST:
        raise RedditCommandBlocked(f"Command blocked by policy: {reddit_cmd}")
    if reddit_cmd not in _ALLOWLIST:
        raise RedditCommandBlocked(f"Command not in allowlist: {reddit_cmd}")


def _parse_posts(stdout: str) -> list[RedditPost]:
    """解析 opencli JSON 输出为 RedditPost 列表."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RedditError(f"Invalid JSON from opencli: {exc}") from exc
    if not isinstance(data, list):
        if isinstance(data, dict):
            data = [data]
        else:
            raise RedditError(f"Expected list from opencli, got {type(data).__name__}")
    posts: list[RedditPost] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            posts.append(RedditPost.model_validate(item))
        except Exception:  # noqa: BLE001 - 单条解析失败不中断整批
            continue
    return posts


def _browser_flags() -> list[str]:
    """opencli 浏览器通用选项：默认后台窗口 + 复用登录会话（可用 OPENCLI_WINDOW 覆盖）。"""
    window = os.environ.get("OPENCLI_WINDOW", "background")
    return ["--window", window, "--site-session", "persistent"]


def _call(argv: list[str], timeout: float = 60.0) -> list[RedditPost]:
    """执行 opencli 命令并解析结果."""
    _check_allowlist(argv)
    r = _run([*argv, *_browser_flags()], timeout=timeout)
    if not r["ok"]:
        stderr = (r["stderr"] or "").strip()
        if "not logged in" in stderr.lower() or "login" in stderr.lower():
            raise RedditSourceUnavailable(f"Reddit not logged in: {stderr}")
        if "bridge" in stderr.lower() or "daemon" in stderr.lower():
            raise RedditSourceUnavailable(f"Browser bridge unavailable: {stderr}")
        if "rate" in stderr.lower() or "too many" in stderr.lower():
            raise RedditRateLimited(f"Rate limited: {stderr}")
        raise RedditError(f"opencli failed (exit={r['exit_code']}): {stderr}")
    return _parse_posts(r["stdout"])


class RedditOpenCliClient:
    """opencli Reddit 只读客户端（仅 search，见 spec §1）。"""

    def search(
        self, query: str, *, sort: str = "relevance", limit: int = 20
    ) -> list[RedditPost]:
        """搜索 Reddit 帖子."""
        argv = [
            "opencli", "reddit", "search",
            query,
            "--sort", sort,
            "--limit", str(limit),
            "-f", "json",
        ]
        return _call(argv, timeout=60.0)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_reddit_client.py -v`
Expected: PASS (16 passed)

- [ ] **Step 5: 提交**

```bash
git add src/finch/reddit/opencli_client.py tests/unit/test_reddit_client.py
git commit -m "feat(reddit): add opencli read-only client"
```

---

## Task 3: RedditPostSearchProvider 替换占位实现

**Files:**
- Modify: `src/finch/engagement/search.py`
- Modify: `src/finch/engagement/scoring.py`
- Test: `tests/unit/test_engagement_search.py`

**Interfaces:**
- Consumes: `RedditPost`, `RedditOpenCliClient`（来自 Task 1/2）、`ExternalPost`（既有）。
- Produces: `RedditPostSearchProvider.search(query: str, *, limit: int) -> list[ExternalPost]`、`RedditPostSearchProvider.available() -> True`、模块级 `_reddit_to_external_post(post: RedditPost, *, topic: str) -> ExternalPost | None`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_engagement_search.py` 顶部 import 区追加（其余 import 不动）：

```python
from finch.reddit.models import RedditPost
```

删除原 `test_reddit_provider_reports_unavailable` 测试，替换为以下两个测试（`_post` helper 已有，沿用；`datetime` / `UTC` 已在文件顶部 import）：

```python
def test_reddit_provider_maps_post_to_external_post():
    class FakeRedditClient:
        def search(self, query, *, sort="relevance", limit=20):
            return [
                RedditPost(
                    id="1abc23",
                    title="How do you test agent reliability?",
                    subreddit="LocalLLaMA",
                    author="alice",
                    score=128,
                    comments=34,
                    url="https://www.reddit.com/r/LocalLLaMA/comments/1abc23/t/",
                    created_utc=1756627200,
                    selftext="We run a failure replay harness.",
                )
            ]

    provider = RedditPostSearchProvider(client=FakeRedditClient())
    posts = provider.search("agent reliability", limit=5)

    assert len(posts) == 1
    post = posts[0]
    assert post.platform == "reddit"
    assert post.id == "1abc23"
    assert post.author_id == "alice"
    assert post.author_name == "alice"
    assert post.content.startswith("How do you test agent reliability?")
    assert post.published_at == datetime(2025, 8, 31, 8, 0, tzinfo=UTC)
    assert post.metrics == {"upvotes": 128, "comments": 34}
    assert post.matched_topics == ["agent reliability"]


def test_reddit_provider_skips_post_without_parseable_time():
    class FakeRedditClient:
        def search(self, query, *, sort="relevance", limit=20):
            return [RedditPost(id="bad", title="t", author="a", url="u", created_utc="bad")]

    provider = RedditPostSearchProvider(client=FakeRedditClient())
    assert provider.search("q", limit=5) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_engagement_search.py -v`
Expected: FAIL — `test_reddit_provider_reports_unavailable` 不存在（已删），新测试 `test_reddit_provider_maps_post_to_external_post` 报错（provider 仍是占位实现，`available()` 为 False 且 `search` 抛异常）。

- [ ] **Step 3: 实现 provider 与热度键**

在 `src/finch/engagement/search.py` 顶部 import 区，把：

```python
from finch.twitter.models import Tweet
from finch.twitter.opencli_client import OpenCliClient
```

改为：

```python
from finch.reddit.models import RedditPost
from finch.reddit.opencli_client import RedditOpenCliClient
from finch.twitter.models import Tweet
from finch.twitter.opencli_client import OpenCliClient
```

把整个 `RedditPostSearchProvider` 类替换为：

```python
class RedditPostSearchProvider:
    """Reddit 搜索适配器：包装 ``RedditOpenCliClient.search`` 并规范化为 ``ExternalPost``。

    ``created_utc`` 无法解析的帖子会被跳过（不伪造时间）；正文为标题 + 截断 selftext，
    ``metrics`` 使用 Reddit 语义键（upvotes / comments）。
    """

    platform: Platform = "reddit"

    def __init__(self, client: RedditOpenCliClient | None = None) -> None:
        self._client = client or RedditOpenCliClient()

    def available(self) -> bool:
        return True

    def search(self, query: str, *, limit: int) -> list[ExternalPost]:
        posts = self._client.search(query, limit=limit)
        result: list[ExternalPost] = []
        for post in posts:
            external = _reddit_to_external_post(post, topic=query)
            if external is not None:
                result.append(external)
        return result


def _reddit_to_external_post(post: RedditPost, *, topic: str) -> ExternalPost | None:
    """将 RedditPost 映射为 ExternalPost；时间无法解析时返回 None（禁止伪造时间）。"""
    published_at = post.published_at()
    if published_at is None:
        return None
    return ExternalPost(
        id=post.id,
        platform="reddit",
        url=post.url,
        author_id=post.author,
        author_name=post.author,
        content=post.content(),
        published_at=published_at,
        metrics={"upvotes": post.score, "comments": post.comments},
        matched_topics=[topic],
    )
```

在 `src/finch/engagement/scoring.py` 中，把：

```python
_POPULARITY_KEYS = ("likes", "favorites", "replies", "reposts", "retweets", "comments")
```

改为：

```python
_POPULARITY_KEYS = ("likes", "favorites", "replies", "reposts", "retweets", "comments", "upvotes")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_engagement_search.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/engagement/search.py src/finch/engagement/scoring.py tests/unit/test_engagement_search.py
git commit -m "feat(engagement): wire reddit search provider"
```

---

## Task 4: DI 贯通 flow.py 与 cli.py

**Files:**
- Modify: `src/finch/engagement/flow.py`
- Modify: `src/finch/cli.py`
- Test: `tests/unit/test_engagement_flow.py`

**Interfaces:**
- Consumes: `RedditOpenCliClient`（来自 Task 2）、`RedditPostSearchProvider`（来自 Task 3）。
- Produces: `run_discovery_engagement_flow(settings, opencli, runner, *, reddit_opencli: RedditOpenCliClient | None = None, run_id, skip_ids=None)` — 新增关键字参数 `reddit_opencli`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_engagement_flow.py` 顶部 import 区追加：

```python
from finch.reddit.models import RedditPost
```

在 `FakeOpenCli` 类定义后新增：

```python
class FakeRedditOpenCli:
    def __init__(self, posts: list[RedditPost] | None = None):
        self._posts = posts or []
        self.calls = 0

    def search(self, query, *, sort="relevance", limit=20):
        self.calls += 1
        return list(self._posts)
```

在 `_tweet` helper 之后新增：

```python
def _reddit_post(id: str = "r1") -> RedditPost:
    return RedditPost(
        id=id,
        title="How do you test agent reliability?",
        subreddit="LocalLLaMA",
        author="bob",
        score=10,
        comments=3,
        url=f"https://www.reddit.com/r/LocalLLaMA/comments/{id}/t/",
        created_utc=1756627200,
        selftext="A concrete failure replay.",
    )
```

把原 `test_failing_provider_still_yields_other_provider_posts_and_records_failures` 整体替换为：

```python
def test_reddit_posts_flow_through_with_x():
    opencli = FakeOpenCli(tweets=[_tweet(id="p1")])
    reddit = FakeRedditOpenCli(posts=[_reddit_post(id="r1")])
    runner = FakeRunner(
        items=[
            ScoreItem(post_id="p1", scores=_dims()),
            ScoreItem(post_id="r1", scores=_dims()),
        ],
        proposal_items=[_proposal("p1"), _proposal("r1")],
    )
    result = run_discovery_engagement_flow(
        _settings(platforms=["x", "reddit"]), opencli, runner,
        reddit_opencli=reddit, run_id="run-1",
    )

    assert result.status == "succeeded"
    assert sorted(c.post.id for c in result.candidates) == ["p1", "r1"]
    assert result.failures == []


def test_reddit_failure_keeps_x_posts_and_records_failure():
    class FailingReddit:
        def search(self, query, *, sort="relevance", limit=20):
            raise RuntimeError("boom")

    opencli = FakeOpenCli(tweets=[_tweet(id="p1")])
    runner = FakeRunner(
        items=[ScoreItem(post_id="p1", scores=_dims())],
        proposal_items=[_proposal("p1")],
    )
    result = run_discovery_engagement_flow(
        _settings(platforms=["x", "reddit"]), opencli, runner,
        reddit_opencli=FailingReddit(), run_id="run-1",
    )

    assert result.status == "succeeded"
    assert [c.post.id for c in result.candidates] == ["p1"]
    assert len(result.failures) == 1
    assert result.failures[0].platform == "reddit"
    assert "boom" in result.failures[0].reason
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_engagement_flow.py -v`
Expected: FAIL — `run_discovery_engagement_flow` 尚无 `reddit_opencli` 参数，`TypeError: unexpected keyword argument 'reddit_opencli'`。

- [ ] **Step 3: 实现 DI 贯通**

在 `src/finch/engagement/flow.py` 顶部 import 区，把：

```python
from ..twitter.opencli_client import OpenCliClient
```

改为：

```python
from ..reddit.opencli_client import RedditOpenCliClient
from ..twitter.opencli_client import OpenCliClient
```

把 `_build_providers` 函数替换为：

```python
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
```

把 `run_discovery_engagement_flow` 的签名与 `providers` 构造改为：

```python
def run_discovery_engagement_flow(
    settings: Settings,
    opencli: OpenCliClient,
    runner: CodexRunner,
    *,
    reddit_opencli: RedditOpenCliClient | None = None,
    run_id: str,
    skip_ids: set[str] | None = None,
) -> EngagementRunResult:
    ...
    engagement = settings.engagement
    providers = _build_providers(engagement.platforms, opencli, reddit_opencli)
```

（`...` 表示函数体其余部分不变；仅改动签名与 `providers =` 这一行。）

在 `src/finch/cli.py` 顶部 import 区，把：

```python
from .twitter.opencli_client import OpenCliClient
```

改为：

```python
from .reddit.opencli_client import RedditOpenCliClient
from .twitter.opencli_client import OpenCliClient
```

在 `run_daily` 函数中，把：

```python
    gh = GhClient()
    opencli = OpenCliClient()
```

改为：

```python
    gh = GhClient()
    opencli = OpenCliClient()
    reddit_opencli = RedditOpenCliClient()
```

并把：

```python
            engagement_track=lambda rid: run_discovery_engagement_flow(
                settings, opencli, CodexRunner(), run_id=rid
            ),
```

改为：

```python
            engagement_track=lambda rid: run_discovery_engagement_flow(
                settings, opencli, CodexRunner(),
                run_id=rid, reddit_opencli=reddit_opencli,
            ),
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_engagement_flow.py tests/unit/test_engagement_search.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/engagement/flow.py src/finch/cli.py tests/unit/test_engagement_flow.py
git commit -m "feat(engagement): thread reddit client through flow and cli"
```

---

## Task 5: 契约测试与 fixture（需真实 opencli 调用）

**Files:**
- Create: `tests/fixtures/opencli/reddit-search.json`
- Create: `tests/contract/test_reddit_models.py`

**Interfaces:**
- Consumes: `RedditPost`（来自 Task 1）。
- Produces: fixture `tests/fixtures/opencli/reddit-search.json`，契约测试 `tests/contract/test_reddit_models.py`（fixture 缺失时 skip）。

- [ ] **Step 1: 捕获真实 opencli 输出为 fixture**

Run（需 opencli + 浏览器桥可用；若 Reddit 需登录请先 `opencli reddit login`）：

```bash
opencli reddit search "agent reliability" --sort relevance --limit 20 -f json > tests/fixtures/opencli/reddit-search.json
```

验证 fixture 是 JSON 数组且非空：

```bash
python3 -c "import json; d=json.load(open('tests/fixtures/opencli/reddit-search.json')); assert isinstance(d, list) and d; print('ok', len(d), 'posts')"
```

Expected: `ok <N> posts`

- [ ] **Step 2: 写契约测试**

创建 `tests/contract/test_reddit_models.py`：

```python
"""Contract test: real opencli reddit JSON output validates against RedditPost model."""
import json
from pathlib import Path

import pytest

from finch.reddit.models import RedditPost

FIXTURE = Path("tests/fixtures/opencli/reddit-search.json")


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not captured")
def test_fixture_parses_as_reddit_posts():
    data = json.loads(FIXTURE.read_text())
    assert isinstance(data, list)
    assert len(data) > 0

    posts = [RedditPost.model_validate(item) for item in data]
    assert len(posts) == len(data)

    for p in posts:
        assert p.id
        assert p.title
        assert p.author
        assert p.url
        assert p.url.startswith("https://")


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not captured")
def test_fixture_published_at_parses():
    data = json.loads(FIXTURE.read_text())
    for item in data:
        p = RedditPost.model_validate(item)
        if p.created_utc is not None:
            assert p.published_at() is not None, f"failed to parse: {p.created_utc}"
```

- [ ] **Step 3: 运行契约测试确认通过**

Run: `uv run pytest tests/contract/test_reddit_models.py -v`
Expected: PASS（2 passed；fixture 已捕获故不 skip）

- [ ] **Step 4: 提交**

```bash
git add tests/fixtures/opencli/reddit-search.json tests/contract/test_reddit_models.py
git commit -m "test(reddit): add opencli contract fixture"
```

---

## 收尾校验（全量）

- [ ] Run: `uv run pytest -v`
  Expected: 全部 PASS（含新增 reddit 测试；契约测试若 fixture 存在则运行）。

- [ ] Run: `uv run ruff check .`
  Expected: 无错误（line-length 100 内；若 `reddit` 模块 import 顺序问题按 ruff 提示调整）。

- [ ] Run: `uv run mypy src`
  Expected: 无类型错误。
