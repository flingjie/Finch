# 文档统一 + Source/发现地基 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Finch 的方向统一为「产品与问题并列打通」，并修复 Source 同步掩盖部分失败、产品发现不可观测两个可靠性缺陷。

**Architecture:** 三个并行的地基改动：(1) 四份规范文档统一方向文字；(2) Source 层加确定性聚合状态 `aggregate_state`（五态）并修三个诚实性 bug（TIMEOUT 记 failed、GitHub 部分失败显式、投影失败显式）；(3) 产品发现新增 `DiscoveryRunReport` 并在 CLI 渲染。

**Tech Stack:** Python 3.12、Pydantic 2、typer（CLI）、pytest（单测）、dataclasses。

## Global Constraints

- Python 3.12+；Pydantic 2 模型；领域服务确定性单线程，无 `asyncio.gather`。
- Ruff selects `E,F,I,B,UP`；line-length 100。跑 `uv run ruff check .` 与 `uv run mypy src`。
- 领域模型落盘用 `Workspace.atomic_write`（幂等覆盖）；不新增数据库、不改存储后端。
- 单源/单 query 失败不阻塞其他源；raw 落盘先于投影，投影失败不丢采集结果。
- 确定性 totals 铁律：LLM 永不写 `score_total`；聚合状态是纯 Python 函数。
- 只读不变式：`gh` 与 `opencli` 只读，禁止写命令。
- 提交信息用英文 conventional commit（`fix(sources):` / `feat(products):` / `docs:` 等）。

---

### Task 1: 文档统一到「问题与产品并列打通」

**Files:**
- Modify: `docs/product-contract.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`（全量重写）

**Interfaces:**
- Consumes: 无（纯文档）。
- Produces: 四份文档对「产品与问题并列打通」的定义一致，作为后续任务的方向约束。

- [ ] **Step 1: 改 `docs/product-contract.md` 定位句 + 对象表 + 北极星**

在开篇段「产品是主要研究对象，创业者是主要连接对象。」之后插入一句并列定义：

```markdown
问题是与产品并列的一等研究对象：一个真实用户问题对应多个候选方案，一个产品可解决多个问题，二者通过证据或人工确认关联。
```

在「对象所有权」表格的 `Product` 行之后、`Founder` 行之前，新增一行：

```markdown
| `Problem` | 问题领域 | 真实用户问题：受阻场景、证据、受影响用户、现有 workaround、候选方案（引用 `Product`）。按语义身份幂等归一化。是产品的并列研究对象，不合成单一总分。 |
```

在「北极星指标」段，把「关系之外，每周分别观察、不合成一个总分的四类进展」改为「五类进展」，并在列举末尾追加：

```markdown
- 问题推进：哪些问题从初步发现推进到更深理解或真实验证。
```

- [ ] **Step 2: 改 `README.md` 定位句**

在定位段「产品是主要研究对象，创业者是主要连接对象。」之后插入同一句：

```markdown
问题是与产品并列的一等研究对象：一个真实用户问题对应多个候选方案，一个产品可解决多个问题，二者通过证据或人工确认关联。
```

在核心闭环代码块之后补一句并列视角：

```markdown
真实问题既可从产品与用户反馈中发现，也可直接从社区问题（issue / 讨论）中发现；问题与产品双向关联，方案比较围绕真实问题展开。
```

- [ ] **Step 3: 改 `CLAUDE.md` 的 Project 段与 architecture 段**

在 Project 段「Products are the main object of study; founders are the main object of connection.」之后补一句：

```
Problems are a first-class object of study alongside products, linked by evidence or manual confirmation. First round scope: product discovery + solution explorer.
```

在 architecture 段的 active 列表里，`products/` 行之后显式加两行（当前 active/frozen 注释未涵盖它们）：

```
  problem_discovery/  real user problem extraction + problem inbox
  relationships/      person task understanding + weekly briefing
```

- [ ] **Step 4: 全量重写 `AGENTS.md`**

用以下内容整体替换 `AGENTS.md`（删除旧定位「跨领域学习/连接/表达伙伴」「不建独立问题库」「问题/workaround 记 ConversationThread」，改为与契约一致）：

```markdown
# AGENTS.md

供 AI 编码代理参考的项目约定。完整命令与目录见 `CLAUDE.md`；产品定义见 `docs/product-contract.md`。

## 核心原则

- Finch 是 AI 产品发现 + 创业者连接 Copilot。产品与问题是并列的一等研究对象，二者通过证据或人工确认关联；创业者是主要连接对象。内容生产不是目的，也不服从连接。
- Evidence First：产品卡片、问题、方案都必须有来源工件；证据不足就留空或写「未知」，不编造。
- Evidence vs Assumption：区分「来源明确说出 / 由材料推导 / 未经验证的假设」三层；未引用证据的主张只能标假设。
- Codex 是智能节点，不是工作流 Runtime；状态、顺序、重试、幂等由确定性 Python 领域服务负责。
- 读取/写入权限分离：`gh` 与 `opencli` 仅读取；禁止写命令。
- 子进程参数用数组传递；每次调用设超时；输出强制 JSON 并 Pydantic 校验。
- 确定性 totals：`score_total` / `score_dimensions` 只由代码计算，LLM 永不输出总分。

## 命令

- 测试：`uv run pytest`
- 代码质量：`uv run ruff check .`、`uv run mypy src`
- 运行：`uv run finch <command>`
- 产品发现（本轮核心）：`finch products daily [--refresh] [--view home|browse] [--json] [--question ...]`、`finch products list`、`finch products show <id>`、`finch products feedback <id>`、`finch products explore <id>`
- 问题发现：`finch problem-discovery add|discover|list|show|summary|present|last-presented`
- 人物关系：`finch relationships import|add-person|add-event|record-contact|next|weekly`
- 采集：`finch sources doctor` / `sync`（只读；twitter|reddit|github|v2ex|weixin|xiaohongshu）
- 仓库热度榜：`finch repos discover` / `list` / `export`

## 目录

- `src/finch/products/` 产品发现 / 产品卡片 / 方案分析 / 用户反馈研究
- `src/finch/problem_discovery/` 真实问题提取与问题收件箱
- `src/finch/relationships/` 人物与关系管理（任务理解 / 周度简报）
- `src/finch/sources/` 跨平台只读抓取（opencli gateway + RawArtifact）
- `src/finch/repos/` X 分享的 GitHub 仓库发现与热度榜
- `src/finch/storage/` 文件 Workspace（YAML/Markdown/JSONL，原子写）
- `src/finch/github/` `src/finch/twitter/` `src/finch/reddit/` 只读 adapter

旧使命命令（connect / peers / people / connections / conversations / practice 等）已冻结：隐藏于默认 CLI，代码与测试保留，不删除。
```

- [ ] **Step 5: 验证一致并提交**

Run: `grep -rn "不建独立问题库\|跨领域学习" AGENTS.md README.md CLAUDE.md docs/product-contract.md`
Expected: 无输出（旧定位已清除）。

Run: `git add -A && git commit -m "docs: align product and problem as first-class linked objects"`

---

### Task 2: `aggregate_state` 五态聚合 + `QueryOutcome.partial`

**Files:**
- Modify: `src/finch/sources/orchestrator.py:63-125`（`QueryOutcome`、`_query_outcome_dict`、`classify_result`）
- Test: `tests/unit/test_source_report.py`

**Interfaces:**
- Produces: `aggregate_state(result: SyncResult) -> str`（返回 `success | empty | partial_success | failed`）；`QueryOutcome` 增 `partial: bool = False`；`_query_outcome_dict` 序列化 `partial`。后续 Task 3/4/5/7 依赖这些。

- [ ] **Step 1: 写失败测试**

替换 `tests/unit/test_source_report.py` 顶部的 import（`classify_result` → `aggregate_state`），并把两个 `classify_*` 测试改为 `aggregate_state` 版本，追加 partial 用例：

```python
from finch.sources.orchestrator import (
    QueryOutcome,
    SyncResult,
    _request_label,
    aggregate_state,
    sync_result_to_dict,
)


def test_aggregate_state_states():
    assert (
        aggregate_state(SyncResult(source=Source.TWITTER, status=SourceStatus.READY, raw_count=1))
        == "success"
    )
    assert (
        aggregate_state(SyncResult(source=Source.TWITTER, status=SourceStatus.READY, raw_count=0))
        == "empty"
    )
    assert (
        aggregate_state(
            SyncResult(source=Source.TWITTER, status=SourceStatus.DEGRADED, kind=ResultKind.TIMEOUT)
        )
        == "failed"
    )
    assert (
        aggregate_state(
            SyncResult(
                source=Source.TWITTER,
                status=SourceStatus.DEGRADED,
                detail="timeline unavailable",
            )
        )
        == "failed"
    )


def test_aggregate_timeline_success_not_failed():
    assert (
        aggregate_state(
            SyncResult(
                source=Source.TWITTER,
                status=SourceStatus.DEGRADED,
                kind=ResultKind.SUCCESS,
                raw_count=3,
            )
        )
        == "success"
    )


def test_aggregate_partial_success_when_some_queries_failed():
    r = SyncResult(
        source=Source.REDDIT,
        status=SourceStatus.READY,
        raw_count=5,
        per_query=[
            QueryOutcome(query="a", outcome="failed", kind=ResultKind.TIMEOUT),
            QueryOutcome(query="b", outcome="ran", raw_count=5, kind=ResultKind.SUCCESS),
        ],
    )
    assert aggregate_state(r) == "partial_success"


def test_aggregate_failed_when_all_queries_failed():
    r = SyncResult(
        source=Source.REDDIT,
        status=SourceStatus.READY,
        raw_count=0,
        per_query=[QueryOutcome(query="a", outcome="failed", kind=ResultKind.TIMEOUT)],
    )
    assert aggregate_state(r) == "failed"


def test_aggregate_partial_flag_counts_as_partial_success():
    r = SyncResult(
        source=Source.GITHUB,
        status=SourceStatus.READY,
        raw_count=3,
        per_query=[
            QueryOutcome(
                query="alice",
                outcome="ran",
                raw_count=3,
                kind=ResultKind.SUCCESS,
                partial=True,
                detail="repos: timeout",
            )
        ],
    )
    assert aggregate_state(r) == "partial_success"
```

在同一个文件里把 `test_sync_result_to_dict_serializes_enums_and_per_query` 末尾加一条断言（在现有 `assert "artifacts" not in d` 之前）：

```python
    assert d["per_query"][0]["partial"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_source_report.py -q`
Expected: FAIL，`ImportError: cannot import name 'aggregate_state'`。

- [ ] **Step 3: 实现**

在 `orchestrator.py` 的 `QueryOutcome` 加 `partial` 字段（`detail: str = ""` 之后）：

```python
    partial: bool = False
```

在 `_query_outcome_dict`（`kind` 与 `detail` 之间）加：

```python
        "partial": o.partial,
```

把 `classify_result` 整段（`orchestrator.py:75-83`）替换为：

```python
def aggregate_state(result: SyncResult) -> str:
    """源级聚合状态：success / empty / partial_success / failed。

    优先聚合 per_query：任一 query failed 或 partial → 有 raw 为 partial_success、
    无 raw 为 failed。无 per_query 时沿用旧 classify 语义（kind 错误 / status 非 READY
    且有 detail 或空 raw → failed）。
    """
    has_failure = any(o.outcome == "failed" for o in result.per_query)
    has_partial = any(o.partial for o in result.per_query)
    if has_failure or has_partial:
        return "partial_success" if result.raw_count > 0 else "failed"
    if result.kind in _ERROR_KINDS:
        return "failed"
    if result.status != SourceStatus.READY and (result.detail or result.raw_count == 0):
        return "failed"
    return "success" if result.raw_count > 0 else "empty"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_source_report.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add tests/unit/test_source_report.py src/finch/sources/orchestrator.py
git commit -m "feat(sources): add five-state aggregate_state and per-query partial flag"
```

---

### Task 3: bug1 — 请求循环把非 break 错误记 failed 并继续

**Files:**
- Modify: `src/finch/sources/orchestrator.py:42-51`（加 `_BREAK_KINDS`）、`src/finch/sources/orchestrator.py:300-347`（请求循环）
- Test: `tests/unit/test_source_connectors.py`

**Interfaces:**
- Consumes: Task 2 的 `aggregate_state`、`QueryOutcome.outcome`、`ResultKind`、`_ERROR_KINDS`。
- Produces: 请求循环对 `_ERROR_KINDS` 一律记 `outcome="failed"`，仅 `_BREAK_KINDS` 中断。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_source_connectors.py` 的 `TestOrchestratorIsolation` 类里追加：

```python
    def test_timeout_query_recorded_failed_not_empty(self, tmp_path: Path):
        ws = Workspace(tmp_path)
        ws.ensure()

        def fake_run(argv, timeout):
            if argv[:2] == ["opencli", "list"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps([{"site": "reddit", "commands": ["search"]}]),
                    "stderr": "",
                }
            if "timeout-me" in argv:
                return {"ok": True, "exit_code": 75, "stdout": "", "stderr": ""}
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": (FIXTURES / "reddit-search.json").read_text(),
                "stderr": "",
            }

        from finch.sources.connectors import DiscoveryContext
        from finch.sources.models import Source as S
        from finch.sources.opencli_gateway import OpenCliGateway

        orch = DiscoveryOrchestrator(ws, gateway=OpenCliGateway(run_fn=fake_run))
        result = orch.sync_source(S.REDDIT, DiscoveryContext(queries=["timeout-me", "ok-me"]))
        outcomes = {o.query: o for o in result.per_query}
        assert outcomes["timeout-me"].outcome == "failed"
        assert outcomes["timeout-me"].kind == ResultKind.TIMEOUT
        assert outcomes["ok-me"].outcome == "ran"
        from finch.sources.orchestrator import aggregate_state
        assert aggregate_state(result) == "partial_success"
```

（需在文件顶部 import 里补 `from finch.sources.models import ResultKind`；现有顶部已 `from finch.sources.models import OpenCliResult, ResultKind, Source`，`Source` 已作为别名 `S` 使用，无需改。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_source_connectors.py::TestOrchestratorIsolation::test_timeout_query_recorded_failed_not_empty -q`
Expected: FAIL，`outcomes["timeout-me"].outcome == "empty"` 而非 `"failed"`。

- [ ] **Step 3: 实现**

在 `_ERROR_KINDS` 之后加常量：

```python
_BREAK_KINDS = {
    ResultKind.AUTH_REQUIRED,
    ResultKind.BRIDGE_DOWN,
    ResultKind.CONFIG_ERROR,
}
```

把请求循环里 `if result.kind in {ResultKind.AUTH_REQUIRED, ResultKind.BRIDGE_DOWN, ResultKind.CONFIG_ERROR}:` 一段替换为：

```python
            last_kind = result.kind
            if result.kind in _ERROR_KINDS:
                detail = result.stderr_summary or result.kind.value
                per_query.append(
                    QueryOutcome(query=label, outcome="failed", kind=result.kind,
                                 detail=detail, raw_count=len(result.rows))
                )
                if result.kind in _BREAK_KINDS:
                    break
                continue
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_source_connectors.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add tests/unit/test_source_connectors.py src/finch/sources/orchestrator.py
git commit -m "fix(sources): record non-breaking error kinds as failed per query"
```

---

### Task 4: bug2 — GitHub 部分失败显式化

**Files:**
- Modify: `src/finch/sources/orchestrator.py:229-237`（GitHub 路径的 `QueryOutcome`）
- Test: `tests/unit/test_source_connectors.py`

**Interfaces:**
- Consumes: Task 2 的 `QueryOutcome.partial`。
- Produces: GitHub `fetch_creator` 的 `stderr_summary` 进入 `QueryOutcome.detail`；有 rows 且 stderr 非空时 `partial=True`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_source_connectors.py` 追加：

```python
class TestGitHubPartial:
    def test_github_partial_failure_recorded(self, tmp_path: Path):
        ws = Workspace(tmp_path)
        ws.ensure()

        from finch.sources.connectors import DiscoveryContext
        from finch.sources.connectors.github import GitHubConnector
        from finch.sources.models import OpenCliResult, ResultKind, Source as S

        class _FakeGitHub(GitHubConnector):
            def __init__(self):
                self.gh = None  # type: ignore[assignment]

            def probe(self, capabilities):
                from finch.sources.models import SourceStatus
                return SourceStatus.READY

            def fetch_creator(self, login):
                return OpenCliResult(
                    rows=[{"_kind": "user", "login": "alice", "id": 1, "name": "Alice",
                           "bio": "builder", "html_url": "https://github.com/alice"}],
                    exit_code=0,
                    kind=ResultKind.SUCCESS,
                    stderr_summary="repos: timeout; commits alice/tool: 403",
                )

        def fake_run(argv, timeout):
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps([{"site": "github", "commands": ["user"]}]),
                "stderr": "",
            }

        from finch.sources.opencli_gateway import OpenCliGateway

        orch = DiscoveryOrchestrator(
            ws, gateway=OpenCliGateway(run_fn=fake_run),
            connectors={S.GITHUB: _FakeGitHub()},
        )
        result = orch.sync_source(S.GITHUB, DiscoveryContext(queries=["alice"]))
        q = result.per_query[0]
        assert q.partial is True
        assert "repos: timeout" in q.detail
        from finch.sources.orchestrator import aggregate_state
        assert aggregate_state(result) == "partial_success"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_source_connectors.py::TestGitHubPartial -q`
Expected: FAIL，`q.partial` 不存在（或为默认 False）。

- [ ] **Step 3: 实现**

把 GitHub 路径（`orchestrator.py` 里 `if source == Source.GITHUB ...` 分支内）的 `per_query.append(QueryOutcome(...))` 替换为：

```python
                per_query.append(
                    QueryOutcome(
                        query=q,
                        outcome="empty" if not result.rows else "ran",
                        raw_count=len(result.rows),
                        normalized_count=len(normalized) - norm_before,
                        kind=result.kind,
                        detail=result.stderr_summary or "",
                        partial=bool(result.rows and result.stderr_summary),
                    )
                )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_source_connectors.py::TestGitHubPartial -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add tests/unit/test_source_connectors.py src/finch/sources/orchestrator.py
git commit -m "fix(sources): surface github creator partial failures in per-query"
```

---

### Task 5: bug3 — 投影失败显式化

**Files:**
- Modify: `src/finch/sources/projector.py:17-25`（`ProjectResult`）
- Modify: `src/finch/sources/orchestrator.py:128-146`（`SyncResult`）、`:106-125`（`sync_result_to_dict`）、`:397-403`（`_project`）、GitHub 与非 GitHub 两条返回路径的 payload + `SyncResult`
- Test: `tests/unit/test_source_connectors.py`

**Interfaces:**
- Consumes: Task 2 的 `ProjectResult` 用法（`_project` 返回 `ProjectResult`）。
- Produces: `ProjectResult.failed/error`；`SyncResult.projection_failed/projection_error`；`sync_result_to_dict` 序列化它们。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_source_connectors.py` 的 `TestOrchestratorIsolation` 类里追加：

```python
    def test_projection_failure_is_surfaced(self, tmp_path: Path):
        ws = Workspace(tmp_path)
        ws.ensure()

        def fake_run(argv, timeout):
            if argv[:2] == ["opencli", "list"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps([{"site": "reddit", "commands": ["search"]}]),
                    "stderr": "",
                }
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": (FIXTURES / "reddit-search.json").read_text(),
                "stderr": "",
            }

        class _RaisingProjector:
            def project(self, artifacts):
                raise RuntimeError("boom")

        from finch.sources.connectors import DiscoveryContext
        from finch.sources.models import Source as S
        from finch.sources.opencli_gateway import OpenCliGateway

        orch = DiscoveryOrchestrator(
            ws, gateway=OpenCliGateway(run_fn=fake_run), projector=_RaisingProjector()
        )
        result = orch.sync_source(S.REDDIT, DiscoveryContext(queries=["q"]), project_peers=True)
        assert result.projection_failed is True
        assert "boom" in result.projection_error
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_source_connectors.py::TestOrchestratorIsolation::test_projection_failure_is_surfaced -q`
Expected: FAIL，`result.projection_failed` 不存在（AttributeError）或为 False。

- [ ] **Step 3: 实现**

`projector.py` 的 `ProjectResult` 末尾加两个字段：

```python
    failed: bool = False
    error: str = ""
```

`orchestrator.py` 的 `_project` 替换为：

```python
    def _project(self, artifacts: list[RawArtifact]) -> ProjectResult:
        if not artifacts:
            return ProjectResult()
        try:
            return self.projector.project(artifacts)
        except Exception as exc:  # noqa: BLE001
            return ProjectResult(failed=True, error=str(exc)[:300])
```

`SyncResult`（`started_at` 之后）加两个字段：

```python
    projection_failed: bool = False
    projection_error: str = ""
```

`sync_result_to_dict` 的返回 dict（`error_type` 之后）加：

```python
        "projection_failed": result.projection_failed,
        "projection_error": result.projection_error,
```

GitHub 返回路径：在 `payload["projected_count"] = proj.projected_peers` 之后加两行，并在该分支 `return SyncResult(...)` 里加 `projection_failed=proj.failed, projection_error=proj.error`：

```python
            payload["projection_failed"] = proj.failed
            payload["projection_error"] = proj.error
```

非 GitHub 返回路径：在 `payload["projected_count"] = proj.projected_peers`（`:368`）之后加同样两行，并在 `return SyncResult(...)` 里加 `projection_failed=proj.failed, projection_error=proj.error`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_source_connectors.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add tests/unit/test_source_connectors.py src/finch/sources/projector.py src/finch/sources/orchestrator.py
git commit -m "fix(sources): surface projection failures instead of swallowing them"
```

---

### Task 6: DiscoveryRunReport + 快照扩展 + 卡片填充返回计数

**Files:**
- Modify: `src/finch/products/discovery.py`（`DiscoveryRunReport`、`ProductDiscoveryResult.report`、`run_product_discovery`）
- Modify: `src/finch/products/models.py:216-225`（`ProductSnapshot`）
- Modify: `src/finch/products/cards.py:79-90`（`ProductCardService.fill`）
- Test: `tests/unit/test_products_discovery.py`

**Interfaces:**
- Consumes: Task 2 的 `aggregate_state`、`QueryOutcome`；现有 `ProductCardService.fill`、`ProductSnapshot`。
- Produces: `DiscoveryRunReport` dataclass（`per_source_state/raw_count/normalized_count/filtered_count/product_count/card_fill_success/card_fill_failed/elapsed_seconds`）；`ProductCardService.fill -> tuple[int, int]`；`ProductSnapshot.source_states/card_fill_success/card_fill_failed/elapsed_seconds`。Task 7 依赖。

> 注：spec 曾列 `query_outcomes` 字段，此处省略——per-query 详情已随 `result.sync_results` 返回，报告只保留聚合计数，避免重复。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_products_discovery.py` 顶部 import 补 `DiscoveryRunReport` 与 `QueryOutcome`：

```python
from finch.products.discovery import (
    DiscoveryRunReport,
    run_product_discovery,
    select_daily_products,
)
from finch.sources.orchestrator import DiscoveryOrchestrator, QueryOutcome, SyncResult
```

更新 `test_card_service_fills_fields` 里 `fill` 的调用（改为元组解包）：

```python
    saved, failed = svc.fill(repo.list_all())
    assert saved == 1
    assert failed == 0
```

追加报告组装测试：

```python
def test_run_product_discovery_builds_report(tmp_path, monkeypatch):
    ws = Workspace(tmp_path)
    ws.ensure()
    fresh = _art(
        "1",
        "https://github.com/acme/widget",
        "We launched widget, an agent harness for QA pain points and flaky test struggles",
    )
    ok = SyncResult(
        source=Source.TWITTER,
        status=SourceStatus.READY,
        raw_count=1,
        normalized_count=1,
        projected_count=0,
        kind=ResultKind.SUCCESS,
        artifacts=[fresh],
        per_query=[
            QueryOutcome(query="github.com has:links", outcome="ran", raw_count=1,
                         kind=ResultKind.SUCCESS)
        ],
    )
    monkeypatch.setattr(DiscoveryOrchestrator, "sync_all", lambda self, **kw: [ok])

    settings = _settings(tmp_path)
    result = run_product_discovery(settings, runner=_FakeCardRunner("github.com/acme/widget"))

    assert result.report is not None
    assert result.report.raw_count == 1
    assert result.report.normalized_count == 1
    assert result.report.product_count == len(result.products)
    assert result.report.card_fill_success == 1
    assert result.report.card_fill_failed == 0
    assert result.report.elapsed_seconds >= 0.0
    assert result.snapshot is not None
    assert result.snapshot.source_states == {"twitter": "success"}
    assert result.snapshot.card_fill_success == 1
    assert result.snapshot.card_fill_failed == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_products_discovery.py -q`
Expected: FAIL，`DiscoveryRunReport` / `report` / `source_states` 不存在。

- [ ] **Step 3: 实现 `ProductSnapshot` 字段**

`products/models.py` 的 `ProductSnapshot`（`source_coverage` 之后）加：

```python
    source_states: dict[str, str] = Field(default_factory=dict)
    card_fill_success: int = 0
    card_fill_failed: int = 0
    elapsed_seconds: float = 0.0
```

- [ ] **Step 4: 实现 `ProductCardService.fill` 返回元组**

把 `cards.py` 的 `fill` 替换为：

```python
    def fill(self, products: list[Product]) -> tuple[int, int]:
        """批量填卡片字段并落库；返回 (saved, failed)。失败批跳过，不阻断。"""
        if self.runner is None or not products:
            return 0, 0

        saved = 0
        failed = 0
        for batch in _chunks(products[: self.max_candidates], self.batch_size):
            try:
                saved += self._fill_batch(batch)
            except Exception:  # noqa: BLE001
                failed += len(batch)
        return saved, failed
```

- [ ] **Step 5: 实现 `DiscoveryRunReport` + `run_product_discovery` 组装**

在 `discovery.py` 顶部补 `import time`，并把 `from finch.sources.orchestrator import DiscoveryOrchestrator, SyncResult` 改为 `from finch.sources.orchestrator import DiscoveryOrchestrator, SyncResult, aggregate_state`。

在 `ProductDiscoveryResult`（`discovery.py:31`）**之前**加：

```python
@dataclass
class DiscoveryRunReport:
    """一次产品发现运行的可观测汇总。"""

    per_source_state: dict[str, str] = field(default_factory=dict)
    raw_count: int = 0
    normalized_count: int = 0
    filtered_count: int = 0
    product_count: int = 0
    card_fill_success: int = 0
    card_fill_failed: int = 0
    elapsed_seconds: float = 0.0
```

`ProductDiscoveryResult` 加一个字段 `report: DiscoveryRunReport | None = None`（文件已有 `from __future__ import annotations`，前向引用安全）。

在 `run_product_discovery` 函数开头（`run_id = ...` 之后）记起点：

```python
    started = time.perf_counter()
```

把卡片填充段（`if runner is not None:`）替换为：

```python
    card_fill_success = 0
    card_fill_failed = 0
    if runner is not None:
        cards = ProductCardService(
            ws,
            runner=runner,
            max_candidates=settings.products.max_card_candidates,
            batch_size=settings.products.card_batch_size,
        )
        card_fill_success, card_fill_failed = cards.fill(selected)
        # 卡片填充是 upsert 新对象；重读以拿到填好的卡片字段。
        selected = [repo.get(p.id) or p for p in selected]
```

把快照构造段替换为（新增 `source_states`/`card_*`/`elapsed_seconds`，并在 `return` 前组装 `report` 与 `detail`）：

```python
    elapsed = round(time.perf_counter() - started, 4)
    source_states = {sr.source.value: aggregate_state(sr) for sr in result.sync_results}
    report = DiscoveryRunReport(
        per_source_state=source_states,
        raw_count=sum(sr.raw_count for sr in result.sync_results),
        normalized_count=sum(sr.normalized_count for sr in result.sync_results),
        filtered_count=sum(result.filtered.values()),
        product_count=len(selected),
        card_fill_success=card_fill_success,
        card_fill_failed=card_fill_failed,
        elapsed_seconds=elapsed,
    )
    snapshot = ProductSnapshot(
        id=run_id,
        created_at=now,
        plan_id=plan.plan_id,
        context_fingerprint=plan.config_fingerprint,
        product_ids=[p.id for p in selected],
        shortfall=shortfall,
        source_coverage={
            sr.source.value: {
                "status": sr.status.value,
                "raw": sr.raw_count,
                "norm": sr.normalized_count,
            }
            for sr in result.sync_results
        },
        source_states=source_states,
        card_fill_success=card_fill_success,
        card_fill_failed=card_fill_failed,
        elapsed_seconds=elapsed,
    )
    ProductSnapshotRepository(ws).upsert(snapshot)

    failed_states = {
        s: st for s, st in source_states.items() if st != "success"
    }
    result.detail = "；".join(f"{s}:{st}" for s, st in failed_states.items())
    result.report = report
    result.products = selected
    result.snapshot = snapshot
    return result
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_products_discovery.py -q`
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add tests/unit/test_products_discovery.py src/finch/products/discovery.py src/finch/products/models.py src/finch/products/cards.py
git commit -m "feat(products): add discovery run report and card fill counts"
```

---

### Task 7: CLI 渲染（`sources sync` + `products daily`）

**Files:**
- Modify: `src/finch/cli.py:143`（顶层 import）、`:970-1036`（`products_daily`）、`:1705-1780`（`sources_sync`）
- Test: `tests/unit/test_cli_sources.py`

**Interfaces:**
- Consumes: Task 2 的 `aggregate_state`；Task 5 的 `SyncResult.projection_failed/projection_error`；Task 6 的 `DiscoveryRunReport`。
- Produces: `products daily` 打印报告行 / 空因；`sources sync` 用五态 + partial + 投影失败。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_cli_sources.py` 追加两个测试：

```python
def test_sources_sync_json_partial_state(tmp_path, monkeypatch):
    settings = Settings(paths=Paths(var_dir=tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def fake_sync_all(self, **kwargs):
        return [
            SyncResult(
                source=Source.REDDIT,
                status=SourceStatus.READY,
                raw_count=5,
                mode="query",
                ran_queries=["a", "b"],
                per_query=[
                    QueryOutcome(query="a", outcome="failed", kind=ResultKind.TIMEOUT),
                    QueryOutcome(query="b", outcome="ran", raw_count=5, kind=ResultKind.SUCCESS),
                ],
                started_at="2026-10-30T10:00:00+00:00",
            )
        ]

    monkeypatch.setattr(DiscoveryOrchestrator, "sync_all", fake_sync_all)
    r = CliRunner().invoke(app, ["sources", "sync", "--all", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    reddit = next(row for row in payload if row["source"] == "reddit")
    assert reddit["state"] == "partial_success"
    assert reddit["per_query"][0]["outcome"] == "failed"
    assert reddit["per_query"][0]["partial"] is False


def test_products_daily_refresh_prints_report(tmp_path, monkeypatch):
    from finch.products.discovery import DiscoveryRunReport, ProductDiscoveryResult

    settings = Settings(paths=Paths(var_dir=tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_runner", lambda _llm: None)

    def fake_run_product_discovery(settings, **kwargs):
        return ProductDiscoveryResult(
            run_id="r1",
            products=[],
            report=DiscoveryRunReport(
                per_source_state={"twitter": "failed"},
                raw_count=0, normalized_count=0, filtered_count=0, product_count=0,
                card_fill_success=0, card_fill_failed=0, elapsed_seconds=0.1,
            ),
        )

    monkeypatch.setattr(cli, "run_product_discovery", fake_run_product_discovery)
    r = CliRunner().invoke(app, ["products", "daily", "--refresh"])
    assert r.exit_code == 0, r.output
    assert "raw=0" in r.output
    assert "twitter:failed" in r.output
```

（`Source`、`SyncResult`、`QueryOutcome`、`ResultKind` 已在 `test_cli_sources.py` 顶部 import；`SourceStatus` 也已 import。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_cli_sources.py -q`
Expected: FAIL，`products daily` 不打印报告行。

- [ ] **Step 3: 实现 `sources_sync` 渲染**

`cli.py` 的 `sources_sync` 内 import 块（`:1705`）把 `classify_result` 改为 `aggregate_state`。把渲染循环里 `state = classify_result(r)` 改为 `state = aggregate_state(r)`；JSON 分支里 `"state": classify_result(r)` 改为 `"state": aggregate_state(r)`。

逐源行在 `projected={r.projected_count}` 之后追加投影失败提示，per-query 行加 partial 与失败 detail：

```python
        state = aggregate_state(r)
        typer.echo(
            f"{r.source.value}: {state} [{r.mode or '-'}] "
            f"started={r.started_at or '-'} raw={r.raw_count} "
            f"norm={r.normalized_count} new={r.created_count} "
            f"projected={r.projected_count}"
            + (f" ({r.detail})" if r.detail else "")
            + (f" projection_failed={r.projection_error}" if r.projection_failed else "")
        )
        for o in r.per_query:
            if o.outcome == "skipped":
                typer.echo(f"  - skipped (mode={r.mode}): {o.query}")
            elif o.outcome == "failed":
                typer.echo(f"  - {o.query}: failed ({o.kind.value if o.kind else 'error'})"
                           + (f" — {o.detail}" if o.detail else ""))
            elif o.outcome == "empty":
                typer.echo(f"  - {o.query}: empty (0 raw)")
            else:
                suffix = " (partial)" if o.partial else ""
                typer.echo(f"  - {o.query}: ran ({o.raw_count} raw){suffix}")
```

- [ ] **Step 4: 实现 `products_daily` 渲染**

顶层 import（`:143`）改为：

```python
from .products.discovery import DiscoveryRunReport, run_product_discovery
```

在 `products_daily` 函数定义前加纯函数：

```python
def _render_report_line(report: DiscoveryRunReport) -> str:
    states = ", ".join(f"{s}:{st}" for s, st in sorted(report.per_source_state.items()))
    return (
        f"raw={report.raw_count} norm={report.normalized_count} "
        f"filtered={report.filtered_count} products={report.product_count} "
        f"cards={report.card_fill_success}ok/{report.card_fill_failed}fail "
        f"{report.elapsed_seconds}s [{states}]"
    )
```

把 `products_daily` 函数体替换为（含 `report`/`sync_results` 作用域、JSON 含 report、空因渲染）：

```python
    from dataclasses import asdict

    from finch.sources.opencli_gateway import OpenCliGateway
    from finch.sources.orchestrator import aggregate_state

    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    repo = ProductRepository(ws)
    snap_repo = ProductSnapshotRepository(ws)
    presentation = ProductPresentationRepository(ws)

    report = None
    sync_results = []
    if refresh:
        runner = cast(CodexRunner, create_runner(settings.llm) or CodexRunner())
        gateway = OpenCliGateway(profile=settings.opencli.profile)
        result = run_product_discovery(
            settings,
            runner=runner,
            gateway=gateway,
            lookback_hours=lookback_hours,
            question=question,
        )
        snapshot = result.snapshot
        products = result.products
        report = result.report
        sync_results = result.sync_results
        if result.detail:
            typer.echo(f"note: {result.detail}")
    else:
        snapshot = snap_repo.latest()
        products = []
        if snapshot is not None:
            for pid in snapshot.product_ids:
                p = repo.get(pid)
                if p is not None:
                    products.append(p)

    if view == "browse":
        products = sorted(repo.list_all(), key=lambda p: (-p.score_total, p.id))
    elif snapshot is not None:
        for p in products:
            presentation.record_shown(product_id=p.id, surface="home", snapshot_id=snapshot.id)

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "snapshot_id": snapshot.id if snapshot else "",
                    "products": [p.model_dump(mode="json") for p in products],
                    "report": asdict(report) if report else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if report is not None:
        typer.echo(_render_report_line(report))

    if not products:
        if sync_results:
            typer.echo("今日无产品候选（空轮保持空）。来源状态：")
            for sr in sync_results:
                typer.echo(
                    f"  {sr.source.value}: {aggregate_state(sr)}"
                    + (f" ({sr.detail})" if sr.detail else "")
                )
        else:
            typer.echo("今日无产品候选（空轮保持空）。可用 `uv run finch products daily --refresh`。")
        return
    typer.echo(f"每日产品候选（{len(products)}）：")
    for i, p in enumerate(products, 1):
        _render_product_line(i, p)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_cli_sources.py -q`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add tests/unit/test_cli_sources.py src/finch/cli.py
git commit -m "feat(cli): render five-state source report and daily discovery report"
```

---

## 完成校验

全部任务完成后运行：

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```

三项均须通过（pytest 全绿、ruff 无告警、mypy 无错误）。
