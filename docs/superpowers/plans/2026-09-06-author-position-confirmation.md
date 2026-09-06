# Author Position Confirmation Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the multi-command `NEEDS_INPUT` recovery flow with a single "author position confirmation card" — `finch run resolve` renders a structured request, applies one decision (confirm/edit/skip/stop), and auto-resumes the graph — plus fingerprint-based reuse of prior approvals.

**Architecture:** A new `src/finch/gate/` package holds the interaction layer (models, rendering, resolve orchestration). `position_gate` gains a structured `input_request` output and an optional fingerprint reuse gate (off by default). The CLI adds `finch run resolve`, built on shared resume helpers extracted from the existing `run_resume`.

**Tech Stack:** Python 3.12, Pydantic 2, SQLModel/SQLite, typer. No new dependencies.

## Global Constraints

- Python 3.12+; Pydantic 2 (`StrEnum`/`Literal`/`Field`); SQLModel records store `payload_json` and upsert via `session.merge`.
- Ruff selects `E,F,I,B,UP`; line-length 100. Mypy clean on `src`.
- Deterministic: `position_fingerprint` is pure sha256; the reuse gate is a pure predicate (no LLM).
- `position_gate`'s new params default to `None` so existing callers/tests are unchanged.
- Safety invariant: a brand-new or materially-changed position must be explicitly confirmed before Draft; reuse only when fingerprint matches a non-revoked approval AND `change_mind_if` is empty.
- Bilingual (Chinese/English) docstrings match the surrounding file.
- Run tests with `uv run pytest`; lint with `uv run ruff check .`; types with `uv run mypy src`.

---

### Task 1: `position_fingerprint`

**Files:**
- Modify: `src/finch/content/jobs.py`
- Test: `tests/unit/test_jobs.py`

**Interfaces:**
- Produces: `position_fingerprint(position: AuthorPosition) -> str` — deterministic sha256 over `claim`/`decision`/`tradeoff`/`change_mind_if` (excludes `confirmed`). Consumed by Task 7 (reuse gate) and Task 8 (resolve service).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_jobs.py`:

```python
from finch.content.jobs import AuthorPosition, position_fingerprint


def test_position_fingerprint_is_deterministic():
    p = AuthorPosition(claim="c", decision="d", tradeoff="t")
    assert position_fingerprint(p) == position_fingerprint(p)


def test_position_fingerprint_ignores_confirmed():
    a = AuthorPosition(claim="c", decision="d", tradeoff="t", confirmed=False)
    b = AuthorPosition(claim="c", decision="d", tradeoff="t", confirmed=True)
    assert position_fingerprint(a) == position_fingerprint(b)


def test_position_fingerprint_changes_with_change_mind_if():
    a = AuthorPosition(claim="c", decision="d", tradeoff="t", change_mind_if="x")
    b = AuthorPosition(claim="c", decision="d", tradeoff="t", change_mind_if="y")
    assert position_fingerprint(a) != position_fingerprint(b)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_jobs.py -k position_fingerprint -v`
Expected: FAIL with `ImportError: cannot import name 'position_fingerprint'`

- [ ] **Step 3: Write minimal implementation**

In `src/finch/content/jobs.py`, add `import hashlib` to the top imports (alongside the existing `import re`), then add after the `AuthorPosition` class:

```python
def position_fingerprint(position: AuthorPosition) -> str:
    """返回 AuthorPosition 的确定性指纹（不含 ``confirmed``）。

    指纹只覆盖内容字段（claim/decision/tradeoff/change_mind_if），因此同一立场
    跨天以新 job_id 重生成时，只要内容逐字一致即可复用确认。
    """
    raw = "\n".join(
        [
            position.claim,
            position.decision,
            position.tradeoff,
            position.change_mind_if or "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_jobs.py -k position_fingerprint -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/content/jobs.py tests/unit/test_jobs.py
git commit -m "feat(content): add position_fingerprint for approval reuse"
```

---

### Task 2: Gate models (`InputRequest`, `ProposedPosition`, `InputAction`, `PositionApproval`)

**Files:**
- Create: `src/finch/gate/__init__.py`
- Create: `src/finch/gate/models.py`
- Test: `tests/unit/test_gate_models.py`

**Interfaces:**
- Produces: `InputAction` (StrEnum), `ProposedPosition` (`.complete()` → bool), `InputRequest` (fields `type`, `run_id`, `job_id`, `topic`, `why_now`, `proposed_position`, `evidence_card_ids`, `questions`, `actions`), `PositionApproval` (fields `position_fingerprint`, `source_job_id`, `approved_at`, `revoked_at`). Consumed by Tasks 3, 5, 6, 7, 8, 10.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_gate_models.py`:

```python
from finch.gate.models import (
    InputAction,
    InputRequest,
    PositionApproval,
    ProposedPosition,
)


def test_proposed_position_complete():
    assert ProposedPosition(claim="c", decision="d", tradeoff="t").complete() is True
    assert ProposedPosition().complete() is False
    assert ProposedPosition(claim="c", decision="d").complete() is False


def test_input_request_serializes_to_json():
    request = InputRequest(
        run_id="r1",
        job_id="j1",
        topic="topic",
        proposed_position=ProposedPosition(claim="c", decision="d", tradeoff="t"),
    )
    data = request.model_dump(mode="json")
    assert data["type"] == "author_position_confirmation"
    assert data["actions"] == [a.value for a in InputAction]
    assert InputRequest.model_validate(data) == request


def test_position_approval_defaults():
    approval = PositionApproval(position_fingerprint="abc", source_job_id="j1")
    assert approval.revoked_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_gate_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finch.gate'`

- [ ] **Step 3: Write minimal implementation**

Create `src/finch/gate/__init__.py`:

```python
"""Gate 交互层：结构化人工输入请求、渲染与解析。"""
```

Create `src/finch/gate/models.py`:

```python
"""Gate 交互层数据模型：结构化人工输入请求与立场批准记录。"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InputAction(StrEnum):
    CONFIRM = "confirm"
    EDIT = "edit"
    SKIP = "skip"
    STOP = "stop"
    SHOW_EVIDENCE = "show_evidence"


class ProposedPosition(BaseModel):
    """作者立场提案。立场缺失/不完整时对应字段为空串；只有三者非空才允许 confirm。"""

    claim: str = ""
    decision: str = ""
    tradeoff: str = ""
    change_mind_if: str | None = None

    def complete(self) -> bool:
        return bool(self.claim and self.decision and self.tradeoff)


class InputRequest(BaseModel):
    """position_gate 停在 needs_input 时产出的结构化请求，供 CLI/Skill/未来 Web UI 消费。"""

    type: Literal["author_position_confirmation"] = "author_position_confirmation"
    run_id: str
    job_id: str
    topic: str
    why_now: str = ""
    proposed_position: ProposedPosition
    evidence_card_ids: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    actions: list[InputAction] = Field(default_factory=lambda: list(InputAction))


class PositionApproval(BaseModel):
    """一次作者立场确认记录。按 fingerprint 复用（而非 job id），撤销后禁止复用。"""

    position_fingerprint: str
    source_job_id: str
    approved_at: datetime = Field(default_factory=_utcnow)
    revoked_at: datetime | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_gate_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/gate/__init__.py src/finch/gate/models.py tests/unit/test_gate_models.py
git commit -m "feat(gate): add InputRequest/ProposedPosition/PositionApproval models"
```

---

### Task 3: `PositionApprovalRepository`

**Files:**
- Modify: `src/finch/storage/repositories.py`
- Test: `tests/unit/test_position_approval_repository.py`

**Interfaces:**
- Consumes: `PositionApproval` (Task 2), `Store` (existing).
- Produces: `PositionApprovalRepository(store)` with `approve(fingerprint: str, job_id: str) -> PositionApproval`, `find_active(fingerprint: str) -> PositionApproval | None`, `revoke(fingerprint: str) -> None`. Consumed by Tasks 7, 8, 10.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_position_approval_repository.py`:

```python
from finch.storage.database import Store
from finch.storage.repositories import PositionApprovalRepository


def _repo(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    return PositionApprovalRepository(store)


def test_approve_and_find_active(tmp_path):
    repo = _repo(tmp_path)
    repo.approve("fp1", "job1")
    approval = repo.find_active("fp1")
    assert approval is not None
    assert approval.source_job_id == "job1"
    assert approval.revoked_at is None


def test_find_active_returns_none_for_unknown(tmp_path):
    assert _repo(tmp_path).find_active("nope") is None


def test_revoke_blocks_find_active(tmp_path):
    repo = _repo(tmp_path)
    repo.approve("fp1", "job1")
    repo.revoke("fp1")
    assert repo.find_active("fp1") is None


def test_approve_refreshes_revoked(tmp_path):
    repo = _repo(tmp_path)
    repo.approve("fp1", "job1")
    repo.revoke("fp1")
    repo.approve("fp1", "job2")
    approval = repo.find_active("fp1")
    assert approval is not None and approval.source_job_id == "job2"
    assert approval.revoked_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_position_approval_repository.py -v`
Expected: FAIL with `ImportError: cannot import name 'PositionApprovalRepository'`

- [ ] **Step 3: Write minimal implementation**

In `src/finch/storage/repositories.py`, add to the imports block (after `from finch.evidence.models import EvidenceCard`):

```python
from finch.gate.models import PositionApproval
```

Then add at the end of the file:

```python
class PositionApprovalRecord(SQLModel, table=True):
    """作者立场批准记录（P2：按 fingerprint 复用，而非 job）。"""

    id: str = Field(primary_key=True)  # = position_fingerprint
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PositionApprovalRepository:
    """立场批准仓储：approve / find_active / revoke。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def approve(self, fingerprint: str, job_id: str) -> PositionApproval:
        """upsert 一条批准记录并清除撤销标记（幂等，刷新 approved_at）。"""
        approval = PositionApproval(
            position_fingerprint=fingerprint,
            source_job_id=job_id,
            approved_at=datetime.now(UTC),
        )
        record = PositionApprovalRecord(
            id=fingerprint,
            payload_json=approval.model_dump_json(),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()
        return approval

    def find_active(self, fingerprint: str) -> PositionApproval | None:
        """返回未被撤销的批准；不存在或已撤销返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(PositionApprovalRecord, fingerprint)
            if record is None:
                return None
            approval = PositionApproval.model_validate_json(record.payload_json)
            return approval if approval.revoked_at is None else None

    def revoke(self, fingerprint: str) -> None:
        """撤销一条批准（幂等；无记录时不操作）。"""
        with Session(self.store.engine) as session:
            record = session.get(PositionApprovalRecord, fingerprint)
            if record is None:
                return
            approval = PositionApproval.model_validate_json(record.payload_json)
            approval.revoked_at = datetime.now(UTC)
            record.payload_json = approval.model_dump_json()
            record.updated_at = datetime.now(UTC)
            session.merge(record)
            session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_position_approval_repository.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/storage/repositories.py tests/unit/test_position_approval_repository.py
git commit -m "feat(storage): add PositionApprovalRepository"
```

---

### Task 4: `Store.find_latest_run`

**Files:**
- Modify: `src/finch/storage/database.py`
- Test: `tests/unit/test_storage.py`

**Interfaces:**
- Consumes: `RunRecord` (existing).
- Produces: `Store.find_latest_run(state: str) -> RunRecord | None` — most recently updated run in `state`. Consumed by Task 10 (resolve with omitted run-id).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_storage.py`:

```python
def test_find_latest_run_returns_most_recent_by_state(tmp_path):
    from finch.storage.database import RunRecord

    store = Store(tmp_path / "db.sqlite")
    store.init()
    store.upsert_run(RunRecord(id="r1", state="NEEDS_INPUT"))
    store.upsert_run(RunRecord(id="r2", state="COMPLETED"))
    store.upsert_run(RunRecord(id="r3", state="NEEDS_INPUT"))

    assert store.find_latest_run("NEEDS_INPUT").id == "r3"
    assert store.find_latest_run("COMPLETED").id == "r2"
    assert store.find_latest_run("FAILED") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_storage.py -k find_latest_run -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'find_latest_run'`

- [ ] **Step 3: Write minimal implementation**

In `src/finch/storage/database.py`, add to the `Store` class (after `get_run`):

```python
    def find_latest_run(self, state: str) -> RunRecord | None:
        """返回指定 state 下最近更新的 run；无则返回 None。"""
        with Session(self.engine) as session:
            stmt = (
                select(RunRecord)
                .where(RunRecord.state == state)
                .order_by(RunRecord.updated_at.desc())
                .limit(1)
            )
            return session.exec(stmt).first()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_storage.py -k find_latest_run -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/storage/database.py tests/unit/test_storage.py
git commit -m "feat(storage): add Store.find_latest_run by state"
```

---

### Task 5: Gate rendering

**Files:**
- Create: `src/finch/gate/render.py`
- Test: `tests/unit/test_gate_render.py`

**Interfaces:**
- Consumes: `InputRequest`, `ProposedPosition` (Task 2), `EvidenceCard` (existing).
- Produces: `render_input_request(request, cards) -> str`, `render_evidence(cards) -> str`, `render_position_diff(before, after) -> str`. Consumed by Task 10 (CLI).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_gate_render.py`:

```python
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.gate.models import InputRequest, ProposedPosition
from finch.gate.render import render_evidence, render_input_request, render_position_diff


def _request():
    return InputRequest(
        run_id="r1",
        job_id="j1",
        topic="topic here",
        why_now="why now here",
        proposed_position=ProposedPosition(claim="c", decision="d", tradeoff="t"),
        questions=["q1"],
    )


def test_render_input_request_contains_sections():
    text = render_input_request(_request(), [])
    assert "topic here" in text
    assert "why now here" in text
    assert "判断：c" in text
    assert "决策：d" in text
    assert "取舍：t" in text
    assert "q1" in text
    assert "请选择" in text


def test_render_input_request_empty_position():
    text = render_input_request(_request().model_copy(update={"proposed_position": ProposedPosition()}), [])
    assert "判断：(未填)" in text


def test_render_evidence():
    cards = [EvidenceCard(
        id="ev1", event_id="e", claim="the claim", sources=[],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=["t"],
    )]
    assert "the claim" in render_evidence(cards)
    assert "无证据" in render_evidence([])


def test_render_position_diff_shows_change():
    before = ProposedPosition(claim="c", decision="d", tradeoff="t")
    after = ProposedPosition(claim="c2", decision="d", tradeoff="t")
    diff = render_position_diff(before, after)
    assert "-claim: c" in diff
    assert "+claim: c2" in diff
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_gate_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finch.gate.render'`

- [ ] **Step 3: Write minimal implementation**

Create `src/finch/gate/render.py`:

```python
"""Gate 交互层渲染：确认卡、证据列表与立场 diff（纯函数，无 IO）。"""

import difflib

from finch.evidence.models import EvidenceCard

from .models import InputRequest, ProposedPosition


def _position_lines(position: ProposedPosition) -> list[str]:
    return [
        f"claim: {position.claim}",
        f"decision: {position.decision}",
        f"tradeoff: {position.tradeoff}",
        f"change_mind_if: {position.change_mind_if or ''}",
    ]


def render_position_diff(before: ProposedPosition, after: ProposedPosition) -> str:
    """返回 before → after 的 unified diff 文本。"""
    return "\n".join(
        difflib.unified_diff(_position_lines(before), _position_lines(after), lineterm="")
    )


def render_evidence(cards: list[EvidenceCard]) -> str:
    """渲染证据卡清单（查看完整证据动作）。"""
    if not cards:
        return "（无证据）"
    return "\n".join(f"- {card.claim} [{card.confidence.value}]" for card in cards)


def render_input_request(request: InputRequest, cards: list[EvidenceCard]) -> str:
    """渲染作者立场确认卡。"""
    pos = request.proposed_position
    lines = [
        "Finch 需要你确认一个作者立场",
        "",
        "主题",
        request.topic or "(none)",
        "",
        "为什么值得现在写",
        request.why_now or "(none)",
        "",
        "建议立场",
        f"判断：{pos.claim or '(未填)'}",
        f"决策：{pos.decision or '(未填)'}",
        f"取舍：{pos.tradeoff or '(未填)'}",
    ]
    if pos.change_mind_if:
        lines.append(f"什么会改变判断：{pos.change_mind_if}")
    if request.questions:
        lines.append("")
        lines.append("待回答问题")
        lines.extend(f"- {q}" for q in request.questions)
    lines.extend(["", f"证据：{len(cards)} 张 Evidence Card", "", "请选择："])
    lines.extend(
        [
            "1. 确认并继续",
            "2. 修改后继续",
            "3. 跳过这个主题，尝试下一个",
            "4. 今天不写",
            "5. 查看完整证据",
        ]
    )
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_gate_render.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/gate/render.py tests/unit/test_gate_render.py
git commit -m "feat(gate): add confirmation-card rendering"
```

---

### Task 6: `position_gate` emits `input_request`

**Files:**
- Modify: `src/finch/graph/content_nodes.py`
- Test: `tests/graph/test_content_nodes.py`

**Interfaces:**
- Consumes: `InputRequest`, `ProposedPosition` (Task 2).
- Produces: `position_gate` `needs_input` output now includes `output["input_request"]` (dict). Existing `items`/`questions` unchanged. Consumed by Task 10 (CLI reads it).

- [ ] **Step 1: Write the failing test**

Add to `tests/graph/test_content_nodes.py` (the helpers `_job`, `_position` already exist in this file):

```python
def test_position_gate_emits_input_request():
    node = make_position_gate_node()
    job = _job(job_id="j1", position=_position(confirmed=False))
    result = node.run(
        {"content_jobs": items_payload([job]), "run_id": "r1"}
    )
    assert result.status == "needs_input"
    req = result.output["input_request"]
    assert req["type"] == "author_position_confirmation"
    assert req["run_id"] == "r1"
    assert req["job_id"] == "j1"
    assert req["proposed_position"]["decision"] == "use token bucket"
    assert req["evidence_card_ids"] == ["ev1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/graph/test_content_nodes.py -k emits_input_request -v`
Expected: FAIL with `KeyError: 'input_request'`

- [ ] **Step 3: Write minimal implementation**

In `src/finch/graph/content_nodes.py`, add imports. At the top, add to the existing `from ..content.jobs import (...)` import list `position_fingerprint` (needed by Task 7 — add it now, used there) is NOT yet needed; for this task only add the gate models import. Add after the existing `from ..evidence.models import ...` line:

```python
from ..gate.models import InputRequest, ProposedPosition
```

Then in `make_position_gate_node`, replace the final `needs_input` return block. Find:

```python
            # primary 缺已确认立场：只问最多 3 个问题。
            output["items"] = [primary.model_dump(mode="json")]
            output["questions"] = list(primary.missing_questions)[:3]
            return NodeResult(
                status="needs_input",
                output=output,
                warnings=[f"primary job {primary.id} needs a confirmed position"],
            )
```

Replace with:

```python
            # primary 缺已确认立场：只问最多 3 个问题，并附带结构化 input_request。
            pos = primary.author_position
            output["items"] = [primary.model_dump(mode="json")]
            output["questions"] = list(primary.missing_questions)[:3]
            output["input_request"] = InputRequest(
                run_id=ctx.get("run_id", ""),
                job_id=primary.id,
                topic=primary.core_message or primary.reader_problem,
                why_now=primary.why_now,
                proposed_position=ProposedPosition(
                    claim=(pos.claim if pos else ""),
                    decision=(pos.decision if pos else ""),
                    tradeoff=(pos.tradeoff if pos else ""),
                    change_mind_if=(pos.change_mind_if if pos else None),
                ),
                evidence_card_ids=list(primary.source_card_ids),
                questions=list(primary.missing_questions)[:3],
            ).model_dump(mode="json")
            return NodeResult(
                status="needs_input",
                output=output,
                warnings=[f"primary job {primary.id} needs a confirmed position"],
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/graph/test_content_nodes.py -k "position_gate or emits_input_request" -v`
Expected: PASS (new test plus all existing position_gate tests)

- [ ] **Step 5: Commit**

```bash
git add src/finch/graph/content_nodes.py tests/graph/test_content_nodes.py
git commit -m "feat(graph): position_gate emits structured input_request"
```

---

### Task 7: `position_gate` fingerprint reuse gate

**Files:**
- Modify: `src/finch/graph/content_nodes.py`
- Test: `tests/graph/test_content_nodes.py`

**Interfaces:**
- Consumes: `position_fingerprint` (Task 1), `PositionApprovalRepository` (Task 3).
- Produces: `make_position_gate_node(jobs_repo=None, approvals_repo=None)`. When `approvals_repo` and `jobs_repo` are both set and the position matches a non-revoked approval with empty `change_mind_if`, the gate returns `succeeded` with `output["reused_approval"]` and persists `confirmed=True`. Consumed by Task 10 (wired via `daily_nodes` — but `daily_nodes` keeps passing no `approvals_repo`, so reuse is off in production until Task 10's resolve records approvals and re-runs).

- [ ] **Step 1: Write the failing test**

Add to `tests/graph/test_content_nodes.py`. Make two edits to the existing import block at the top of the file:

1. Change `from finch.storage.repositories import ContentJobRepository` to:

```python
from finch.storage.repositories import ContentJobRepository, PositionApprovalRepository
```

2. Add `position_fingerprint` to the existing `from finch.content.jobs import (...)` block (which currently imports `AuthorPosition`, `ContentJob`, `ContentJobStatus`, `IntendedEffect`, `PlanTopicsOutput`, `SuccessCriterion`, `TopicProposal`):

```python
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    IntendedEffect,
    PlanTopicsOutput,
    SuccessCriterion,
    TopicProposal,
    position_fingerprint,
)
```

Then add these tests (at the end of the file):

```python
def test_position_gate_reuses_approved_position(tmp_path):
    store = _store(tmp_path)
    jobs_repo = ContentJobRepository(store)
    approvals = PositionApprovalRepository(store)
    job = _job(job_id="j1", position=_position(confirmed=False))
    jobs_repo.upsert_job(job)
    approvals.approve(position_fingerprint(job.author_position), "j1")

    node = make_position_gate_node(jobs_repo=jobs_repo, approvals_repo=approvals)
    result = node.run({"content_jobs": items_payload([job]), "run_id": "r1"})
    assert result.status == "succeeded"
    assert result.output["reused_approval"] == position_fingerprint(job.author_position)
    assert ContentJobRepository(store).get_job("j1").author_position.confirmed is True


def test_position_gate_reasks_when_change_mind_if_set(tmp_path):
    store = _store(tmp_path)
    jobs_repo = ContentJobRepository(store)
    approvals = PositionApprovalRepository(store)
    pos = AuthorPosition(
        claim="c", decision="d", tradeoff="t", change_mind_if="x", confirmed=False
    )
    job = _job(job_id="j1", position=pos)
    jobs_repo.upsert_job(job)
    approvals.approve(position_fingerprint(pos), "j1")

    node = make_position_gate_node(jobs_repo=jobs_repo, approvals_repo=approvals)
    result = node.run({"content_jobs": items_payload([job])})
    assert result.status == "needs_input"


def test_position_gate_reasks_when_revoked(tmp_path):
    store = _store(tmp_path)
    jobs_repo = ContentJobRepository(store)
    approvals = PositionApprovalRepository(store)
    job = _job(job_id="j1", position=_position(confirmed=False))
    jobs_repo.upsert_job(job)
    approvals.approve(position_fingerprint(job.author_position), "j1")
    approvals.revoke(position_fingerprint(job.author_position))

    node = make_position_gate_node(jobs_repo=jobs_repo, approvals_repo=approvals)
    result = node.run({"content_jobs": items_payload([job])})
    assert result.status == "needs_input"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/graph/test_content_nodes.py -k "reuses_approved or reasks_when" -v`
Expected: FAIL (reuse test: `result.status == "needs_input"` not `"succeeded"`; and `KeyError: 'reused_approval'`)

- [ ] **Step 3: Write minimal implementation**

In `src/finch/graph/content_nodes.py`:

1. Change the `make_position_gate_node` signature:

```python
def make_position_gate_node(
    jobs_repo: ContentJobRepository | None = None,
    approvals_repo: PositionApprovalRepository | None = None,
) -> Node:
```

2. Add `PositionApprovalRepository` to the existing `from ..storage.repositories import ContentJobRepository` import:

```python
from ..storage.repositories import ContentJobRepository, PositionApprovalRepository
```

3. Add `position_fingerprint` to the `from ..content.jobs import (...)` import block.

4. In the node body, after the `ready` early-return and before the "primary 缺已确认立场" block, insert the reuse gate:

```python
            # 复用门禁（P2）：立场逐字一致、未被撤销、且作者未写出「什么会改变判断」→ 复用确认。
            if (
                approvals_repo is not None
                and jobs_repo is not None
                and position is not None
                and bool(position.decision)
                and bool(position.tradeoff)
            ):
                fingerprint = position_fingerprint(position)
                approval = approvals_repo.find_active(fingerprint)
                if approval is not None and not position.change_mind_if:
                    confirmed_pos = position.model_copy(update={"confirmed": True})
                    confirmed_job = primary.model_copy(update={"author_position": confirmed_pos})
                    jobs_repo.upsert_job(confirmed_job)
                    output["items"] = [confirmed_job.model_dump(mode="json")]
                    output["reused_approval"] = fingerprint
                    return NodeResult(status="succeeded", output=output)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/graph/test_content_nodes.py -k "position_gate or reuses_approved or reasks_when" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/graph/content_nodes.py tests/graph/test_content_nodes.py
git commit -m "feat(graph): position_gate reuses prior approvals by fingerprint"
```

---

### Task 8: `resolve_input` service

**Files:**
- Create: `src/finch/gate/resolve.py`
- Test: `tests/unit/test_gate_resolve.py`

**Interfaces:**
- Consumes: `InputRequest`, `InputAction`, `ProposedPosition` (Task 2), `position_fingerprint` (Task 1), `ContentJobRepository`/`PositionApprovalRepository` (existing/Task 3).
- Produces: `position_yaml(position) -> str`, `parse_position_yaml(text) -> ProposedPosition`, `resolve_input(request, action, *, jobs_repo, approvals_repo=None, edited_position=None, skip_reason=None) -> str`. Consumed by Task 10.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_gate_resolve.py`:

```python
import pytest

from finch.content.jobs import AuthorPosition, ContentJobStatus, position_fingerprint
from finch.gate.models import InputAction, InputRequest, ProposedPosition
from finch.gate.resolve import parse_position_yaml, position_yaml, resolve_input
from finch.storage.database import Store
from finch.storage.repositories import ContentJobRepository, PositionApprovalRepository


def _store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.init()
    return s


def _seed(tmp_path, *, confirmed=False, change_mind_if=None):
    store = _store(tmp_path)
    jobs = ContentJobRepository(store)
    position = AuthorPosition(
        claim="c", decision="d", tradeoff="t",
        change_mind_if=change_mind_if, confirmed=confirmed,
    )
    job = _job(author_position=position)
    jobs.upsert_job(job)
    return store, jobs, PositionApprovalRepository(store), job


def _job(author_position):
    from finch.content.jobs import ContentJob, IntendedEffect, SuccessCriterion
    from finch.content.models import DraftKind

    return ContentJob(
        id="j1",
        source_card_ids=["ev1"],
        reader_problem="rp",
        audience="aud",
        intended_effect=IntendedEffect(understand="u"),
        author_position=author_position,
        success_criteria=[SuccessCriterion(id="c1", description="d", measurement="critic")],
        recommended_format=DraftKind.REPLY,
        status=ContentJobStatus.READY,
    )


def _request(job):
    p = job.author_position
    return InputRequest(
        run_id="r1", job_id="j1", topic="t",
        proposed_position=ProposedPosition(
            claim=p.claim, decision=p.decision, tradeoff=p.tradeoff,
            change_mind_if=p.change_mind_if,
        ),
        evidence_card_ids=["ev1"],
    )


def test_confirm_sets_confirmed_and_approves(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    msg = resolve_input(_request(job), InputAction.CONFIRM, jobs_repo=jobs, approvals_repo=approvals)
    assert "confirmed" in msg
    assert jobs.get_job("j1").author_position.confirmed is True
    assert approvals.find_active(position_fingerprint(job.author_position)) is not None


def test_edit_revokes_old_approves_new(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    approvals.approve(position_fingerprint(job.author_position), "j1")
    edited = ProposedPosition(claim="new", decision="d", tradeoff="t")
    msg = resolve_input(
        _request(job), InputAction.EDIT, jobs_repo=jobs, approvals_repo=approvals,
        edited_position=edited,
    )
    assert "edited" in msg
    assert jobs.get_job("j1").author_position.claim == "new"
    assert approvals.find_active(position_fingerprint(job.author_position)) is None
    assert approvals.find_active(position_fingerprint(
        AuthorPosition(claim="new", decision="d", tradeoff="t")
    )) is not None


def test_skip_marks_do_not_write(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    msg = resolve_input(
        _request(job), InputAction.SKIP, jobs_repo=jobs, approvals_repo=approvals,
        skip_reason="not relevant",
    )
    assert "skipped" in msg
    updated = jobs.get_job("j1")
    assert updated.status == ContentJobStatus.DO_NOT_WRITE
    assert updated.reject_reason == "not relevant"


def test_stop_marks_all_active_do_not_write(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    msg = resolve_input(_request(job), InputAction.STOP, jobs_repo=jobs, approvals_repo=approvals)
    assert "stopped" in msg
    assert jobs.get_job("j1").status == ContentJobStatus.DO_NOT_WRITE


def test_confirm_incomplete_position_raises(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    jobs.upsert_job(job.model_copy(update={"author_position": None}))
    request = InputRequest(
        run_id="r1", job_id="j1", topic="t", proposed_position=ProposedPosition(),
    )
    with pytest.raises(ValueError):
        resolve_input(request, InputAction.CONFIRM, jobs_repo=jobs, approvals_repo=approvals)


def test_skip_without_reason_raises(tmp_path):
    store, jobs, approvals, job = _seed(tmp_path)
    with pytest.raises(ValueError):
        resolve_input(_request(job), InputAction.SKIP, jobs_repo=jobs, approvals_repo=approvals)


def test_position_yaml_roundtrip():
    p = ProposedPosition(claim="c", decision="d", tradeoff="t", change_mind_if="x")
    assert parse_position_yaml(position_yaml(p)) == p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_gate_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finch.gate.resolve'`

- [ ] **Step 3: Write minimal implementation**

Create `src/finch/gate/resolve.py`:

```python
"""Gate 解析与决策落地：把一次作者决策落到 job/approval（不负责 replay）。"""

import yaml

from finch.content.jobs import AuthorPosition, ContentJobStatus, position_fingerprint
from finch.storage.repositories import ContentJobRepository, PositionApprovalRepository

from .models import InputAction, InputRequest, ProposedPosition


def _to_author_position(position: ProposedPosition, confirmed: bool = True) -> AuthorPosition:
    return AuthorPosition(
        claim=position.claim,
        decision=position.decision,
        tradeoff=position.tradeoff,
        change_mind_if=position.change_mind_if,
        confirmed=confirmed,
    )


def position_yaml(position: ProposedPosition) -> str:
    """把立场渲染成预填 YAML（供 --edit 编辑器）。"""
    return yaml.safe_dump(
        {
            "claim": position.claim,
            "decision": position.decision,
            "tradeoff": position.tradeoff,
            "change_mind_if": position.change_mind_if,
        },
        sort_keys=False,
        allow_unicode=True,
    )


def parse_position_yaml(text: str) -> ProposedPosition:
    """从 YAML 文本解析立场；非法输入抛 ValueError。"""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("position file is not a YAML mapping")
    return ProposedPosition(
        claim=str(data.get("claim") or ""),
        decision=str(data.get("decision") or ""),
        tradeoff=str(data.get("tradeoff") or ""),
        change_mind_if=data.get("change_mind_if"),
    )


def resolve_input(
    request: InputRequest,
    action: InputAction,
    *,
    jobs_repo: ContentJobRepository,
    approvals_repo: PositionApprovalRepository | None = None,
    edited_position: ProposedPosition | None = None,
    skip_reason: str | None = None,
) -> str:
    """把一次决策落到 job/approval 上，返回人类可读摘要。不负责 replay。"""
    job = jobs_repo.get_job(request.job_id)
    if job is None:
        raise ValueError(f"job not found: {request.job_id}")
    proposed_fp = position_fingerprint(
        _to_author_position(request.proposed_position, confirmed=False)
    )

    if action is InputAction.CONFIRM:
        position = job.author_position
        if (
            position is None
            or not position.claim
            or not position.decision
            or not position.tradeoff
        ):
            raise ValueError("position incomplete; use --edit or --file")
        confirmed = position.model_copy(update={"confirmed": True})
        jobs_repo.upsert_job(job.model_copy(update={"author_position": confirmed}))
        if approvals_repo is not None:
            approvals_repo.approve(position_fingerprint(confirmed), request.job_id)
        return f"confirmed {request.job_id}"

    if action is InputAction.EDIT:
        if edited_position is None or not edited_position.complete():
            raise ValueError("edited position incomplete")
        position = _to_author_position(edited_position, confirmed=True)
        jobs_repo.upsert_job(job.model_copy(update={"author_position": position}))
        if approvals_repo is not None:
            new_fp = position_fingerprint(position)
            if new_fp != proposed_fp:
                approvals_repo.revoke(proposed_fp)
            approvals_repo.approve(new_fp, request.job_id)
        return f"edited {request.job_id}"

    if action is InputAction.SKIP:
        if not skip_reason:
            raise ValueError("--skip requires --reason")
        jobs_repo.upsert_job(
            job.model_copy(
                update={
                    "status": ContentJobStatus.DO_NOT_WRITE,
                    "reject_reason": skip_reason,
                }
            )
        )
        if approvals_repo is not None:
            approvals_repo.revoke(proposed_fp)
        return f"skipped {request.job_id}"

    if action is InputAction.STOP:
        for existing in jobs_repo.list_jobs():
            if existing.status != ContentJobStatus.DO_NOT_WRITE:
                jobs_repo.upsert_job(
                    existing.model_copy(
                        update={
                            "status": ContentJobStatus.DO_NOT_WRITE,
                            "reject_reason": "stopped original track",
                        }
                    )
                )
        if approvals_repo is not None:
            approvals_repo.revoke(proposed_fp)
        return "stopped original track"

    raise ValueError(f"unsupported action: {action}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_gate_resolve.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/gate/resolve.py tests/unit/test_gate_resolve.py
git commit -m "feat(gate): add resolve_input decision service"
```

---

### Task 9: Extract shared resume helpers in the CLI

**Files:**
- Modify: `src/finch/cli.py`

**Interfaces:**
- Produces: `_resume_nodes(settings: Settings, store: Store) -> list[Node]`, `_resume_and_echo(store: Store, nodes: list[Node], run_id: str) -> None`. `run_resume` is rewritten to use both. Consumed by Task 10. No behavior change — existing tests must stay green.

- [ ] **Step 1: Add the helpers (refactor only — no new test; existing tests guard)**

In `src/finch/cli.py`, add `from .graph.nodes import Node` to the imports (near `from .graph.runtime import GraphRuntime`). Then insert these two module-level functions immediately before `@run_app.command("resume")`:

```python
def _resume_nodes(settings: Settings, store: Store) -> list[Node]:
    """run_resume 与 resolve 共用：空 groups 的 daily_nodes 装配。"""
    gh = GhClient()
    opencli = OpenCliClient()
    groups_by_repo: dict[str, list[list[CommitDetail]]] = {
        repo: [] for repo in settings.repositories
    }
    known_commit_urls: set[str] = set()
    repo_is_private = {repo: False for repo in settings.repositories}

    return daily_nodes(
        settings=settings,
        store=store,
        gh=gh,
        opencli=opencli,
        extractor=Extractor(
            create_runner(settings.llm) or CodexRunner(),
            settings=settings.extraction,
            cache_path=settings.paths.cache_dir / "extraction_cache.json",
        ),
        runner=CodexRunner(),
        groups_by_repo=groups_by_repo,
        known_commit_urls=known_commit_urls,
        repo_is_private=repo_is_private,
        voice_profile=load_voice_profile(settings.paths.voice_profile_path),
        inference_runners={
            "match_evidence": create_runner(settings.llm, "match_evidence"),
            "plan_topics": create_runner(settings.llm, "plan_topics"),
            "expand_job": create_runner(settings.llm, "expand_job"),
            "critique": create_runner(settings.llm, "critique"),
        },
    )


def _resume_and_echo(store: Store, nodes: list[Node], run_id: str) -> None:
    """replay + 打印 state + 持久化 run 输出 + 打印 brief。"""
    run = replay(store, nodes, run_id)
    typer.echo(run.state)
    _persist_run_outputs(store, run_id)
    _echo_daily_brief(store, run_id)
```

- [ ] **Step 2: Rewrite `run_resume` to use the helpers**

Replace the body of `run_resume` (currently `src/finch/cli.py:393-438`) with:

```python
@run_app.command("resume")
def run_resume(run_id: str) -> None:
    """从 run-id 恢复：复用已完成节点，从 position_gate 继续（读取用户最新 job 编辑）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    nodes = _resume_nodes(settings, store)
    _resume_and_echo(store, nodes, run_id)
```

- [ ] **Step 3: Run tests to verify no regression**

Run: `uv run pytest tests/unit/test_cli_run.py -k "run_resume or run_daily" -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/finch/cli.py
git commit -m "refactor(cli): extract shared resume helpers"
```

---

### Task 10: `finch run resolve` command

**Files:**
- Modify: `src/finch/cli.py`
- Test: `tests/unit/test_cli_run.py`

**Interfaces:**
- Consumes: everything from Tasks 1–9, plus `_resume_nodes`/`_resume_and_echo` (Task 9).
- Produces: `finch run resolve [run-id] [--confirm|--edit|--file|--skip --reason|--stop] [--json]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_cli_run.py`. First add imports at the top:

```python
from finch.content.jobs import position_fingerprint
from finch.gate.models import InputRequest, ProposedPosition
from finch.graph.state import GraphState
from finch.storage.repositories import PositionApprovalRepository
```

(`AuthorPosition`, `ContentJobRepository`, `json`, `Store` are already imported in this file.) Then add these tests:

```python
def _seed_needs_input(store, *, run_id="r1", job_id="job1"):
    from finch.storage.database import NodeRecord, RunRecord

    repo = ContentJobRepository(store)
    repo.upsert_job(_job(job_id=job_id, author_position=AuthorPosition(
        claim="c", decision="d", tradeoff="t",
    )))
    store.upsert_run(RunRecord(id=run_id, state=GraphState.NEEDS_INPUT.value))
    request = InputRequest(
        run_id=run_id, job_id=job_id, topic="t",
        proposed_position=ProposedPosition(claim="c", decision="d", tradeoff="t"),
        evidence_card_ids=["ev1"],
    )
    # 等价 position_gate 已跑完停在 needs_input：直接落一条节点记录。
    store.upsert_node(NodeRecord(
        id=f"{run_id}:position_gate:default", run_id=run_id, node_name="position_gate",
        idempotency_key="default", status="needs_input",
        output_json=json.dumps({"input_request": request.model_dump(mode="json")}),
    ))
    return request


def test_run_resolve_json_fetches_input_request(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    request = _seed_needs_input(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["run", "resolve", "--json"])
    assert r.exit_code == 0, r.output
    assert request.job_id in r.output
    assert "author_position_confirmation" in r.output


def test_run_resolve_confirm_resumes(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    request = _seed_needs_input(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    resumed = {}
    monkeypatch.setattr(cli, "_resume_nodes", lambda s, st: [])
    monkeypatch.setattr(
        cli, "_resume_and_echo", lambda st, nodes, rid: resumed.setdefault("run_id", rid)
    )

    r = CliRunner().invoke(app, ["run", "resolve", "--confirm"])
    assert r.exit_code == 0, r.output
    assert "confirmed" in r.output
    assert ContentJobRepository(store).get_job(request.job_id).author_position.confirmed is True
    approved = AuthorPosition(claim="c", decision="d", tradeoff="t")
    assert PositionApprovalRepository(store).find_active(position_fingerprint(approved)) is not None
    assert resumed["run_id"] == "r1"


def test_run_resolve_skip_marks_job(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    request = _seed_needs_input(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_resume_nodes", lambda s, st: [])
    monkeypatch.setattr(cli, "_resume_and_echo", lambda st, nodes, rid: None)

    r = CliRunner().invoke(app, ["run", "resolve", "--skip", "--reason", "not now"])
    assert r.exit_code == 0, r.output
    job = ContentJobRepository(store).get_job(request.job_id)
    assert job.status.value == "do_not_write"
    assert job.reject_reason == "not now"


def test_run_resolve_file_edits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    request = _seed_needs_input(store)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_resume_nodes", lambda s, st: [])
    monkeypatch.setattr(cli, "_resume_and_echo", lambda st, nodes, rid: None)

    answers = tmp_path / "p.yaml"
    answers.write_text("claim: new\ndecision: d\ntradeoff: t\n")
    r = CliRunner().invoke(app, ["run", "resolve", "--file", str(answers)])
    assert r.exit_code == 0, r.output
    assert ContentJobRepository(store).get_job(request.job_id).author_position.claim == "new"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_run.py -k run_resolve -v`
Expected: FAIL with `Error: No such command 'resolve'` (typer exits 2)

- [ ] **Step 3: Write minimal implementation**

In `src/finch/cli.py`, add imports:

```python
import os
import subprocess
import tempfile
```

```python
from .gate.models import InputAction, InputRequest, ProposedPosition
from .gate.render import render_evidence, render_input_request, render_position_diff
from .gate.resolve import parse_position_yaml, position_yaml, resolve_input
from .graph.state import GraphState
```

Also add `PositionApprovalRepository` to the **existing** `from .storage.repositories import (...)` block (alongside `ContentJobRepository`). `EvidenceCard` and `EvidenceRepository` are already imported.

Then add module-level helpers (near `_resume_nodes`):

```python
_ACTION_BY_CHOICE = {
    "1": InputAction.CONFIRM,
    "2": InputAction.EDIT,
    "3": InputAction.SKIP,
    "4": InputAction.STOP,
}


def _latest_needs_input_run_id(store: Store) -> str | None:
    record = store.find_latest_run(GraphState.NEEDS_INPUT.value)
    return record.id if record is not None else None


def _read_input_request(store: Store, run_id: str) -> InputRequest:
    record = store.find_node(run_id, "position_gate", "default")
    if record is None or not record.output_json:
        raise ValueError(f"run {run_id} has no position_gate output")
    raw = json.loads(record.output_json).get("input_request")
    if raw is None:
        raise ValueError(f"run {run_id} has no pending input_request")
    return InputRequest.model_validate(raw)


def _cards_for(request: InputRequest, store: Store) -> list[EvidenceCard]:
    cards_by_id = {card.id: card for card in EvidenceRepository(store).list_cards()}
    return [cards_by_id[cid] for cid in request.evidence_card_ids if cid in cards_by_id]


def _open_editor(prefill: str) -> str:
    """在 $VISUAL/$EDITOR（回退 vi）中打开预填内容，返回编辑后的文本。"""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(prefill)
        tmp_path = tmp.name
    try:
        subprocess.run([editor, tmp_path], check=True)
        return Path(tmp_path).read_text(encoding="utf-8")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _edit_position(proposed: ProposedPosition) -> ProposedPosition:
    return parse_position_yaml(_open_editor(position_yaml(proposed)))
```

Then add the command after `run_resume`:

```python
@run_app.command("resolve")
def run_resolve(
    run_id: str | None = typer.Argument(None, help="run id（缺省取最近 NEEDS_INPUT 的 run）"),
    confirm: bool = typer.Option(False, "--confirm", help="确认并继续"),  # noqa: B008
    edit: bool = typer.Option(False, "--edit", help="打开预填编辑器修改立场"),  # noqa: B008
    file: Path | None = typer.Option(None, "--file", help="从 YAML 文件读取立场"),  # noqa: B008
    skip: bool = typer.Option(False, "--skip", help="跳过当前主题，尝试下一个"),  # noqa: B008
    reason: str | None = typer.Option(None, "--reason", help="--skip 的拒绝理由"),  # noqa: B008
    stop: bool = typer.Option(False, "--stop", help="结束今天的原创轨道"),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="输出 JSON（供 Skill/Agent）"),  # noqa: B008
) -> None:
    """处理当前阻塞点（author position confirmation），完成后自动 resume。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()

    resolved_run_id = run_id or _latest_needs_input_run_id(store)
    if resolved_run_id is None:
        typer.echo("no run awaiting input")
        raise typer.Exit(code=1)
    try:
        request = _read_input_request(store, resolved_run_id)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    flags = [confirm, edit, skip, stop, file is not None]
    if sum(1 for f in flags if f) > 1:
        typer.echo("choose exactly one of --confirm/--edit/--file/--skip/--stop")
        raise typer.Exit(code=1)

    if as_json:
        if any(flags):
            typer.echo("--json 不能与动作 flag 同时使用")
            raise typer.Exit(code=1)
        typer.echo(request.model_dump_json(indent=2))
        return

    jobs_repo = ContentJobRepository(store)
    approvals_repo = PositionApprovalRepository(store)
    cards = _cards_for(request, store)

    action: InputAction
    edited: ProposedPosition | None = None
    skip_reason: str | None = reason

    if confirm:
        action = InputAction.CONFIRM
    elif edit:
        action = InputAction.EDIT
        edited = _edit_position(request.proposed_position)
        typer.echo(render_position_diff(request.proposed_position, edited))
    elif file is not None:
        action = InputAction.EDIT
        edited = parse_position_yaml(file.read_text())
        typer.echo(render_position_diff(request.proposed_position, edited))
    elif skip:
        action = InputAction.SKIP
    elif stop:
        action = InputAction.STOP
    else:
        typer.echo(render_input_request(request, cards))
        choice = typer.prompt("请选择", default="1")
        if choice == "5":
            typer.echo(render_evidence(cards))
            return
        if choice not in _ACTION_BY_CHOICE:
            typer.echo("invalid choice")
            raise typer.Exit(code=1)
        action = _ACTION_BY_CHOICE[choice]
        if action is InputAction.EDIT:
            edited = _edit_position(request.proposed_position)
            typer.echo(render_position_diff(request.proposed_position, edited))
        elif action is InputAction.SKIP:
            skip_reason = typer.prompt("跳过理由", default="not_now")

    try:
        summary = resolve_input(
            request,
            action,
            jobs_repo=jobs_repo,
            approvals_repo=approvals_repo,
            edited_position=edited,
            skip_reason=skip_reason,
        )
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(summary)

    nodes = _resume_nodes(settings, store)
    _resume_and_echo(store, nodes, resolved_run_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli_run.py -k run_resolve -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/cli.py tests/unit/test_cli_run.py
git commit -m "feat(cli): add finch run resolve with auto-resume"
```

---

### Task 11: Update the `$finch` Skill

**Files:**
- Modify: `skills/finch/SKILL.md`

**Interfaces:**
- No code. Replaces the hardcoded 4-command `NEEDS_INPUT` recovery block with the `$finch resolve` mode.

- [ ] **Step 1: Update the modes table**

In `skills/finch/SKILL.md`, add a `$finch resolve` row after the `$finch resume` row:

```markdown
| `$finch resolve` | 处理当前 author-position 阻塞点（确认/修改/跳过/停止），完成后自动 resume | `finch run resolve [RUN_ID] --confirm` / `--edit` / `--skip --reason` / `--stop` / `--json` |
```

- [ ] **Step 2: Replace the recovery bullet**

Replace the existing line:

```markdown
- 每日 Graph 停在 `NEEDS_INPUT`（position_gate 需要作者立场）：`finch jobs show <JOB_ID>` 看问题 → `finch jobs answer <JOB_ID> --file answers.yaml` → `finch jobs confirm-position <JOB_ID>` → `finch run resume <RUN_ID>`。
```

with:

```markdown
- 每日 Graph 停在 `NEEDS_INPUT`（position_gate 需要作者立场）：运行 `finch run resolve --json` 取结构化 `input_request`，把建议立场渲染成对话选项，依据用户选择调用 `finch run resolve [RUN_ID] --confirm | --edit | --skip --reason | --stop`（完成后自动 resume）。底层 `finch jobs answer/confirm-position/reject` 与 `finch run resume` 保留给脚本与高级用户。
```

- [ ] **Step 3: Commit**

```bash
git add skills/finch/SKILL.md
git commit -m "docs(skill): route NEEDS_INPUT recovery through finch run resolve"
```

---

## Final Verification

Run the whole suite and linters once after Task 11:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```

Expected: all pass (the `ss` skips are pre-existing). If `ruff` flags unused imports introduced by the plan (e.g. `os`/`subprocess`/`tempfile` if only used in helpers), they are used by `_open_editor`.
