# Finch Phase 2（GitHub Commit Intelligence）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 GitHub Commit Intelligence：通过 `gh` 只读增量读取 `flingjie/FDE-Gym` 的 Commit/PR/Issue，过滤噪声、聚合相关 Commit 为工程事件，调用 Codex 提取 Engineering Event，生成 Evidence Card，并输出 `finch github sync` / `finch reflect` CLI。

**Architecture:** 在 Phase 1 骨架上扩展：`GhClient` 增加只读读取方法（数组传参、JSON 输出、Pydantic 校验、指数退避）；`commit_reader` 用时间戳游标做增量读取并过滤噪声；`change_grouper` 按消息前缀 + 文件重叠聚合 Commit；`evidence` 定义 `EngineeringEvent`/`EvidenceCard`/`ClaimConfidence` 模型；`codex` 封装 `codex exec --output-schema` 非交互调用并做结构化校验；`extractor` 把「Commit 组 → prompt → Codex → EngineeringEvent → EvidenceCard」串起来。

**Tech Stack:** Python 3.12、Pydantic v2、`gh` CLI、Codex CLI（`exec` + `--output-schema`）、pytest、Ruff、mypy。

## Global Constraints

- `gh` 仅读取；子进程参数用数组传递，不拼接 shell 字符串；每次调用设超时；输出强制 JSON 并 Pydantic 校验（spec 5.2）。
- API 限流用指数退避，最多重试 3 次；保存 `stderr`、退出码与脱敏命令摘要（spec 5.2）。
- Commit patch 缺失或截断时标记 `evidence_incomplete`，不自行补全（spec 5.2）。
- 私有仓库默认 `publishable=false`（spec 5.2）。
- Evidence First：Commit → EngineeringEvent → Verified/Inferred Claims → EvidenceCard（spec 2.2）。
- ClaimConfidence 五级 `VERIFIED/SUPPORTED/INFERRED/USER_CONFIRMED/UNKNOWN`；`INFERRED`/`UNKNOWN` 不得写成确定事实（spec 7.4）。
- Codex 是智能节点，不决定 Graph 路由/权限；确定性逻辑留在 Python（spec 2.1）。
- 所有外部数据（含 GitHub 文本）视为不可信数据，不进入系统指令区。

---

### Task 1: GitHub 数据模型与 contract fixture

**Files:**
- Create: `src/finch/github/models.py`
- Create: `tests/fixtures/gh/repo-view.json`
- Create: `tests/fixtures/gh/commits-page.json`
- Create: `tests/fixtures/gh/commit-detail.json`
- Create: `tests/fixtures/gh/pr-view.json`
- Test: `tests/contract/test_gh_models.py`

**Interfaces:**
- Produces:
  - `finch.github.models.RepoInfo`：`name_with_owner: str`、`default_branch: str`、`url: str`、`is_private: bool`。别名 `nameWithOwner`/`defaultBranchRef.name`/`isPrivate` 由 `parse_repo_view(data)` 归一化。
  - `finch.github.models.CommitSummary`：`sha: str`、`message: str`、`author_date: datetime`、`html_url: str`、`parents: list[str]`。
  - `finch.github.models.CommitFile`：`filename: str`、`status: str`、`additions: int`、`deletions: int`、`patch: str | None`。
  - `finch.github.models.CommitDetail`：继承 `CommitSummary` 追加 `files: list[CommitFile]`、`stats: dict`、`patch_incomplete: bool`。
  - `finch.github.models.PullRequest`：`number: int`、`title: str`、`body: str | None`、`url: str`、`state: str`。
  - `parse_commit_summary(data) -> CommitSummary`、`parse_commit_detail(data) -> CommitDetail`、`parse_pull_request(data) -> PullRequest`。

- [ ] **Step 1: 抓取脱敏 fixture**

Run（真实只读命令，输出即 fixture）:
```bash
mkdir -p tests/fixtures/gh
gh repo view flingjie/FDE-Gym --json nameWithOwner,defaultBranchRef,url,isPrivate > tests/fixtures/gh/repo-view.json
gh api -H "Accept: application/vnd.github+json" "repos/flingjie/FDE-Gym/commits?sha=main&since=$(date -u -v-7d +%Y-%m-%dT%H:%M:%SZ)&per_page=20" > tests/fixtures/gh/commits-page.json
gh api -H "Accept: application/vnd.github+json" "repos/flingjie/FDE-Gym/commits/c204a79" > tests/fixtures/gh/commit-detail.json
gh pr view 1 --repo flingjie/FDE-Gym --json number,title,body,url,state,commits,files,reviews,comments > tests/fixtures/gh/pr-view.json
```
确认四份 JSON 均不含 token/密钥（FDE-Gym 公开，`gh` 已脱敏）。

- [ ] **Step 2: 写失败测试**

```python
# tests/contract/test_gh_models.py
import json
from pathlib import Path

from finch.github.models import (
    CommitDetail,
    CommitSummary,
    PullRequest,
    RepoInfo,
    parse_commit_detail,
    parse_commit_summary,
    parse_pull_request,
    parse_repo_view,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "gh"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_repo_view():
    r = parse_repo_view(_load("repo-view.json"))
    assert isinstance(r, RepoInfo)
    assert r.name_with_owner == "flingjie/FDE-Gym"
    assert r.is_private is False
    assert r.default_branch == "main"


def test_parse_commit_summary_list():
    data = _load("commits-page.json")
    assert isinstance(data, list) and len(data) >= 1
    c = parse_commit_summary(data[0])
    assert isinstance(c, CommitSummary)
    assert len(c.sha) == 40
    assert c.author_date.tzinfo is not None
    assert c.parents


def test_parse_commit_detail_has_files_and_patch_flag():
    d = parse_commit_detail(_load("commit-detail.json"))
    assert isinstance(d, CommitDetail)
    assert d.files, "commit detail must carry files"
    assert d.stats
    # patch 缺失/截断时必须能显式表达
    assert isinstance(d.patch_incomplete, bool)


def test_parse_pull_request():
    p = parse_pull_request(_load("pr-view.json"))
    assert isinstance(p, PullRequest)
    assert p.number == 1
    assert p.state in {"OPEN", "MERGED", "CLOSED"}
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/contract/test_gh_models.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 4: 写 `src/finch/github/models.py`**

```python
"""GitHub 数据模型与 `gh` JSON 归一化（spec 5.2 / 7）。"""

from datetime import datetime
from pydantic import BaseModel, Field


class RepoInfo(BaseModel):
    name_with_owner: str
    default_branch: str
    url: str
    is_private: bool


class CommitSummary(BaseModel):
    sha: str
    message: str
    author_date: datetime
    html_url: str
    parents: list[str] = Field(default_factory=list)


class CommitFile(BaseModel):
    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    patch: str | None = None


class CommitDetail(CommitSummary):
    files: list[CommitFile] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    patch_incomplete: bool = False


class PullRequest(BaseModel):
    number: int
    title: str
    body: str | None = None
    url: str
    state: str


def parse_repo_view(data: dict) -> RepoInfo:
    return RepoInfo(
        name_with_owner=data["nameWithOwner"],
        default_branch=(data.get("defaultBranchRef") or {}).get("name", "main"),
        url=data["url"],
        is_private=data.get("isPrivate", False),
    )


def parse_commit_summary(data: dict) -> CommitSummary:
    commit = data.get("commit", {})
    return CommitSummary(
        sha=data["sha"],
        message=(commit.get("message") or "").split("\n", 1)[0],
        author_date=commit.get("author", {}).get("date"),
        html_url=data.get("html_url", ""),
        parents=[p["sha"] for p in data.get("parents", []) if p.get("sha")],
    )


def parse_commit_detail(data: dict) -> CommitDetail:
    base = parse_commit_summary(data)
    files = [
        CommitFile(
            filename=f["filename"],
            status=f.get("status", "modified"),
            additions=f.get("additions", 0),
            deletions=f.get("deletions", 0),
            patch=f.get("patch"),
        )
        for f in data.get("files", [])
    ]
    patch_incomplete = any(f.patch is None or (f.patch and f.patch.endswith("\n...")) for f in files)
    return CommitDetail(
        **base.model_dump(),
        files=files,
        stats=data.get("stats", {}),
        patch_incomplete=patch_incomplete,
    )


def parse_pull_request(data: dict) -> PullRequest:
    return PullRequest(
        number=data["number"],
        title=data["title"],
        body=data.get("body"),
        url=data.get("url", ""),
        state=(data.get("state") or "").upper(),
    )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/contract/test_gh_models.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 6: Commit**

```bash
git add src/finch/github/models.py tests/fixtures/gh/repo-view.json tests/fixtures/gh/commits-page.json tests/fixtures/gh/commit-detail.json tests/fixtures/gh/pr-view.json tests/contract/test_gh_models.py
git commit -m "feat(github): add GitHub data models and contract fixtures"
```

---

### Task 2: GhClient 只读读取方法 + 指数退避

**Files:**
- Modify: `src/finch/github/gh_client.py`
- Test: `tests/unit/test_gh_client.py`

**Interfaces:**
- Consumes: `models`（Task 1）。
- Produces（`GhClient` 新增方法，全部返回 Pydantic 模型或空列表，失败抛 `GhError`）:
  - `repo_view(repo) -> RepoInfo`
  - `list_commits(repo, since, per_page=100) -> list[CommitSummary]`（`--paginate`）
  - `commit_detail(repo, sha) -> CommitDetail`
  - `pr_view(repo, number) -> PullRequest`
  - `_gh_json(argv, timeout, retries=3) -> dict | list`（数组传参、JSON 解析、指数退避：首次失败后 sleep 2^attempt 秒，最多 3 次；仍失败抛 `GhError`）

- [ ] **Step 1: 写失败测试（用 monkeypatch 伪造 `_run`，不真调 gh）**

```python
# tests/unit/test_gh_client.py
import json

import pytest

from finch.github.gh_client import GhClient, GhError


@pytest.fixture
def gh(monkeypatch):
    client = GhClient()
    monkeypatch.setattr("finch.github.gh_client._run", lambda argv, timeout: (
        {"ok": True, "exit_code": 0, "stdout": _FAKE, "stderr": ""}
    ))
    return client


_FAKE = ""


def _set(stdout: str, monkeypatch):
    def fake(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
    monkeypatch.setattr("finch.github.gh_client._run", fake)


def test_repo_view_parses(monkeypatch):
    _set(json.dumps({"nameWithOwner": "flingjie/FDE-Gym", "defaultBranchRef": {"name": "main"},
                     "url": "u", "isPrivate": False}), monkeypatch)
    r = GhClient().repo_view("flingjie/FDE-Gym")
    assert r.name_with_owner == "flingjie/FDE-Gym"
    assert r.is_private is False


def test_list_commits_uses_since(monkeypatch):
    captured = {}

    def fake(argv, timeout):
        captured["argv"] = argv
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    monkeypatch.setattr("finch.github.gh_client._run", fake)
    GhClient().list_commits("flingjie/FDE-Gym", since="2026-01-01T00:00:00Z")
    joined = " ".join(captured["argv"])
    assert "since=2026-01-01T00:00:00Z" in joined
    assert "commits" in joined


def test_backoff_retries_then_raises(monkeypatch):
    calls = {"n": 0}

    def always_fail(argv, timeout):
        calls["n"] += 1
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "boom"}

    monkeypatch.setattr("finch.github.gh_client._run", always_fail)
    monkeypatch.setattr("finch.github.gh_client._sleep", lambda s: None)  # 避免真实退避 sleep
    with pytest.raises(GhError):
        GhClient().repo_view("x/y")
    assert calls["n"] == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_gh_client.py -v`
Expected: FAIL — `AttributeError`/`ModuleNotFoundError`（方法未定义）。

- [ ] **Step 3: 写 `gh_client.py` 新增实现**

在现有 `GhClient` 内追加（保留 `version`/`auth_status` 与 `_run`）：

```python
import json
import time


class GhError(RuntimeError):
    pass


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


class GhClient:
    # …既有 version/auth_status 不变…

    def _gh_json(self, argv: list[str], timeout: float = 30.0, retries: int = 3) -> dict | list:
        last: dict | None = None
        for attempt in range(retries):
            r = _run(argv, timeout)
            if r["ok"]:
                try:
                    return json.loads(r["stdout"])
                except json.JSONDecodeError as exc:
                    raise GhError(f"gh returned non-JSON: {exc}") from exc
            last = r
            if attempt < retries - 1:
                _sleep(2 ** attempt)
        assert last is not None
        raise GhError(f"gh failed after {retries} attempts: {last['stderr'].strip()}")

    def repo_view(self, repo: str) -> RepoInfo:
        data = self._gh_json(
            ["gh", "repo", "view", repo, "--json", "nameWithOwner,defaultBranchRef,url,isPrivate"]
        )
        return parse_repo_view(data)

    def list_commits(self, repo: str, since: str, per_page: int = 100) -> list[CommitSummary]:
        url = f"repos/{repo}/commits?sha=main&since={since}&per_page={per_page}"
        data = self._gh_json(
            ["gh", "api", "--paginate", "-H", "Accept: application/vnd.github+json", url],
            timeout=60.0,
        )
        return [parse_commit_summary(item) for item in data]

    def commit_detail(self, repo: str, sha: str) -> CommitDetail:
        data = self._gh_json(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", f"repos/{repo}/commits/{sha}"]
        )
        return parse_commit_detail(data)

    def pr_view(self, repo: str, number: int) -> PullRequest:
        data = self._gh_json(
            ["gh", "pr", "view", str(number), "--repo", repo,
             "--json", "number,title,body,url,state,commits,files,reviews,comments"]
        )
        return parse_pull_request(data)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_gh_client.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/finch/github/gh_client.py tests/unit/test_gh_client.py
git commit -m "feat(github): add read-only GhClient methods with exponential backoff"
```

---

### Task 3: Commit 游标 + 增量读取 + 噪声过滤

**Files:**
- Create: `src/finch/github/commit_reader.py`
- Test: `tests/unit/test_commit_reader.py`

**Interfaces:**
- Consumes: `GhClient`（Task 2）、`CommitSummary`（Task 1）。
- Produces:
  - `finch.github.commit_reader.CommitReader(gh: GhClient, repo: str)`。
  - `sync(since: str | None = None) -> list[CommitSummary]`：`since` 为 None 时读游标文件（`var/cache/github_sync_state.json`）里的 `last_synced_at`，否则用显式时间窗；成功后推进游标到当前 UTC 时间；返回本次新增 Commit。
  - `filter_noise(commits) -> list[CommitSummary]`：过滤锁文件、纯格式化、机械重命名的 Commit（见规则）。
  - 模块函数 `is_noise(commit: CommitDetail) -> bool` 与 `_cursor_path() -> Path`。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_commit_reader.py
from pathlib import Path

from finch.github.commit_reader import CommitReader, is_noise
from finch.github.models import CommitDetail, CommitFile


def _detail(files, message="feat: x"):
    return CommitDetail(
        sha="a" * 40, message=message, author_date="2026-09-01T00:00:00Z",
        html_url="u", parents=[], files=files, stats={},
    )


def test_is_noise_lockfile():
    d = _detail([CommitFile(filename="package-lock.json", status="modified", additions=1, deletions=1)])
    assert is_noise(d) is True


def test_is_noise_format_only():
    d = _detail([CommitFile(filename="src/a.ts", status="modified", additions=0, deletions=0, patch=" ")],
                message="chore: format with prettier")
    assert is_noise(d) is True


def test_is_noise_rename():
    d = _detail([CommitFile(filename="src/a.ts", status="renamed", additions=0, deletions=0)])
    assert is_noise(d) is True


def test_not_noise_real_change():
    d = _detail([CommitFile(filename="src/graph/runtime.ts", status="modified", additions=10, deletions=4,
                            patch="+export function run")],
                message="feat: node-ize orchestrator")
    assert is_noise(d) is False


def test_sync_uses_cursor_and_advances(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = {}

    class FakeGh:
        def list_commits(self, repo, since, per_page=100):
            calls["since"] = since
            return []

    r = CommitReader(FakeGh(), repo="flingjie/FDE-Gym")
    # 首次无游标 → 用显式 since
    r.sync(since="2026-09-01T00:00:00Z")
    assert calls["since"] == "2026-09-01T00:00:00Z"
    # 游标已推进，存在 cursor 文件
    assert Path("var/cache/github_sync_state.json").exists()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_commit_reader.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 写 `src/finch/github/commit_reader.py`**

```python
"""增量读取 Commit 并过滤噪声（spec 12.1「Commit 游标推进」）。"""

import json
from datetime import UTC, datetime
from pathlib import Path

from .gh_client import GhClient
from .models import CommitDetail, CommitSummary

_LOCKFILE_MARKERS = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
                     "Cargo.lock", "Gemfile.lock", "go.sum", "uv.lock")
_FORMAT_MARKERS = ("format", "formatting", "prettier", "lint", "style", "black", "ruff")


def _cursor_path() -> Path:
    return Path("var/cache/github_sync_state.json")


def is_noise(commit: CommitDetail) -> bool:
    if not commit.files:
        return True
    paths = [f.filename.lower() for f in commit.files]
    if all(any(m in p for m in _LOCKFILE_MARKERS) for p in paths):
        return True
    if all(f.status == "renamed" for f in commit.files):
        return True
    if any(m in commit.message.lower() for m in _FORMAT_MARKERS) and not any(
        f.additions > 0 and f.status == "added" for f in commit.files
    ):
        return True
    return False


class CommitReader:
    def __init__(self, gh: GhClient, repo: str):
        self.gh = gh
        self.repo = repo

    def sync(self, since: str | None = None) -> list[CommitSummary]:
        if since is None:
            since = self._load_cursor()
        commits = self.gh.list_commits(self.repo, since=since)
        self._save_cursor()
        return commits

    def filter_noise(self, commits: list[CommitDetail]) -> list[CommitDetail]:
        return [c for c in commits if not is_noise(c)]

    def _load_cursor(self) -> str:
        path = _cursor_path()
        if path.exists():
            data = json.loads(path.read_text())
            return data.get("last_synced_at") or "1970-01-01T00:00:00Z"
        return "1970-01-01T00:00:00Z"

    def _save_cursor(self) -> None:
        path = _cursor_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_synced_at": datetime.now(UTC).isoformat()}))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_commit_reader.py -v`
Expected: PASS（5 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/finch/github/commit_reader.py tests/unit/test_commit_reader.py
git commit -m "feat(github): add incremental commit reader with cursor and noise filter"
```

---

### Task 4: Commit 聚合（change_grouper）

**Files:**
- Create: `src/finch/github/change_grouper.py`
- Test: `tests/unit/test_change_grouper.py`

**Interfaces:**
- Consumes: `CommitSummary`/`CommitDetail`（Task 1）。
- Produces:
  - `finch.github.change_grouper.group_commits(commits: list[CommitDetail], *, window_minutes=90, max_files=200) -> list[list[CommitDetail]]`：把「时间窗口内、作者相同、且消息前缀或文件路径有交集」的 Commit 聚合为组（工程事件候选）。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_change_grouper.py
from finch.github.change_grouper import group_commits
from finch.github.models import CommitDetail, CommitFile


def _c(sha, msg, files, t):
    return CommitDetail(sha=sha, message=msg, author_date=t, html_url="u",
                        parents=[], files=files, stats={})


def test_groups_by_message_prefix():
    commits = [
        _c("a" * 40, "feat: node-ize the four subgraphs (Phase 3 foundation)",
           [CommitFile(filename="src/graph/a.ts", status="modified")], "2026-09-01T05:19:04Z"),
        _c("b" * 40, "feat: node-ize the orchestrator (Phase 3 integration)",
           [CommitFile(filename="src/graph/b.ts", status="modified")], "2026-09-01T06:32:54Z"),
    ]
    groups = group_commits(commits)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_does_not_group_unrelated():
    commits = [
        _c("a" * 40, "feat: add telemetry", [CommitFile(filename="src/telemetry.ts", status="modified")],
           "2026-09-01T05:00:00Z"),
        _c("b" * 40, "docs: update readme", [CommitFile(filename="README.md", status="modified")],
           "2026-09-01T07:00:00Z"),
    ]
    assert len(group_commits(commits)) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_change_grouper.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 写 `src/finch/github/change_grouper.py`**

```python
"""聚合相关 Commit 为工程事件候选（spec 2.2 / Phase 2）。"""

from datetime import datetime

from .models import CommitDetail

_PREFIX_LEN = 24


def _prefix(msg: str) -> str:
    return msg[: _PREFIX_LEN].strip().rstrip(":")


def group_commits(commits: list[CommitDetail], *, window_minutes: int = 90,
                  max_files: int = 200) -> list[list[CommitDetail]]:
    ordered = sorted(commits, key=lambda c: c.author_date)
    groups: list[list[CommitDetail]] = []
    for c in ordered:
        placed = False
        for g in groups:
            head = g[-1]
            if _within_window(head, c, window_minutes) and _related(head, c, max_files):
                g.append(c)
                placed = True
                break
        if not placed:
            groups.append([c])
    return groups


def _within_window(a: CommitDetail, b: CommitDetail, minutes: int) -> bool:
    dt = abs((a.author_date - b.author_date).total_seconds()) / 60.0
    return dt <= minutes


def _related(a: CommitDetail, b: CommitDetail, max_files: int) -> bool:
    if _prefix(a.message) == _prefix(b.message):
        return True
    pa = {f.filename for f in a.files}
    pb = {f.filename for f in b.files}
    return bool(pa & pb)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_change_grouper.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/finch/github/change_grouper.py tests/unit/test_change_grouper.py
git commit -m "feat(github): add commit grouping into engineering-event candidates"
```

---

### Task 5: Evidence 数据模型（EngineeringEvent / EvidenceCard / ClaimConfidence）

**Files:**
- Create: `src/finch/evidence/models.py`
- Test: `tests/unit/test_evidence_models.py`

**Interfaces:**
- Produces:
  - `finch.evidence.models.ClaimConfidence(str, Enum)`：`VERIFIED/SUPPORTED/INFERRED/USER_CONFIRMED/UNKNOWN`，属性 `assertable: bool`（`VERIFIED`/`SUPPORTED`/`USER_CONFIRMED` 为 True）。
  - `finch.evidence.models.Claim`：`statement: str`、`confidence: ClaimConfidence`。
  - `finch.evidence.models.Source`：`type: Literal["commit","test","pr","issue"]`、`url: str`、`path: str | None`。
  - `finch.evidence.models.EngineeringEvent`：`id: str`、`repository: str`、`commits: list[str]`、`problem: Claim`、`decision: Claim`、`result: Claim`、`missing_context: list[str]`。
  - `finch.evidence.models.EvidenceCard`：`id: str`、`event_id: str`、`claim: str`、`sources: list[Source]`、`confidence: ClaimConfidence`、`publishable: bool`、`topics: list[str]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_evidence_models.py
import pytest
from pydantic import ValidationError

from finch.evidence.models import (
    ClaimConfidence,
    EngineeringEvent,
    EvidenceCard,
    Claim,
    Source,
)


def test_confidence_assertable_rules():
    assert ClaimConfidence.VERIFIED.assertable
    assert ClaimConfidence.INFERRED.assertable is False
    assert ClaimConfidence.UNKNOWN.assertable is False


def test_engineering_event_shape_matches_spec_7_1():
    e = EngineeringEvent(
        id="evt_1", repository="flingjie/FDE-Gym", commits=["abc123"],
        problem=Claim(statement="false positive", confidence=ClaimConfidence.VERIFIED),
        decision=Claim(statement="add checks", confidence=ClaimConfidence.INFERRED),
        result=Claim(statement="4 tests pass", confidence=ClaimConfidence.VERIFIED),
        missing_context=["real run or adversarial?"],
    )
    assert e.decision.confidence is ClaimConfidence.INFERRED


def test_evidence_card_sources():
    c = EvidenceCard(
        id="ev_1", event_id="evt_1", claim="final answer != correct execution",
        sources=[Source(type="commit", url="https://github.com/flingjie/FDE-Gym/commit/abc")],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=["agent-evals"],
    )
    assert c.publishable is True


def test_invalid_confidence_rejected():
    with pytest.raises(ValidationError):
        Claim(statement="x", confidence="NOT_A_LEVEL")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_evidence_models.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 写 `src/finch/evidence/models.py`**

```python
"""Evidence 数据模型（spec 7.1/7.2/7.4）。"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ClaimConfidence(str, Enum):
    VERIFIED = "VERIFIED"
    SUPPORTED = "SUPPORTED"
    INFERRED = "INFERRED"
    USER_CONFIRMED = "USER_CONFIRMED"
    UNKNOWN = "UNKNOWN"

    @property
    def assertable(self) -> bool:
        return self in {ClaimConfidence.VERIFIED, ClaimConfidence.SUPPORTED, ClaimConfidence.USER_CONFIRMED}


class Claim(BaseModel):
    statement: str
    confidence: ClaimConfidence


class Source(BaseModel):
    type: Literal["commit", "test", "pr", "issue"]
    url: str
    path: str | None = None


class EngineeringEvent(BaseModel):
    id: str
    repository: str
    commits: list[str] = Field(default_factory=list)
    problem: Claim
    decision: Claim
    result: Claim
    missing_context: list[str] = Field(default_factory=list)


class EvidenceCard(BaseModel):
    id: str
    event_id: str
    claim: str
    sources: list[Source] = Field(default_factory=list)
    confidence: ClaimConfidence
    publishable: bool = False
    topics: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_evidence_models.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/finch/evidence/models.py tests/unit/test_evidence_models.py
git commit -m "feat(evidence): add EngineeringEvent/EvidenceCard/ClaimConfidence models"
```

---

### Task 6: Codex 非交互调用封装

**Files:**
- Create: `src/finch/codex/runner.py`
- Create: `src/finch/codex/structured_output.py`
- Test: `tests/unit/test_codex_runner.py`

**Interfaces:**
- Consumes: `BaseModel`（Pydantic）。
- Produces:
  - `finch.codex.structured_output.model_to_json_schema(model: type[BaseModel]) -> dict`：`model.model_json_schema()`。
  - `finch.codex.structured_output.parse_checked(data, model) -> BaseModel`：用 `model.model_validate` 校验，失败抛 `StructuredOutputError`。
  - `finch.codex.runner.CodexRunner`：`run(prompt: str, output_model: type[BaseModel], *, timeout=180.0) -> BaseModel`。内部：把 JSON Schema 写入临时文件 → `codex exec --output-schema <schema.json> --json -o <out.json> --ephemeral --skip-git-repo-check <prompt>`（prompt 从 stdin 传入）→ 读 `<out.json>` → `parse_checked`。

- [ ] **Step 1: 写失败测试（monkeypatch `_run`，不真调 codex）**

```python
# tests/unit/test_codex_runner.py
import json

import pytest
from pydantic import BaseModel

from finch.codex.runner import CodexRunner
from finch.codex.structured_output import StructuredOutputError, model_to_json_schema, parse_checked


class FakeOut(BaseModel):
    name: str
    score: int


def _run_success(argv, timeout):
    return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}


def test_model_to_json_schema():
    s = model_to_json_schema(FakeOut)
    assert s["type"] == "object"
    assert "name" in s["properties"]


def test_parse_checked_valid():
    out = parse_checked({"name": "x", "score": 3}, FakeOut)
    assert isinstance(out, FakeOut)
    assert out.score == 3


def test_parse_checked_invalid_raises():
    with pytest.raises(StructuredOutputError):
        parse_checked({"name": "x"}, FakeOut)  # 缺 score


def test_runner_invokes_codex_and_parses(tmp_path, monkeypatch):
    schema_written = {}

    def fake_run(argv, timeout):
        # 找到 --output-schema 后的 schema 文件路径
        assert argv[0] == "codex"
        i = argv.index("--output-schema")
        schema_written["path"] = argv[i + 1]
        out_i = argv.index("-o")
        out_path = argv[out_i + 1]
        Path(out_path).write_text(json.dumps({"name": "y", "score": 9}))
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("finch.codex.runner._run", fake_run)
    r = CodexRunner().run("please extract", FakeOut, timeout=10.0)
    assert isinstance(r, FakeOut)
    assert r.name == "y"
    assert Path(schema_written["path"]).exists()
```

（测试文件顶部需 `from pathlib import Path`。）

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_codex_runner.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 写 `structured_output.py`**

```python
"""把 Pydantic 模型转成 Codex --output-schema 的 JSON Schema，并校验解析结果。"""

from pydantic import BaseModel, ValidationError


class StructuredOutputError(RuntimeError):
    pass


def model_to_json_schema(model: type[BaseModel]) -> dict:
    return model.model_json_schema()


def parse_checked(data: dict, model: type[BaseModel]) -> BaseModel:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise StructuredOutputError(f"invalid structured output for {model.__name__}: {exc}") from exc
```

- [ ] **Step 4: 写 `runner.py`**

```python
"""Codex CLI 非交互调用封装（spec 3.1）。"""

import json
import tempfile
from pathlib import Path

from pydantic import BaseModel

from ..github.gh_client import _run
from .structured_output import model_to_json_schema, parse_checked


class CodexRunner:
    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 180.0) -> BaseModel:
        with tempfile.TemporaryDirectory() as td:
            schema_path = Path(td) / "schema.json"
            out_path = Path(td) / "out.json"
            schema_path.write_text(json.dumps(model_to_json_schema(output_model)))
            argv = [
                "codex", "exec",
                "--output-schema", str(schema_path),
                "--json",
                "-o", str(out_path),
                "--ephemeral",
                "--skip-git-repo-check",
                prompt,
            ]
            r = _run(argv, timeout=timeout)
            if not r["ok"]:
                raise RuntimeError(f"codex exec failed: {r['stderr'].strip() or r['stdout'].strip()}")
            if not out_path.exists():
                raise RuntimeError("codex exec produced no output file")
            data = json.loads(out_path.read_text())
        return parse_checked(data, output_model)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_codex_runner.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 6: Commit**

```bash
git add src/finch/codex/runner.py src/finch/codex/structured_output.py tests/unit/test_codex_runner.py
git commit -m "feat(codex): add non-interactive Codex runner with JSON-schema output"
```

---

### Task 7: Engineering Event 提取（extractor）

**Files:**
- Create: `src/finch/evidence/extractor.py`
- Create: `prompts/extract-engineering-event.md`
- Test: `tests/unit/test_extractor.py`

**Interfaces:**
- Consumes: `group_commits`（Task 4）、`EngineeringEvent`/`EvidenceCard`/`ClaimConfidence`（Task 5）、`CodexRunner`（Task 6）。
- Produces:
  - `finch.evidence.extractor.Extractor(runner: CodexRunner)`。
  - `extract(commits: list[CommitDetail], repo: str) -> list[EngineeringEvent]`：分组 → 渲染 prompt → 调用 Codex（`output_model=EngineeringEvent`）→ 校验。
  - `build_cards(events: list[EngineeringEvent], repo: str) -> list[EvidenceCard]`：每个事件的 `problem`/`result` 生成 `VERIFIED` 卡片（绑定 commit URL），`decision` 生成 `INFERRED` 卡片。

- [ ] **Step 1: 写失败测试（假 runner 返回固定 EngineeringEvent）**

```python
# tests/unit/test_extractor.py
from finch.evidence.extractor import Extractor, build_cards
from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
from finch.github.models import CommitDetail, CommitFile


class FakeRunner:
    def run(self, prompt, output_model, **kw):
        return EngineeringEvent(
            id="evt_1", repository="flingjie/FDE-Gym", commits=["a" * 40],
            problem=Claim(statement="p", confidence=ClaimConfidence.VERIFIED),
            decision=Claim(statement="d", confidence=ClaimConfidence.INFERRED),
            result=Claim(statement="r", confidence=ClaimConfidence.VERIFIED),
        )


def _commit(sha):
    return CommitDetail(sha=sha, message="feat: node-ize X", author_date="2026-09-01T00:00:00Z",
                        html_url=f"https://github.com/flingjie/FDE-Gym/commit/{sha}", parents=[],
                        files=[CommitFile(filename="src/graph/a.ts", status="modified")], stats={})


def test_extract_groups_and_calls_runner():
    commits = [_commit("a" * 40), _commit("b" * 40)]
    events = Extractor(FakeRunner()).extract(commits, repo="flingjie/FDE-Gym")
    assert len(events) == 1
    assert events[0].repository == "flingjie/FDE-Gym"


def test_build_cards_binds_sources_and_confidence():
    events = [
        EngineeringEvent(
            id="evt_1", repository="flingjie/FDE-Gym", commits=["a" * 40],
            problem=Claim(statement="p", confidence=ClaimConfidence.VERIFIED),
            decision=Claim(statement="d", confidence=ClaimConfidence.INFERRED),
            result=Claim(statement="r", confidence=ClaimConfidence.VERIFIED),
        )
    ]
    cards = build_cards(events, repo="flingjie/FDE-Gym")
    # problem + result → verified 卡片；decision → inferred 卡片
    assert any(c.confidence is ClaimConfidence.VERIFIED for c in cards)
    assert any(c.confidence is ClaimConfidence.INFERRED for c in cards)
    assert all(c.sources for c in cards)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 写 `prompts/extract-engineering-event.md`**

````markdown
# Extract Engineering Event

You are given a group of related commits from one repository. Extract ONE engineering event.

## Rules

- Distinguish what the code PROVES from what is INFERRED.
- `problem` / `result` must be directly provable from the diff/tests/PR → `VERIFIED`; if only strongly implied, use `SUPPORTED`.
- `decision` (why / motivation) is almost always `INFERRED` unless a PR/issue explicitly states it.
- Never mark an inference as `VERIFIED`.
- If context is missing (e.g. was this a real bug or a proactive hardening?), list it in `missing_context`.
- `id` must be a stable slug, e.g. `evt_<repo-slug>_<short-topic>`.

## Input commits

{commits}

## Output

Respond with a JSON object matching the schema, with fields `id`, `repository`, `commits`, `problem`, `decision`, `result`, `missing_context`. `problem`/`decision`/`result` are objects `{"statement": str, "confidence": "VERIFIED|SUPPORTED|INFERRED|USER_CONFIRMED|UNKNOWN"}`.
````

- [ ] **Step 4: 写 `src/finch/evidence/extractor.py`**

```python
"""从 Commit 组提取 Engineering Event 并生成 Evidence Card（spec 2.2）。"""

from pathlib import Path

from ..codex.runner import CodexRunner
from ..github.change_grouper import group_commits
from ..github.models import CommitDetail
from .models import ClaimConfidence, EngineeringEvent, EvidenceCard, Source

_PROMPT_PATH = Path("prompts/extract-engineering-event.md")


def _render_commits(commits: list[CommitDetail]) -> str:
    lines = []
    for c in commits:
        lines.append(f"- {c.sha[:8]} {c.message}")
        for f in c.files[:12]:
            lines.append(f"    {f.status} {f.filename} (+{f.additions}/-{f.deletions})")
    return "\n".join(lines)


class Extractor:
    def __init__(self, runner: CodexRunner):
        self.runner = runner

    def extract(self, commits: list[CommitDetail], repo: str) -> list[EngineeringEvent]:
        events: list[EngineeringEvent] = []
        for group in group_commits(commits):
            prompt = _PROMPT_PATH.read_text().replace("{commits}", _render_commits(group))
            event = self.runner.run(prompt, EngineeringEvent)
            if event.repository != repo:
                event = event.model_copy(update={"repository": repo})
            events.append(event)
        return events


def build_cards(events: list[EngineeringEvent], repo: str) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    for ev in events:
        base = f"https://github.com/{repo}/commit/"
        for claim, label in ((ev.problem, "problem"), (ev.result, "result")):
            cards.append(EvidenceCard(
                id=f"ev_{ev.id}_{label}",
                event_id=ev.id,
                claim=claim.statement,
                sources=[Source(type="commit", url=base + c) for c in ev.commits],
                confidence=claim.confidence,
                publishable=True,
                topics=[],
            ))
        cards.append(EvidenceCard(
            id=f"ev_{ev.id}_decision",
            event_id=ev.id,
            claim=ev.decision.statement,
            sources=[Source(type="commit", url=base + c) for c in ev.commits],
            confidence=ev.decision.confidence,
            publishable=True,
            topics=[],
        ))
    return cards
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_extractor.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 6: Commit**

```bash
git add src/finch/evidence/extractor.py prompts/extract-engineering-event.md tests/unit/test_extractor.py
git commit -m "feat(evidence): add EngineeringEvent extractor and EvidenceCard builder"
```

---

### Task 8: CLI（finch github sync / reflect）与手动验收

**Files:**
- Modify: `src/finch/cli.py`
- Test: `tests/unit/test_cli_github.py`

**Interfaces:**
- Consumes: `load_settings`、`CommitReader`、`GhClient`、`Extractor`、`CodexRunner`、`build_cards`。
- Produces:
  - `finch.cli.app` 新增子命令组 `github`（`github sync`、`github reflect`）。
  - `finch reflect --repo flingjie/FDE-Gym --since 7d`：读取 Commit → 过滤噪声 → 提取事件 → 生成卡片 → 输出 Markdown。

- [ ] **Step 1: 写失败测试（monkeypatch 整条链路，不真调 gh/codex）**

```python
# tests/unit/test_cli_github.py
from typer.testing import CliRunner

from finch.cli import app


def test_github_sync_smoke(monkeypatch):
    captured = {}

    class FakeGh:
        def list_commits(self, repo, since, per_page=100):
            captured["since"] = since
            return []

    monkeypatch.setattr("finch.cli.GhClient", lambda: FakeGh())
    monkeypatch.setattr("finch.cli.load_settings", lambda: None)
    r = CliRunner().invoke(app, ["github", "sync", "--since", "72h"])
    assert r.exit_code == 0
    assert captured["since"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_cli_github.py -v`
Expected: FAIL（`github` 子命令不存在）。

- [ ] **Step 3: 扩展 `src/finch/cli.py`**

```python
from datetime import UTC, datetime, timedelta

from .github.commit_reader import CommitReader
from .github.gh_client import GhClient
from .codex.runner import CodexRunner
from .evidence.extractor import Extractor, build_cards

github_app = typer.Typer(help="GitHub 读取与工程事件提取")
app.add_typer(github_app, name="github")


def _since_iso(since: str | None) -> str | None:
    if since is None:
        return None
    if since.endswith("h"):
        return (datetime.now(UTC) - timedelta(hours=int(since[:-1]))).isoformat()
    if since.endswith("d"):
        return (datetime.now(UTC) - timedelta(days=int(since[:-1]))).isoformat()
    return since


@github_app.command("sync")
def github_sync(repo: str = typer.Option("flingjie/FDE-Gym"), since: str | None = None) -> None:
    """增量读取仓库 Commit 并推进游标。"""
    reader = CommitReader(GhClient(), repo=repo)
    commits = reader.sync(since=_since_iso(since))
    typer.echo(f"synced {len(commits)} commits for {repo}")


@github_app.command("reflect")
def github_reflect(repo: str = typer.Option("flingjie/FDE-Gym"),
                   since: str = typer.Option("7d")) -> None:
    """读取最近 Commit，提取工程事件并输出 Evidence Cards。"""
    gh = GhClient()
    summaries = gh.list_commits(repo, since=_since_iso(since))
    details = [gh.commit_detail(repo, c.sha) for c in summaries]
    reader = CommitReader(gh, repo)
    details = reader.filter_noise(details)
    events = Extractor(CodexRunner()).extract(details, repo=repo)
    cards = build_cards(events)
    typer.echo(f"# Finch reflect: {repo}\n")
    for ev in events:
        typer.echo(f"## {ev.id}\n- problem: {ev.problem.statement} [{ev.problem.confidence.value}]")
        typer.echo(f"- decision: {ev.decision.statement} [{ev.decision.confidence.value}]")
        typer.echo(f"- result: {ev.result.statement} [{ev.result.confidence.value}]")
    typer.echo(f"\n{len(cards)} evidence cards")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_cli_github.py -v`
Expected: PASS（1 passed）。

- [ ] **Step 5: 全量门禁 + 手动验收**

Run: `uv run pytest && uv run ruff check . && uv run mypy src`
Expected: 全部通过。

Run（真实链路，验收「解释 FDE-Gym 最近 Commit」）:
```bash
uv run finch reflect --repo flingjie/FDE-Gym --since 7d
```
Expected: 输出若干 `## evt_*` 事件，每条 problem/decision/result 带 `VERIFIED`/`INFERRED` 标注；`decision` 不得标 `VERIFIED`；每张 Evidence Card 绑定 commit URL。

- [ ] **Step 6: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_github.py
git commit -m "feat(cli): add finch github sync/reflect commands"
```

---

## Self-Review

**1. Spec coverage（Phase 2 + spec 5.2/7/10/12.2）：**
- 实现 `GhClient` 读取 → Task 2。
- 增量读取 Commit + 游标推进 → Task 3。
- 过滤格式化/锁文件/机械重命名 → Task 3 `is_noise`。
- 聚合相关 Commit → Task 4。
- 调用 Codex 提取 Engineering Event → Task 6/7。
- 生成 Evidence Card → Task 5/7。
- 数据模型 spec 7.1/7.2/7.4 → Task 1/5。
- contract fixture（spec 12.2）→ Task 1。
- CLI `finch github sync`/`reflect`（spec 10）→ Task 8。
- 验收「解释 FDE-Gym 最近 Commit」→ Task 8 Step 5 真实 `reflect`。

**2. Placeholder scan：** 无 TBD；所有代码步骤含完整代码；fixture 由真实只读命令抓取（脱敏、公开仓库）。

**3. Type consistency：**
- `CommitSummary.sha/message/author_date/html_url/parents` 在 Task 1 定义，Task 2/3/4/7 使用一致。
- `ClaimConfidence` 五级 + `assertable` 在 Task 5 定义，Task 7 使用一致。
- `CodexRunner.run(prompt, output_model, *, timeout)` 在 Task 6 定义，Task 7 的 `FakeRunner.run(prompt, output_model, **kw)` 兼容。
- `GhClient._gh_json` 返回 `dict | list`，`list_commits` 消费 list、其余消费 dict，与 fixture 形状一致。
