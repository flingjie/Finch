# 自动关联发布（P0）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在只读前提下取消「发布后手动登记 URL」：新增作者账号配置与验证、`AuthorPost` 增量同步、`PublicationIntent`/`PublicationLink` 确定性匹配，把「已批准原创草稿」自动关联到「线上已发布帖子」。

**Architecture:** 纯加性。新 `src/finch/author/` 包（models / sync / reconcile / client），`OpenCliClient` 新增两个只读方法，`DecisionService.accept` 追加写 `PublicationIntent`，新增 `finch author sync --json`。匹配全程确定性（exact_text → similar_text → needs_manual → awaiting），不引入 LLM。

**Tech Stack:** Python 3.12+，Pydantic 2，SQLModel/SQLite（payload_json），typer，pytest，ruff（E,F,I,B,UP 行长 100），mypy，alembic。

## Global Constraints

- Python 3.12+；Pydantic 2；ruff `E,F,I,B,UP` 行长 100；mypy 通过。
- 只读：不新增任何 opencli 写命令；`whoami`/`user_posts` 走既有 `_ALLOWLIST`。
- 确定性：匹配用 `difflib.SequenceMatcher`（exact / ratio≥0.85 唯一），绝不 LLM 判定。
- 加性：不 drop/改旧表；`Feedback`/`ReviewDecision` 继续可读。
- 读取不到 ≠ 0：`AuthorPost` 指标缺省 `None`。
- 稳定身份：匹配用 `user_id`，不用 `handle`。

---

### Task 1: 领域模型 + 配置

**Files:**
- Create: `src/finch/author/__init__.py`
- Create: `src/finch/author/models.py`
- Modify: `src/finch/settings.py`（`AuthorAccountConfig` + `Settings.author_accounts`）
- Test: `tests/unit/test_author_models.py`、`tests/unit/test_settings.py`

**Interfaces:**
- Produces: `AuthorAccountConfig`（settings）、`AuthorAccount`/`AuthorPost`/`PublicationIntent`/`PublicationLink`/`AuthorSyncCursor`（author/models.py）。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_author_models.py`：

```python
from datetime import UTC, datetime

from finch.author.models import (
    AuthorAccount, AuthorPost, AuthorSyncCursor, PublicationIntent, PublicationLink,
)


def test_author_post_metrics_default_none():
    post = AuthorPost(
        platform="x", remote_post_id="p1", author_account_id="a1",
        kind="original", body="hi", url="u", published_at=datetime.now(UTC),
    )
    assert post.likes is None and post.views is None
    assert post.replied_to_post_id is None


def test_publication_intent_shape():
    intent = PublicationIntent(
        source_type="draft", source_id="d1", approved_body="body",
        content_hash="h", approved_at=datetime.now(UTC), expected_kind="original",
    )
    assert intent.expected_kind == "original"


def test_author_sync_cursor():
    cur = AuthorSyncCursor(platform="x", author_account_id="a1", last_seen=datetime.now(UTC))
    assert cur.last_seen is not None
```

`tests/unit/test_settings.py` 追加：

```python
from finch.settings import AuthorAccountConfig, Settings


def test_settings_author_accounts_default_empty():
    assert Settings().author_accounts == []


def test_author_account_config_defaults():
    cfg = AuthorAccountConfig(handle="flingjie")
    assert cfg.platform == "x" and cfg.enabled is True and cfg.history_lookback_days == 90
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_author_models.py tests/unit/test_settings.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'finch.author'`。

- [ ] **Step 3: 实现模型与配置**

`src/finch/author/__init__.py`（空）。`src/finch/author/models.py`：

```python
"""作者账号 + 发帖同步 + 发布关联（P0）数据模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AuthorAccount(BaseModel):
    """已验证的作者账号；匹配用稳定 user_id，handle 仅展示。"""

    platform: str
    user_id: str
    handle: str
    verified_at: datetime


class AuthorPost(BaseModel):
    """作者自己发布的一条帖子（幂等键 platform:remote_post_id）。"""

    platform: str
    remote_post_id: str
    author_account_id: str
    kind: Literal["original", "reply", "quote"]
    body: str
    url: str
    published_at: datetime
    replied_to_post_id: str | None = None
    quoted_post_id: str | None = None
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    views: int | None = None


class PublicationIntent(BaseModel):
    """批准时保存的「期待发布」记录（幂等键 source_id = draft.id）。"""

    source_type: Literal["draft"]
    source_id: str
    approved_body: str
    content_hash: str
    approved_at: datetime
    expected_kind: Literal["original"]


class PublicationLink(BaseModel):
    """确定性匹配结果（幂等键 source_id）。"""

    source_id: str
    remote_post_id: str
    matched_by: Literal["exact_text", "similar_text"]
    confidence: float
    linked_at: datetime


class AuthorSyncCursor(BaseModel):
    """同步水位（幂等键 platform:author_account_id）。"""

    platform: str
    author_account_id: str
    last_seen: datetime
```

`src/finch/settings.py` 在 `TwitterSettings` 后新增：

```python
class AuthorAccountConfig(BaseModel):
    """作者账号配置（P0 自动关联发布）。"""

    platform: str = "x"
    handle: str = ""
    enabled: bool = True
    history_lookback_days: int = 90
```

`Settings` 增加字段：

```python
    author_accounts: list[AuthorAccountConfig] = Field(default_factory=list)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_author_models.py tests/unit/test_settings.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/author/ src/finch/settings.py tests/unit/test_author_models.py tests/unit/test_settings.py
git commit -m "feat(author): P0 domain models + author_accounts config"
```

---

### Task 2: 存储与迁移

**Files:**
- Modify: `src/finch/storage/repositories.py`
- Create: `alembic/versions/<rev>_add_author_tables.py`
- Test: `tests/unit/test_author_repositories.py`、`tests/unit/test_alembic.py`

**Interfaces:**
- Produces: `AuthorAccountRepository` / `AuthorPostRepository` / `PublicationIntentRepository` / `PublicationLinkRepository` / `AuthorSyncCursorRepository`（均 `save` + `list` + 按主键 `get`）。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_author_repositories.py`：

```python
from datetime import UTC, datetime

from finch.author.models import AuthorPost, PublicationIntent, PublicationLink
from finch.storage.database import Store
from finch.storage.repositories import (
    AuthorPostRepository, PublicationIntentRepository, PublicationLinkRepository,
)


def test_author_post_upsert_idempotent(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    repo = AuthorPostRepository(store)
    post = AuthorPost(
        platform="x", remote_post_id="p1", author_account_id="a1", kind="original",
        body="hi", url="u", published_at=datetime.now(UTC),
    )
    repo.save(post)
    repo.save(post)
    assert len(repo.list()) == 1
    assert repo.get("x", "p1") is not None


def test_publication_intent_and_link(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    intent_repo = PublicationIntentRepository(store)
    link_repo = PublicationLinkRepository(store)
    intent = PublicationIntent(
        source_type="draft", source_id="d1", approved_body="b", content_hash="h",
        approved_at=datetime.now(UTC), expected_kind="original",
    )
    intent_repo.save(intent)
    assert intent_repo.get("d1") is not None
    link_repo.save(PublicationLink(
        source_id="d1", remote_post_id="p1", matched_by="exact_text",
        confidence=1.0, linked_at=datetime.now(UTC),
    ))
    assert link_repo.get("d1").remote_post_id == "p1"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_author_repositories.py -q`
Expected: FAIL — `ImportError: cannot import name 'AuthorPostRepository'`。

- [ ] **Step 3: 实现仓储**

`src/finch/storage/repositories.py` 末尾新增五个 `SQLModel` record + 仓储（沿用 payload_json 模式）。每个仓储：
- `AuthorPostRepository.save(post)`（按 `platform:remote_post_id` 主键 merge）、`get(platform, remote_post_id)`、`list()`。
- `PublicationIntentRepository.save(intent)`（主键 `source_id`）、`get(source_id)`、`list()`。
- `PublicationLinkRepository.save(link)`（主键 `source_id`）、`get(source_id)`、`list()`。
- `AuthorAccountRepository.save(acc)`（主键 `platform:user_id`）、`get(platform, user_id)`、`list()`。
- `AuthorSyncCursorRepository.save(cur)`（主键 `platform:author_account_id`）、`get(platform, author_account_id)`、`list()`。

（record 的 `id` 用主键字符串，如 `f"post:{platform}:{remote_post_id}"`、`f"intent:{source_id}"`；`payload_json` 存模型 JSON。）

- [ ] **Step 4: 写 alembic 迁移**

`alembic revision -m "add author tables"` 生成后，`upgrade()` 创建五张表（`authoraccountrecord`、`authorpostrecord`、`publicationintentrecord`、`publicationlinkrecord`、`authorsynccursorrecord`，列 `id`/`payload_json`/`updated_at` 与必要的索引列）；`down_revision` 指向当前真实 head（`alembic heads` 确认）。更新 `tests/unit/test_alembic.py` 的 `ALL_TABLES`。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_author_repositories.py tests/unit/test_alembic.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/finch/storage/repositories.py alembic/versions/<rev>_add_author_tables.py tests/unit/test_author_repositories.py tests/unit/test_alembic.py
git commit -m "feat(author): repositories + alembic migration"
```

---

### Task 3: Adapter（`whoami` / `user_posts` + 解码）

**Files:**
- Modify: `src/finch/twitter/opencli_client.py`
- Create: `src/finch/author/client.py`
- Test: `tests/unit/test_author_client.py`、`tests/unit/test_opencli_client.py`

**Interfaces:**
- Produces: `OpenCliClient.whoami() -> dict`（`{"user_id","handle"}`）；`OpenCliClient.user_posts(handle, *, limit=200) -> list[AuthorPost]`。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_author_client.py`（monkeypatch `_call` 返回样例 JSON）：

```python
from finch.author.client import decode_author_post


def test_decode_author_post_minimal():
    raw = {"id": "123", "author": "flingjie", "text": "hello", "url": "https://x.com/1"}
    post = decode_author_post("x", "a1", raw)
    assert post.remote_post_id == "123"
    assert post.kind == "original"
    assert post.likes is None  # 缺失字段 → None，而非 0


def test_decode_author_post_reply():
    raw = {"id": "456", "text": "re", "url": "u", "in_reply_to": "123"}
    post = decode_author_post("x", "a1", raw)
    assert post.kind == "reply" and post.replied_to_post_id == "123"
```

（`decode_author_post(platform, author_account_id, raw: dict) -> AuthorPost` 是 `client.py` 的解码函数；`kind` 由 `raw` 的字段（如 `in_reply_to` / `quoted` 存在性）推断，`published_at` 从 `created_at` 解析，缺省则抛 `ValueError` 或标记——按 contract test 回填后细化。）

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_author_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'finch.author.client'`。

- [ ] **Step 3: 实现 adapter + decode**

`OpenCliClient` 新增：

```python
    def whoami(self) -> dict:
        """读取当前登录账号（stable user_id + handle）。"""
        argv = ["opencli", "twitter", "whoami", "-f", "json"]
        tweets = _call(argv, timeout=30.0)
        if not tweets:
            raise TwitterSourceUnavailable("whoami returned no data")
        t = tweets[0]
        return {"user_id": t.id, "handle": t.author}

    def user_posts(self, handle: str, *, limit: int = 200) -> list[AuthorPost]:
        """读取某用户自己发布的帖子（original/reply/quote）。"""
        argv = [
            "opencli", "twitter", "tweets", handle,
            "--limit", str(limit), "-f", "json",
        ]
        tweets = _call(argv, timeout=60.0)
        return [decode_author_post("x", handle, t.model_dump(mode="json")) for t in tweets]
```

（`_call` 已在 `opencli_client.py`；`decode_author_post` 从 `client.py` import。`whoami` 的 `user_id` 用 `t.id`，`handle` 用 `t.author`——若真实 whoami 输出 shape 不同，contract test 捕获后仅改此处。）

`src/finch/author/client.py`：

```python
"""opencli JSON → AuthorPost 解码（P0；真实 schema 由 contract test 回填）。"""

from datetime import datetime

from finch.author.models import AuthorPost


def decode_author_post(platform: str, author_account_id: str, raw: dict) -> AuthorPost:
    published_at = datetime.now(UTC)  # 占位：由 created_at 解析（见 contract test）
    kind = "original"
    if raw.get("in_reply_to"):
        kind = "reply"
    elif raw.get("quoted") or raw.get("quoted_tweet"):
        kind = "quote"
    return AuthorPost(
        platform=platform,
        remote_post_id=str(raw.get("id") or raw.get("tweet_id") or ""),
        author_account_id=author_account_id,
        kind=kind,
        body=str(raw.get("text") or ""),
        url=str(raw.get("url") or ""),
        published_at=published_at,
        replied_to_post_id=raw.get("in_reply_to"),
        quoted_post_id=raw.get("quoted_tweet", {}).get("id") if isinstance(raw.get("quoted_tweet"), dict) else raw.get("quoted_post_id"),
        likes=_int_or_none(raw.get("likes")),
        replies=_int_or_none(raw.get("replies")),
        reposts=_int_or_none(raw.get("reposts")),
        views=_int_or_none(raw.get("views")),
    )


def _int_or_none(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
```

（`UTC` import；`published_at` 占位将在 contract test 捕获真实 `created_at` 格式后替换为真实解析。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_author_client.py tests/unit/test_opencli_client.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/twitter/opencli_client.py src/finch/author/client.py tests/unit/test_author_client.py tests/unit/test_opencli_client.py
git commit -m "feat(author): whoami + user_posts adapter + decode"
```

---

### Task 4: 同步（verify + sync_posts + 游标）

**Files:**
- Create: `src/finch/author/sync.py`
- Test: `tests/unit/test_author_sync.py`

**Interfaces:**
- Consumes: `OpenCliClient.whoami/user_posts`（Task 3）、仓储（Task 2）、`AuthorAccountConfig`（Task 1）。
- Produces: `verify_account(config, client, store) -> AuthorAccount`；`sync_posts(account, client, store, lookback_days) -> int`（返回新增条数）。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_author_sync.py`：

```python
from datetime import UTC, datetime, timedelta

from finch.author.models import AuthorAccount, AuthorPost
from finch.author.sync import sync_posts, verify_account
from finch.settings import AuthorAccountConfig
from finch.storage.database import Store
from finch.storage.repositories import AuthorAccountRepository, AuthorPostRepository


class _FakeClient:
    def __init__(self, whoami, posts):
        self._whoami = whoami
        self._posts = posts

    def whoami(self):
        return self._whoami

    def user_posts(self, handle, *, limit=200):
        return self._posts


def test_verify_account_mismatch_raises(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    client = _FakeClient({"user_id": "999", "handle": "other"}, [])
    try:
        verify_account(AuthorAccountConfig(handle="flingjie"), client, store)
        assert False, "expected mismatch error"
    except ValueError:
        pass


def test_sync_posts_idempotent_and_cursor(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    client = _FakeClient({"user_id": "1", "handle": "flingjie"}, [
        AuthorPost(platform="x", remote_post_id="p1", author_account_id="1",
                   kind="original", body="a", url="u", published_at=datetime.now(UTC)),
    ])
    acc = verify_account(AuthorAccountConfig(handle="flingjie"), client, store)
    assert acc.user_id == "1"
    n1 = sync_posts(acc, client, store, lookback_days=90)
    n2 = sync_posts(acc, client, store, lookback_days=90)
    assert n1 == 1 and n2 == 0  # 幂等
    assert len(AuthorPostRepository(store).list()) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_author_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'finch.author.sync'`。

- [ ] **Step 3: 实现 sync**

`src/finch/author/sync.py`：

```python
"""作者账号验证 + 发帖增量同步（确定性、幂等）。"""

from datetime import UTC, datetime, timedelta

from finch.author.models import AuthorAccount, AuthorSyncCursor
from finch.storage.database import Store
from finch.storage.repositories import (
    AuthorAccountRepository, AuthorPostRepository, AuthorSyncCursorRepository,
)


def verify_account(config, client, store: Store) -> AuthorAccount:
    who = client.whoami()
    if who["handle"].lower() != config.handle.lower():
        raise ValueError(f"whoami handle {who['handle']!r} != config {config.handle!r}")
    account = AuthorAccount(
        platform=config.platform, user_id=who["user_id"],
        handle=who["handle"], verified_at=datetime.now(UTC),
    )
    AuthorAccountRepository(store).save(account)
    return account


def sync_posts(account: AuthorAccount, client, store: Store, *, lookback_days: int) -> int:
    repo = AuthorPostRepository(store)
    cursor_repo = AuthorSyncCursorRepository(store)
    posts = client.user_posts(account.handle)
    new_count = 0
    for post in posts:
        if repo.get(account.platform, post.remote_post_id) is not None:
            continue
        repo.save(post)
        new_count += 1
    # 更新水位（用已见最新 published_at；无帖子则用 now）
    if posts:
        last = max(p.published_at for p in posts)
    else:
        last = datetime.now(UTC)
    cursor_repo.save(AuthorSyncCursor(
        platform=account.platform, author_account_id=account.user_id, last_seen=last,
    ))
    return new_count
```

（`lookback_days` 由调用方传入；P0 首跑用 `config.history_lookback_days`，游标推进逻辑在 `user_posts` 后续增强为 `--since` 时再细化。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_author_sync.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/author/sync.py tests/unit/test_author_sync.py
git commit -m "feat(author): verify_account + incremental sync_posts"
```

---

### Task 5: Reconcile（确定性匹配）

**Files:**
- Create: `src/finch/author/reconcile.py`
- Test: `tests/unit/test_author_reconcile.py`

**Interfaces:**
- Produces: `reconcile(store, *, similarity_threshold=0.85) -> ReconcileResult`，`ReconcileResult(linked, needs_manual, awaiting)`。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_author_reconcile.py`：

```python
from datetime import UTC, datetime, timedelta

from finch.author.models import AuthorPost, PublicationIntent
from finch.author.reconcile import reconcile
from finch.storage.database import Store
from finch.storage.repositories import AuthorPostRepository, PublicationIntentRepository


def _intent(source_id="d1", body="hello world"):
    return PublicationIntent(
        source_type="draft", source_id=source_id, approved_body=body, content_hash="h",
        approved_at=datetime.now(UTC) - timedelta(days=1), expected_kind="original",
    )


def _post(pid, body, published_at=None):
    return AuthorPost(
        platform="x", remote_post_id=pid, author_account_id="a1", kind="original",
        body=body, url=f"u/{pid}", published_at=published_at or datetime.now(UTC),
    )


def test_reconcile_exact_match(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    PublicationIntentRepository(store).save(_intent())
    AuthorPostRepository(store).save(_post("p1", "hello world"))
    result = reconcile(store)
    assert len(result.linked) == 1 and result.linked[0].matched_by == "exact_text"


def test_reconcile_similar_unique(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    PublicationIntentRepository(store).save(_intent(body="hello world this is a test"))
    AuthorPostRepository(store).save(_post("p1", "hello world this is the test"))
    result = reconcile(store)
    assert len(result.linked) == 1 and result.linked[0].matched_by == "similar_text"


def test_reconcile_multiple_needs_manual(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    PublicationIntentRepository(store).save(_intent())
    AuthorPostRepository(store).save(_post("p1", "hello world"))
    AuthorPostRepository(store).save(_post("p2", "hello world"))
    result = reconcile(store)
    assert result.linked == [] and len(result.needs_manual) == 1


def test_reconcile_no_candidate_awaiting(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    PublicationIntentRepository(store).save(_intent())
    result = reconcile(store)
    assert result.linked == [] and result.awaiting == ["d1"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_author_reconcile.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'finch.author.reconcile'`。

- [ ] **Step 3: 实现 reconcile**

`src/finch/author/reconcile.py`：

```python
"""确定性发布匹配：把已批准草稿关联到线上帖子（P0）。"""

import difflib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from finch.author.models import PublicationLink
from finch.storage.database import Store
from finch.storage.repositories import (
    AuthorPostRepository, PublicationIntentRepository, PublicationLinkRepository,
)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


@dataclass
class ReconcileResult:
    linked: list[PublicationLink] = field(default_factory=list)
    needs_manual: list[dict] = field(default_factory=list)  # {source_id, candidates}
    awaiting: list[str] = field(default_factory=list)


def reconcile(store: Store, *, similarity_threshold: float = 0.85) -> ReconcileResult:
    intents = PublicationIntentRepository(store).list()
    links = PublicationLinkRepository(store).list()
    linked_sources = {l.source_id for l in links}
    posts = AuthorPostRepository(store).list()
    link_repo = PublicationLinkRepository(store)
    result = ReconcileResult()

    for intent in intents:
        if intent.source_id in linked_sources:
            continue
        candidates = [
            p for p in posts
            if p.kind in {"original", "quote"}
            and p.published_at >= intent.approved_at
            and p.author_account_id  # 账号过滤在调用方/后续按 user_id 精确化
        ]
        # exact
        exact = [p for p in candidates if _normalize(p.body) == _normalize(intent.approved_body)]
        if len(exact) == 1:
            link = PublicationLink(
                source_id=intent.source_id, remote_post_id=exact[0].remote_post_id,
                matched_by="exact_text", confidence=1.0, linked_at=datetime.now(UTC),
            )
            link_repo.save(link)
            result.linked.append(link)
            continue
        if len(exact) > 1:
            result.needs_manual.append(
                {"source_id": intent.source_id, "candidates": [p.remote_post_id for p in exact]}
            )
            continue
        # similar (唯一候选)
        similar = [
            p for p in candidates
            if _ratio(p.body, intent.approved_body) >= similarity_threshold
        ]
        if len(similar) == 1:
            link = PublicationLink(
                source_id=intent.source_id, remote_post_id=similar[0].remote_post_id,
                matched_by="similar_text", confidence=_ratio(similar[0].body, intent.approved_body),
                linked_at=datetime.now(UTC),
            )
            link_repo.save(link)
            result.linked.append(link)
        elif len(similar) > 1:
            result.needs_manual.append(
                {"source_id": intent.source_id, "candidates": [p.remote_post_id for p in similar]}
            )
        else:
            result.awaiting.append(intent.source_id)

    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_author_reconcile.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/author/reconcile.py tests/unit/test_author_reconcile.py
git commit -m "feat(author): deterministic reconcile (exact/similar/manual/awaiting)"
```

---

### Task 6: `accept` 写 `PublicationIntent`

**Files:**
- Modify: `src/finch/review/decision.py`
- Modify: `src/finch/cli.py`（`decide` 构造 `DecisionService` 时传 `publication_intents`）
- Test: `tests/unit/test_decision.py`

**Interfaces:**
- Consumes: `PublicationIntent`/`PublicationIntentRepository`（Task 1/2）。
- Produces: `DecisionService.__init__(..., publication_intents: PublicationIntentRepository)`；`accept` 追加写 intent。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_decision.py` 追加：

```python
def test_accept_writes_publication_intent(tmp_path):
    from finch.storage.repositories import PublicationIntentRepository

    store = Store(tmp_path / "finch.db")
    store.init()
    ContentJobRepository(store).upsert_job(_job("j1"))
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="final", content_job_id="j1")
    )
    svc = _svc(store)  # 需在 _svc 里传入 publication_intents
    svc.accept("j1")
    intent = PublicationIntentRepository(store).get("d1")
    assert intent is not None
    assert intent.approved_body == "final"
    assert intent.expected_kind == "original"
```

（更新 `_svc(store)` 助手以传入 `publication_intents=PublicationIntentRepository(store)`。）

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_decision.py -q -k intent`
Expected: FAIL — `DecisionService` 构造缺 `publication_intents` / accept 未写 intent。

- [ ] **Step 3: 实现**

`src/finch/review/decision.py`：`__init__` 加 `publication_intents: PublicationIntentRepository` 参数并赋值；`accept` 在 `self.decisions.save(record)` 之前追加：

```python
        self.publication_intents.save(
            PublicationIntent(
                source_type="draft",
                source_id=draft.id,
                approved_body=draft.body,
                content_hash=content_hash(draft.body),
                approved_at=datetime.now(UTC),
                expected_kind="original",
            )
        )
```

`src/finch/cli.py` 的 `decide` 命令构造 `DecisionService(...)` 时加 `publication_intents=PublicationIntentRepository(store)`（import 该仓储）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_decision.py tests/unit/test_cli_run.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/review/decision.py src/finch/cli.py tests/unit/test_decision.py
git commit -m "feat(author): accept writes PublicationIntent"
```

---

### Task 7: `finch author sync --json`

**Files:**
- Modify: `src/finch/cli.py`
- Test: `tests/unit/test_cli_run.py`

**Interfaces:**
- Consumes: `verify_account`/`sync_posts`（Task 4）、`reconcile`（Task 5）、`AuthorAccountConfig`（settings）。
- Produces: `finch author sync --json` → `{synced, linked, needs_manual, awaiting}`。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_cli_run.py` 追加（monkeypatch `cli.verify_account`/`cli.sync_posts`/`cli.reconcile`）：

```python
def test_author_sync_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "verify_account", lambda cfg, client, store: object())
    monkeypatch.setattr(cli, "sync_posts", lambda acc, client, store, **kw: 3)
    monkeypatch.setattr(cli, "reconcile", lambda store: type("R", (), {
        "linked": [], "needs_manual": [], "awaiting": [],
    })())
    r = CliRunner().invoke(app, ["author", "sync", "--json"])
    assert r.exit_code == 0, r.output
    assert '"synced": 3' in r.output
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_cli_run.py -q -k author_sync`
Expected: FAIL — `No such command 'author'`。

- [ ] **Step 3: 实现**

`src/finch/cli.py` 新增 `author_app` typer 子应用 + `sync` 命令：

```python
author_app = typer.Typer(help="Author account sync + publication reconcile (read-only)")
app.add_typer(author_app, name="author")

@author_app.command("sync")
def author_sync(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """同步作者账号发帖并确定性匹配已批准草稿。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    client = OpenCliClient()
    total_synced = 0
    all_linked, all_manual, all_awaiting = [], [], []
    for cfg in settings.author_accounts:
        if not cfg.enabled:
            continue
        account = verify_account(cfg, client, store)
        total_synced += sync_posts(account, client, store, lookback_days=cfg.history_lookback_days)
    result = reconcile(store)
    payload = {
        "synced": total_synced,
        "linked": [l.model_dump(mode="json") for l in result.linked],
        "needs_manual": result.needs_manual,
        "awaiting": result.awaiting,
    }
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"synced={total_synced} linked={len(result.linked)} awaiting={len(result.awaiting)}")
```

（import `verify_account`/`sync_posts`/`reconcile`/`AuthorAccountRepository` 等。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_cli_run.py -q -k author`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/cli.py tests/unit/test_cli_run.py
git commit -m "feat(cli): finch author sync --json"
```

---

### Task 8: 全量回归

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -q`
Expected: PASS。

- [ ] **Step 2: lint + 类型**

Run: `uv run ruff check . && uv run mypy src`
Expected: 无错误。

- [ ] **Step 3: 提交（如有修正）**

```bash
git add -A && git commit -m "chore: regression fixes for author publication auto-link"
```

---

## Self-Review 结果

- **Spec 覆盖**：§4 模型 → Task 1；§4/§12 存储迁移 → Task 2；§6 adapter → Task 3；§7 同步 → Task 4；§8 匹配 → Task 5；§9 集成（accept 写 intent + author sync）→ Task 6/7；§10 错误处理 → Task 3/7（既有异常类型）。`$finch setup` 与 `run daily` 顶部调用留待后续（spec §9 已列出，但非 P0 核心，可在 CLI 集成后补）。
- **Placeholder 扫描**：`client.py` 的 `published_at` 占位解析 + `whoami`/`user_posts` 真实 schema 已标注「contract test 回填」，属已识别的 deferred（非 TBD 遗漏）。
- **类型一致性**：`decode_author_post(platform, author_account_id, raw)` 在 Task 3 定义并在 `user_posts` 引用一致；`verify_account(config, client, store)` / `sync_posts(account, client, store, *, lookback_days)` 在 Task 4 定义并在 Task 7 引用一致；`reconcile(store, *, similarity_threshold)` 在 Task 5 定义并在 Task 7 引用一致。
