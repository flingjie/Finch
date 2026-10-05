# Learning Loop (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire learning into connection by adding two entities — `ActiveProblem` (≤3 open) and `PracticeAttempt` (problem/attempt/observation/unknown) — and threading them into idea-discovery, draft writing, and weekly reflection.

**Architecture:** Deterministic domain services, no orchestrator. `ActiveProblem` and `PracticeAttempt` are Workspace-persisted entities with content-addressed ids. `idea-discovery` gains an `--attempt` source that maps attempt fields onto `ContentJob` with evidence split (observation→`facts`/`observed`, unknown→`limitations`). Drafts gain a `JUDGMENT_SHIFT` content type plus writer-context fields. `finch weekly` reads three learning-loop signals deterministically.

**Tech Stack:** Python 3.12, Pydantic 2, Typer, pytest, ruff (E,F,I,B,UP, line-length 100), mypy.

## Global Constraints

- Python 3.12+; Pydantic 2 models (`Literal`/`Field`); models serialize to YAML under `var/` via `Workspace` helpers.
- Domain services are deterministic and single-threaded; no `asyncio.gather`; no new LLM agent loop.
- Evidence first: `gh`/`opencli` stay read-only; nothing auto-publishes. Never mark unverified as observed.
- Subprocess args as arrays (Phase 1 adds no new subprocess calls; existing `CodexRunner` is reused).
- Bilingual (Chinese/English) docstrings; match surrounding file.
- Commands: `uv run pytest`, `uv run ruff check .`, `uv run mypy src` must stay green.
- Persistence is the file workspace (`Workspace`); no database. New fields default `None` so existing YAML loads unchanged.

---

### Task 1: `ActiveProblem` entity + repository + service

**Files:**
- Create: `src/finch/problems/__init__.py`
- Create: `src/finch/problems/models.py`
- Create: `src/finch/problems/service.py`
- Modify: `src/finch/storage/repositories.py` (add `ProblemRepository` + import)
- Test: `tests/unit/test_problems.py`

**Interfaces:**
- Consumes: `Workspace` (`_write`/`_read`/`_list_all` helpers in `storage/repositories.py`).
- Produces:
  - `ActiveProblem` (fields: `id, title, why_it_matters, status, attempt_ids, closed_reason, created_at, updated_at, closed_at`)
  - `problem_id_for(title: str) -> str`
  - `ProblemService(problems: ProblemRepository)` with `add(*, title, why_it_matters="")`, `list(*, status=None)`, `show(problem_id)`, `close(problem_id, *, reason="")`, `append_attempt(problem_id, attempt_id)`
  - `ProblemRepository` with `upsert`, `get`, `list_all`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_problems.py`:

```python
"""ProblemService：add / list / show / close / append_attempt；≤3 open 不变式。"""

from finch.problems.service import ProblemService
from finch.storage.repositories import ProblemRepository
from finch.storage.workspace import Workspace


def _service(tmp_path):
    return ProblemService(ProblemRepository(Workspace(tmp_path)))


def test_add_and_show(tmp_path):
    svc = _service(tmp_path)
    p = svc.add(title="Agent 重试何时值得？")
    assert p.status == "open"
    assert p.id.startswith("problem_")
    assert svc.show(p.id).title == "Agent 重试何时值得？"


def test_add_is_idempotent_by_title(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(title="Agent 重试何时值得？")
    b = svc.add(title="Agent 重试何时值得？")
    assert a.id == b.id
    assert len(svc.list()) == 1


def test_add_rejects_fourth_open_problem(tmp_path):
    svc = _service(tmp_path)
    for i in range(3):
        svc.add(title=f"问题 {i}")
    try:
        svc.add(title="问题 3")
    except ValueError as exc:
        assert "max 3" in str(exc)
        return
    raise AssertionError("expected ValueError on 4th open problem")


def test_close_then_add_allowed(tmp_path):
    svc = _service(tmp_path)
    for i in range(3):
        svc.add(title=f"问题 {i}")
    first = svc.list()[0]
    closed = svc.close(first.id, reason="验证完了")
    assert closed.status == "closed"
    assert closed.closed_reason == "验证完了"
    assert closed.closed_at is not None
    svc.add(title="问题 4")  # now allowed


def test_list_filter_by_status(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(title="问题 A")
    svc.close(a.id)
    svc.add(title="问题 B")
    assert {p.status for p in svc.list(status="open")} == {"open"}
    assert svc.list(status="open")[0].title == "问题 B"


def test_append_attempt_is_idempotent(tmp_path):
    svc = _service(tmp_path)
    p = svc.add(title="问题 A")
    p = svc.append_attempt(p.id, "attempt_1")
    p = svc.append_attempt(p.id, "attempt_1")
    assert p.attempt_ids == ["attempt_1"]


def test_show_missing_raises(tmp_path):
    svc = _service(tmp_path)
    try:
        svc.show("problem_nope")
    except KeyError:
        return
    raise AssertionError("expected KeyError")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_problems.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'finch.problems'`.

- [ ] **Step 3: Write the model**

Create `src/finch/problems/__init__.py`:

```python
"""problems 领域：活跃问题（学习闭环的脊柱）。"""
```

Create `src/finch/problems/models.py`:

```python
"""ActiveProblem 领域模型：活跃问题（≤3 open，学习闭环的脊柱）。"""

import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ActiveProblem(BaseModel):
    """一个正在研究的活跃问题。open 数量 ≤ 3，在 ``ProblemService.add`` 强制。"""

    id: str
    title: str
    why_it_matters: str = ""
    status: Literal["open", "closed"] = "open"
    attempt_ids: list[str] = Field(default_factory=list)
    closed_reason: str = ""
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None


def problem_id_for(title: str) -> str:
    """内容寻址 id：相同 title → 相同 id（幂等）。"""
    return f"problem_{hashlib.sha256(title.strip().encode('utf-8')).hexdigest()[:8]}"
```

- [ ] **Step 4: Add the repository**

Modify `src/finch/storage/repositories.py`. Add the import near the other model imports (after `from finch.peers.models import PeerProfile`):

```python
from finch.problems.models import ActiveProblem
```

Add the class (after `PracticeSessionRepository`, around line 376):

```python
class ProblemRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, problem: ActiveProblem) -> None:
        _write(self.ws, "problems", problem.id, problem)

    def get(self, problem_id: str) -> ActiveProblem | None:
        return _read(self.ws, "problems", problem_id, ActiveProblem)

    def list_all(self) -> list[ActiveProblem]:
        return _list_all(self.ws, "problems", ActiveProblem)
```

- [ ] **Step 5: Write the service**

Create `src/finch/problems/service.py`:

```python
"""ProblemService：活跃问题状态机（open → closed；≤3 open）。"""

from datetime import UTC, datetime

from finch.problems.models import ActiveProblem, problem_id_for
from finch.storage.repositories import ProblemRepository


class ProblemService:
    """驱动活跃问题生命周期；不调用 LLM、不访问 DB（依赖注入 repository）。"""

    def __init__(self, problems: ProblemRepository) -> None:
        self.problems = problems

    def add(self, *, title: str, why_it_matters: str = "") -> ActiveProblem:
        """新建（幂等）；open 数 ≥ 3 时拒绝，提示先 close 一个。"""
        pid = problem_id_for(title)
        existing = self.problems.get(pid)
        if existing is not None:
            return existing
        if sum(1 for p in self.problems.list_all() if p.status == "open") >= 3:
            raise ValueError("too many open problems (max 3); close one first")
        now = datetime.now(UTC)
        problem = ActiveProblem(
            id=pid,
            title=title.strip(),
            why_it_matters=why_it_matters,
            status="open",
            created_at=now,
            updated_at=now,
        )
        self.problems.upsert(problem)
        return problem

    def list(self, *, status: str | None = None) -> list[ActiveProblem]:
        problems = self.problems.list_all()
        if status is None or status == "all":
            return problems
        return [p for p in problems if p.status == status]

    def show(self, problem_id: str) -> ActiveProblem:
        problem = self.problems.get(problem_id)
        if problem is None:
            raise KeyError(problem_id)
        return problem

    def close(self, problem_id: str, *, reason: str = "") -> ActiveProblem:
        """open → closed（幂等）；记 closed_reason + closed_at。"""
        problem = self.show(problem_id)
        if problem.status == "closed":
            return problem
        now = datetime.now(UTC)
        problem = problem.model_copy(
            update={
                "status": "closed",
                "closed_reason": reason,
                "closed_at": now,
                "updated_at": now,
            }
        )
        self.problems.upsert(problem)
        return problem

    def append_attempt(self, problem_id: str, attempt_id: str) -> ActiveProblem:
        """append-only 回链 PracticeAttempt；重复 id 不追加。"""
        problem = self.show(problem_id)
        if attempt_id in problem.attempt_ids:
            return problem
        problem = problem.model_copy(
            update={
                "attempt_ids": [*problem.attempt_ids, attempt_id],
                "updated_at": datetime.now(UTC),
            }
        )
        self.problems.upsert(problem)
        return problem
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_problems.py -q`
Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add src/finch/problems/ src/finch/storage/repositories.py tests/unit/test_problems.py
git commit -m "feat(problems): add ActiveProblem entity with <=3 open invariant"
```

---

### Task 2: `PracticeAttempt` entity + repository + service

**Files:**
- Create: `src/finch/practice/attempts.py` (model only — no repository import, avoids circular import)
- Create: `src/finch/practice/attempts_service.py`
- Modify: `src/finch/storage/repositories.py` (add `PracticeAttemptRepository` + import)
- Test: `tests/unit/test_attempts.py`

**Interfaces:**
- Consumes: `ProblemRepository` (from Task 1), `Workspace` helpers.
- Produces:
  - `PracticeAttempt` (fields: `id, problem_id, problem, attempt, observation, unknown, next_step, result, status, source_refs, created_at, updated_at`)
  - `attempt_id_for(problem, attempt, observation) -> str`
  - `PracticeAttemptService(attempts, problems)` with `add(*, problem_id=None, problem, attempt, observation, unknown="", next_step="", source_refs=None)`, `list(*, status=None)`, `show(attempt_id)`, `verify(attempt_id, *, result)`, `close(attempt_id)`
  - `PracticeAttemptRepository` with `upsert`, `get`, `list_all`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_attempts.py`:

```python
"""PracticeAttemptService：add / verify / close / problem 回链。"""

from finch.practice.attempts_service import PracticeAttemptService
from finch.storage.repositories import PracticeAttemptRepository, ProblemRepository
from finch.storage.workspace import Workspace


def _service(tmp_path):
    ws = Workspace(tmp_path)
    return PracticeAttemptService(
        PracticeAttemptRepository(ws), ProblemRepository(ws)
    )


def test_add_attempt(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(problem="重试何时值得", attempt="加重试", observation="多数失败卡在输入",
                unknown="阈值怎么定", next_step="测 3 个案例")
    assert a.status == "open"
    assert a.id.startswith("attempt_")
    assert a.unknown == "阈值怎么定"


def test_add_is_idempotent(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(problem="P", attempt="T", observation="O")
    b = svc.add(problem="P", attempt="T", observation="O")
    assert a.id == b.id
    assert len(svc.list()) == 1


def test_verify_sets_result(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(problem="P", attempt="T", observation="O")
    v = svc.verify(a.id, result="3 个案例里 2 个重试有效")
    assert v.status == "verified"
    assert v.result == "3 个案例里 2 个重试有效"


def test_verify_requires_open(tmp_path):
    svc = _service(tmp_path)
    a = svc.add(problem="P", attempt="T", observation="O")
    svc.verify(a.id, result="r")
    try:
        svc.verify(a.id, result="again")
    except ValueError as exc:
        assert "verified" in str(exc)
        return
    raise AssertionError("expected ValueError on non-open verify")


def test_add_with_problem_id_links_attempt(tmp_path):
    ws = Workspace(tmp_path)
    from finch.problems.service import ProblemService

    problems = ProblemRepository(ws)
    p = ProblemService(problems).add(title="重试何时值得")
    svc = PracticeAttemptService(PracticeAttemptRepository(ws), problems)
    a = svc.add(problem_id=p.id, problem="重试何时值得", attempt="T", observation="O")
    linked = problems.get(p.id)
    assert linked.attempt_ids == [a.id]


def test_add_with_missing_problem_raises(tmp_path):
    svc = _service(tmp_path)
    try:
        svc.add(problem_id="problem_nope", problem="P", attempt="T", observation="O")
    except ValueError as exc:
        assert "problem not found" in str(exc)
        return
    raise AssertionError("expected ValueError on missing problem")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_attempts.py -q`
Expected: `ModuleNotFoundError: No module named 'finch.practice.attempts_service'`.

- [ ] **Step 3: Write the model**

Create `src/finch/practice/attempts.py`:

```python
"""PracticeAttempt 领域模型：实践尝试原始素材（问题/尝试/观察/未知/下一步/结果）。

独立于表达训练的 ``PracticeSession`` 与证据库 ``PracticeItem``；CLI 用 ``finch attempts``。
本文件只放模型，不 import repository（避免与 ``storage/repositories.py`` 循环导入）。
"""

import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class PracticeAttempt(BaseModel):
    """一次实践尝试：问题 → 尝试 → 观察 → 未知 → 下一步；有结果时在 verify 回填。

    ``observation`` 是实际观察（可 ``observed``）；``unknown`` / ``next_step`` 明确是
    待验证（``unverified``）——这条证据边界是 idea 提炼时「不把读到的方法写成亲历」的依据。
    """

    id: str
    problem_id: str | None = None
    problem: str
    attempt: str
    observation: str
    unknown: str = ""
    next_step: str = ""
    result: str = ""
    status: Literal["open", "verified", "closed"] = "open"
    source_refs: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


def attempt_id_for(problem: str, attempt: str, observation: str) -> str:
    """内容寻址 id：相同 (problem+attempt+observation) → 相同 id（幂等）。"""
    raw = "\n".join([problem, attempt, observation])
    return f"attempt_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]}"
```

- [ ] **Step 4: Add the repository**

Modify `src/finch/storage/repositories.py`. Add import:

```python
from finch.practice.attempts import PracticeAttempt
```

Add the class (after `ProblemRepository`):

```python
class PracticeAttemptRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def upsert(self, attempt: PracticeAttempt) -> None:
        _write(self.ws, "attempts", attempt.id, attempt)

    def get(self, attempt_id: str) -> PracticeAttempt | None:
        return _read(self.ws, "attempts", attempt_id, PracticeAttempt)

    def list_all(self) -> list[PracticeAttempt]:
        return _list_all(self.ws, "attempts", PracticeAttempt)
```

- [ ] **Step 5: Write the service**

Create `src/finch/practice/attempts_service.py`:

```python
"""PracticeAttemptService：实践尝试状态机（open → verified / closed）。"""

from datetime import UTC, datetime

from finch.practice.attempts import PracticeAttempt, attempt_id_for
from finch.storage.repositories import PracticeAttemptRepository, ProblemRepository


class PracticeAttemptService:
    """驱动实践尝试生命周期；确定性状态转换，不调用 LLM。"""

    def __init__(
        self,
        attempts: PracticeAttemptRepository,
        problems: ProblemRepository,
    ) -> None:
        self.attempts = attempts
        self.problems = problems

    def add(
        self,
        *,
        problem_id: str | None = None,
        problem: str,
        attempt: str,
        observation: str,
        unknown: str = "",
        next_step: str = "",
        source_refs: list[str] | None = None,
    ) -> PracticeAttempt:
        """新建（幂等）；给 problem_id 时回链到 ActiveProblem.attempt_ids。"""
        if problem_id is not None and self.problems.get(problem_id) is None:
            raise ValueError(f"problem not found: {problem_id}")
        aid = attempt_id_for(problem, attempt, observation)
        existing = self.attempts.get(aid)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        obj = PracticeAttempt(
            id=aid,
            problem_id=problem_id,
            problem=problem.strip(),
            attempt=attempt,
            observation=observation,
            unknown=unknown,
            next_step=next_step,
            source_refs=list(source_refs or []),
            created_at=now,
            updated_at=now,
        )
        self.attempts.upsert(obj)
        if problem_id is not None:
            self._append_attempt(problem_id, aid)
        return obj

    def list(self, *, status: str | None = None) -> list[PracticeAttempt]:
        attempts = self.attempts.list_all()
        if status is None or status == "all":
            return attempts
        return [a for a in attempts if a.status == status]

    def show(self, attempt_id: str) -> PracticeAttempt:
        attempt = self.attempts.get(attempt_id)
        if attempt is None:
            raise KeyError(attempt_id)
        return attempt

    def verify(self, attempt_id: str, *, result: str) -> PracticeAttempt:
        """open → verified，回填 result；唯一把未验证标为已验证的路径。"""
        attempt = self.show(attempt_id)
        if attempt.status != "open":
            raise ValueError(
                f"illegal transition: attempt {attempt_id} in status {attempt.status}; "
                "only open -> verified is legal"
            )
        attempt = attempt.model_copy(
            update={"status": "verified", "result": result, "updated_at": datetime.now(UTC)}
        )
        self.attempts.upsert(attempt)
        return attempt

    def close(self, attempt_id: str) -> PracticeAttempt:
        """置 closed（弃置一条素材）；幂等。"""
        attempt = self.show(attempt_id)
        if attempt.status == "closed":
            return attempt
        attempt = attempt.model_copy(
            update={"status": "closed", "updated_at": datetime.now(UTC)}
        )
        self.attempts.upsert(attempt)
        return attempt

    def _append_attempt(self, problem_id: str, attempt_id: str) -> None:
        problem = self.problems.get(problem_id)
        if problem is None or attempt_id in problem.attempt_ids:
            return
        problem = problem.model_copy(
            update={
                "attempt_ids": [*problem.attempt_ids, attempt_id],
                "updated_at": datetime.now(UTC),
            }
        )
        self.problems.upsert(problem)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_attempts.py -q`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add src/finch/practice/attempts.py src/finch/practice/attempts_service.py \
        src/finch/storage/repositories.py tests/unit/test_attempts.py
git commit -m "feat(attempts): add PracticeAttempt entity feeding idea-discovery"
```

---

### Task 3: CLI `finch problems` + `finch attempts`

**Files:**
- Modify: `src/finch/cli.py` (register two typer apps + commands)
- Test: `tests/unit/test_cli_problems_attempts.py`

**Interfaces:**
- Consumes: `ProblemService`, `ProblemRepository` (Task 1); `PracticeAttemptService`, `PracticeAttemptRepository` (Task 2); existing `load_settings`, `Workspace`, `typer` in `cli.py`.
- Produces: CLI commands `problems add/list/show/close`, `attempts add/list/show/verify/close`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_problems_attempts.py`:

```python
"""CLI：finch problems / finch attempts 命令的落库与守卫。"""

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings


def _patch(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))


def test_problems_add_and_list(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["problems", "add", "--title", "重试何时值得"])
    assert r.exit_code == 0, r.output
    assert "problem_" in r.output
    r = CliRunner().invoke(app, ["problems", "list"])
    assert "重试何时值得" in r.output


def test_problems_rejects_fourth(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    for i in range(3):
        CliRunner().invoke(app, ["problems", "add", "--title", f"问题 {i}"])
    r = CliRunner().invoke(app, ["problems", "add", "--title", "问题 3"])
    assert r.exit_code == 1
    assert "max 3" in r.output


def test_attempts_add_links_problem(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["problems", "add", "--title", "重试"])
    pid = r.output.strip().split()[2]
    r = CliRunner().invoke(
        app,
        ["attempts", "add", "--problem-id", pid, "--problem", "重试",
         "--attempt", "加重试", "--observation", "多数卡输入"],
    )
    assert r.exit_code == 0, r.output
    assert "attempt_" in r.output


def test_attempts_verify(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(
        app,
        ["attempts", "add", "--problem", "P", "--attempt", "T", "--observation", "O"],
    )
    aid = r.output.strip().split()[2]
    r = CliRunner().invoke(app, ["attempts", "verify", aid, "--result", "有效"])
    assert r.exit_code == 0, r.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_problems_attempts.py -q`
Expected: FAIL — `finch problems` subcommand not registered (exit code 2 / no such command).

- [ ] **Step 3: Register the typer apps**

Modify `src/finch/cli.py`. After the `practice_app` registration (`app.add_typer(practice_app, name="practice")` around line 199), add:

```python
problems_app = typer.Typer(help="活跃问题（≤3 open；学习闭环的脊柱）")
app.add_typer(problems_app, name="problems")

attempts_app = typer.Typer(help="实践尝试原始素材（问题/尝试/观察/未知）")
app.add_typer(attempts_app, name="attempts")
```

- [ ] **Step 4: Add the command functions**

Append the command functions (near the `practice_app` command functions at the end of `cli.py`):

```python
@problems_app.command("add")
def problems_add(
    title: str = typer.Option(..., "--title", help="一句话问题"),
    why: str = typer.Option("", "--why", help="为什么值得追"),
) -> None:
    """新建活跃问题（open 数 ≤ 3）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    from finch.problems.service import ProblemService

    try:
        problem = ProblemService(ProblemRepository(ws)).add(title=title, why_it_matters=why)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(f"added problem {problem.id} ({problem.status.value}): {problem.title}")


@problems_app.command("list")
def problems_list(
    status: str = typer.Option(None, "--status", help="open | closed | all"),
) -> None:
    """列出活跃问题（默认 open）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    from finch.problems.service import ProblemService

    for p in ProblemService(ProblemRepository(ws)).list(status=status):
        marker = f"[{p.status.value}]"
        typer.echo(f"{p.id} {marker} {p.title}")


@problems_app.command("show")
def problems_show(problem_id: str = typer.Argument(...)) -> None:
    """显示单条问题 + 其 attempt_ids。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    from finch.problems.service import ProblemService

    try:
        p = ProblemService(ProblemRepository(ws)).show(problem_id)
    except KeyError:
        typer.echo(f"problem not found: {problem_id}")
        raise typer.Exit(code=1)
    typer.echo(f"{p.id} ({p.status.value}) {p.title}")
    if p.why_it_matters:
        typer.echo(f"why: {p.why_it_matters}")
    if p.attempt_ids:
        typer.echo("attempts: " + ", ".join(p.attempt_ids))


@problems_app.command("close")
def problems_close(
    problem_id: str = typer.Argument(...),
    reason: str = typer.Option("", "--reason", help="关闭原因"),
) -> None:
    """关闭一个活跃问题，释放 open 名额。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    from finch.problems.service import ProblemService

    try:
        p = ProblemService(ProblemRepository(ws)).close(problem_id, reason=reason)
    except KeyError:
        typer.echo(f"problem not found: {problem_id}")
        raise typer.Exit(code=1)
    typer.echo(f"closed problem {p.id}")


@attempts_app.command("add")
def attempts_add(
    problem_id: str = typer.Option(None, "--problem-id", help="回链的活跃问题 id（可选）"),
    problem: str = typer.Option(..., "--problem", help="当前问题（一句话）"),
    attempt: str = typer.Option(..., "--attempt", help="尝试了什么"),
    observation: str = typer.Option(..., "--observation", help="实际观察到了什么"),
    unknown: str = typer.Option("", "--unknown", help="未知/卡点"),
    next_step: str = typer.Option("", "--next-step", help="下一步准备验证"),
    ref: list[str] = typer.Option([], "--ref", help="溯源 URL（可重复）"),
) -> None:
    """新建实践尝试；给 --problem-id 时回链到对应问题。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    from finch.practice.attempts_service import PracticeAttemptService

    svc = PracticeAttemptService(PracticeAttemptRepository(ws), ProblemRepository(ws))
    try:
        a = svc.add(
            problem_id=problem_id,
            problem=problem,
            attempt=attempt,
            observation=observation,
            unknown=unknown,
            next_step=next_step,
            source_refs=list(ref),
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(f"added attempt {a.id} ({a.status.value}): {a.problem}")


@attempts_app.command("list")
def attempts_list(
    status: str = typer.Option(None, "--status", help="open | verified | closed | all"),
) -> None:
    """列出实践尝试（默认 open）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    from finch.practice.attempts_service import PracticeAttemptService

    svc = PracticeAttemptService(PracticeAttemptRepository(ws), ProblemRepository(ws))
    for a in svc.list(status=status):
        typer.echo(f"{a.id} [{a.status.value}] {a.problem} → {a.observation[:60]}")


@attempts_app.command("show")
def attempts_show(attempt_id: str = typer.Argument(...)) -> None:
    """显示单条实践尝试。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    from finch.practice.attempts_service import PracticeAttemptService

    svc = PracticeAttemptService(PracticeAttemptRepository(ws), ProblemRepository(ws))
    try:
        a = svc.show(attempt_id)
    except KeyError:
        typer.echo(f"attempt not found: {attempt_id}")
        raise typer.Exit(code=1)
    typer.echo(f"{a.id} ({a.status.value}) problem: {a.problem}")
    typer.echo(f"attempt: {a.attempt}")
    typer.echo(f"observation: {a.observation}")
    if a.unknown:
        typer.echo(f"unknown: {a.unknown}")
    if a.next_step:
        typer.echo(f"next_step: {a.next_step}")
    if a.result:
        typer.echo(f"result: {a.result}")


@attempts_app.command("verify")
def attempts_verify(
    attempt_id: str = typer.Argument(...),
    result: str = typer.Option(..., "--result", help="验证结果"),
) -> None:
    """open → verified，回填结果。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    from finch.practice.attempts_service import PracticeAttemptService

    svc = PracticeAttemptService(PracticeAttemptRepository(ws), ProblemRepository(ws))
    try:
        a = svc.verify(attempt_id, result=result)
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(f"verified attempt {a.id}")


@attempts_app.command("close")
def attempts_close(attempt_id: str = typer.Argument(...)) -> None:
    """置 closed（弃置一条素材）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    from finch.practice.attempts_service import PracticeAttemptService

    svc = PracticeAttemptService(PracticeAttemptRepository(ws), ProblemRepository(ws))
    try:
        a = svc.close(attempt_id)
    except KeyError:
        typer.echo(f"attempt not found: {attempt_id}")
        raise typer.Exit(code=1)
    typer.echo(f"closed attempt {a.id}")
```

Note: `ProblemRepository` and `PracticeAttemptRepository` must be imported in `cli.py`; check the existing import block and add `from .storage.repositories import ProblemRepository, PracticeAttemptRepository` alongside the other repository imports if not already present.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli_problems_attempts.py -q`
Expected: 4 passed.

- [ ] **Step 6: Run the CLI smoke test**

Run:
```bash
uv run finch problems add --title "测试问题" && uv run finch problems list
uv run finch attempts add --problem "测试问题" --attempt "试" --observation "观察"
```
Expected: prints the new `problem_*` and `attempt_*` ids.

- [ ] **Step 7: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_problems_attempts.py
git commit -m "feat(cli): add finch problems and finch attempts sub-apps"
```

---

### Task 4: Shared model extensions (enums + ContentJob fields + create_candidate threading)

**Files:**
- Modify: `src/finch/content/jobs.py` (`SourceKind` Literal + `ContentJob.attempt_id`/`problem_id`)
- Modify: `src/finch/ideas/models.py` (`SourceRef.type` Literal + `IdeaCandidate.content_type`/`attempt_id`/`problem_id`)
- Modify: `src/finch/ideas/service.py` (`create_candidate` copies `content_type`/`attempt_id`/`problem_id`)
- Test: `tests/unit/test_idea_attempt_fields.py`

**Interfaces:**
- Consumes: `ContentType` (exists in `content/models.py`), `SourceKind`, `SourceRef`.
- Produces: `SourceKind` includes `"attempt"`; `SourceRef.type` includes `"attempt"`; `ContentJob` has `attempt_id: str | None`, `problem_id: str | None`; `IdeaCandidate` has `content_type: ContentType | None`, `attempt_id: str | None`, `problem_id: str | None`; `IdeaService.create_candidate` persists all three from the candidate.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_idea_attempt_fields.py`:

```python
"""idea 落库把 content_type / attempt_id / problem_id 从候选透传到 ContentJob。"""

from finch.content.jobs import AuthorPosition, SourceKind
from finch.content.models import ContentType, RecommendedFormat
from finch.ideas.models import IdeaCandidate, IdeaGenerator, SourceRef
from finch.ideas.service import IdeaService
from finch.storage.repositories import ContentJobRepository
from finch.storage.workspace import Workspace


def _candidate(**kw) -> IdeaCandidate:
    base = dict(
        id="idea_x",
        origin="practice",
        core_point="重试只在输入不变时值得",
        reader_problem="读者在纠结何时加重试",
        why_worth_saying="给一个可判断的条件",
        author_position=AuthorPosition(
            claim="重试只在输入不变时值得", decision="先判输入是否稳定", tradeoff="多一次调用"
        ),
        source_refs=[SourceRef(type="attempt", ref="attempt_abc", summary="一次尝试")],
        generator=IdeaGenerator(skill="idea-discovery", version="1.0.0"),
        source_kind="attempt",
    )
    base.update(kw)
    return IdeaCandidate(**base)


def test_source_kind_and_ref_accept_attempt():
    assert "attempt" in SourceKind.__args__


def test_create_candidate_threads_attempt_fields(tmp_path):
    ws = Workspace(tmp_path)
    cand = _candidate(
        content_type=ContentType.JUDGMENT_SHIFT,
        attempt_id="attempt_abc",
        problem_id="problem_def",
    )
    job = IdeaService(ContentJobRepository(ws)).create_candidate(cand)
    assert job.content_type == ContentType.JUDGMENT_SHIFT
    assert job.attempt_id == "attempt_abc"
    assert job.problem_id == "problem_def"


def test_create_candidate_defaults_none(tmp_path):
    ws = Workspace(tmp_path)
    job = IdeaService(ContentJobRepository(ws)).create_candidate(_candidate())
    assert job.content_type is None
    assert job.attempt_id is None
    assert job.problem_id is None
```

Note: `ContentType.JUDGMENT_SHIFT` is added in Task 5. To keep this task self-contained, either (a) add `JUDGMENT_SHIFT` to the enum in this task instead, or (b) assert `job.content_type is None` here and add the `JUDGMENT_SHIFT` assertion in Task 5. Use option (a): add the `JUDGMENT_SHIFT` enum member in this task (it is a one-line `ContentType` addition), and Task 5 only wires the checker suite + writer context.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_idea_attempt_fields.py -q`
Expected: FAIL — `IdeaCandidate` has no `content_type`/`attempt_id`/`problem_id` (validation error).

- [ ] **Step 3: Extend `SourceKind` and `ContentJob`**

Modify `src/finch/content/jobs.py`:

```python
SourceKind = Literal["note", "commit", "log", "artifact", "post", "conversation", "attempt"]
```

Add two fields to `ContentJob` after `method_version_hash: str = ""`:

```python
    # ---- Learning loop 回链 ----
    attempt_id: str | None = None
    problem_id: str | None = None
```

- [ ] **Step 4: Extend `ContentType`, `SourceRef`, `IdeaCandidate`**

Modify `src/finch/content/models.py`, add to `ContentType`:

```python
    JUDGMENT_SHIFT = "judgment_shift"
```

Modify `src/finch/ideas/models.py`:
- Change the import `from finch.content.models import RecommendedFormat` to `from finch.content.models import ContentType, RecommendedFormat`.
- Add `"attempt"` to `SourceRef.type` Literal.
- Add to `IdeaCandidate` after `method_version_hash: str = ""`:

```python
    content_type: ContentType | None = None
    attempt_id: str | None = None
    problem_id: str | None = None
```

- [ ] **Step 5: Thread through `create_candidate`**

Modify `src/finch/ideas/service.py`, in `create_candidate`, add to the `ContentJob(...)` construction (after `method_version_hash=idea.method_version_hash`):

```python
            content_type=idea.content_type,
            attempt_id=idea.attempt_id,
            problem_id=idea.problem_id,
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_idea_attempt_fields.py -q`
Expected: 3 passed.

- [ ] **Step 7: Run the full suite + lint + typecheck**

Run:
```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```
Expected: all green. If mypy flags the new `Literal` union or enum, fix the annotation to match existing style.

- [ ] **Step 8: Commit**

```bash
git add src/finch/content/jobs.py src/finch/content/models.py src/finch/ideas/models.py \
        src/finch/ideas/service.py tests/unit/test_idea_attempt_fields.py
git commit -m "feat(ideas): thread attempt_id/problem_id/content_type into ContentJob"
```

---

### Task 5: `FragmentService.from_attempt` + `--attempt` CLI + skill reference

**Files:**
- Modify: `src/finch/ideas/fragment_service.py` (add `_ATTEMPT_PROMPT`, `from_attempt`, extend `_to_candidate` and `IdeaDraftOutput`)
- Modify: `src/finch/cli.py` (`ideas_create` gains `--attempt`)
- Create: `skills/idea-discovery/references/attempt-signals.md`
- Test: `tests/unit/test_from_attempt.py`

**Interfaces:**
- Consumes: `PracticeAttempt` (Task 2), `IdeaDraftOutput`/`_to_candidate` (existing), `PracticeAttemptRepository` (Task 2), `ContentType` (Task 4).
- Produces: `FragmentService.from_attempt(attempt: PracticeAttempt) -> IdeaCandidate` with `origin="practice"`, `source_kind="attempt"`, `evidence_status="observed"`, `attempt_id`/`problem_id` set, and `content_type` from LLM detection.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_from_attempt.py`:

```python
"""FragmentService.from_attempt：把实践尝试提炼为 idea，证据分列。"""

from finch.content.models import ContentType
from finch.ideas.fragment_service import FragmentService, IdeaDraftOutput
from finch.practice.attempts import PracticeAttempt
from datetime import UTC, datetime


class FakeRunner:
    def __init__(self, out: IdeaDraftOutput):
        self.out = out

    def run(self, prompt, output_model, **kw):
        assert "## Practice attempt" in prompt
        return self.out


def _attempt() -> PracticeAttempt:
    return PracticeAttempt(
        id="attempt_abc",
        problem_id="problem_def",
        problem="重试何时值得",
        attempt="给调用加重试",
        observation="多数失败卡在输入没变",
        unknown="重试阈值怎么定",
        next_step="测 3 个案例",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_from_attempt_observed_and_unknown_split():
    out = IdeaDraftOutput(
        core_point="重试只在输入不变时值得",
        observation="多数失败卡在输入没变",
        reader_problem="读者纠结何时加重试",
        why_worth_saying="给出可判断的条件",
        intent="stance",
        author_position=__import__("finch.content.jobs", fromlist=["AuthorPosition"]).AuthorPosition(
            claim="重试只在输入不变时值得", decision="先判输入稳定", tradeoff="多一次调用"
        ),
        source_kind="attempt",
        facts=["多数失败卡在输入没变"],
        evidence_status="observed",
        content_type=ContentType.JUDGMENT_SHIFT,
    )
    idea = FragmentService(FakeRunner(out)).from_attempt(_attempt())
    assert idea.origin == "practice"
    assert idea.source_kind == "attempt"
    assert idea.evidence_status == "observed"
    assert idea.attempt_id == "attempt_abc"
    assert idea.problem_id == "problem_def"
    assert idea.content_type == ContentType.JUDGMENT_SHIFT
    assert "重试阈值怎么定" in idea.boundaries.unknown
    assert "测 3 个案例" in idea.boundaries.unknown
    assert any(r.type == "attempt" for r in idea.source_refs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_from_attempt.py -q`
Expected: `AttributeError: 'FragmentService' object has no attribute 'from_attempt'`.

- [ ] **Step 3: Extend `IdeaDraftOutput` and `_to_candidate`**

Modify `src/finch/ideas/fragment_service.py`. Change the import `from finch.content.models import RecommendedFormat` to `from finch.content.models import ContentType, RecommendedFormat`.

Add to `IdeaDraftOutput` after `limitations: str = ""`:

```python
    content_type: ContentType | None = None
```

Extend `_to_candidate` signature and body. Add two keyword params and two fields in the returned `IdeaCandidate`:

```python
def _to_candidate(
    out: "IdeaDraftOutput",
    *,
    origin: IdeaOrigin,
    source_refs: list[SourceRef],
    source_kind: SourceKind | None = None,
    evidence_status: EvidenceStatus | None = None,
    attempt_id: str | None = None,
    problem_id: str | None = None,
) -> IdeaCandidate:
    ...
    return IdeaCandidate(
        ...
        limitations=out.limitations or "",
        content_type=out.content_type,
        attempt_id=attempt_id,
        problem_id=problem_id,
    )
```

The existing `_to_candidate` returns an `IdeaCandidate` without `content_type`/`attempt_id`/`problem_id`/`method_version_hash` keys (they all default). Add exactly three keys to the returned `IdeaCandidate(...)`, right after `limitations=out.limitations or ""`:

- [ ] **Step 4: Add `_ATTEMPT_PROMPT` and `from_attempt`**

In `src/finch/ideas/fragment_service.py`, add after `_SIGNALS_PROMPT`:

```python
_ATTEMPT_PROMPT = """\
You turn a user's own practice attempt (problem → attempt → observation → unknown) into a
single publishable Idea candidate, or decline.

Rules:
- The observation is the user's own lived practice: evidence_status must be "observed";
  facts come from the observation (not judgments).
- The unknown and next_step are NOT verified: put them in boundaries.unknown and open_question.
- Never rewrite the unknown as a completed practice, and never invent a result the attempt
  does not state.
- intent is "stance" only when the observation supports a position; otherwise "exploration".
- content_type is "judgment_shift" only when the attempt shows a prior belief that the
  observation overturned or narrowed (e.g. "I assumed X, but observed Y"). Otherwise omit.
- source_kind is "attempt".
- recommended_format: reply | quote | short_post | thread | dm | do_not_publish.

## Practice attempt

{attempt}

Return JSON matching the schema (core_point / observation / facts / interpretation /
evidence_status / source_kind / limitations / reader_problem / why_worth_saying /
intent / open_question / author_position / boundaries / recommended_format /
communication_goal / content_type).
"""
```

Add the method to `FragmentService` (after `from_text`):

```python
    def from_attempt(self, attempt: PracticeAttempt) -> IdeaCandidate:
        """实践尝试 → IdeaCandidate，origin=practice、source_kind=attempt、evidence_status=observed。

        观察进 facts；unknown/next_step 强制进 boundaries.unknown（即使模型软化了也兜底）。
        """
        import json

        attempt_text = json.dumps(
            {
                "problem": attempt.problem,
                "attempt": attempt.attempt,
                "observation": attempt.observation,
                "unknown": attempt.unknown,
                "next_step": attempt.next_step,
                "result": attempt.result,
            },
            ensure_ascii=False,
        )
        out = cast(
            IdeaDraftOutput,
            self.runner.run(_ATTEMPT_PROMPT.format(attempt=attempt_text), IdeaDraftOutput),
        )
        merged_unknown = list(
            dict.fromkeys(
                [attempt.unknown, attempt.next_step, *out.boundaries.unknown]
            )
        )
        merged_unknown = [u for u in merged_unknown if u]
        out = out.model_copy(
            update={
                "boundaries": IdeaBoundaries(
                    known=out.boundaries.known,
                    inferred=out.boundaries.inferred,
                    unknown=merged_unknown,
                )
            }
        )
        source_refs = [SourceRef(type="attempt", ref=attempt.id, summary=attempt.problem)]
        source_refs.extend(
            SourceRef(type="attempt", ref=r, summary="attempt source")
            for r in attempt.source_refs
        )
        return _to_candidate(
            out,
            origin="practice",
            source_refs=source_refs,
            source_kind="attempt",
            evidence_status="observed",
            attempt_id=attempt.id,
            problem_id=attempt.problem_id,
        )
```

Add the import at the top of `fragment_service.py`:

```python
from finch.practice.attempts import PracticeAttempt
```

- [ ] **Step 5: Wire `--attempt` into `ideas_create`**

Modify `src/finch/cli.py`, in `ideas_create`:
- Add parameter `attempt: str = typer.Option(None, "--attempt", help="实践尝试 id")`.
- Change `provided = sum(x is not None for x in (text, conversation))` to include `attempt`.
- Change the "exactly one" message to `"exactly one of --text / --conversation / --attempt is required"`.
- Add an `elif attempt is not None:` branch that loads the attempt via `PracticeAttemptRepository(ws)` and calls `service.from_attempt(att)` (mirror the existing `conversation` branch).

```python
        elif attempt is not None:
            att = PracticeAttemptRepository(ws).get(attempt)
            if att is None:
                typer.echo(f"attempt not found: {attempt}")
                raise typer.Exit(code=1)
            idea = service.from_attempt(att)
```

Ensure `PracticeAttemptRepository` is imported in `cli.py` (added in Task 3).

- [ ] **Step 6: Write the skill reference**

Create `skills/idea-discovery/references/attempt-signals.md`:

```markdown
# attempt-signals：实践尝试是否够格成为 Idea 的判据

`finch ideas create --attempt <id>` 读一条 `PracticeAttempt`（问题/尝试/观察/未知/下一步）。
判据与 `commit-signals.md` 同风格：**有没有真实观察 + 一个非显然的未知**。

**够格（值得提炼）**

- 观察推翻了之前的假设（原以为 X，实际发现 Y）。
- 一次失败 + 修复，或一个方案的前后比较。
- 尚未解决、适合请教同行的问题（未知具体、可被他人补充）。
- 有真实观察支撑的「这个判断只在某条件下成立」。

**不够格（→ 空 / 不提炼）**

- 机械操作、无观察、纯情绪或新闻。
- 只有结论没有观察；或「未知」泛泛到无法被同行回应。

**证据映射（不把读到的方法写成亲历）**

- `observation` → `facts`（`evidence_status=observed`）。
- `unknown` + `next_step` → `boundaries.unknown`，绝不写成已完成。
- 你的判断 → `interpretation`，与 `facts` 分列。
- 有「原先以为 → 实际发现」对照时，`content_type=judgment_shift`。
```

Also add a one-line pointer in `skills/idea-discovery/SKILL.md` (or the top `idea-discovery.md`) listing the new source under "四种来源" (rename to "五种来源") and pointing to `references/attempt-signals.md`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_from_attempt.py -q`
Expected: 1 passed.

- [ ] **Step 8: Run full suite + lint + typecheck**

Run:
```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add src/finch/ideas/fragment_service.py src/finch/cli.py \
        skills/idea-discovery/references/attempt-signals.md skills/idea-discovery/SKILL.md \
        tests/unit/test_from_attempt.py
git commit -m "feat(idea-discovery): add --attempt source with evidence split"
```

---

### Task 6: `JUDGMENT_SHIFT` checker suite + writer context + draft skeleton

**Files:**
- Modify: `src/finch/content/checkers/suites.py` (add `JUDGMENT_SHIFT` to `type_specific`)
- Modify: `src/finch/content/writer.py` (`_render_job_context` renders facts/interpretation/evidence_status/limitations/position_revisions)
- Modify: `prompts/draft-from-job.md` (judgment-shift skeleton guidance)
- Test: `tests/unit/test_writer_context.py`, `tests/unit/test_checker_suites.py`

**Interfaces:**
- Consumes: `ContentType.JUDGMENT_SHIFT` (Task 4), `ContentJob.facts/interpretation/evidence_status/limitations/position_revisions` (existing), `StructureChecker`.
- Produces: `checker_suite_for(JUDGMENT_SHIFT, ...)` returns `[SafetyChecker, VoiceChecker, SpecificityChecker, StructureChecker]`; `_render_job_context` includes the new evidence lines.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_writer_context.py`:

```python
"""writer 上下文补渲染：facts / interpretation / evidence_status / limitations / revisions。"""

from finch.content.jobs import ContentJob
from finch.content.writer import _render_job_context
from finch.content.models import RecommendedFormat
from finch.content.jobs import ContentJobStatus


def _job() -> ContentJob:
    return ContentJob(
        id="idea_1",
        source_card_ids=[],
        reader_problem="读者纠结",
        recommended_format=RecommendedFormat.SHORT_POST,
        status=ContentJobStatus.PROPOSED,
        core_message="重试只在输入不变时值得",
        facts=["多数失败卡在输入没变"],
        interpretation="重试值得与否取决于输入稳定性",
        evidence_status="observed",
        limitations="只在本次实现规模下成立",
    )


def test_render_job_context_includes_evidence_fields():
    ctx = _render_job_context(_job())
    assert "Observed facts" in ctx
    assert "evidence_status: observed" in ctx
    assert "多数失败卡在输入没变" in ctx
    assert "只在本次实现规模下成立" in ctx
```

Extend `tests/unit/test_checker_suites.py` (or add inline) with:

```python
def test_judgment_shift_uses_structure_checker():
    from finch.content.checkers.suites import checker_suite_for
    from finch.content.models import ContentType
    names = [c.__class__.__name__ for c in checker_suite_for(ContentType.JUDGMENT_SHIFT)]
    assert "StructureChecker" in names
    assert "SafetyChecker" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_writer_context.py tests/unit/test_checker_suites.py -q`
Expected: FAIL — `_render_job_context` output lacks the new lines; `checker_suite_for` KeyError on `JUDGMENT_SHIFT`.

- [ ] **Step 3: Add `JUDGMENT_SHIFT` to the checker suite**

Modify `src/finch/content/checkers/suites.py`, add to `type_specific`:

```python
        ContentType.JUDGMENT_SHIFT: [StructureChecker(runner)],
```

- [ ] **Step 4: Extend `_render_job_context`**

Modify `src/finch/content/writer.py`. Add `import json` at the top (it is already imported — verify; `writer.py` already does `import json`). In `_render_job_context`, after the author-position block (before the final `return`), add:

```python
    blocks.append("## Observed facts vs interpretation")
    blocks.append(f"- evidence_status: {job.evidence_status or '(none)'}")
    blocks.append(
        f"- facts: {json.dumps(job.facts, ensure_ascii=False) if job.facts else '(none)'}"
    )
    blocks.append(f"- interpretation: {job.interpretation or '(none)'}")
    blocks.append(f"- limitations (适用边界): {job.limitations or '(none)'}")
    if job.position_revisions:
        blocks.append("## Position revision history (oldest → newest)")
        for rev in job.position_revisions:
            blocks.append(
                f"- claim: {rev.claim} (reason={rev.change_reason or 'n/a'}; "
                f"scope={rev.scope or 'n/a'}; counterexample={rev.counterexample or 'n/a'})"
            )
```

Note: the test asserts the string `"Observed facts"`, which matches this `## Observed facts vs interpretation` header.

- [ ] **Step 5: Add the skeleton guidance to the draft prompt**

Modify `prompts/draft-from-job.md`. Add a bullet after the existing instructions:

```markdown
- 若 job 显示「原先以为 → 实际发现」的判断转变（见 position revision history 与 facts），
  优先呈现这个转变骨架：原以为… / 实际尝试后发现… / 目前这个判断适用于… / 下一步准备验证…。
  骨架是引导，不必固定成四段；保留真实细节，区分实际观察（facts）与待验证猜想（open_question / limitations）。
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_writer_context.py tests/unit/test_checker_suites.py -q`
Expected: all pass.

- [ ] **Step 7: Run full suite + lint + typecheck**

Run:
```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/finch/content/checkers/suites.py src/finch/content/writer.py \
        prompts/draft-from-job.md tests/unit/test_writer_context.py tests/unit/test_checker_suites.py
git commit -m "feat(drafts): add judgment-shift content type and writer evidence context"
```

---

### Task 7: Weekly three-signal projection

**Files:**
- Modify: `src/finch/learn/reflection.py` (add `learning_loop_lines`, `learning_loop` param, `{learning_loop}` prompt block)
- Modify: `src/finch/cli.py` (`run_weekly` computes and passes the three signals)
- Test: `tests/unit/test_learning_loop_lines.py`

**Interfaces:**
- Consumes: `PracticeAttemptRepository`, `ContentJobRepository` (with `attempt_id`/`problem_id` from Task 4), `InteractionRecordRepository`, `WeeklyReflectionService`.
- Produces: `learning_loop_lines(*, attempts, jobs, interactions, since) -> list[str]`; `reflect(..., learning_loop=...)` accepts the new kwarg.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_learning_loop_lines.py`:

```python
"""learning_loop_lines：把三个验收信号渲染成行。"""

from datetime import UTC, datetime, timedelta

from finch.learn.reflection import learning_loop_lines
from finch.practice.attempts import PracticeAttempt

NOW = datetime.now(UTC)


def _attempt(status, updated_at):
    return PracticeAttempt(
        id=f"attempt_{status}", problem="P", attempt="T", observation="O",
        status=status, created_at=NOW, updated_at=updated_at,
    )


def test_counts_verified_attempts():
    lines = learning_loop_lines(
        attempts=[_attempt("verified", NOW), _attempt("open", NOW)],
        jobs=[],
        interactions=[],
        since=NOW - timedelta(days=7),
    )
    assert any("verified attempts" in l and "1" in l for l in lines)


def test_counts_judgment_changes_and_repeats():
    from finch.content.jobs import ContentJobStatus, PositionRevision
    from finch.content.jobs import ContentJob
    from finch.content.models import RecommendedFormat

    job = ContentJob(
        id="idea_1", source_card_ids=[], reader_problem="r",
        recommended_format=RecommendedFormat.SHORT_POST, status=ContentJobStatus.PROPOSED,
        position_revisions=[PositionRevision(
            claim="重试值得", change_reason="同行反馈：输入稳定时才值得",
            created_at=NOW,
        )],
    )
    class _Rec:
        peer_id = "p1"
    lines = learning_loop_lines(
        attempts=[], jobs=[job], interactions=[_Rec(), _Rec()],
        since=NOW - timedelta(days=7),
    )
    assert any("judgment changes" in l and "1" in l for l in lines)
    assert any("repeat interactions" in l and "1" in l for l in lines)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_learning_loop_lines.py -q`
Expected: `ImportError: cannot import name 'learning_loop_lines'`.

- [ ] **Step 3: Add `learning_loop_lines`**

Modify `src/finch/learn/reflection.py`, add after `idea_revision_diff_lines`:

```python
def learning_loop_lines(
    *,
    attempts: list,
    jobs: list,
    interactions: list,
    since,
) -> list[str]:
    """渲染学习闭环三信号：验证的 attempt / 反馈驱动的判断改变 / 同人再交流。"""
    from collections import Counter

    lines: list[str] = []
    verified = [
        a for a in attempts
        if getattr(a, "status", None) == "verified" and a.updated_at >= since
    ]
    lines.append(f"verified attempts (open→verified this week): {len(verified)}")
    for a in verified:
        lines.append(f"  - [{a.id}] {a.problem} → result: {(a.result or '')[:80]}")

    changed: list[tuple] = []
    for job in jobs:
        for rev in getattr(job, "position_revisions", None) or []:
            if rev.change_reason and rev.created_at >= since:
                changed.append((job, rev))
    lines.append(f"judgment changes (feedback-driven revise_position): {len(changed)}")
    for job, rev in changed:
        lines.append(f"  - [{job.id}] {rev.claim[:80]} (reason: {rev.change_reason[:80]})")

    counts = Counter(i.peer_id for i in interactions)
    repeats = {p: c for p, c in counts.items() if c >= 2}
    lines.append(f"repeat interactions (same peer ≥2): {len(repeats)}")
    for p, c in list(repeats.items())[:5]:
        lines.append(f"  - {p}: {c} interactions")
    return lines
```

- [ ] **Step 4: Thread `learning_loop` into `reflect`**

Modify `src/finch/learn/reflection.py`:
- Add a `{learning_loop}` section to `_REFLECT_PROMPT` after `## Idea position changes\n{idea_diffs}\n\n`:

```text
## Learning loop (attempts / judgment changes / repeat interactions)
{learning_loop}

```

- Add parameter `learning_loop: list[str] | None = None` to `reflect(...)`.
- In the `_REFLECT_PROMPT.format(...)` call, add `learning_loop=_render_lines(learning_loop or [])`.

- [ ] **Step 5: Wire into `run_weekly`**

Modify `src/finch/cli.py`, in `run_weekly`, after `idea_diffs = idea_revision_diff_lines(...)`:

```python
    from finch.learn.reflection import learning_loop_lines

    learning_loop = learning_loop_lines(
        attempts=PracticeAttemptRepository(ws).list_all(),
        jobs=ContentJobRepository(ws).list_jobs(),
        interactions=InteractionRecordRepository(ws).list_all(),
        since=since,
    )
```

And pass `learning_loop=learning_loop` to the `WeeklyReflectionService(runner).reflect(...)` call.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_learning_loop_lines.py -q`
Expected: 2 passed.

- [ ] **Step 7: Run full suite + lint + typecheck**

Run:
```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```
Expected: all green.

- [ ] **Step 8: Smoke-test the weekly output**

Run: `uv run finch weekly`
Expected: the reflection prompt now receives the three learning-loop lines (no crash; verify with a couple of seeded attempts/interactions if the LLM path is exercised).

- [ ] **Step 9: Commit**

```bash
git add src/finch/learn/reflection.py src/finch/cli.py tests/unit/test_learning_loop_lines.py
git commit -m "feat(weekly): project learning-loop signals into weekly reflection"
```

---

## Self-Review Notes

- **Spec coverage:** §3.1→Task 1; §3.2→Task 2; §3.3→Task 4; §4→Task 3; §5→Task 5; §6→Tasks 4/6; §8/§9.4→Task 7. Phase 2 (§7) is intentionally out of scope.
- **Type consistency:** `ActiveProblem`/`PracticeAttempt` field names identical across Tasks 1–3 and reused verbatim in Tasks 5/7. `ContentJob.attempt_id/problem_id` (Task 4) consumed by `create_candidate` (Task 4), `from_attempt` (Task 5), and `learning_loop_lines` (Task 7).
- **Circular-import guard:** `practice/attempts.py` holds only the model (no repository import); the service lives in `attempts_service.py`.
