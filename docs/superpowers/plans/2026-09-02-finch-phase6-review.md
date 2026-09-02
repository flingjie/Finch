# Finch Phase 6（Review 与反馈）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **并发：** 同一 Wave 的任务文件互斥，并行派发。共享类型以本文 **Shared Contracts** 为准，不得自行改名。

**Goal:** 落地 Phase 6：`review/`（service/feedback/models）、`finch review list/show/approve/revise/skip` CLI、修改前后 Diff 保存、标准跳过原因、发布链接与互动数据反馈，以及草稿/审核/反馈三类仓储（补齐 Phase 5 漏掉的 `DraftRecord`）。

**Architecture:** 确定性 Python + SQLModel 仓储。`review` 是 CLI 层（非每日 Graph 节点）：`run_daily` 跑完后把最终草稿落 `DraftRecord`；后续 `finch review` 命令按 draft_id 查询草稿、落 `ReviewRecord`（approve/revise/skip）与 `FeedbackRecord`（发布链接/互动数据）。`revise` 从 CLI 重新进入 writer（不走 Graph 回边，本阶段只保存用户修订 + diff，不重新 critique）。

**Tech Stack:** Python 3.12、Pydantic v2、SQLModel、Typer、`difflib`、既有 `Store` / `Draft` / `DraftKind` / `GraphRuntime`、pytest、Ruff、mypy。

**Spec:** `docs/superpowers/specs/2026-09-02-finch-phases-4-9-design.md`（§3.4 仓储、§3.7、§5 人工审核）。需求基线 `docs/Finch-Codex-Development-Plan.md`（Phase 6、§10 review CLI）。

## Global Constraints

- 人工审核是 CLI，不是每日 Graph 节点（设计 doc §5）。
- `approve/revise/skip` 三条路径**可重放**（幂等，重复执行不产生重复记录）。
- 区分「内容不好」（`skip` 理由 `evidence_insufficient`/`low_quality`/`not_relevant`）与「用户暂时不想参与」（`not_now`）。
- `revise` 保存修改前后 diff；不自动发布、不触发 Twitter 写命令。
- 发布链接与互动数据由用户手动填写（Finch 不自动发布）。
- Python `>=3.12`；命令：`uv run pytest`、`uv run ruff check .`、`uv run mypy src`。

---

## Parallel Dispatch

| Wave | 并行任务 | 依赖 |
|---|---|---|
| 1 | G1 Review 模型 · G2 仓储 | 无 |
| 2 | G3 Review 服务 + 反馈 | Wave 1 |
| 3 | G4 CLI + run_daily 落草稿 | Wave 2 |

每 Wave 一个 worktree per task（`git worktree add ../finch-phase6-gN -b phase6/gN-<slug>`）。

---

## File Map

| 路径 | 职责 | 独占任务 |
|---|---|---|
| `src/finch/review/models.py`, `tests/unit/test_review_models.py` | `ReviewAction`/`SkipReason`/`ReviewDecision`/`Feedback` | G1 |
| `src/finch/storage/repositories.py`, `tests/unit/test_repositories.py` | `DraftRecord`/`ReviewRecord`/`FeedbackRecord` + 3 仓储 | G2 |
| `src/finch/review/service.py`, `src/finch/review/feedback.py`, `tests/unit/test_review_service.py` | `ReviewService`/`compute_diff`/`FeedbackService` | G3 |
| `src/finch/cli.py`, `tests/unit/test_cli_review.py` | `finch review *` + `run_daily` 落草稿 | G4 |

**禁止改：** `src/finch/review/__init__.py`、`src/finch/storage/__init__.py`。

---

## Shared Contracts

### C1. Review 模型（G1 实现）

```python
from datetime import UTC, datetime
from enum import StrEnum
from pydantic import BaseModel, Field

class ReviewAction(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    SKIP = "skip"

class SkipReason(StrEnum):
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    NOT_RELEVANT = "not_relevant"
    LOW_QUALITY = "low_quality"
    NOT_NOW = "not_now"
    OTHER = "other"

class ReviewDecision(BaseModel):
    id: str                                  # "rev_<draft_id>"（幂等键）
    draft_id: str
    action: ReviewAction
    reason: str | None = None                # skip 理由（SkipReason.value）
    revised_body: str | None = None          # revise 后的正文
    diff: str | None = None                  # 修改前后 unified diff
    decided_at: datetime

class Feedback(BaseModel):
    draft_id: str
    published_url: str | None = None
    interaction_metrics: dict = Field(default_factory=dict)
    recorded_at: datetime
```

### C2. 仓储（G2 实现）

```python
class DraftRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)             # = draft.id
    kind: str                                     # DraftKind.value
    candidate_id: str | None = None
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

class DraftRepository:
    def __init__(self, store: Store): ...
    def upsert_draft(self, draft: Draft) -> None: ...      # 按 id merge
    def get_draft(self, draft_id: str) -> Draft | None: ...
    def list_drafts(self) -> list[Draft]: ...

class ReviewRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)             # = decision.id
    draft_id: str = Field(index=True)
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

class ReviewRepository:
    def __init__(self, store: Store): ...
    def save_review(self, decision: ReviewDecision) -> None: ...  # 按 id merge
    def get_review(self, draft_id: str) -> ReviewDecision | None: ...
    def list_reviews(self) -> list[ReviewDecision]: ...

class FeedbackRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)             # = feedback.draft_id
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

class FeedbackRepository:
    def __init__(self, store: Store): ...
    def save_feedback(self, feedback: Feedback) -> None: ...     # 按 id merge
    def get_feedback(self, draft_id: str) -> Feedback | None: ...
```

`Store.init()` 内 `from finch.storage import repositories as _  # noqa: F401` 再 `create_all` 已由 Phase 4 建立，新增表自动注册（无需改 `database.py`，除非新表未 import）。G2 需在 `repositories.py` 追加三个 Record + 三个 Repository，并 import `Draft`（`finch.content.models`）、`ReviewDecision`/`Feedback`（`finch.review.models`）。`get_*` 用 `model_validate_json`，不存在返回 None。

### C3. Review 服务 + 反馈（G3 实现）

```python
def compute_diff(before: str, after: str) -> str:
    # difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="") 的 "\n".join

class ReviewService:
    def __init__(self, drafts: DraftRepository, reviews: ReviewRepository): ...
    def list_pending(self) -> list[Draft]:
        # 返回尚未有 ReviewRecord 的草稿（已审核的 draft_id 过滤掉）
    def show(self, draft_id: str) -> Draft | None: ...
    def approve(self, draft_id: str) -> ReviewDecision: ...   # id="rev_<draft_id>"
    def revise(self, draft_id: str, revised_body: str) -> ReviewDecision: ...  # diff + revised_body
    def skip(self, draft_id: str, reason: SkipReason) -> ReviewDecision: ...   # reason=reason.value

# review/feedback.py
class FeedbackService:
    def __init__(self, feedbacks: FeedbackRepository): ...
    def record(self, draft_id: str, *, published_url: str | None = None,
               metrics: dict | None = None) -> Feedback: ...
```

`approve/revise/skip` 对不存在的 draft_id 抛 `KeyError`（或 ValueError）。`save_review` 幂等：`ReviewDecision.id = "rev_<draft_id>"`，重复调用同 draft_id 覆盖同一条记录（可重放）。

### C4. CLI（G4 实现）

```python
review_app = typer.Typer(help="Review drafts")
app.add_typer(review_app, name="review")

@review_app.command("list")     # 列出 pending 草稿（id + kind + 正文前 80 字符）
@review_app.command("show")     # finch review show <DRAFT_ID> 打印全文
@review_app.command("approve")  # finch review approve <DRAFT_ID>
@review_app.command("revise")   # finch review revise <DRAFT_ID> --file revised.md
@review_app.command("skip")     # finch review skip <DRAFT_ID> --reason evidence_insufficient
@review_app.command("feedback") # finch review feedback <DRAFT_ID> --url <URL> --metrics '<json>'
```

`run_daily` 追加：GraphRuntime.run 后，读 `critique` 节点（若无则 `draft` 节点）的 `output_json`，`parse_items(..., Draft)` 后对每张 `DraftRepository.upsert_draft`。草稿为空/节点缺失则跳过。

---

### Task G1: Review 数据模型

**Files:** Create `src/finch/review/models.py`, `tests/unit/test_review_models.py`

**Interfaces:** Produces C1。

- [ ] **Step 1: 写失败测试**

```python
from finch.review.models import Feedback, ReviewAction, ReviewDecision, SkipReason

def test_enums():
    assert ReviewAction.APPROVE.value == "approve"
    assert SkipReason.EVIDENCE_INSUFFICIENT.value == "evidence_insufficient"

def test_review_decision_shape():
    d = ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.SKIP,
                       reason=SkipReason.NOT_NOW.value,
                       decided_at=__import__("datetime").datetime(2026, 1, 1))
    assert d.action == ReviewAction.SKIP and d.reason == "not_now"

def test_feedback_shape():
    f = Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                 interaction_metrics={"likes": 3}, recorded_at=__import__("datetime").datetime(2026, 1, 1))
    assert f.interaction_metrics["likes"] == 3
```

- [ ] **Step 2:** `uv run pytest tests/unit/test_review_models.py -v` → FAIL
- [ ] **Step 3:** 实现 C1（`decided_at`/`recorded_at` 用 `datetime`，测试里 `datetime(2026,1,1)` 无 tz 也可通过——用 `datetime` 类型不加 tz 约束）
- [ ] **Step 4:** PASS
- [ ] **Step 5:** commit `feat: add review action, skip reason, and decision models`

### Task G2: 仓储

**Files:** Modify `src/finch/storage/repositories.py`；Modify `tests/unit/test_repositories.py`

**Interfaces:** Consumes `Store`/`Draft`/C1。Produces C2。

- [ ] **Step 1: 写失败测试**

```python
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence
from finch.review.models import Feedback, ReviewAction, ReviewDecision, SkipReason
from finch.storage.database import Store
from finch.storage.repositories import DraftRepository, FeedbackRepository, ReviewRepository

def _draft():
    return Draft(id="d1", kind=DraftKind.REPLY, candidate_id="t1", body="hi",
                 claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                  confidence=ClaimConfidence.VERIFIED)])

def test_draft_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite"); store.init()
    repo = DraftRepository(store)
    repo.upsert_draft(_draft())
    assert repo.get_draft("d1") is not None
    assert repo.list_drafts()[0].id == "d1"

def test_review_save_idempotent(tmp_path):
    store = Store(tmp_path / "db.sqlite"); store.init()
    repo = ReviewRepository(store)
    d1 = ReviewDecision(id="rev_d1", draft_id="d1", action=ReviewAction.APPROVE,
                        decided_at=__import__("datetime").datetime(2026, 1, 1))
    repo.save_review(d1)
    repo.save_review(d1.model_copy(update={"action": ReviewAction.SKIP, "reason": "not_now"}))
    got = repo.get_review("d1")
    assert got is not None and got.action == ReviewAction.SKIP  # 覆盖同一条，不重复

def test_feedback_roundtrip(tmp_path):
    store = Store(tmp_path / "db.sqlite"); store.init()
    repo = FeedbackRepository(store)
    repo.save_feedback(Feedback(draft_id="d1", published_url="https://x.com/u/status/1",
                                recorded_at=__import__("datetime").datetime(2026, 1, 1)))
    assert repo.get_feedback("d1").published_url == "https://x.com/u/status/1"
```

- [ ] **Step 2:** FAIL
- [ ] **Step 3:** 追加三个 Record + 三个 Repository；`repositories.py` 顶部 import `Draft`、`ReviewDecision`/`Feedback`（注意避免循环 import：`review.models` 只依赖 pydantic/`evidence.models`，安全）
- [ ] **Step 4:** PASS（`test_repositories.py` + `test_storage.py`）
- [ ] **Step 5:** commit `feat: persist draft, review, and feedback records`

### Task G3: Review 服务 + 反馈

**Files:** Create `src/finch/review/service.py`, `src/finch/review/feedback.py`, `tests/unit/test_review_service.py`

**Interfaces:** Consumes C1/C2。Produces C3。

- [ ] **Step 1: 写失败测试**

```python
from datetime import UTC, datetime
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence
from finch.review.feedback import FeedbackService
from finch.review.models import ReviewAction, SkipReason
from finch.review.service import ReviewService, compute_diff
from finch.storage.database import Store
from finch.storage.repositories import DraftRepository, FeedbackRepository, ReviewRepository

def _draft(id="d1", body="hi"):
    return Draft(id=id, kind=DraftKind.REPLY, candidate_id="t1", body=body,
                 claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                  confidence=ClaimConfidence.VERIFIED)])

def _svc(tmp_path):
    store = Store(tmp_path / "db.sqlite"); store.init()
    return (ReviewService(DraftRepository(store), ReviewRepository(store)), store)

def test_compute_diff():
    diff = compute_diff("hello\nworld", "hello\nfinch")
    assert "-world" in diff and "+finch" in diff

def test_approve_revise_skip_replayable(tmp_path):
    svc, store = _svc(tmp_path)
    DraftRepository(store).upsert_draft(_draft())
    a = svc.approve("d1")
    assert a.action == ReviewAction.APPROVE
    # 可重放：重复 approve 不新增记录
    svc.approve("d1")
    assert len(ReviewRepository(store).list_reviews()) == 1
    # skip 覆盖
    s = svc.skip("d1", SkipReason.NOT_NOW)
    assert s.reason == "not_now"

def test_revise_saves_diff(tmp_path):
    svc, store = _svc(tmp_path)
    DraftRepository(store).upsert_draft(_draft(body="before"))
    r = svc.revise("d1", "after")
    assert r.revised_body == "after" and r.diff and "-before" in r.diff

def test_list_pending_excludes_reviewed(tmp_path):
    svc, store = _svc(tmp_path)
    repo = DraftRepository(store)
    repo.upsert_draft(_draft("d1")); repo.upsert_draft(_draft("d2"))
    svc.approve("d1")
    assert [d.id for d in svc.list_pending()] == ["d2"]
```

- [ ] **Step 2:** FAIL
- [ ] **Step 3:** 实现 C3（`compute_diff` 用 `difflib`；`ReviewService` 方法在 draft 缺失时抛 `KeyError`；`FeedbackService.record` 落 Feedback）
- [ ] **Step 4:** PASS
- [ ] **Step 5:** commit `feat: add review service with approve revise skip`

### Task G4: CLI + run_daily 落草稿

**Files:** Modify `src/finch/cli.py`；Create `tests/unit/test_cli_review.py`

**Interfaces:** Consumes C2/C3、`parse_items`/`Draft`/`DailyBrief`。Produces C4。

- [ ] **Step 1: 写失败测试**

```python
from typer.testing import CliRunner
from finch.cli import app

def test_review_subcommands_exist():
    r = CliRunner()
    for cmd in ["list", "show", "approve", "revise", "skip", "feedback"]:
        res = r.invoke(app, ["review", cmd, "--help"])
        assert res.exit_code == 0, cmd
```

- [ ] **Step 2:** FAIL（`review` 子命令不存在）
- [ ] **Step 3:** 实现 `review_app` 六个子命令（`show`/`approve`/`revise`/`skip`/`feedback` 从 settings 加载 store + 仓储 + 服务；`revise` 读 `--file` 内容；`skip` 用 `SkipReason` 枚举校验 `--reason`；`feedback` 的 `--metrics` 解析 JSON）。`run_daily` 末尾读 `critique`（或 `draft`）节点 output_json → `parse_items(..., Draft)` → 每张 `DraftRepository.upsert_draft`
- [ ] **Step 4:** `uv run pytest`、`uv run ruff check .`、`uv run mypy src` 全 PASS（`test_cli_review` 只测 `--help`，不跑真实 daily/review）
- [ ] **Step 5:** commit `feat: add review CLI and persist drafts on daily run`

---

## Out of scope（禁止本计划实现）

- `revise` 重新 critique / 重新进入 writer 的完整闭环（本阶段只保存修订 + diff；设计 doc §3.7 的完整语义留待后续）。
- 自动发布、自动回复、任何 Twitter 写命令。
- `NEEDS_INPUT`、归档为学习材料。
- 每周复盘分析（Phase 9）。

## Self-review

| Spec 条目 | 任务 |
|---|---|
| ReviewAction/SkipReason/ReviewDecision/Feedback 模型 | G1 |
| DraftRecord（补 Phase 5）+ ReviewRecord + FeedbackRecord 仓储 | G2 |
| approve/revise/skip 服务 + diff + 幂等可重放 | G3 |
| `finch review list/show/approve/revise/skip/feedback` CLI | G4 |
| run_daily 落草稿供 review 查询 | G4 |
| 发布链接 + 互动数据反馈 | G3, G4 |
| 三条路径可重放 + 区分内容不好/暂不参与 | G3 |
| 自动发布 | 明确 Out of scope |
