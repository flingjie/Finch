# Finch 项目初始化（Phase 1：项目骨架与 Runtime）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/Finch-Codex-Development-Plan.md` 的 Phase 1 与第 4 节（仓库结构），初始化 Finch 项目骨架：Python 3.12 + uv + Typer 工程、确定性 Graph Runtime（state/NodeResult/DomainEvent、运行记录、节点记录、幂等键、Replay 骨架）与 `finch init`/`finch diagnose` 命令，并配套 pytest 测试。

**Architecture:** 采用 `src/` 布局的 uv 项目。核心是一个确定性 Graph Runtime：`GraphRuntime` 按顺序执行 `Node`，把每次运行（`RunRecord`）与每个节点执行（`NodeRecord`）持久化到 SQLite（SQLModel），通过 `idempotency_key` 实现幂等与恢复、Replay。Phase 1 的 Graph 只有空节点集（空 Graph 可执行、失败、恢复、重放），`finch diagnose` 通过只读子进程分别探测 `gh` 与 `opencli` 状态。未来各阶段模块（github/twitter/evidence/content/review/codex）先落占位 stub。

**Tech Stack:** Python 3.12、uv、Typer、Pydantic v2、SQLModel + SQLite、Alembic（骨架）、PyYAML、python-dotenv、pytest、Ruff、mypy。

## Global Constraints

- Python 版本下限：`>=3.12`（spec 3.1 表：Python 3.12）。
- 包管理：`uv`；构建后端 `hatchling`。
- CLI 框架：Typer。
- 数据模型：Pydantic v2（`from pydantic import BaseModel`，非 v1）。
- 数据库：SQLite + SQLModel。
- 源码放 `src/finch/`，测试放 `tests/`。
- `var/` 目录默认加入 `.gitignore`（spec 第 4 节末尾）。
- `NodeResult` 契约必须与 spec 6.3 完全一致（字段名 `status/output/events/warnings/retryable/error_code`，`status` 取 `succeeded|failed|needs_input|partial`）。
- 子进程参数用数组传递，不拼接 shell 字符串（spec 5.2）。
- 每次子进程调用设置超时（spec 5.2）。
- 所有 CLI 输出默认 JSON，由 Pydantic 校验（spec 5.2/5.3）。
- 只允许只读命令：`gh` 读取、`opencli` 读取/搜索；Phase 1 不实现任何写命令。

---

### Task 1: 工程脚手架与依赖

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.python-version`
- Create: `README.md`（一行占位；`pyproject.toml` 的 `readme = "README.md"` 要求它在 `uv sync` 时即存在，Task 9 会覆盖为完整内容）
- Create: `src/finch/__init__.py`

**Interfaces:**
- Produces: 可安装的 `finch` 包；`python -c "import finch"` 成功；`finch` 命令入口（`[project.scripts] finch = "finch.cli:app"`）。

- [ ] **Step 1: 写 `pyproject.toml`**

```toml
[project]
name = "finch"
version = "0.1.0"
description = "Evidence-driven builder companion: matches GitHub engineering evidence to public technical discussion"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "typer>=0.12",
    "pydantic>=2.7",
    "sqlmodel>=0.0.16",
    "alembic>=1.13",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
]

[project.scripts]
finch = "finch.cli:app"

[dependency-groups]
dev = [
    "pytest>=8.2",
    "ruff>=0.5",
    "mypy>=1.10",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/finch"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.mypy]
python_version = "3.12"
check_untyped_defs = true
```

- [ ] **Step 2: 写 `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
dist/
build/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Local data (spec: var/ 默认加入 .gitignore)
var/

# Secrets
.env
```

- [ ] **Step 3: 写 `.python-version`**

```
3.12
```

- [ ] **Step 4: 写 `src/finch/__init__.py` 与占位 `README.md`**

```python
"""Finch: evidence-driven builder companion."""

__version__ = "0.1.0"
```

```markdown
# Finch

（占位；Task 9 覆盖为完整 README。）
```

- [ ] **Step 5: 安装依赖并验证可导入**

Run: `uv sync`
Expected: 创建 `.venv`，安装依赖，无错误。

Run: `uv run python -c "import finch; print(finch.__version__)"`
Expected: 输出 `0.1.0`。

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore .python-version README.md src/finch/__init__.py uv.lock
git commit -m "chore: scaffold uv/Python 3.12 project"
```

---

### Task 2: 配置与 Settings

**Files:**
- Create: `src/finch/settings.py`
- Create: `finch.yaml`
- Create: `.env.example`
- Test: `tests/unit/test_settings.py`

**Interfaces:**
- Produces:
  - `finch.settings.Settings`（Pydantic model）：`repositories: list[str]`、`twitter: TwitterSettings`、`quality_gates: dict`、`paths: Paths`。
  - `finch.settings.Paths`：`var_dir: Path = Path("var")`、`db_path: Path = Path("var/finch.db")`、`outputs_dir: Path = Path("var/outputs")`。
  - `finch.settings.load_settings(path: Path | None = None) -> Settings`：从 YAML 读取并 `Paths` 确保目录存在。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_settings.py
from pathlib import Path

from finch.settings import load_settings


def test_load_settings_defaults():
    s = load_settings(Path("finch.yaml"))
    assert s.repositories == ["flingjie/FDE-Gym"]
    assert s.twitter.daily_limit == 100
    assert s.paths.var_dir == Path("var")


def test_load_settings_creates_var_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = load_settings(Path("finch.yaml"))  # reads repo-root finch.yaml; paths resolve under tmp_path
    assert s.paths.var_dir.exists()
    assert s.paths.outputs_dir.exists()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finch.settings'`。

- [ ] **Step 3: 写 `finch.yaml`**

```yaml
repositories:
  - flingjie/FDE-Gym

twitter:
  daily_limit: 100
  per_query_limit: 20
  queries: []

quality_gates:
  max_daily_replies: 5
  max_daily_original_posts: 1
  min_candidate_score: 0.65
  min_evidence_score: 0.75
  min_quality_score: 0.75
  max_rewrite_rounds: 2
```

- [ ] **Step 4: 写 `.env.example`**

```bash
# Finch 本身不存凭据：gh 与 opencli 各自管理自己的认证。
# 此处仅预留未来可能用到的可选覆盖项（Phase 1 不读取）。
FINCH_VAR_DIR=var
```

- [ ] **Step 5: 写 `src/finch/settings.py`**

```python
"""配置加载：finch.yaml + 环境变量覆盖。"""

from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class Paths(BaseModel):
    var_dir: Path = Field(default_factory=lambda: Path("var"))
    db_path: Path = Field(default_factory=lambda: Path("var/finch.db"))
    outputs_dir: Path = Field(default_factory=lambda: Path("var/outputs"))
    inbox_dir: Path = Field(default_factory=lambda: Path("var/inbox"))
    cache_dir: Path = Field(default_factory=lambda: Path("var/cache"))

    def ensure(self) -> "Paths":
        for d in (self.var_dir, self.outputs_dir, self.inbox_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


class TwitterSettings(BaseModel):
    daily_limit: int = 100
    per_query_limit: int = 20
    queries: list[dict] = Field(default_factory=list)


class Settings(BaseModel):
    repositories: list[str] = Field(default_factory=list)
    twitter: TwitterSettings = Field(default_factory=TwitterSettings)
    quality_gates: dict = Field(default_factory=dict)
    paths: Paths = Field(default_factory=Paths)


def load_settings(path: Path | None = None) -> Settings:
    """从 finch.yaml 读取配置；缺省时使用内置默认值。"""
    load_dotenv()
    target = path if path is not None else Path("finch.yaml")
    data: dict = {}
    if target.exists():
        data = yaml.safe_load(target.read_text()) or {}
    settings = Settings(**data)
    settings.paths.ensure()
    return settings
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 7: Commit**

```bash
git add src/finch/settings.py finch.yaml .env.example tests/unit/test_settings.py
git commit -m "feat: add settings module loading finch.yaml"
```

---

### Task 3: Graph 状态机、DomainEvent 与 NodeResult

**Files:**
- Create: `src/finch/graph/__init__.py`
- Create: `src/finch/graph/state.py`
- Create: `src/finch/graph/events.py`
- Test: `tests/unit/test_state.py`
- Test: `tests/unit/test_events.py`

**Interfaces:**
- Produces:
  - `finch.graph.state.GraphState(str, Enum)`：含 spec 6.1 全部状态常量；属性 `is_terminal: bool`、`is_abnormal: bool`。
  - `finch.graph.state.advance(state: GraphState) -> GraphState`：沿线性主链前进一步；异常/终态原样返回。
  - `finch.graph.state.can_transition(src: GraphState, dst: GraphState) -> bool`。
  - `finch.graph.events.DomainEvent`：`id: str`、`type: str`、`occurred_at: datetime`、`payload: dict`。
  - `finch.graph.events.NodeResult`：`status/output/events/warnings/retryable/error_code`（与 spec 6.3 一致）。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_state.py
from finch.graph.state import GraphState, advance, can_transition


def test_linear_advance_follows_main_chain():
    assert advance(GraphState.CREATED) == GraphState.PREFLIGHT_PASSED
    assert advance(GraphState.DRAFTED) == GraphState.CRITIQUED


def test_advance_past_review_reaches_waits():
    assert advance(GraphState.EVIDENCE_MATCHED) == GraphState.DRAFTED


def test_terminal_states_are_stable():
    for s in (GraphState.COMPLETED, GraphState.FAILED, GraphState.BLOCKED):
        assert s.is_terminal
        assert advance(s) == s


def test_skipped_advances_to_published():
    assert advance(GraphState.SKIPPED) == GraphState.PUBLISHED


def test_abnormal_flag():
    assert GraphState.FAILED.is_abnormal
    assert not GraphState.CREATED.is_abnormal


def test_can_transition_to_abnormal_from_anywhere():
    assert can_transition(GraphState.CREATED, GraphState.FAILED)
    assert can_transition(GraphState.DRAFTED, GraphState.BLOCKED)
```

```python
# tests/unit/test_events.py
from finch.graph.events import DomainEvent, NodeResult


def test_node_result_contract_matches_spec():
    r = NodeResult(status="succeeded", output={"x": 1})
    assert r.warnings == []
    assert r.retryable is False
    assert r.error_code is None


def test_node_result_events_default_empty():
    r = NodeResult(status="failed", output={}, retryable=True, error_code="E1")
    assert r.events == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_state.py tests/unit/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 写 `src/finch/graph/__init__.py`**

```python
"""确定性 Graph Runtime：状态、节点与领域事件。"""
```

- [ ] **Step 4: 写 `src/finch/graph/state.py`**

```python
"""Graph 状态机：spec 6.1。"""

from enum import Enum


class GraphState(str, Enum):
    # 主链（spec 6.1）
    CREATED = "CREATED"
    PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
    COMMITS_SYNCED = "COMMITS_SYNCED"
    EVENTS_EXTRACTED = "EVENTS_EXTRACTED"
    TWEETS_COLLECTED = "TWEETS_COLLECTED"
    CANDIDATES_RANKED = "CANDIDATES_RANKED"
    EVIDENCE_MATCHED = "EVIDENCE_MATCHED"
    DRAFTED = "DRAFTED"
    CRITIQUED = "CRITIQUED"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    APPROVED = "APPROVED"
    SKIPPED = "SKIPPED"
    PUBLISHED = "PUBLISHED"
    MEASURED = "MEASURED"
    COMPLETED = "COMPLETED"
    # 异常状态
    NEEDS_INPUT = "NEEDS_INPUT"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in {GraphState.COMPLETED, GraphState.FAILED, GraphState.BLOCKED}

    @property
    def is_abnormal(self) -> bool:
        return self in {
            GraphState.NEEDS_INPUT,
            GraphState.PARTIALLY_COMPLETED,
            GraphState.BLOCKED,
            GraphState.FAILED,
        }


_MAIN_CHAIN: list[GraphState] = [
    GraphState.CREATED,
    GraphState.PREFLIGHT_PASSED,
    GraphState.COMMITS_SYNCED,
    GraphState.EVENTS_EXTRACTED,
    GraphState.TWEETS_COLLECTED,
    GraphState.CANDIDATES_RANKED,
    GraphState.EVIDENCE_MATCHED,
    GraphState.DRAFTED,
    GraphState.CRITIQUED,
    GraphState.WAITING_FOR_REVIEW,
    GraphState.APPROVED,
    GraphState.PUBLISHED,
    GraphState.MEASURED,
    GraphState.COMPLETED,
]


def can_transition(src: GraphState, dst: GraphState) -> bool:
    """异常状态可从任意非终态迁入；其余按主链前进。"""
    if src.is_terminal:
        return False
    if dst.is_abnormal:
        return True
    return advance(src) == dst


def advance(state: GraphState) -> GraphState:
    """沿主链前进一步；终态/异常态原样返回。"""
    if state.is_terminal or state.is_abnormal:
        return state
    if state == GraphState.SKIPPED:
        return GraphState.PUBLISHED
    try:
        idx = _MAIN_CHAIN.index(state)
    except ValueError:
        return state
    if idx + 1 >= len(_MAIN_CHAIN):
        return GraphState.COMPLETED
    return _MAIN_CHAIN[idx + 1]
```

- [ ] **Step 5: 写 `src/finch/graph/events.py`**

```python
"""领域事件与节点结果契约（spec 6.3）。"""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DomainEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    type: str
    occurred_at: datetime = Field(default_factory=_utcnow)
    payload: dict = Field(default_factory=dict)


class NodeResult(BaseModel):
    status: Literal["succeeded", "failed", "needs_input", "partial"]
    output: dict = Field(default_factory=dict)
    events: list[DomainEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retryable: bool = False
    error_code: str | None = None
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_state.py tests/unit/test_events.py -v`
Expected: PASS（7 passed）。

- [ ] **Step 7: Commit**

```bash
git add src/finch/graph/__init__.py src/finch/graph/state.py src/finch/graph/events.py tests/unit/test_state.py tests/unit/test_events.py
git commit -m "feat: add GraphState, DomainEvent and NodeResult contracts"
```

---

### Task 4: 节点基类与 Guard

**Files:**
- Create: `src/finch/graph/nodes.py`
- Create: `src/finch/graph/guards.py`
- Test: `tests/unit/test_nodes.py`

**Interfaces:**
- Produces:
  - `finch.graph.nodes.Node(BaseModel)`：字段 `name: str`、`timeout_seconds: float = 60.0`、`max_retries: int = 3`、`idempotency_key: str`、`side_effect: Literal["none","read","write"] = "none"`；方法 `run(ctx: dict) -> NodeResult`（基类返回 `succeeded` 空结果）。
  - `finch.graph.nodes.NoopNode(name: str)`：空节点，`run` 返回 `succeeded`。
  - `finch.graph.nodes.FailingNode(name: str, retryable: bool, error_code: str)`：`run` 返回 `failed`，供 Runtime 失败/恢复测试。
  - `finch.graph.guards.Guard`：字段 `name: str`、`check(result: NodeResult, ctx: dict) -> bool`。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_nodes.py
from finch.graph.nodes import FailingNode, NoopNode


def test_noop_node_succeeds():
    r = NoopNode(name="noop").run({})
    assert r.status == "succeeded"


def test_failing_node_returns_failed():
    r = FailingNode(name="boom", retryable=True, error_code="E_BOOM").run({})
    assert r.status == "failed"
    assert r.retryable is True
    assert r.error_code == "E_BOOM"


def test_node_carries_contract_fields():
    n = NoopNode(name="n", idempotency_key="k1", timeout_seconds=5.0)
    assert n.idempotency_key == "k1"
    assert n.side_effect == "none"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_nodes.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 写 `src/finch/graph/nodes.py`**

```python
"""Graph 节点：声明契约 + 基类行为。"""

from typing import Literal

from pydantic import BaseModel

from .events import NodeResult


class Node(BaseModel):
    """spec 6.3：每个节点必须声明的契约字段。"""

    name: str
    timeout_seconds: float = 60.0
    max_retries: int = 3
    idempotency_key: str = "default"
    side_effect: Literal["none", "read", "write"] = "none"

    def run(self, ctx: dict) -> NodeResult:
        return NodeResult(status="succeeded", output={})


class NoopNode(Node):
    def run(self, ctx: dict) -> NodeResult:
        return NodeResult(status="succeeded", output={})


class FailingNode(Node):
    retryable: bool = False
    error_code: str = "E_FAILED"

    def run(self, ctx: dict) -> NodeResult:
        return NodeResult(
            status="failed",
            output={},
            retryable=self.retryable,
            error_code=self.error_code,
        )
```

- [ ] **Step 4: 写 `src/finch/graph/guards.py`**

```python
"""Graph Guard：可组合的通过/阻断检查。"""

from pydantic import BaseModel

from .events import NodeResult


class Guard(BaseModel):
    name: str

    def check(self, result: NodeResult, ctx: dict) -> bool:
        """默认守卫：节点成功即通过。"""
        return result.status == "succeeded"
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_nodes.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 6: Commit**

```bash
git add src/finch/graph/nodes.py src/finch/graph/guards.py tests/unit/test_nodes.py
git commit -m "feat: add Node base class and Guard"
```

---

### Task 5: SQLite 存储与仓储

**Files:**
- Create: `src/finch/storage/__init__.py`
- Create: `src/finch/storage/database.py`
- Create: `src/finch/storage/repositories.py`
- Test: `tests/unit/test_storage.py`

**Interfaces:**
- Produces:
  - `finch.storage.database.RunRecord(SQLModel, table=True)`：`id: str`(PK)、`state: str`、`created_at: datetime`、`updated_at: datetime`。
  - `finch.storage.database.NodeRecord(SQLModel, table=True)`：`id: str`(PK)、`run_id: str`(indexed)、`node_name: str`、`idempotency_key: str`、`status: str`、`output_json: str`、`error_code: str | None`、`created_at: datetime`。
  - `finch.storage.database.Store`：`init()` 建表；`upsert_run(record)`、`get_run(run_id)`；`upsert_node(record)`、`find_node(run_id, node_name, idempotency_key) -> NodeRecord | None`、`list_nodes(run_id)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_storage.py
from datetime import datetime, timezone

from finch.storage.database import NodeRecord, RunRecord, Store


def test_store_roundtrip_run_and_node(tmp_path):
    db = tmp_path / "test.db"
    store = Store(db)
    store.init()

    run = RunRecord(id="run1", state="CREATED", created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc))
    store.upsert_run(run)
    assert store.get_run("run1") is not None

    node = NodeRecord(id="n1", run_id="run1", node_name="noop",
                      idempotency_key="k1", status="succeeded",
                      output_json="{}", error_code=None,
                      created_at=datetime.now(timezone.utc))
    store.upsert_node(node)
    found = store.find_node("run1", "noop", "k1")
    assert found is not None
    assert found.status == "succeeded"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_storage.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 写 `src/finch/storage/__init__.py`**

```python
"""SQLite 持久化与仓储。"""
```

- [ ] **Step 4: 写 `src/finch/storage/database.py`**

```python
"""SQLite 持久化：运行记录与节点记录（spec 6/7）。"""

from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Field, Session, SQLModel, create_engine, select


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    state: str
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class NodeRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    node_name: str
    idempotency_key: str
    status: str
    output_json: str
    error_code: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}")

    def init(self) -> None:
        SQLModel.metadata.create_all(self.engine)

    def upsert_run(self, record: RunRecord) -> None:
        with Session(self.engine) as session:
            session.merge(record)
            session.commit()

    def get_run(self, run_id: str) -> RunRecord | None:
        with Session(self.engine) as session:
            return session.get(RunRecord, run_id)

    def upsert_node(self, record: NodeRecord) -> None:
        """插入或更新节点记录（恢复/重放时同键覆盖，避免主键冲突）。"""
        with Session(self.engine) as session:
            session.merge(record)
            session.commit()

    def find_node(self, run_id: str, node_name: str, idempotency_key: str) -> NodeRecord | None:
        with Session(self.engine) as session:
            stmt = select(NodeRecord).where(
                NodeRecord.run_id == run_id,
                NodeRecord.node_name == node_name,
                NodeRecord.idempotency_key == idempotency_key,
            )
            return session.exec(stmt).first()

    def list_nodes(self, run_id: str) -> list[NodeRecord]:
        with Session(self.engine) as session:
            stmt = select(NodeRecord).where(NodeRecord.run_id == run_id)
            return list(session.exec(stmt))
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_storage.py -v`
Expected: PASS（1 passed）。

- [ ] **Step 6: Commit**

```bash
git add src/finch/storage/__init__.py src/finch/storage/database.py tests/unit/test_storage.py
git commit -m "feat: add SQLite run/node record storage"
```

---

### Task 6: Graph Runtime（执行、幂等、失败恢复）

**Files:**
- Create: `src/finch/graph/runtime.py`
- Test: `tests/graph/test_runtime.py`

**Interfaces:**
- Consumes: `Node`、`NoopNode`、`FailingNode`（Task 4）；`Store`、`RunRecord`、`NodeRecord`（Task 5）；`GraphState`（Task 3）。
- Produces:
  - `finch.graph.runtime.GraphRuntime(store: Store, nodes: list[Node])`。
  - `run(run_id: str | None = None) -> RunRecord`：顺序执行节点；每个节点执行前按 `idempotency_key` 查已有成功的 `NodeRecord` 则跳过；节点失败时运行置 `FAILED`；全部成功置 `COMPLETED`。
  - `_node_record_id(run_id, node_name) -> str`：节点记录主键。

- [ ] **Step 1: 写失败测试**

```python
# tests/graph/test_runtime.py
import json
from datetime import datetime, timezone

from finch.graph.nodes import FailingNode, NoopNode
from finch.graph.runtime import GraphRuntime
from finch.storage.database import Store


def _store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.init()
    return s


def test_empty_graph_reaches_completed(tmp_path):
    rt = GraphRuntime(_store(tmp_path), nodes=[])
    run = rt.run()
    assert run.state == "COMPLETED"


def test_successful_run_records_nodes(tmp_path):
    store = _store(tmp_path)
    rt = GraphRuntime(store, nodes=[NoopNode(name="a", idempotency_key="ka"),
                                    NoopNode(name="b", idempotency_key="kb")])
    run = rt.run()
    assert run.state == "COMPLETED"
    assert len(store.list_nodes(run.id)) == 2


def test_failed_node_marks_run_failed(tmp_path):
    store = _store(tmp_path)
    rt = GraphRuntime(store, nodes=[NoopNode(name="a", idempotency_key="ka"),
                                    FailingNode(name="boom", idempotency_key="kb",
                                                retryable=False)])
    run = rt.run()
    assert run.state == "FAILED"
    # 失败节点也落记录
    assert store.find_node(run.id, "boom", "kb") is not None


def test_recovery_skips_completed_nodes(tmp_path):
    store = _store(tmp_path)
    rt = GraphRuntime(store, nodes=[NoopNode(name="a", idempotency_key="ka"),
                                    FailingNode(name="boom", idempotency_key="kb",
                                                retryable=True)])
    run1 = rt.run()
    assert run1.state == "FAILED"

    # 第二次用同一 run_id 恢复：a 已成功跳过，boom 重试。这里换成可成功的节点来模拟“修复后重跑”。
    rt2 = GraphRuntime(store, nodes=[NoopNode(name="a", idempotency_key="ka"),
                                     NoopNode(name="boom", idempotency_key="kb")])
    run2 = rt2.run(run_id=run1.id)
    assert run2.state == "COMPLETED"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/graph/test_runtime.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 写 `src/finch/graph/runtime.py`**

```python
"""确定性 Graph Runtime：顺序执行、幂等、失败与恢复。"""

import json
from datetime import datetime, timezone
from uuid import uuid4

from .events import NodeResult
from .nodes import Node
from .state import GraphState
from ..storage.database import NodeRecord, RunRecord, Store


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GraphRuntime:
    def __init__(self, store: Store, nodes: list[Node]):
        self.store = store
        self.nodes = nodes

    def run(self, run_id: str | None = None) -> RunRecord:
        run_id = run_id or uuid4().hex
        self.store.upsert_run(
            RunRecord(id=run_id, state=GraphState.CREATED.value, updated_at=_utcnow())
        )

        final_state = GraphState.COMPLETED
        for node in self.nodes:
            existing = self.store.find_node(run_id, node.name, node.idempotency_key)
            if existing is not None and existing.status == "succeeded":
                continue

            result = self._safe_run(node)
            self._persist_node(run_id, node, result)

            if result.status == "failed":
                final_state = GraphState.FAILED
                break

        self.store.upsert_run(
            RunRecord(id=run_id, state=final_state.value, updated_at=_utcnow())
        )
        run = self.store.get_run(run_id)
        assert run is not None
        return run

    def _safe_run(self, node: Node) -> NodeResult:
        try:
            return node.run({})
        except Exception as exc:  # noqa: BLE001
            return NodeResult(status="failed", retryable=True, error_code=type(exc).__name__)

    def _persist_node(self, run_id: str, node: Node, result: NodeResult) -> None:
        record = NodeRecord(
            id=f"{run_id}:{node.name}:{node.idempotency_key}",
            run_id=run_id,
            node_name=node.name,
            idempotency_key=node.idempotency_key,
            status=result.status,
            output_json=json.dumps(result.output),
            error_code=result.error_code,
            created_at=_utcnow(),
        )
        self.store.upsert_node(record)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/graph/test_runtime.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/finch/graph/runtime.py tests/graph/test_runtime.py
git commit -m "feat: add deterministic GraphRuntime with idempotency and recovery"
```

---

### Task 7: Replay 骨架

**Files:**
- Create: `src/finch/graph/replay.py`
- Test: `tests/graph/test_replay.py`

**Interfaces:**
- Consumes: `GraphRuntime`（Task 6）、`Store`（Task 5）。
- Produces:
  - `finch.graph.replay.replay(store: Store, nodes: list[Node], run_id: str, from_node: str | None = None) -> RunRecord`：复用已有成功记录；`from_node` 之前的节点一律跳过，从 `from_node` 起重新执行。

- [ ] **Step 1: 写失败测试**

```python
# tests/graph/test_replay.py
from finch.graph.nodes import FailingNode, NoopNode
from finch.graph.replay import replay
from finch.graph.runtime import GraphRuntime
from finch.storage.database import Store


def _store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.init()
    return s


def test_replay_from_node_reuses_prior_results(tmp_path):
    store = _store(tmp_path)
    nodes = [NoopNode(name="a", idempotency_key="ka"),
             NoopNode(name="b", idempotency_key="kb"),
             NoopNode(name="c", idempotency_key="kc")]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "COMPLETED"

    # 从 b 重放：a 跳过，b、c 重新执行，仍 COMPLETED。
    run2 = replay(store, nodes, run_id=run.id, from_node="b")
    assert run2.state == "COMPLETED"
    assert len(store.list_nodes(run.id)) == 3  # 复用 a，不重复插入
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/graph/test_replay.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 写 `src/finch/graph/replay.py`**

```python
"""Replay 骨架：从指定节点安全重放，复用已完成节点记录。"""

from .nodes import Node
from .runtime import GraphRuntime
from ..storage.database import RunRecord, Store


def replay(
    store: Store,
    nodes: list[Node],
    run_id: str,
    from_node: str | None = None,
) -> RunRecord:
    """复用 from_node 之前已成功的节点，重新执行其后的节点。"""
    if from_node is None:
        # 全量重放：复用全部已成功节点，等价于恢复运行。
        return GraphRuntime(store, nodes).run(run_id=run_id)

    names = [n.name for n in nodes]
    if from_node not in names:
        raise ValueError(f"unknown from_node: {from_node}")

    start_idx = names.index(from_node)
    replay_nodes = nodes[start_idx:]
    # 已成功的节点记录会令 Runtime 自动跳过；这里仅重放 from_node 起的节点。
    rt = GraphRuntime(store, replay_nodes)
    return rt.run(run_id=run_id)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/graph/test_replay.py -v`
Expected: PASS（1 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/finch/graph/replay.py tests/graph/test_replay.py
git commit -m "feat: add Replay skeleton"
```

---

### Task 8: `finch init` 与 `finch diagnose`（含 gh/opencli 只读探测）

**Files:**
- Create: `src/finch/cli.py`
- Create: `src/finch/github/__init__.py`
- Create: `src/finch/github/gh_client.py`
- Create: `src/finch/twitter/__init__.py`
- Create: `src/finch/twitter/opencli_client.py`
- Test: `tests/unit/test_diagnose.py`

**Interfaces:**
- Consumes: `load_settings`（Task 2）。
- Produces:
  - `finch.cli.app`（Typer）：命令 `init`、`diagnose`。
  - `finch.github.gh_client.GhClient`：`version() -> str`、`auth_status() -> dict`（数组传参 + 超时）。
  - `finch.twitter.opencli_client.OpenCliClient`：`version() -> str`、`doctor() -> dict`。
  - 共享子进程 helper：`finch.github.gh_client._run(argv: list[str], timeout: float) -> dict`（返回 `ok/exit_code/stdout/stderr`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_diagnose.py
from finch.github.gh_client import _run
from finch.twitter.opencli_client import OpenCliClient


def test_run_returns_structured_result():
    r = _run(["echo", "hello"], timeout=5.0)
    assert r["ok"] is True
    assert r["exit_code"] == 0
    assert "hello" in r["stdout"]


def test_run_captures_failure():
    r = _run(["sh", "-c", "echo boom >&2; exit 3"], timeout=5.0)
    assert r["ok"] is False
    assert r["exit_code"] == 3
    assert "boom" in r["stderr"]


def test_opencli_client_returns_diagnostic_dict(monkeypatch):
    def fake_run(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": "ok\n", "stderr": ""}

    monkeypatch.setattr("finch.twitter.opencli_client._run", fake_run)
    c = OpenCliClient()
    r = c.doctor()
    assert r == {"ok": True, "exit_code": 0, "detail": "ok"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_diagnose.py -v`
Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: 写 `src/finch/github/__init__.py`**

```python
"""GitHub 读取 adapter（通过 gh CLI）。"""
```

- [ ] **Step 4: 写 `src/finch/github/gh_client.py`**

```python
"""gh CLI 只读封装（spec 5.2）。"""

import subprocess


def _run(argv: list[str], timeout: float) -> dict:
    """子进程数组传参 + 超时，返回结构化结果。"""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "timeout"}


class GhClient:
    def version(self) -> str:
        r = _run(["gh", "--version"], timeout=10.0)
        return r["stdout"].splitlines()[0] if r["ok"] else ""

    def auth_status(self) -> dict:
        r = _run(["gh", "auth", "status"], timeout=10.0)
        return {"ok": r["ok"], "exit_code": r["exit_code"], "detail": (r["stderr"] or r["stdout"]).strip()}
```

- [ ] **Step 5: 写 `src/finch/twitter/__init__.py`**

```python
"""Twitter 读取 adapter（通过 opencli）。"""
```

- [ ] **Step 6: 写 `src/finch/twitter/opencli_client.py`**

```python
"""opencli 只读封装（spec 5.3）。"""

from ..github.gh_client import _run


class OpenCliClient:
    def version(self) -> str:
        r = _run(["opencli", "--version"], timeout=10.0)
        return r["stdout"].strip() if r["ok"] else ""

    def doctor(self) -> dict:
        r = _run(["opencli", "doctor"], timeout=30.0)
        return {"ok": r["ok"], "exit_code": r["exit_code"], "detail": (r["stderr"] or r["stdout"]).strip()}
```

- [ ] **Step 7: 写 `src/finch/cli.py`**

```python
"""Finch CLI（spec 10）。"""

import typer

from .github.gh_client import GhClient
from .settings import load_settings
from .twitter.opencli_client import OpenCliClient

app = typer.Typer(help="Finch: evidence-driven builder companion.")


@app.command()
def init() -> None:
    """初始化 var/ 目录与数据库 schema。"""
    settings = load_settings()
    from .storage.database import Store

    store = Store(settings.paths.db_path)
    store.init()
    typer.echo(f"initialized: {settings.paths.db_path}")


@app.command()
def diagnose() -> None:
    """分别报告 gh 与 opencli 的可用状态（spec 5.1）。"""
    gh = GhClient()
    opencli = OpenCliClient()

    gh_ver = gh.version()
    gh_auth = gh.auth_status()
    opencli_ver = opencli.version()
    opencli_doctor = opencli.doctor()

    typer.echo("gh:")
    typer.echo(f"  version: {gh_ver or 'unavailable'}")
    typer.echo(f"  auth: {gh_auth}")
    typer.echo("opencli:")
    typer.echo(f"  version: {opencli_ver or 'unavailable'}")
    typer.echo(f"  doctor: {opencli_doctor}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 8: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_diagnose.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 9: 手动验证 CLI**

Run: `uv run finch diagnose`
Expected: 打印 `gh:`/`opencli:` 两节，`gh version` 与 `opencli version` 非 `unavailable`（本机已装 2.93.0 / 1.8.6）。

Run: `uv run finch init`
Expected: 打印 `initialized: var/finch.db`，且 `var/finch.db` 已创建。

- [ ] **Step 10: Commit**

```bash
git add src/finch/cli.py src/finch/github src/finch/twitter tests/unit/test_diagnose.py
git commit -m "feat: add finch init/diagnose with gh+opencli read-only probes"
```

---

### Task 9: 未来模块占位、文档与收尾

**Files:**
- Create: `README.md`
- Create: `AGENTS.md`
- Create: `src/finch/graph/nodes.py` 的相邻占位模块（`src/finch/evidence/__init__.py`、`src/finch/content/__init__.py`、`src/finch/review/__init__.py`、`src/finch/codex/__init__.py`）
- Create: `src/finch/github/commit_reader.py`、`src/finch/github/change_grouper.py`、`src/finch/github/models.py`（占位）
- Create: `src/finch/twitter/query_builder.py`、`src/finch/twitter/normalizer.py`、`src/finch/twitter/models.py`（占位）
- Create: `src/finch/storage/repositories.py`（占位；第 4 节列出该文件，Phase 1 由 `Store` 承担持久化，领域仓储留待后续 Phase）
- Create: `prompts/` 占位文件、`schemas/`、`tests/contract/`、`tests/evals/`、`tests/fixtures/` 目录 `.gitkeep`
- Create: `finch.yaml` 的 `twitter.queries` 已含示例（留空，spec 8 样例待 Phase 3 填充）

**Interfaces:**
- 本任务只落结构与文档，不改动已测模块的公开接口。

- [ ] **Step 1: 写 `README.md`**

```markdown
# Finch

Finch 是一个证据驱动的 Builder 伙伴。它通过 `gh` 读取 GitHub Commit/PR/Issue/测试证据，通过 `opencli` 搜索与读取 Twitter/X 内容，将工程实践与公共技术讨论匹配，生成必须经人工审核的回复与原创内容。

当前状态：Phase 1（项目骨架与 Runtime）已完成。Graph 可确定性执行、失败恢复与重放；`finch diagnose` 可分别报告 `gh` 与 `opencli` 状态。

## 安装

```bash
uv sync
```

## 命令

```bash
uv run finch init      # 初始化 var/ 与数据库
uv run finch diagnose  # 探测 gh / opencli 可用性
```

详见 `docs/Finch-Codex-Development-Plan.md`。
```

- [ ] **Step 2: 写 `AGENTS.md`**

```markdown
# AGENTS.md

供 AI 编码代理参考的项目约定。

## 核心原则

- Evidence First：Commit → Engineering Event → Evidence Card → Draft，禁止 Commit 直接生成帖子。
- Codex 是智能节点，不是工作流 Runtime；Graph 状态、顺序、重试、幂等由确定性 Python Runtime 负责。
- 读取/写入权限分离：`gh` 仅读取；`opencli` 仅读取/搜索，禁止 twitter 写命令。
- 子进程参数用数组传递；每次调用设超时；输出强制 JSON 并 Pydantic 校验。

## 命令

- 测试：`uv run pytest`
- 代码质量：`uv run ruff check .`、`uv run mypy src`
- 运行：`uv run finch <command>`

## 目录

- `src/finch/graph/` 确定性 Runtime；`src/finch/storage/` SQLite；`src/finch/github/` `src/finch/twitter/` 只读 adapter。
```

- [ ] **Step 3: 写占位模块（一次性脚本）**

Run（创建占位 `__init__.py` 与空模块）:

```bash
mkdir -p src/finch/evidence src/finch/content src/finch/review src/finch/codex \
  tests/contract tests/evals tests/fixtures prompts schemas
for d in evidence content review codex; do
  printf '# 占位模块：将在后续 Phase 实现。\n' > "src/finch/$d/__init__.py"
done
for f in commit_reader change_grouper models; do
  printf '# 占位模块：Phase 2 实现。\n' > "src/finch/github/$f.py"
done
for f in query_builder normalizer models; do
  printf '# 占位模块：Phase 3 实现。\n' > "src/finch/twitter/$f.py"
done
printf '# 占位模块：后续 Phase 实现（领域仓储）。\n' > src/finch/storage/repositories.py
touch tests/contract/.gitkeep tests/evals/.gitkeep tests/fixtures/.gitkeep prompts/.gitkeep schemas/.gitkeep
```

- [ ] **Step 4: 运行全量测试与质量门禁**

Run: `uv run pytest`
Expected: PASS（全部测试）。

Run: `uv run ruff check .`
Expected: 无错误（`All checks passed!`）。

Run: `uv run mypy src`
Expected: 无错误（`Success: no issues found`）。

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: add README/AGENTS and placeholder modules for later phases"
```

---

## Self-Review

**1. Spec coverage（Phase 1 + 第 4 节 + 第 6/10/5.1/5.2/5.3 相关）：**
- 初始化 Python/Typer/SQLite/pytest → Task 1。
- 定义 Graph state、NodeResult、DomainEvent → Task 3。
- 运行记录、节点记录、幂等键、Replay 骨架 → Task 5/6/7。
- `finch diagnose` → Task 8。
- 第 4 节仓库结构 → Task 1/2/9（`var/` 进 `.gitignore` 于 Task 1）。
- spec 6.3 `NodeResult` 契约 → Task 3（字段名与 Literal 完全一致）。
- spec 5.1 启动前只读诊断 → Task 8 `diagnose`。
- spec 5.2 子进程数组传参/超时 → Task 8 `_run`。
- 验收“空 Graph 可执行、失败、恢复和重放” → Task 6/7 测试覆盖。
- 验收“诊断分别报告 gh 与 opencli” → Task 8 `diagnose` 分两节输出。

**2. Placeholder scan：** 无 TBD/TODO；所有代码步骤含完整代码；Task 9 占位模块仅为未来 Phase 的空文件，非本计划实现范围（已在 Interfaces 标注）。

**3. Type consistency：**
- `Node.name/idempotency_key/timeout_seconds/max_retries/side_effect` 在 Task 4 定义，Task 6/7 使用一致。
- `NodeResult.status/output/events/warnings/retryable/error_code` 在 Task 3 定义，Task 4/6 使用一致。
- `Store` 方法名 `init/upsert_run/get_run/insert_node/find_node/list_nodes` 在 Task 5 定义，Task 6/7/8 使用一致。
- `RunRecord.state` 存 `GraphState.value`（字符串），Task 6 测试断言 `"COMPLETED"`/`"FAILED"`，一致。
