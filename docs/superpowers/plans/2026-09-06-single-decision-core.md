# 单一决策点 — Plan 1（核心：非阻塞 gate + 决策模型）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `position_gate` 对「可推断但未确认」的立场不再阻塞，改为生成候选草稿并标记 `position_source=INFERRED`；新增 `DecisionRecord` 模型与 `finch decide accept|skip` 命令，把「确认立场 + 批准草稿」合并为一次原子决策。

**Architecture:** 纯加性改动：`AuthorPosition`/`Draft` 增加字段（存于 payload_json，无需列迁移），新增 `DecisionRecord` 表（`create_all` 自动建表 + 一条加性 alembic 迁移），`position_gate` 放宽 `confirmed` 要求，新增 `DecisionService.accept/skip`（原子落地 + 向后兼容投影）。

**Tech Stack:** Python 3.12+，Pydantic 2，SQLModel/SQLite（`payload_json` 模式），typer，pytest，ruff（E,F,I,B,UP，行长 100），mypy。

## Global Constraints

- Python 3.12+；Pydantic 2（`StrEnum`/`Literal`/`Field`）；ruff 选择 `E,F,I,B,UP`，行长 100；mypy 通过。
- SQLModel 记录存 `payload_json`，`session.merge` 幂等 upsert；新表用 `create_all` + 加性 alembic 迁移。
- 双语（中/英）docstring，匹配所在文件；面向用户文案用中文。
- 不变量不变：Evidence first、No auto-publish、External ≠ evidence、Deterministic totals。
- 不修改 `GraphRuntime` / `replay` 语义；`position_gate` 仍确定性（single-primary 选择逻辑不变）。
- 向后兼容：不 drop `AuthorPosition.confirmed` / `PositionApproval` / `ReviewDecision`；旧命令仍可用。
- 复用门禁（`position_fingerprint` + `PositionApproval.find_active`）语义不变，仅新增 `position_source=REUSED` 标记。

---

### Task 1: 领域模型（`PositionSource` / `DecisionAction` / `DecisionRecord` + 字段）

**Files:**
- Modify: `src/finch/content/jobs.py`（`AuthorPosition` + `PositionSource`）
- Modify: `src/finch/content/models.py`（`Draft.run_id`）
- Modify: `src/finch/review/models.py`（`DecisionAction` + `DecisionRecord`）
- Test: `tests/unit/test_jobs.py`、`tests/unit/test_content_models.py`、`tests/unit/test_review_models.py`

**Interfaces:**
- Produces（后续 Task 2/3/5 依赖）:
  - `PositionSource`（`content/jobs.py`）: `INFERRED="inferred"` / `HUMAN_CONFIRMED="human_confirmed"` / `REUSED="reused"`
  - `AuthorPosition.position_source: PositionSource | None = None`
  - `Draft.run_id: str = ""`
  - `DecisionAction`（`review/models.py`）: `ACCEPT="accept"` / `REVISE="revise"` / `SKIP="skip"`
  - `DecisionRecord`（`review/models.py`）: `id/job_id/draft_id/action/position_source/position_fingerprint/approved_content_hash/revised_body/diff/decided_at`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_jobs.py` 追加：

```python
from finch.content.jobs import AuthorPosition, PositionSource


def test_author_position_source_defaults_none():
    pos = AuthorPosition(claim="c", decision="d", tradeoff="t")
    assert pos.position_source is None
    assert pos.confirmed is False
```

`tests/unit/test_content_models.py` 追加：

```python
from finch.content.models import Draft, DraftKind


def test_draft_run_id_defaults_empty():
    draft = Draft(id="d1", kind=DraftKind.ORIGINAL, body="hi")
    assert draft.run_id == ""
    assert draft.content_job_id is None
```

`tests/unit/test_review_models.py` 追加：

```python
from datetime import UTC, datetime

from finch.content.jobs import PositionSource
from finch.review.models import DecisionAction, DecisionRecord


def test_decision_record_shape():
    rec = DecisionRecord(
        id="dec_j1",
        job_id="j1",
        draft_id="d1",
        action=DecisionAction.ACCEPT,
        position_source=PositionSource.HUMAN_CONFIRMED,
        position_fingerprint="fp",
        approved_content_hash="h",
        decided_at=datetime.now(UTC),
    )
    assert rec.id == "dec_j1"
    assert rec.action == DecisionAction.ACCEPT
    assert rec.position_source == PositionSource.HUMAN_CONFIRMED
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_jobs.py tests/unit/test_content_models.py tests/unit/test_review_models.py -q`
Expected: FAIL — `ImportError: cannot import name 'PositionSource'` 等。

- [ ] **Step 3: 实现模型**

`src/finch/content/jobs.py`：在 `ContentJobStatus` 后新增：

```python
class PositionSource(StrEnum):
    """作者立场的确认来源：推断 / 人类确认 / 复用门禁确认。"""

    INFERRED = "inferred"
    HUMAN_CONFIRMED = "human_confirmed"
    REUSED = "reused"
```

`AuthorPosition` 增加字段：

```python
class AuthorPosition(BaseModel):
    """作者立场：判断是否值得写。"""

    claim: str
    decision: str
    tradeoff: str
    change_mind_if: str | None = None
    confirmed: bool = False
    position_source: PositionSource | None = None
```

`src/finch/content/models.py` 的 `Draft` 增加 `run_id`：

```python
class Draft(BaseModel):
    id: str
    kind: DraftKind
    candidate_id: str | None = None   # reply 有；original 为 None
    language: str = "en"              # reply="en"；original="zh"
    body: str
    claims: list[ClaimRef] = Field(default_factory=list)
    content_job_id: str | None = None
    position_statement: str = ""
    critic_report_id: str | None = None
    run_id: str = ""                  # 当次 run（回溯到来源）
```

`src/finch/review/models.py` 顶部 import `PositionSource` 并新增：

```python
from finch.content.jobs import PositionSource


class DecisionAction(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    SKIP = "skip"


class DecisionRecord(BaseModel):
    """一次原子决策：采用同时确认立场 + 批准草稿 + 绑定正文 hash。"""

    id: str                                   # "dec_<job_id>"（幂等键）
    job_id: str
    draft_id: str
    action: DecisionAction
    position_source: PositionSource
    position_fingerprint: str
    approved_content_hash: str                # 采用时绑定最终正文；revise 改变 hash → 旧批准失效
    revised_body: str | None = None
    diff: str | None = None
    decided_at: datetime
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_jobs.py tests/unit/test_content_models.py tests/unit/test_review_models.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/content/jobs.py src/finch/content/models.py src/finch/review/models.py tests/unit/test_jobs.py tests/unit/test_content_models.py tests/unit/test_review_models.py
git commit -m "feat(models): PositionSource/DecisionAction/DecisionRecord + Draft.run_id"
```

---

### Task 2: 存储与迁移（`DecisionRecordRepository` + `DraftRepository.list_by_job` + alembic）

**Files:**
- Modify: `src/finch/storage/repositories.py`（`DecisionRecordRecord` + `DecisionRecordRepository` + `DraftRepository.list_by_job`）
- Create: `alembic/versions/<rev>_add_decision_record.py`
- Test: `tests/unit/test_repositories.py`、`tests/unit/test_alembic.py`

**Interfaces:**
- Produces:
  - `DecisionRecordRepository(store).save(rec: DecisionRecord)` / `.get(job_id) -> DecisionRecord | None` / `.list() -> list[DecisionRecord]`
  - `DraftRepository.list_by_job(job_id: str) -> list[Draft]`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_repositories.py` 追加：

```python
from datetime import UTC, datetime

from finch.content.jobs import PositionSource
from finch.content.models import Draft, DraftKind
from finch.review.models import DecisionAction, DecisionRecord
from finch.storage.repositories import DecisionRecordRepository, DraftRepository


def test_decision_record_repository_roundtrip(tmp_path):
    from finch.storage.database import Store

    store = Store(tmp_path / "finch.db")
    store.init()
    repo = DecisionRecordRepository(store)
    rec = DecisionRecord(
        id="dec_j1", job_id="j1", draft_id="d1",
        action=DecisionAction.ACCEPT,
        position_source=PositionSource.HUMAN_CONFIRMED,
        position_fingerprint="fp", approved_content_hash="h",
        decided_at=datetime.now(UTC),
    )
    repo.save(rec)
    assert repo.get("j1") is not None
    assert repo.get("j1").action == DecisionAction.ACCEPT
    assert len(repo.list()) == 1


def test_draft_repository_list_by_job(tmp_path):
    from finch.storage.database import Store

    store = Store(tmp_path / "finch.db")
    store.init()
    repo = DraftRepository(store)
    repo.upsert_draft(Draft(id="d1", kind=DraftKind.ORIGINAL, body="a", content_job_id="j1"))
    repo.upsert_draft(Draft(id="d2", kind=DraftKind.ORIGINAL, body="b", content_job_id="j2"))
    assert [d.id for d in repo.list_by_job("j1")] == ["d1"]
    assert repo.list_by_job("nope") == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_repositories.py -q`
Expected: FAIL — `ImportError: cannot import name 'DecisionRecordRepository'`。

- [ ] **Step 3: 实现存储**

`src/finch/storage/repositories.py` 在 `ReviewRecord` 附近新增：

```python
class DecisionRecordRecord(SQLModel, table=True):
    """DecisionRecord 持久化模型（单一决策点）。"""

    id: str = Field(primary_key=True)  # "dec_<job_id>"
    job_id: str = Field(index=True)
    draft_id: str = Field(index=True)
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DecisionRecordRepository:
    """DecisionRecord 仓储（加性，与旧 ReviewRepository 并存）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, record: DecisionRecord) -> None:
        with Session(self.store.engine) as session:
            session.merge(
                DecisionRecordRecord(
                    id=record.id,
                    job_id=record.job_id,
                    draft_id=record.draft_id,
                    payload_json=record.model_dump_json(),
                    updated_at=datetime.now(UTC),
                )
            )
            session.commit()

    def get(self, job_id: str) -> DecisionRecord | None:
        with Session(self.store.engine) as session:
            record = session.get(DecisionRecordRecord, f"dec_{job_id}")
            if record is None:
                return None
            return DecisionRecord.model_validate_json(record.payload_json)

    def list(self) -> list[DecisionRecord]:
        with Session(self.store.engine) as session:
            records = list(session.exec(select(DecisionRecordRecord)))
            return [DecisionRecord.model_validate_json(r.payload_json) for r in records]
```

`DraftRepository` 追加 `list_by_job`：

```python
    def list_by_job(self, job_id: str) -> list[Draft]:
        """按 content_job_id 列出草稿（payload_json 内字段，需全表扫描后过滤）。"""
        return [d for d in self.list_drafts() if d.content_job_id == job_id]
```

（需在 `repositories.py` 顶部确认已 import `DecisionRecord` / `DecisionAction` / `PositionSource`。）

- [ ] **Step 4: 写 alembic 迁移**

`alembic/versions/<rev>_add_decision_record.py`（`down_revision` 指向 `07a1d0830293`）：

```python
"""add decision record

Revision ID: <rev>
Revises: 07a1d0830293
Create Date: 2026-09-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = '<rev>'
down_revision: Union[str, None] = '07a1d0830293'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('decisionrecordrecord',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('job_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('draft_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('payload_json', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('decisionrecordrecord', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_decisionrecordrecord_job_id'), ['job_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_decisionrecordrecord_draft_id'), ['draft_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('decisionrecordrecord', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_decisionrecordrecord_draft_id'))
        batch_op.drop_index(batch_op.f('ix_decisionrecordrecord_job_id'))
    op.drop_table('decisionrecordrecord')
```

（用 `alembic revision -m "add decision record"` 生成文件名与 revision id，替换 `<rev>`。）

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_repositories.py tests/unit/test_alembic.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/finch/storage/repositories.py alembic/versions/<rev>_add_decision_record.py tests/unit/test_repositories.py tests/unit/test_alembic.py
git commit -m "feat(storage): DecisionRecordRepository + list_by_job + alembic migration"
```

---

### Task 3: `position_gate` 非阻塞 + `position_source`

**Files:**
- Modify: `src/finch/graph/content_nodes.py`（`make_position_gate_node` 内 `PositionGateNode.run`）
- Test: `tests/graph/test_content_nodes.py`

**Interfaces:**
- Consumes: `PositionSource`（Task 1）、`position_fingerprint`（已有）。
- Produces: `position_gate` 输出在推断通过时带 `inferred_position=True`，`items` 内 job 的 `author_position.position_source=INFERRED`；字段缺失仍 `needs_input` + `output["must_ask"]=["position_incomplete"]`；复用门禁命中带 `reused_approval` + `position_source=REUSED`。

- [ ] **Step 1: 写失败测试**

在 `tests/graph/test_content_nodes.py` 追加（用现有 fake jobs_repo/approvals_repo 模式；若无，构造最小 job）：

```python
from finch.content.jobs import AuthorPosition, ContentJobStatus, PositionSource
from finch.graph.content_nodes import make_position_gate_node


def _job_with_position(job_id, decision, tradeoff, confirmed=False, change_mind_if=None):
    from finch.content.jobs import ContentJob, IntendedEffect, SuccessCriterion
    from finch.content.models import DraftKind

    return ContentJob(
        id=job_id, source_card_ids=["ev1"], reader_problem="r", audience="a",
        intended_effect=IntendedEffect(understand="u"),
        author_position=AuthorPosition(
            claim="c", decision=decision, tradeoff=tradeoff,
            change_mind_if=change_mind_if, confirmed=confirmed,
        ),
        success_criteria=[SuccessCriterion(id="c1", description="d", measurement="critic")],
        recommended_format=DraftKind.ORIGINAL, status=ContentJobStatus.NEEDS_INPUT,
    )


def test_position_gate_passes_inferred_position():
    from finch.graph.context import items_payload

    node = make_position_gate_node(jobs_repo=None, approvals_repo=None)
    job = _job_with_position("j1", "d", "t")
    ctx = {"content_jobs": items_payload([job]), "evidence_cards": items_payload([])}
    result = node.run(ctx)
    assert result.status == "succeeded"
    items = json.loads(json.dumps(result.output)) if False else result.output
    # output["items"] 已含 job（作者立场 source=INFERRED）
```

（注：`items_payload` 序列化后 `output["items"]` 是 dict 列表；断言改为从 `result.output["items"]` 解析出 `ContentJob` 并检查 `author_position.position_source == PositionSource.INFERRED`。用 `parse_items` 反解。）

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/graph/test_content_nodes.py -q -k inferred`
Expected: FAIL — 当前 gate 对未确认立场返回 `needs_input`。

- [ ] **Step 3: 改 `PositionGateNode.run`**

将 `content_nodes.py:948-1003` 的 `ready`/reuse/needs_input 段替换为：

```python
            position = primary.author_position
            has_decision_tradeoff = (
                position is not None
                and bool(position.decision)
                and bool(position.tradeoff)
            )

            # 复用门禁（P2）：逐字一致 + 未撤销 + 无 change_mind_if → 复用确认（REUSED）。
            if (
                has_decision_tradeoff
                and approvals_repo is not None
                and jobs_repo is not None
                and position is not None
            ):
                fingerprint = position_fingerprint(position)
                approval = approvals_repo.find_active(fingerprint)
                if approval is not None and not position.change_mind_if:
                    confirmed_pos = position.model_copy(
                        update={"confirmed": True, "position_source": PositionSource.REUSED}
                    )
                    confirmed_job = primary.model_copy(update={"author_position": confirmed_pos})
                    jobs_repo.upsert_job(confirmed_job)
                    output["items"] = [confirmed_job.model_dump(mode="json")]
                    output["reused_approval"] = fingerprint
                    return NodeResult(status="succeeded", output=output)

            if position is not None and position.confirmed:
                # 已人类确认：原样通过。
                output["items"] = [primary.model_dump(mode="json")]
                return NodeResult(status="succeeded", output=output)

            if has_decision_tradeoff:
                # 可推断但未确认：非阻塞，标记 INFERRED 后通过（生成候选草稿）。
                inferred_pos = position.model_copy(
                    update={"position_source": PositionSource.INFERRED}
                )
                inferred_job = primary.model_copy(update={"author_position": inferred_pos})
                if jobs_repo is not None:
                    jobs_repo.upsert_job(inferred_job)
                output["items"] = [inferred_job.model_dump(mode="json")]
                output["inferred_position"] = True
                return NodeResult(status="succeeded", output=output)

            # 立场不完整：无法起草，仍阻塞 + must_ask 信号。
            pos = primary.author_position
            output["items"] = [primary.model_dump(mode="json")]
            output["questions"] = list(primary.missing_questions)[:3]
            output["must_ask"] = ["position_incomplete"]
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

（顶部 import `PositionSource`。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/graph/test_content_nodes.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/graph/content_nodes.py tests/graph/test_content_nodes.py
git commit -m "feat(gate): non-blocking inferred positions + position_source"
```

---

### Task 4: draft 节点写入 `run_id`

**Files:**
- Modify: `src/finch/graph/content_nodes.py`（`DraftNode.run` 收集段）
- Test: `tests/graph/test_content_nodes.py`

- [ ] **Step 1: 写失败测试**

在 `tests/graph/test_content_nodes.py` 追加（monkeypatch `write_original` 返回带 content_job_id 的 Draft，注入 ctx["run_id"]）：

```python
def test_draft_node_sets_run_id(monkeypatch):
    from finch.content.models import Draft, DraftKind
    from finch.graph.context import items_payload
    from finch.graph.content_nodes import make_draft_node

    def fake_write_original(runner, cards, job):
        return Draft(id="d1", kind=DraftKind.ORIGINAL, body="hi", content_job_id=job.id)

    monkeypatch.setattr("finch.graph.content_nodes.write_original", fake_write_original)
    node = make_draft_node(None, None, fake_write_original, None)
    # ... 构造 ctx：ready_jobs + evidence_cards + candidates + run_id
    ctx = {
        "ready_jobs": items_payload([_job_with_position("j1", "d", "t")]),
        "evidence_cards": items_payload([]),
        "candidates": items_payload([]),
        "run_id": "r1",
    }
    result = node.run(ctx)
    drafts = parse_items(result.output, Draft)
    assert drafts and drafts[0].run_id == "r1"
```

（`make_draft_node` 签名需确认：`make_draft_node(runner, write_reply, write_original, gates)`；`gates` 用 `QualityGates()`。）

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/graph/test_content_nodes.py -q -k draft_node`
Expected: FAIL — `run_id` 未写入。

- [ ] **Step 3: 写 run_id**

在 `DraftNode.run` 的 Phase 3 收集段（`content_nodes.py:156`）后，给每条 draft 打 `run_id`：

```python
            # Phase 3（收集）：cap 已在 Phase 1 预留，按顺序丢弃 None，并打上 run_id。
            run_id = ctx.get("run_id", "")
            drafts = [
                d.model_copy(update={"run_id": run_id})
                for d in written
                if d is not None
            ]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/graph/test_content_nodes.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/graph/content_nodes.py tests/graph/test_content_nodes.py
git commit -m "feat(graph): stamp run_id on drafts"
```

---

### Task 5: 决策服务 `accept`/`skip`（原子落地 + 投影）

**Files:**
- Create: `src/finch/review/decision.py`
- Test: `tests/unit/test_review_service.py`（或新建 `tests/unit/test_decision.py`）

**Interfaces:**
- Consumes: `ContentJobRepository` / `DraftRepository` / `PositionApprovalRepository` / `ReviewRepository` / `DecisionRecordRepository`（Task 2）、`DecisionRecord` / `DecisionAction` / `PositionSource`（Task 1）、`position_fingerprint`（已有）。
- Produces:
  - `DecisionService(...).accept(job_id) -> DecisionRecord`
  - `DecisionService(...).skip(job_id, reason) -> DecisionRecord`
  - `content_hash(body: str) -> str`（`hashlib.sha256`）

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_decision.py`：

```python
from datetime import UTC, datetime

from finch.content.jobs import (
    AuthorPosition, ContentJob, ContentJobStatus, IntendedEffect, PositionSource, SuccessCriterion, position_fingerprint,
)
from finch.content.models import Draft, DraftKind
from finch.review.decision import DecisionService, content_hash
from finch.review.models import DecisionAction
from finch.storage.database import Store
from finch.storage.repositories import (
    ContentJobRepository, DecisionRecordRepository, DraftRepository,
    PositionApprovalRepository, ReviewRepository,
)


def _job(job_id="j1"):
    return ContentJob(
        id=job_id, source_card_ids=["ev1"], reader_problem="r", audience="a",
        intended_effect=IntendedEffect(understand="u"),
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        success_criteria=[SuccessCriterion(id="c1", description="d", measurement="critic")],
        recommended_format=DraftKind.ORIGINAL, status=ContentJobStatus.NEEDS_INPUT,
    )


def _svc(store):
    return DecisionService(
        jobs=ContentJobRepository(store),
        drafts=DraftRepository(store),
        approvals=PositionApprovalRepository(store),
        reviews=ReviewRepository(store),
        decisions=DecisionRecordRepository(store),
    )


def test_accept_confirms_position_and_approves_draft(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    ContentJobRepository(store).upsert_job(_job("j1"))
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="final body", content_job_id="j1")
    )
    rec = _svc(store).accept("j1")

    assert rec.action == DecisionAction.ACCEPT
    assert rec.position_source == PositionSource.HUMAN_CONFIRMED
    assert rec.approved_content_hash == content_hash("final body")

    job = ContentJobRepository(store).get_job("j1")
    assert job.author_position.confirmed is True
    assert job.author_position.position_source == PositionSource.HUMAN_CONFIRMED
    # 向后兼容投影：PositionApproval 落库
    assert PositionApprovalRepository(store).find_active(
        position_fingerprint(job.author_position)
    ) is not None
    # 决策记录落库
    assert DecisionRecordRepository(store).get("j1") is not None


def test_skip_marks_do_not_write(tmp_path):
    store = Store(tmp_path / "finch.db")
    store.init()
    ContentJobRepository(store).upsert_job(_job("j1"))
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="b", content_job_id="j1")
    )
    rec = _svc(store).skip("j1", "not_now")
    assert rec.action == DecisionAction.SKIP
    job = ContentJobRepository(store).get_job("j1")
    assert job.status == ContentJobStatus.DO_NOT_WRITE
    assert job.reject_reason == "not_now"


def test_content_hash_deterministic():
    assert content_hash("a") == content_hash("a")
    assert content_hash("a") != content_hash("b")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_decision.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'finch.review.decision'`。

- [ ] **Step 3: 实现 `decision.py`**

```python
"""单一决策点服务：accept/skip 把「确认立场 + 批准草稿」合并为一次原子落地（加性）。"""

import hashlib
from datetime import UTC, datetime

from finch.content.jobs import ContentJobStatus, PositionSource, position_fingerprint
from finch.review.models import DecisionAction, DecisionRecord, ReviewAction, ReviewDecision, SkipReason
from finch.storage.repositories import (
    ContentJobRepository, DecisionRecordRepository, DraftRepository,
    PositionApprovalRepository, ReviewRepository,
)


def content_hash(body: str) -> str:
    """返回正文的确定性 SHA-256 摘要（批准绑定具体文本版本）。"""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class DecisionService:
    """把一次「采用/跳过」落到 job/approval/review/decision 上（权威 = DecisionRecord）。"""

    def __init__(
        self,
        *,
        jobs: ContentJobRepository,
        drafts: DraftRepository,
        approvals: PositionApprovalRepository,
        reviews: ReviewRepository,
        decisions: DecisionRecordRepository,
    ) -> None:
        self.jobs = jobs
        self.drafts = drafts
        self.approvals = approvals
        self.reviews = reviews
        self.decisions = decisions

    def _require_job_and_draft(self, job_id: str) -> tuple:
        job = self.jobs.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        drafts = self.drafts.list_by_job(job_id)
        if not drafts:
            raise KeyError(f"no draft for job {job_id}")
        return job, drafts[0]

    def accept(self, job_id: str) -> DecisionRecord:
        job, draft = self._require_job_and_draft(job_id)
        position = job.author_position
        if position is None or not position.claim or not position.decision or not position.tradeoff:
            raise ValueError(f"position incomplete for job {job_id}")
        confirmed = position.model_copy(
            update={"confirmed": True, "position_source": PositionSource.HUMAN_CONFIRMED}
        )
        # 向后兼容投影 1：ContentJob.author_position
        self.jobs.upsert_job(job.model_copy(update={"author_position": confirmed}))
        # 向后兼容投影 2：PositionApproval（fingerprint 复用）
        self.approvals.approve(position_fingerprint(confirmed), job_id)
        # 向后兼容投影 3：ReviewDecision(APPROVE)（周复盘/voice 读旧记录）
        self.reviews.save_review(
            ReviewDecision(
                id=f"rev_{draft.id}", draft_id=draft.id,
                action=ReviewAction.APPROVE, decided_at=datetime.now(UTC),
            )
        )
        # 权威记录
        record = DecisionRecord(
            id=f"dec_{job_id}", job_id=job_id, draft_id=draft.id,
            action=DecisionAction.ACCEPT,
            position_source=PositionSource.HUMAN_CONFIRMED,
            position_fingerprint=position_fingerprint(confirmed),
            approved_content_hash=content_hash(draft.body),
            decided_at=datetime.now(UTC),
        )
        self.decisions.save(record)
        return record

    def skip(self, job_id: str, reason: str) -> DecisionRecord:
        job, draft = self._require_job_and_draft(job_id)
        self.jobs.upsert_job(
            job.model_copy(
                update={"status": ContentJobStatus.DO_NOT_WRITE, "reject_reason": reason}
            )
        )
        if job.author_position is not None:
            self.approvals.revoke(position_fingerprint(job.author_position))
        record = DecisionRecord(
            id=f"dec_{job_id}", job_id=job_id, draft_id=draft.id,
            action=DecisionAction.SKIP,
            position_source=PositionSource.INFERRED,
            position_fingerprint=(
                position_fingerprint(job.author_position) if job.author_position else ""
            ),
            approved_content_hash="",
            decided_at=datetime.now(UTC),
        )
        self.decisions.save(record)
        return record
```

（需确认 `PositionApprovalRepository.revoke(fp)` 与 `approve(fp, job_id)` 的现有签名；沿用 `gate/resolve.py` 中已使用的 `approve(fp, job_id)` / `revoke(fp)`。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_decision.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/review/decision.py tests/unit/test_decision.py
git commit -m "feat(review): DecisionService accept/skip (atomic + back-compat projections)"
```

---

### Task 6: `finch decide` CLI

**Files:**
- Modify: `src/finch/cli.py`（新增 `decide_app` 或顶层命令 `finch decide`）
- Test: `tests/unit/test_cli_run.py`

**Interfaces:**
- Consumes: `DecisionService`（Task 5）、`DecisionRecordRepository`（Task 2）。
- Produces: `finch decide <job-id> --action accept|skip [--reason "..."] [--json]`。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_cli_run.py` 追加：

```python
def test_decide_accept(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    # 种子 job + draft
    ContentJobRepository(store).upsert_job(
        _job(job_id="j1", author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"))
    )
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="b", content_job_id="j1")
    )
    r = CliRunner().invoke(app, ["decide", "j1", "--action", "accept", "--json"])
    assert r.exit_code == 0, r.output
    assert '"action": "accept"' in r.output
    assert ContentJobRepository(store).get_job("j1").author_position.confirmed is True


def test_decide_skip(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    ContentJobRepository(store).upsert_job(
        _job(job_id="j1", author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"))
    )
    DraftRepository(store).upsert_draft(
        Draft(id="d1", kind=DraftKind.ORIGINAL, body="b", content_job_id="j1")
    )
    r = CliRunner().invoke(app, ["decide", "j1", "--action", "skip", "--reason", "not_now", "--json"])
    assert r.exit_code == 0, r.output
    assert ContentJobRepository(store).get_job("j1").status.value == "do_not_write"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_cli_run.py -q -k decide`
Expected: FAIL — `No such command 'decide'`。

- [ ] **Step 3: 实现 CLI**

`src/finch/cli.py`：新增 import 与命令：

```python
from .review.decision import DecisionService
from .review.models import DecisionAction
from .storage.repositories import DecisionRecordRepository

app.add_typer(...)  # 已有；decide 直接用 @app.command

@app.command("decide")
def decide(
    item_id: str = typer.Argument(..., help="primary ContentJob id"),
    action: str = typer.Option(..., "--action", help="accept|revise|skip"),
    reason: str = typer.Option(None, "--reason", help="--action skip 的拒绝理由"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """单一决策点：accept 同时确认立场 + 批准草稿；skip 标记 DO_NOT_WRITE。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    svc = DecisionService(
        jobs=ContentJobRepository(store),
        drafts=DraftRepository(store),
        approvals=PositionApprovalRepository(store),
        reviews=ReviewRepository(store),
        decisions=DecisionRecordRepository(store),
    )
    try:
        action_enum = DecisionAction(action)
    except ValueError as exc:
        typer.echo(f"invalid --action: {action}")
        raise typer.Exit(code=1) from exc
    try:
        if action_enum is DecisionAction.ACCEPT:
            record = svc.accept(item_id)
        elif action_enum is DecisionAction.SKIP:
            if not reason:
                typer.echo("--action skip requires --reason")
                raise typer.Exit(code=1)
            record = svc.skip(item_id, reason)
        else:
            typer.echo("--action revise not yet supported (Plan 2)")
            raise typer.Exit(code=1)
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(record.model_dump_json(indent=2))
    else:
        typer.echo(record.action.value)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_cli_run.py -q -k decide`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/cli.py tests/unit/test_cli_run.py
git commit -m "feat(cli): finch decide accept/skip (single decision point)"
```

---

### Task 7: 全量回归

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -q`
Expected: PASS（新增测试通过，旧测试回归绿；`position_gate` 非阻塞后，依赖 `needs_input` 的旧测试若有按 `confirmed=False` 预期阻塞的，需同步更新为预期 `inferred` 通过。）

- [ ] **Step 2: lint + 类型**

Run: `uv run ruff check . && uv run mypy src`
Expected: 无错误。

- [ ] **Step 3: 提交（如有修正）**

```bash
git add -A && git commit -m "chore: regression fixes for single decision point"
```

---

## Self-Review 结果

- **Spec 覆盖（Plan 1 范围）**：§3.1 非阻塞 gate → Task 3；§3.2 `position_incomplete` 信号 → Task 3；§4 模型 → Task 1/2；§4.1 原子落地 + 投影 → Task 5；§5 采用/跳过语义 → Task 5/6；§11 加性迁移 → Task 2。`revise`、`safety_risk`、`daily --json`/`next --json`、Codex 编排 属 Plan 2，明确 deferred。
- **Placeholder 扫描**：无 TBD/TODO；alembic revision id 用 `alembic revision` 生成后回填（唯一非字面量，已注明）。
- **类型一致性**：`PositionSource`（content/jobs.py）在 Task 1 定义、Task 3/5 引用一致；`DecisionService` 构造签名在 Task 5 定义、Task 6 引用一致；`DecisionRecordRepository.get(job_id)`/`.save(rec)` 在 Task 2 定义、Task 5/6 引用一致。
