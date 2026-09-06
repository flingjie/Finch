# 收件箱统一 + 存储收敛（Phase 1）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `inbox/` 产品层，让 `finch next`/`decide` 成为同时处理原创草稿与互动候选的唯一人工门；删除 `review`/`engagement`/`jobs`/`gate` 命令与 `ReviewRecord`/`ReviewHistoryRecord`/`PositionApprovalRecord` 三张表，`DecisionRecord` 成为唯一权威决策记录，`feedback` 提升为 `learn`、`run weekly` 提升为 `weekly`。

**Architecture:** 新建 `src/finch/inbox/`（`models.py` 投影模型 + `DecisionRecord`/`DecisionAction` 迁入；`service.py` 投影、确定性选择 `next_item`、双轨决策 `InboxDecisionService`）。CLI 瘦身为薄命令层，`next`/`decide` 走 inbox 服务。旧 `review/`、`gate/` 模块删除，`feedback`→`learn`、`weekly` 读者迁移到 `DecisionRecord`。本阶段**不改** 10 节点 Graph（Graph 收敛在 Phase 2）。

**Tech Stack:** Python 3.12、Pydantic 2、SQLModel、typer、pytest、alembic。

## Global Constraints

- Python 3.12+；Pydantic 2 模型（`StrEnum`/`Literal`/`Field`）；SQLModel 记录用 `payload_json` + `session.merge`（幂等）。
- Ruff 选择 `E,F,I,B,UP`，行宽 100，py312；mypy 跑 `src`；测试 `uv run pytest tests/unit/... -v`。
- **无自动发布**：`gh`/`opencli` 只读；`decide accept` 只写本地决策记录，不对外发布。
- **决策唯一权威**：`DecisionRecord` 是 accept/skip/revise 的唯一记录；不再写 `ReviewDecision`/`PositionApproval`。
- **外部帖 ≠ 证据**：互动 `InteractionCandidate` 永远不升级为 `EvidenceCard`（`promote_to_personal` 路径除外）。
- **分数由代码算**：`next_item` 排序、`original_score`、`weighted_total` 全部确定性，模型不得输出 `total`。
- 中文/英文 docstring 均可；新 `inbox/` 模块 docstring 用中文，与相邻模块一致。

---

## File Structure

| 文件 | 职责 | 本阶段动作 |
|---|---|---|
| `src/finch/inbox/__init__.py` | 包 docstring | 新建 |
| `src/finch/inbox/models.py` | `InboxTrack`/`InboxItem` + `DecisionAction`/`DecisionRecord`/`SkipReason`（迁入） | 新建 |
| `src/finch/inbox/service.py` | `build_original_item`/`build_engagement_item`/`original_score`/`select_next`/`next_item` + `content_hash`/`InboxDecisionService` | 新建 |
| `src/finch/inbox/render.py` | `render_inbox`/`render_card` 人类输出 | 新建 |
| `src/finch/learn/__init__.py` / `learn/service.py` | 发布后学习（`Feedback` 迁入），承载 `finch learn` | 新建 |
| `src/finch/cli.py` | 收编 next/decide、加 draft 别名、提升 learn/weekly、删旧命令 | 修改 |
| `src/finch/review/*` | 旧审核模块 | 删除（Task 5） |
| `src/finch/gate/*` | 旧交互层 | 删除（Task 5） |
| `src/finch/content/jobs.py` | 删 `position_fingerprint`、`AuthorPosition.confirmed/position_source` | 修改（Task 6） |
| `src/finch/graph/content_nodes.py` | 删 `make_position_gate_node` 的复用门禁块 | 修改（Task 6） |
| `alembic/versions/XXXX_drop_review_and_position_approval.py` | drop 三张表 | 新建（Task 5） |

---

### Task 1: inbox 投影模型

**Files:**
- Create: `src/finch/inbox/__init__.py`
- Create: `src/finch/inbox/models.py`
- Test: `tests/unit/test_inbox_models.py`

**Interfaces:**
- Produces:
  - `InboxTrack(StrEnum)`：`ORIGINAL = "original"`、`ENGAGEMENT = "engagement"`
  - `InboxItem(BaseModel)`：字段见下（与 spec §7.1 一致）

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_inbox_models.py`：

```python
"""Unit tests for inbox projection models."""

from finch.inbox.models import InboxItem, InboxTrack


def test_inbox_item_round_trips():
    item = InboxItem(
        id="job_1",
        track=InboxTrack.ORIGINAL,
        content_type="original",
        provenance="personal",
        source_refs=["https://github.com/o/r/commit/abc"],
        why_now="replay 是本周热点",
        score=0.72,
        draft_id="draft_1",
        draft="正文",
        position={"claim": "c", "decision": "d", "tradeoff": "t"},
        must_ask=False,
        ask_reasons=[],
        risks=[],
    )
    back = InboxItem.model_validate(item.model_dump(mode="json"))
    assert back == item
    assert back.track == InboxTrack.ORIGINAL
    assert back.content_type == "original"


def test_inbox_item_defaults():
    item = InboxItem(
        id="cand_1",
        track=InboxTrack.ENGAGEMENT,
        content_type="reply",
        provenance="external",
        source_refs=[],
        why_now="",
        score=0.0,
        draft_id=None,
        draft="",
    )
    assert item.position is None
    assert item.must_ask is False
    assert item.ask_reasons == []
    assert item.risks == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_inbox_models.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'finch.inbox'`）

- [ ] **Step 3: 实现模型**

创建 `src/finch/inbox/__init__.py`：

```python
"""收件箱产品层：把原创草稿与互动候选投影成统一决策卡（只读投影，不落库）。"""
```

创建 `src/finch/inbox/models.py`：

```python
"""收件箱投影模型（产品层只读投影 + 唯一权威决策记录）。"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class InboxTrack(StrEnum):
    """收件箱轨道：原创（ContentJob+Draft）或互动（InteractionCandidate）。"""

    ORIGINAL = "original"
    ENGAGEMENT = "engagement"


class InboxItem(BaseModel):
    """一条待决策项的统一投影。"""

    id: str                    # original=job_id；engagement=candidate.id
    track: InboxTrack
    content_type: Literal["original", "reply", "quote"]
    provenance: Literal["personal", "external", "idea"]
    source_refs: list[str]     # commit URL / 帖子 URL / idea 文本摘要
    why_now: str
    score: float               # 原创用 original_score；互动用 ConversationScore.total
    draft_id: str | None = None
    draft: str = ""
    position: dict | None = None  # claim/decision/tradeoff；互动可空
    must_ask: bool = False
    ask_reasons: list[str] = []
    risks: list[str] = []
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_inbox_models.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/finch/inbox/__init__.py src/finch/inbox/models.py tests/unit/test_inbox_models.py
git commit -m "feat(inbox): InboxItem/InboxTrack projection models"
```

---

### Task 2: inbox 投影 + 确定性选择服务

**Files:**
- Create: `src/finch/inbox/service.py`（本任务只含投影 + 选择；决策服务在 Task 3）
- Test: `tests/unit/test_inbox_service.py`

**Interfaces:**
- Consumes（来自 Task 1）: `InboxItem`、`InboxTrack`
- Produces（Task 4 CLI 使用）:
  - `original_score(job: ContentJob, cards_by_id: dict[str, EvidenceCard]) -> float`
  - `build_original_item(job: ContentJob, draft: Draft, *, cards_by_id: dict[str, EvidenceCard], must_ask: bool, ask_reasons: list[str], risks: list[str]) -> InboxItem`
  - `build_engagement_item(candidate: InteractionCandidate) -> InboxItem`
  - `select_next(items: list[InboxItem]) -> InboxItem | None`
  - （`next_item` 组装函数在 Task 4 追加，见 Task 4 Step 3(h)）

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_inbox_service.py`：

```python
"""Unit tests for the inbox projection and selection service."""

from datetime import datetime

from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    ContentScope,
    IntendedEffect,
    SuccessCriterion,
)
from finch.content.models import Draft, DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionCandidate,
)
from finch.inbox.models import InboxTrack
from finch.inbox.service import (
    build_engagement_item,
    build_original_item,
    original_score,
    select_next,
)


def _job(candidate_id=None, decision="d", tradeoff="t", why_now="w"):
    return ContentJob(
        id="job_1",
        source_card_ids=["ev_1"],
        candidate_id=candidate_id,
        reader_problem="rp",
        audience="aud",
        intended_effect=IntendedEffect(understand="u"),
        author_position=AuthorPosition(claim="c", decision=decision, tradeoff=tradeoff),
        success_criteria=[SuccessCriterion(id="s", description="d", measurement="human")],
        recommended_format=DraftKind.ORIGINAL,
        status=ContentJobStatus.READY,
        core_message="core",
        why_now=why_now,
        scope=ContentScope.BOUNDED_LESSON,
    )


def _draft(job_id="job_1", candidate_id=None):
    return Draft(
        id="draft_1", kind=DraftKind.ORIGINAL, candidate_id=candidate_id,
        language="zh", body="正文", content_job_id=job_id,
    )


def _card(card_id="ev_1", conf=ClaimConfidence.SUPPORTED):
    return EvidenceCard(
        id=card_id, event_id="evt", claim="claim", sources=[],
        confidence=conf, publishable=True, topics=["t"],
    )


def _candidate(cand_id="x:p1:reply", action=InteractionAction.DRAFT_REPLY):
    return InteractionCandidate(
        id=cand_id,
        post=ExternalPost(
            id="p1", platform="x", url="https://x.com/u/1", author_id="a",
            author_name="A", content="a post long enough", published_at=datetime.now(),
        ),
        score=ConversationScore(
            relevance=0.5, novelty=0.5, discussability=0.5,
            practical_evidence=0.5, relationship_value=0.5, total=0.5, reasons=[],
        ),
        action=action,
        draft="回复正文",
        approval_required=True,
    )


def test_original_score_is_deterministic():
    a = original_score(_job(), {"ev_1": _card()})
    b = original_score(_job(), {"ev_1": _card()})
    assert a == b
    assert 0.0 <= a <= 1.0


def test_build_original_item_projection():
    item = build_original_item(
        _job(), _draft(), cards_by_id={"ev_1": _card()},
        must_ask=False, ask_reasons=[], risks=[],
    )
    assert item.id == "job_1"
    assert item.track == InboxTrack.ORIGINAL
    assert item.content_type == "original"
    assert item.provenance == "personal"
    assert item.draft == "正文"
    assert item.draft_id == "draft_1"
    assert item.position == {"claim": "c", "decision": "d", "tradeoff": "t"}


def test_build_original_item_idea_provenance():
    job = _job()
    job = job.model_copy(update={"id": "idea_abc123"})
    item = build_original_item(
        job, _draft("idea_abc123"), cards_by_id={},
        must_ask=False, ask_reasons=[], risks=[],
    )
    assert item.provenance == "idea"


def test_build_engagement_item_projection():
    item = build_engagement_item(_candidate())
    assert item.id == "x:p1:reply"
    assert item.track == InboxTrack.ENGAGEMENT
    assert item.content_type == "reply"
    assert item.provenance == "external"
    assert item.draft == "回复正文"
    assert item.score == 0.5
    assert item.source_refs == ["https://x.com/u/1"]


def test_select_next_orders_must_ask_then_original_then_score():
    high_must = InboxItem(
        id="b", track=InboxTrack.ORIGINAL, content_type="original",
        provenance="personal", source_refs=[], why_now="", score=0.1,
        must_ask=True, ask_reasons=["position_incomplete"],
    )
    original = InboxItem(
        id="a", track=InboxTrack.ORIGINAL, content_type="original",
        provenance="personal", source_refs=[], why_now="", score=0.5,
    )
    engagement = InboxItem(
        id="c", track=InboxTrack.ENGAGEMENT, content_type="reply",
        provenance="external", source_refs=[], why_now="", score=0.99,
    )
    assert select_next([engagement, original, high_must]).id == "b"
    assert select_next([engagement, original]).id == "a"
    assert select_next([]) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_inbox_service.py -v`
Expected: FAIL（`ImportError: cannot import name 'original_score' from 'finch.inbox.service'`）

- [ ] **Step 3: 实现投影 + 选择**

创建 `src/finch/inbox/service.py`：

```python
"""收件箱服务：投影 + 确定性选择（决策服务在 Task 3 追加）。

纯函数、不访问 DB（next_item 只读仓储调用方传入的结果）。不依赖 finch.cli，避免循环导入。
"""

from finch.content.jobs import ContentJob
from finch.content.models import Draft
from finch.engagement.models import InteractionAction, InteractionCandidate
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.inbox.models import InboxItem, InboxTrack

_STRONG = {ClaimConfidence.VERIFIED, ClaimConfidence.SUPPORTED}


def original_score(job: ContentJob, cards_by_id: dict[str, EvidenceCard]) -> float:
    """原创轨道的确定性排序分（0–1，仅同轨可比，跨轨分数不可比）。

    只反映「是否值得先写」的稳定维度，不依赖 confirmed/position_source
    （Phase 2 将删除立场确认机制，本函数保持不变）。
    """
    cards = [cards_by_id[cid] for cid in job.source_card_ids if cid in cards_by_id]
    strong = sum(1 for c in cards if c.confidence in _STRONG) / len(cards) if cards else 0.0
    position = job.author_position
    return (
        0.5 * (1.0 if job.candidate_id is not None else 0.0)
        + 0.25 * strong
        + 0.15 * (1.0 if position is not None and position.decision and position.tradeoff else 0.0)
        + 0.10 * (1.0 if job.why_now else 0.0)
    )


def _provenance(job: ContentJob) -> str:
    return "idea" if job.id.startswith("idea_") else "personal"


def _content_type(job: ContentJob) -> str:
    return "reply" if job.candidate_id is not None else "original"


def build_original_item(
    job: ContentJob,
    draft: Draft,
    *,
    cards_by_id: dict[str, EvidenceCard],
    must_ask: bool,
    ask_reasons: list[str],
    risks: list[str],
) -> InboxItem:
    """把 ContentJob + Draft 投影成 InboxItem（original/idea 轨道）。"""
    source_refs = [
        src.url for cid in job.source_card_ids
        if cid in cards_by_id for src in cards_by_id[cid].sources
    ]
    position = None
    if job.author_position is not None:
        position = {
            "claim": job.author_position.claim,
            "decision": job.author_position.decision,
            "tradeoff": job.author_position.tradeoff,
        }
    return InboxItem(
        id=job.id,
        track=InboxTrack.ORIGINAL,
        content_type=_content_type(job),  # type: ignore[arg-type]
        provenance=_provenance(job),  # type: ignore[arg-type]
        source_refs=source_refs,
        why_now=job.why_now,
        score=original_score(job, cards_by_id),
        draft_id=draft.id,
        draft=draft.body,
        position=position,
        must_ask=must_ask,
        ask_reasons=ask_reasons,
        risks=risks,
    )


def build_engagement_item(candidate: InteractionCandidate) -> InboxItem:
    """把 InteractionCandidate 投影成 InboxItem（engagement 轨道）。"""
    if candidate.action in {InteractionAction.DRAFT_QUOTE, InteractionAction.DRAFT_DM}:
        content_type = "quote"
    else:
        content_type = "reply"
    body = candidate.revised_draft or candidate.draft or ""
    return InboxItem(
        id=candidate.id,
        track=InboxTrack.ENGAGEMENT,
        content_type=content_type,  # type: ignore[arg-type]
        provenance="external",
        source_refs=[candidate.post.url],
        why_now="；".join(candidate.score.reasons),
        score=candidate.score.total,
        draft_id=None,
        draft=body,
        position=None,
        must_ask=bool(candidate.factual_risks),
        ask_reasons=list(candidate.factual_risks),
        risks=list(candidate.factual_risks),
    )


def select_next(items: list[InboxItem]) -> InboxItem | None:
    """§7.2 确定性选择：must_ask 优先 → original 先于 engagement → score 降序、id 升序。"""
    if not items:
        return None
    rank = {
        InboxTrack.ORIGINAL: 0,
        InboxTrack.ENGAGEMENT: 1,
    }
    return sorted(
        items,
        key=lambda it: (not it.must_ask, rank[it.track], -it.score, it.id),
    )[0]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_inbox_service.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 提交**

```bash
git add src/finch/inbox/service.py tests/unit/test_inbox_service.py
git commit -m "feat(inbox): projection + deterministic selection service"
```

---

### Task 3: inbox 决策服务（DecisionRecord 迁入 + 双轨分发）

**Files:**
- Modify: `src/finch/inbox/models.py`（追加 `DecisionAction`/`DecisionRecord`/`SkipReason`，从 `review/models.py` 迁入并去 `position_source`/`position_fingerprint`）
- Modify: `src/finch/inbox/service.py`（追加 `content_hash`/`_diff`/`InboxDecisionService`）
- Modify: `src/finch/review/models.py`（保留 `ReviewDecision`/`ReviewAction`/`OutcomeAssessment`/`Feedback`/`SkipReason` 于原地，`DecisionRecord`/`DecisionAction`/`SkipReason` 改为从 inbox re-export，避免旧 import 断裂）
- Test: `tests/unit/test_inbox_decision.py`

**Interfaces:**
- Consumes: `DecisionRecordRepository.save/get/list`、`PublicationIntentRepository.save`、`InteractionRepository.approve/reject/get`、`DraftRepository.list_by_job`、`ContentJobRepository.get_job`、`rewrite_with_instruction`、`critique`
- Produces（Task 4 CLI 使用）:
  - `content_hash(body: str) -> str`
  - `InboxDecisionService(jobs=..., drafts=..., decisions=..., publication_intents=..., interactions=...)`
  - `InboxDecisionService.accept(item_id: str) -> DecisionRecord | InteractionCandidate`
  - `InboxDecisionService.skip(item_id: str, reason: str) -> DecisionRecord | InteractionCandidate`
  - `InboxDecisionService.revise(item_id: str, instruction: str, *, runner, cards_by_id) -> dict`（original 用 LLM 重写；engagement 抛 ValueError）

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_inbox_decision.py`：

```python
"""Unit tests for the inbox decision service (original + engagement dispatch)."""

from datetime import datetime

from finch.author.models import PublicationIntent
from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import Draft, DraftKind
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionCandidate,
)
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.inbox.service import InboxDecisionService


class _Jobs:
    def __init__(self, job): self._job = job
    def get_job(self, jid): return self._job if self._job and self._job.id == jid else None


class _Drafts:
    def __init__(self, draft): self._draft = draft
    def list_by_job(self, jid):
        return [self._draft] if self._draft and self._draft.content_job_id == jid else []


class _Decisions:
    def __init__(self): self.saved = []
    def save(self, r): self.saved.append(r)
    def list(self): return self.saved


class _Intents:
    def __init__(self): self.saved = []
    def save(self, i): self.saved.append(i)


class _Interactions:
    def __init__(self, cand): self._cand = cand
    def get(self, cid): return self._cand if self._cand and self._cand.id == cid else None
    def approve(self, cid): self._cand = self._cand.model_copy(update={"status": "approved"})
    def reject(self, cid, reason):
        self._cand = self._cand.model_copy(update={"status": "rejected", "reject_reason": reason})


def _job(job_id="job_1"):
    return ContentJob(
        id=job_id, source_card_ids=[], reader_problem="rp", audience="a",
        intended_effect={"understand": "u"}, author_position=None,
        success_criteria=[], recommended_format=DraftKind.ORIGINAL,
        status=ContentJobStatus.READY,
    )


def _draft(job_id="job_1"):
    return Draft(id="draft_1", kind=DraftKind.ORIGINAL, language="zh", body="正文",
                 content_job_id=job_id)


def _candidate(cand_id="x:p1:reply"):
    return InteractionCandidate(
        id=cand_id,
        post=ExternalPost(id="p1", platform="x", url="u", author_id="a", author_name="A",
                          content="x" * 30, published_at=datetime.now()),
        score=ConversationScore(relevance=0.5, novelty=0.5, discussability=0.5,
                                practical_evidence=0.5, relationship_value=0.5,
                                total=0.5, reasons=[]),
        action=InteractionAction.DRAFT_REPLY, approval_required=True,
    )


def _svc(job=None, draft=None, cand=None):
    decisions = _Decisions()
    intents = _Intents()
    return InboxDecisionService(
        jobs=_Jobs(job), drafts=_Drafts(draft), decisions=decisions,
        publication_intents=intents, interactions=_Interactions(cand),
    ), decisions, intents


def test_accept_original_writes_decision_and_intent_only():
    svc, decisions, intents = _svc(job=_job(), draft=_draft())
    record = svc.accept("job_1")
    assert isinstance(record, DecisionRecord)
    assert record.action == DecisionAction.ACCEPT
    assert record.approved_content_hash
    assert len(decisions.saved) == 1
    assert len(intents.saved) == 1
    assert isinstance(intents.saved[0], PublicationIntent)


def test_accept_engagement_approves_candidate():
    cand = _candidate()
    svc, decisions, intents = _svc(cand=cand)
    out = svc.accept("x:p1:reply")
    assert out.status.value == "approved"
    assert len(decisions.saved) == 0  # 互动不写 DecisionRecord


def test_skip_original_marks_do_not_write():
    svc, decisions, _ = _svc(job=_job(), draft=_draft())
    record = svc.skip("job_1", "not_now")
    assert record.action == DecisionAction.SKIP
    assert len(decisions.saved) == 1


def test_skip_engagement_rejects_candidate():
    cand = _candidate()
    svc, _, _ = _svc(cand=cand)
    out = svc.skip("x:p1:reply", "not_now")
    assert out.status.value == "rejected"
    assert out.reject_reason == "not_now"


def test_unknown_id_raises_keyerror():
    svc, _, _ = _svc()
    try:
        svc.accept("nope")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_inbox_decision.py -v`
Expected: FAIL（`ImportError: cannot import name 'InboxDecisionService'`）

- [ ] **Step 3: 迁入决策模型**

修改 `src/finch/inbox/models.py`，在文件末尾追加：

```python
from datetime import datetime

from pydantic import BaseModel, Field


class DecisionAction(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    SKIP = "skip"


class DecisionRecord(BaseModel):
    """一次原子决策的唯一权威记录。"""

    id: str                          # "dec_<job_id>"（幂等键）
    job_id: str
    draft_id: str
    action: DecisionAction
    approved_content_hash: str       # 采用时绑定最终正文；revise 改变 hash → 旧批准失效
    revised_body: str | None = None
    diff: str | None = None
    decided_at: datetime


class SkipReason(StrEnum):
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    NOT_RELEVANT = "not_relevant"
    LOW_QUALITY = "low_quality"
    NOT_NOW = "not_now"
    OTHER = "other"
    NO_CLEAR_POSITION = "no_clear_position"
    GENERIC_VOICE = "generic_voice"
    JOB_NOT_USEFUL = "job_not_useful"
    FACT_ERROR = "fact_error"
```

注意：文件顶部已 `from enum import StrEnum`、`from pydantic import BaseModel`，追加块只需补 `from datetime import datetime`（放在文件顶部 import 区）。

修改 `src/finch/review/models.py`，把 `DecisionAction`/`DecisionRecord`/`SkipReason` 的定义替换为 re-export（其余 `ReviewAction`/`ReviewDecision`/`OutcomeAssessment`/`Feedback` 保留）：

```python
from finch.inbox.models import DecisionAction, DecisionRecord, SkipReason

__all__ = ["ReviewAction", "ReviewDecision", "DecisionAction", "DecisionRecord",
           "SkipReason", "OutcomeAssessment", "Feedback"]
```

（删除该文件里原来的 `class DecisionAction`、`class DecisionRecord`、`class SkipReason` 三个定义；`DecisionRecord` 不再含 `position_source`/`position_fingerprint` 字段，`DecisionAction`/`SkipReason` 内容与 inbox 一致。）

- [ ] **Step 4: 实现决策服务**

修改 `src/finch/inbox/service.py`，在 import 区追加：

```python
import hashlib
import difflib
from datetime import UTC, datetime

from finch.author.models import PublicationIntent
from finch.codex.runner import CodexRunner
from finch.content.critic import critique
from finch.content.jobs import ContentJobStatus
from finch.content.writer import rewrite_with_instruction
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.storage.repositories import (
    ContentJobRepository,
    DecisionRecordRepository,
    DraftRepository,
    InteractionRepository,
    PublicationIntentRepository,
)
```

在文件末尾追加：

```python
def content_hash(body: str) -> str:
    """正文的确定性 SHA-256 摘要（批准绑定具体文本版本）。"""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _diff(before: str, after: str) -> str:
    return "\n".join(
        difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
    )


class InboxDecisionService:
    """把一次「采用/跳过/修订」落到唯一权威记录（DecisionRecord）或互动候选状态。

    original 走 DecisionRecord + PublicationIntent；engagement 走
    InteractionCandidate.status。先查 ContentJob，再查 InteractionCandidate。
    """

    def __init__(
        self,
        *,
        jobs: ContentJobRepository,
        drafts: DraftRepository,
        decisions: DecisionRecordRepository,
        publication_intents: PublicationIntentRepository,
        interactions: InteractionRepository,
    ) -> None:
        self.jobs = jobs
        self.drafts = drafts
        self.decisions = decisions
        self.publication_intents = publication_intents
        self.interactions = interactions

    def _resolve(self, item_id: str) -> tuple[str, object]:
        """先查 ContentJob，再查 InteractionCandidate。返回 ("original"|"engagement", entity)。"""
        job = self.jobs.get_job(item_id)
        if job is not None:
            return "original", job
        candidate = self.interactions.get(item_id)
        if candidate is not None:
            return "engagement", candidate
        raise KeyError(item_id)

    def accept(self, item_id: str):
        kind, entity = self._resolve(item_id)
        if kind == "original":
            return self._accept_original(item_id)
        self.interactions.approve(item_id)
        return self.interactions.get(item_id)

    def skip(self, item_id: str, reason: str):
        kind, _ = self._resolve(item_id)
        if kind == "original":
            return self._skip_original(item_id, reason)
        self.interactions.reject(item_id, reason)
        return self.interactions.get(item_id)

    def revise(self, item_id: str, instruction: str, *, runner: CodexRunner, cards_by_id: dict) -> dict:
        kind, _ = self._resolve(item_id)
        if kind == "engagement":
            raise ValueError(f"engagement revise not supported yet: {item_id}")
        return self._revise_original(item_id, instruction, runner=runner, cards_by_id=cards_by_id)

    def _accept_original(self, job_id: str) -> DecisionRecord:
        job = self.jobs.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        drafts = self.drafts.list_by_job(job_id)
        if not drafts:
            raise KeyError(f"no draft for job {job_id}")
        draft = drafts[0]
        record = DecisionRecord(
            id=f"dec_{job_id}",
            job_id=job_id,
            draft_id=draft.id,
            action=DecisionAction.ACCEPT,
            approved_content_hash=content_hash(draft.body),
            decided_at=datetime.now(UTC),
        )
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
        self.decisions.save(record)
        return record

    def _skip_original(self, job_id: str, reason: str) -> DecisionRecord:
        job = self.jobs.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        drafts = self.drafts.list_by_job(job_id)
        if not drafts:
            raise KeyError(f"no draft for job {job_id}")
        draft = drafts[0]
        self.jobs.upsert_job(
            job.model_copy(update={"status": ContentJobStatus.DO_NOT_WRITE, "reject_reason": reason})
        )
        record = DecisionRecord(
            id=f"dec_{job_id}",
            job_id=job_id,
            draft_id=draft.id,
            action=DecisionAction.SKIP,
            approved_content_hash="",
            decided_at=datetime.now(UTC),
        )
        self.decisions.save(record)
        return record

    def _revise_original(
        self, job_id: str, instruction: str, *, runner: CodexRunner, cards_by_id: dict
    ) -> dict:
        job = self.jobs.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        drafts = self.drafts.list_by_job(job_id)
        if not drafts:
            raise KeyError(f"no draft for job {job_id}")
        draft = drafts[0]
        new_draft = rewrite_with_instruction(runner, draft, instruction, cards_by_id, job)
        critic = critique(runner, new_draft, cards_by_id)
        self.drafts.upsert_draft(new_draft)
        self.decisions.save(
            DecisionRecord(
                id=f"dec_{job_id}",
                job_id=job_id,
                draft_id=draft.id,
                action=DecisionAction.REVISE,
                approved_content_hash=content_hash(new_draft.body),
                revised_body=new_draft.body,
                diff=_diff(draft.body, new_draft.body),
                decided_at=datetime.now(UTC),
            )
        )
        return {"new_body": new_draft.body, "diff": _diff(draft.body, new_draft.body),
                "critic": critic.model_dump(mode="json")}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_inbox_decision.py tests/unit/test_inbox_models.py -v`
Expected: PASS（5 + 2 = 7 passed）

- [ ] **Step 6: 提交**

```bash
git add src/finch/inbox/models.py src/finch/inbox/service.py src/finch/review/models.py tests/unit/test_inbox_decision.py
git commit -m "feat(inbox): DecisionRecord move + dual-track decide service"
```

---

### Task 4: CLI 收编（next/decide 统一 + draft 别名 + learn/weekly 提升 + 删旧命令）

**Files:**
- Create: `src/finch/learn/__init__.py`
- Create: `src/finch/learn/service.py`（`FeedbackService` + `OutcomeAssessment` 迁入）
- Create: `src/finch/inbox/render.py`
- Modify: `src/finch/cli.py`（大量改动）
- Test: `tests/unit/test_cli_inbox.py`

**Interfaces:**
- Consumes: `InboxDecisionService`、`next_item`/`build_original_item`/`build_engagement_item`、`FeedbackService`、`weekly_analysis`、`render_weekly`
- Produces: 命令 `next`、`decide`、`draft`（= `idea`）、`learn`、`weekly`（顶层）；删除 `review`/`engagement`/`jobs`/`gate` typer app 与 `run resume`/`run resolve`

- [ ] **Step 1: 迁 FeedbackService 到 learn**

创建 `src/finch/learn/__init__.py`：

```python
"""发布后学习：登记发布链接、互动数据、结果评估与学习记录（承载 finch learn）。"""
```

创建 `src/finch/learn/service.py`：

```python
"""learn 服务：发布链接与互动数据的手动登记。"""

from datetime import UTC, datetime

from finch.review.models import Feedback, OutcomeAssessment
from finch.storage.repositories import FeedbackRepository


class FeedbackService:
    def __init__(self, feedbacks: FeedbackRepository) -> None:
        self.feedbacks = feedbacks

    def record(
        self,
        draft_id: str,
        *,
        published_url: str | None = None,
        metrics: dict | None = None,
        outcome: OutcomeAssessment | None = None,
        learning: str | None = None,
    ) -> Feedback:
        existing = self.feedbacks.get_feedback(draft_id)
        if existing is not None:
            feedback = Feedback(
                draft_id=draft_id,
                published_url=existing.published_url if published_url is None else published_url,
                interaction_metrics={**existing.interaction_metrics, **(metrics or {})},
                recorded_at=existing.recorded_at,
                outcome=existing.outcome if outcome is None else outcome,
                learning=existing.learning if learning is None else learning,
            )
        else:
            feedback = Feedback(
                draft_id=draft_id,
                published_url=published_url,
                interaction_metrics=metrics or {},
                recorded_at=datetime.now(UTC),
                outcome=outcome,
                learning=learning,
            )
        self.feedbacks.save_feedback(feedback)
        return feedback
```

- [ ] **Step 2: 实现 inbox 渲染**

创建 `src/finch/inbox/render.py`：

```python
"""收件箱人类输出（不含 run_id / 节点名）。"""

from finch.inbox.models import InboxItem, InboxTrack

_TRACK_LABEL = {InboxTrack.ORIGINAL: "原创", InboxTrack.ENGAGEMENT: "互动"}
_CONTENT_LABEL = {"original": "原创", "reply": "回复", "quote": "引用"}


def render_inbox(items: list[InboxItem]) -> str:
    """「今天 N 条待决定」汇总。"""
    if not items:
        return "今天没有待决定的内容。"
    lines = [f"今天 {len(items)} 条待决定。", ""]
    for i, item in enumerate(items, start=1):
        title = item.position.get("decision") if item.position else ""
        title = title or item.why_now or item.draft[:40] or item.id
        lines.append(f"{i}. [{_CONTENT_LABEL[item.content_type]}] {title}")
    lines += ["", "finch next     # 看第 1 条"]
    return "\n".join(lines)


def render_card(item: InboxItem) -> str:
    """单条决策卡。"""
    lines = [f"[{_CONTENT_LABEL[item.content_type]}] {item.id}"]
    if item.source_refs:
        lines.append("来源：" + " ".join(item.source_refs))
    if item.why_now:
        lines.append(f"为什么现在：{item.why_now}")
    if item.position:
        lines.append(
            f"立场：{item.position.get('decision') or item.position.get('claim') or '（待确认）'}"
        )
    if item.must_ask:
        lines.append("需要你确认：" + "；".join(item.ask_reasons))
    lines += ["", item.draft, "", f"decide {item.id} --action accept|skip"]
    return "\n".join(lines)
```

- [ ] **Step 3: 改 cli.py 收编 next/decide + 删除旧命令**

这是最大的改动。按下列顺序修改 `src/finch/cli.py`：

（a）删除 `review_app`、`engagement_app`、`jobs_app` typer 定义与 `app.add_typer(...)`，以及所有 `@review_app.command`/`@engagement_app.command`/`@jobs_app.command` 函数（`review_list`/`review_show`/`review_approve`/`review_revise`/`review_skip`/`review_confirm_position`/`review_feedback`、`engagement_list`/`engagement_show`/`engagement_approve`/`engagement_reject`/`engagement_edit`/`engagement_metrics`、`jobs_list`/`jobs_show`/`jobs_answer`/`jobs_confirm_position`/`jobs_reject`）。

（b）删除 `@run_app.command("resume")` 与 `@run_app.command("resolve")`（及 `_resume_nodes`/`_resume_and_echo`/`_edit_position`/`_open_editor`/`_mark_stopped`/`_read_input_request`/`_cards_for`/`_latest_needs_input_run_id` 等仅被它们使用的 helper；`gate/` 的 import 一并删除）。

（c）在 import 区新增：

```python
from .inbox.models import DecisionAction, InboxItem
from .inbox.render import render_card, render_inbox
from .inbox.service import (
    InboxDecisionService,
    build_engagement_item,
    build_original_item,
    next_item,
)
from .learn.service import FeedbackService
```

（d）把 `finch idea` 命令重命名为 `draft` 并加别名。typer 用两个装饰器指向同一函数：

> **依赖前提**：`finch idea` CLI 命令当前**尚未落地**（idea 实现计划只完成到 Task 2——models + build helper；Task 3–5 的 assess/write LLM、critic 循环、CLI 命令未提交）。执行本步骤前，先完成 idea 计划 Task 3–5，或把 idea CLI 命令（含 `_echo_idea`/`_echo_idea_error` helper）并入本步骤。完成后再做下面的重命名：

```python
@app.command("draft")
@app.command("idea", hidden=True)
def draft(
    text: str = typer.Argument(..., help="想法或片段"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),  # noqa: B008
) -> None:
    """判断一个想法能否发，能发时生成样稿进入收件箱。"""
    ...  # 函数体与已完成的 idea 命令完全一致（见 idea 实现计划 Task 5）
```

（e）新增顶层 `learn` 与 `weekly` 命令（内容分别从原 `review_feedback` 与 `run_weekly` 复制，去掉 `review_app`/`run_app` 前缀）：

```python
@app.command("learn")
def learn(
    draft_id: str = typer.Argument(..., help="草稿 id"),
    url: str | None = typer.Option(None, "--url", help="发布链接"),
    metrics: str | None = typer.Option(None, "--metrics", help="互动数据 JSON"),
    outcome: str | None = typer.Option(None, "--outcome", help="结果评估 JSON"),
    learning: str | None = typer.Option(None, "--learning", help="学习记录"),
) -> None:
    """登记发布链接、互动数据、结果评估与学习记录。"""
    metrics_dict: dict | None = json.loads(metrics) if metrics else None
    outcome_obj: OutcomeAssessment | None = (
        OutcomeAssessment.model_validate_json(outcome) if outcome else None
    )
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    feedback = FeedbackService(FeedbackRepository(store)).record(
        draft_id, published_url=url, metrics=metrics_dict,
        outcome=outcome_obj, learning=learning,
    )
    typer.echo(f"feedback recorded: {feedback.draft_id}")


@app.command("weekly")
def weekly() -> None:
    """周复盘：汇总最近 7 天的批准率、修改/跳过原因、内容效果指标。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    since = datetime.now(UTC) - timedelta(days=7)
    report = weekly_analysis(
        DraftRepository(store), ReviewRepository(store),
        FeedbackRepository(store), ContentJobRepository(store),
        CriticReportRepository(store), since=since,
    )
    typer.echo(render_weekly(report))
```

（注：本步骤 `weekly` 命令沿用当前 `weekly_analysis` 签名（`ReviewRepository`）；Task 5 Step 3 会把签名与这里的调用一起改为 `DecisionRecordRepository`。）

（f）重写 `decide` 命令为调用 `InboxDecisionService`：

```python
@app.command("decide")
def decide(
    item_id: str = typer.Argument(..., help="待决策项 id（job_id 或 candidate id）"),
    action: str = typer.Option(..., "--action", help="accept|skip|revise"),
    reason: str = typer.Option(None, "--reason", help="--action skip 的拒绝理由"),
    instruction: str | None = typer.Option(None, "--instruction", help="--action revise 的指令"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """单一决策点：accept 确认立场+批准；skip 标记不写；revise 按指令重写。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    svc = InboxDecisionService(
        jobs=ContentJobRepository(store), drafts=DraftRepository(store),
        decisions=DecisionRecordRepository(store),
        publication_intents=PublicationIntentRepository(store),
        interactions=InteractionRepository(store),
    )
    try:
        action_enum = DecisionAction(action)
    except ValueError as exc:
        typer.echo(f"invalid --action: {action}")
        raise typer.Exit(code=1) from exc
    try:
        if action_enum is DecisionAction.ACCEPT:
            result = svc.accept(item_id)
        elif action_enum is DecisionAction.SKIP:
            if not reason:
                typer.echo("--action skip requires --reason")
                raise typer.Exit(code=1)
            result = svc.skip(item_id, reason)
        else:
            if not instruction:
                typer.echo("--action revise requires --instruction")
                raise typer.Exit(code=1)
            cards_by_id = {c.id: c for c in EvidenceRepository(store).list_cards()}
            runner = cast(CodexRunner, create_runner(settings.llm) or CodexRunner())
            result = svc.revise(item_id, instruction, runner=runner, cards_by_id=cards_by_id)
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if as_json:
        if isinstance(result, dict):
            typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            typer.echo(result.model_dump_json(indent=2))
    else:
        if isinstance(result, dict):
            typer.echo(result["new_body"])
        else:
            typer.echo(getattr(result, "action", result.status).value
                       if hasattr(getattr(result, "action", result.status), "value")
                       else str(getattr(result, "status", result.action)))
```

（g）重写 `next` 命令为调用 `next_item` 投影：

```python
@app.command("next")
def next_item_cli(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """返回下一个待决策卡（原创 + 互动），无则 status=none。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    payload = next_item(
        jobs=ContentJobRepository(store), drafts=DraftRepository(store),
        decisions=DecisionRecordRepository(store),
        interactions=InteractionRepository(store),
        cards=EvidenceRepository(store),
    )
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if payload.get("status") == "none":
            typer.echo("no pending items")
        else:
            typer.echo(payload.get("topic") or payload.get("id", ""))
```

（h）在 `inbox/service.py` 末尾追加 `next_item`（Task 2 预留、本步骤补全），实现投影组装 + 选择：

```python
def next_item(
    *,
    jobs: ContentJobRepository,
    drafts: DraftRepository,
    decisions: DecisionRecordRepository,
    interactions: InteractionRepository,
    cards: EvidenceRepository,
) -> dict:
    """组装收件箱并返回第一条待决策卡（JSON 载荷），空则 {"status": "none"}。"""
    decided_job_ids = {
        r.job_id for r in decisions.list()
        if r.action in {DecisionAction.ACCEPT, DecisionAction.SKIP}
    }
    cards_by_id = {c.id: c for c in cards.list_cards()}
    items: list[InboxItem] = []
    for draft in drafts.list_drafts():
        if not draft.content_job_id or draft.content_job_id in decided_job_ids:
            continue
        job = jobs.get_job(draft.content_job_id)
        if job is None:
            continue
        must_ask, ask_reasons = _original_ask(job)
        items.append(build_original_item(
            job, draft, cards_by_id=cards_by_id,
            must_ask=must_ask, ask_reasons=ask_reasons, risks=[],
        ))
    for candidate in interactions.list_pending():
        if candidate.draft or candidate.revised_draft:
            items.append(build_engagement_item(candidate))

    first = select_next(items)
    if first is None:
        return {"status": "none"}
    payload = first.model_dump(mode="json")
    payload["status"] = "review_required"
    if first.track == InboxTrack.ORIGINAL:
        payload["job_id"] = first.id  # 兼容旧客户端
        payload["topic"] = first.position.get("decision") if first.position else first.id
    return payload


def _original_ask(job: ContentJob) -> tuple[bool, list[str]]:
    """立场不完整 → must_ask（Graph 不停，问题放到卡上）。"""
    position = job.author_position
    if position is None or not position.decision or not position.tradeoff:
        return True, ["position_incomplete"]
    return False, []
```

- [ ] **Step 4: 写 CLI 测试**

创建 `tests/unit/test_cli_inbox.py`：

```python
"""Unit tests for the unified inbox CLI (next/decide/draft/learn/weekly)."""

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings


def _settings(tmp_path):
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


def test_next_empty_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    r = CliRunner().invoke(app, ["next", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == {"status": "none"}


def test_old_commands_removed():
    # 确保 review/engagement/jobs/gate 已不在命令表
    names = {c.name for c in app.registered_commands}
    for gone in ("review", "engagement", "jobs", "gate"):
        assert gone not in names


def test_product_commands_present():
    names = {c.name for c in app.registered_commands}
    for present in ("next", "decide", "draft", "learn", "weekly", "daily", "author"):
        assert present in names
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_cli_inbox.py tests/unit/test_inbox_decision.py -v`
Expected: PASS

- [ ] **Step 6: 全量回归 + lint + mypy**

Run:
```bash
uv run pytest tests/unit/ -q
uv run ruff check src/finch/inbox src/finch/learn src/finch/cli.py
uv run mypy src/finch/inbox src/finch/learn
```
Expected: 全绿（旧 review/engagement/jobs CLI 测试若失败，删除对应测试文件 `test_cli_review.py`、`test_cli_engagement.py`、`test_cli_run.py` 中已被删命令的用例）。

- [ ] **Step 7: 提交**

```bash
git add src/finch/cli.py src/finch/inbox/render.py src/finch/inbox/service.py src/finch/learn tests/unit/test_cli_inbox.py
git commit -m "feat(inbox): unified next/decide CLI + draft alias + learn/weekly top-level"
```

---

### Task 5: 存储收敛（删 review/gate 模块 + 删 3 表 + 迁 voice/weekly 读者）

**Files:**
- Delete: `src/finch/review/service.py`、`src/finch/review/decision.py`、`src/finch/review/models.py`、`src/finch/review/feedback.py`、`src/finch/gate/*`
- Modify: `src/finch/review/weekly.py`（改读 `DecisionRecordRepository`，删 `ReviewRepository` 依赖）
- Modify: `src/finch/storage/repositories.py`（删 `ReviewRecord`/`ReviewHistoryRecord`/`PositionApprovalRecord` 表类 + `ReviewRepository`/`PositionApprovalRepository`）
- Modify: `src/finch/storage/database.py`（若 record 类在此登记，同步删）
- Create: `alembic/versions/XXXX_drop_review_and_position_approval.py`
- Modify: `src/finch/cli.py`（`voice approve-example` 改读 `DecisionRecord`）
- Modify: `src/finch/review/weekly.py` 的消费者（`cli.py` 的 `weekly` 命令已改；见 Task 4）
- Test: `tests/unit/test_weekly.py`、`tests/unit/test_cli_review.py`（更新/删除）

**Interfaces:**
- Consumes: `DecisionRecordRepository.list/get`
- Produces: `weekly_analysis(drafts, decisions: DecisionRecordRepository, feedbacks, jobs, critic_reports, *, since) -> WeeklyReport`

- [ ] **Step 1: 写 alembic 迁移**

创建 `alembic/versions/XXXX_drop_review_and_position_approval.py`（revision 用 `alembic revision -m "drop_review_and_position_approval"` 生成的 id，`down_revision` 指向当前 head）：

```python
"""drop review/position-approval tables (inbox deep refactor phase 1)."""

from alembic import op
import sqlalchemy as sa  # noqa: F401

revision = "XXXX"
down_revision = "<current-head>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("reviewhistoryrecord")
    op.drop_table("reviewrecord")
    op.drop_table("positionapprovalrecord")


def downgrade() -> None:
    # 重建为最小 schema（仅结构，不含历史数据；downgrade 属尽力而为）。
    op.create_table(
        "reviewrecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "reviewhistoryrecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "positionapprovalrecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
```

（把 `XXXX` 与 `<current-head>` 换成实际生成的 revision id 与当前 head。）

- [ ] **Step 2: 删 record 类与仓储**

在 `src/finch/storage/repositories.py` 删除 `class ReviewRecord`、`class ReviewHistoryRecord`、`class PositionApprovalRecord`（含其 `__tablename__` 依赖的 model 定义）与 `class ReviewRepository`、`class PositionApprovalRepository` 全部方法。同时删除这些仓储所 import 的 `ReviewDecision`/`ReviewAction`/`PositionApproval` 模型引用。

- [ ] **Step 3: 迁 weekly 读者**

修改 `src/finch/review/weekly.py`（若决定整体保留 `weekly.py`，可先移到 `src/finch/inbox/weekly.py` 再改；本计划保留原路径以减小 diff）：

把签名与实现改为读 `DecisionRecordRepository`：

```python
from finch.inbox.models import DecisionAction, DecisionRecord, SkipReason
# 删除: from finch.review.models import ... ReviewAction, ReviewDecision ...
# 删除: from finch.storage.repositories import ReviewRepository

def weekly_analysis(
    drafts: DraftRepository,
    decisions: DecisionRecordRepository,
    feedbacks: FeedbackRepository,
    jobs: ContentJobRepository,
    critic_reports: CriticReportRepository,
    *,
    since: datetime | None = None,
) -> WeeklyReport:
    all_drafts = drafts.list_drafts()
    records = decisions.list()
    latest: dict[str, DecisionRecord] = {}
    for r in records:
        if since is not None and r.decided_at < since:
            continue
        latest[r.job_id] = r  # 同 job 后写覆盖（DecisionRecord 是 last-write-wins）

    approved = sum(1 for r in latest.values() if r.action == DecisionAction.ACCEPT)
    skipped = sum(1 for r in latest.values() if r.action == DecisionAction.SKIP)
    reviewed = len(latest)
    revised = sum(1 for r in records if r.action == DecisionAction.REVISE
                  and (since is None or r.decided_at >= since))
    skip_reasons = Counter(
        r.reason if getattr(r, "reason", None) else "unknown"
        for r in latest.values() if r.action == DecisionAction.SKIP
    )
    # ... 其余（feedbacks/jobs/critic_reports 的指标）不变，保持原实现 ...
```

注意：原 `skip_reasons` 读的是 `ReviewDecision.reason`，而 `DecisionRecord` 无 `reason` 字段（skip 理由现在写在 `ContentJob.reject_reason`）。把 skip 理由改为从 `jobs` 侧取：

```python
    jobs_by_id = {job.id: job for job in jobs.list_jobs()}
    skip_reasons = Counter(
        jobs_by_id[r.job_id].reject_reason or "unknown"
        for r in latest.values() if r.action == DecisionAction.SKIP
        if r.job_id in jobs_by_id
    )
```

`_human_correction_rate` 原用 `ReviewAction.REVISE` 历史 + `SkipReason.FACT_ERROR/NO_CLEAR_POSITION`；改为：

```python
def _human_correction_rate(latest, records, eligible_ids, jobs_by_id, since):
    reviewed_ids = {r.job_id for r in latest.values() if r.job_id in eligible_ids}
    if not reviewed_ids:
        return None
    revised_ids = {
        r.job_id for r in records
        if r.action == DecisionAction.REVISE and r.job_id in eligible_ids
        and (since is None or r.decided_at >= since)
    }
    fact_skip_ids = {
        r.job_id for r in latest.values()
        if r.job_id in eligible_ids and r.action == DecisionAction.SKIP
        and (jobs_by_id.get(r.job_id).reject_reason
             if r.job_id in jobs_by_id else None)
        in {SkipReason.FACT_ERROR.value, SkipReason.NO_CLEAR_POSITION.value}
    }
    return len(revised_ids | fact_skip_ids) / len(reviewed_ids)
```

（其余 `_evidence_coverage`/`_decision_density`/`_generic_sentence_rate`/`_job_completion_rate`/`_useful_reply_rate`/`_do_not_write_rate`/`_rewrite_rounds` 不依赖 Review 模块，保持原样。）

- [ ] **Step 4: 迁 voice 读者**

修改 `src/finch/cli.py` 的 `voice approve-example`：删除对 `review_repo.get_position_review(...).voice_match` 的依赖，改用 `DecisionRecordRepository` 判断是否 ACCEPT：

```python
@app.command("approve-example")
def voice_approve_example(draft_id: str) -> None:
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    draft = DraftRepository(store).get_draft(draft_id)
    if draft is None:
        typer.echo(f"draft not found: {draft_id}")
        raise typer.Exit(code=1)
    decisions = {
        d.draft_id: d for d in DecisionRecordRepository(store).list()
    }
    decision = decisions.get(draft_id)
    if decision is None or decision.action != DecisionAction.ACCEPT:
        typer.echo(f"not accepted: {draft_id}")
        raise typer.Exit(code=1)
    text = decision.revised_body or draft.body
    path = settings.paths.voice_profile_path
    profile = load_voice_profile(path)
    if any(ex.id == draft_id for ex in profile.approved_examples):
        typer.echo(f"already approved: {draft_id}")
        return
    profile.rejected_examples = [ex for ex in profile.rejected_examples if ex.id != draft_id]
    profile.approved_examples.append(ApprovedExample(id=draft_id, text=text))
    save_voice_profile(profile, path)
    typer.echo(f"approved example: {draft_id}")
```

（`voice reject-example` 不再要求「已批准」门，直接按 id 入库，删除 `review_repo` 依赖。）

- [ ] **Step 5: 删 review/gate 模块文件**

```bash
rm src/finch/review/service.py src/finch/review/decision.py src/finch/review/models.py src/finch/review/feedback.py
rm -r src/finch/gate
```

删除 `src/finch/review/__init__.py` 中相关导出；`src/finch/review/weekly.py` 保留（改读 DecisionRecord）。

- [ ] **Step 6: 跑测试确认通过**

Run:
```bash
uv run pytest tests/unit/test_weekly.py tests/unit/test_inbox_decision.py -v
uv run ruff check src/finch/review src/finch/cli.py src/finch/storage/repositories.py
```
Expected: PASS（`test_weekly.py` 需更新以传入 `DecisionRecordRepository` 替代 `ReviewRepository`）。

- [ ] **Step 7: 全量回归**

Run:
```bash
uv run pytest tests/unit/ tests/graph/ -q
uv run mypy src/finch
```
Expected: 全绿（删除已失效的 `test_cli_review.py`/`test_cli_engagement.py`/`test_gate_*`/`test_position_approval_repository.py`/`test_review_service.py` 等，或改为断言命令已删）。

- [ ] **Step 8: 提交**

```bash
git add -A src/finch/review src/finch/gate src/finch/storage/repositories.py src/finch/storage/database.py src/finch/cli.py alembic/versions
git commit -m "refactor(inbox): drop review/gate modules + tables, migrate weekly/voice to DecisionRecord"
```

---

### Task 6: 立场机制清理（删 position_fingerprint / confirmed / position_source + 复用门禁）

**Files:**
- Modify: `src/finch/content/jobs.py`（删 `position_fingerprint`、`PositionSource.REUSED/INFERRED`、`AuthorPosition.confirmed/position_source`）
- Modify: `src/finch/graph/content_nodes.py`（删 `make_position_gate_node` 的复用门禁块）
- Modify: `src/finch/idea/service.py`（`build_content_job` 不再写 `confirmed`/`position_source`）
- Modify: `src/finch/content/jobs.py` 的 `_substantive_key`（去掉 `confirmed` 维）
- Test: `tests/unit/test_jobs.py`、`tests/unit/test_nodes.py`、`tests/unit/test_idea_service.py`、`tests/graph/test_daily.py`

**Interfaces:**
- Consumes: `AuthorPosition(claim, decision, tradeoff, change_mind_if)`（去掉 `confirmed`/`position_source`）
- Produces: 无 `position_fingerprint`/`PositionSource.REUSED/INFERRED`

- [ ] **Step 1: 改 AuthorPosition 与 PositionSource**

修改 `src/finch/content/jobs.py`：

```python
class PositionSource(StrEnum):
    """作者立场的确认来源（收窄后仅 HUMAN_CONFIRMED）。"""

    HUMAN_CONFIRMED = "human_confirmed"


class AuthorPosition(BaseModel):
    """作者立场：判断是否值得写。"""

    claim: str
    decision: str
    tradeoff: str
    change_mind_if: str | None = None
```

删除 `position_fingerprint` 函数。

- [ ] **Step 2: 改 _substantive_key（去 confirmed 维）**

修改 `src/finch/content/jobs.py` 的 `_substantive_key`：

```python
def _substantive_key(
    job: ContentJob, cards_by_id: dict[str, EvidenceCard]
) -> tuple[bool, float, bool, bool]:
    """§2.3 排序主键（去掉 confirmed 维后）。"""
    position = job.author_position
    has_decision_tradeoff = (
        position is not None
        and bool(position.decision)
        and bool(position.tradeoff)
    )
    return (
        job.candidate_id is not None,
        _evidence_ratio(job, cards_by_id),
        has_decision_tradeoff,
        bool(job.why_now),
    )
```

（同步更新 `select_primary_job`/`_defer_reason`/`_DEFER_LABELS` 中依赖 5 维的代码为 4 维；`_DEFER_LABELS` 去掉 "position not confirmed or not ready" 项。）

- [ ] **Step 3: 删复用门禁块**

修改 `src/finch/graph/content_nodes.py` 的 `make_position_gate_node`：删除 `approvals_repo` 参数与 `run` 里的复用门禁块（`fingerprint = position_fingerprint(...)` 至 `output["reused_approval"] = fingerprint` 那段），以及 `if position is not None and position.confirmed` 分支（confirmed 已删）。`position_gate` 保留「立场不完整 → needs_input」的阻塞（Phase 2 才移除）。

- [ ] **Step 4: 改 idea 服务**

修改 `src/finch/idea/service.py` 的 `build_content_job`：`AuthorPosition(...)` 构造去掉 `confirmed=True, position_source=PositionSource.HUMAN_CONFIRMED`；`PositionSource` import 删除。

- [ ] **Step 5: 跑测试确认通过**

Run:
```bash
uv run pytest tests/unit/test_jobs.py tests/unit/test_nodes.py tests/unit/test_idea_service.py tests/graph/test_daily.py -v
uv run ruff check src/finch/content/jobs.py src/finch/graph/content_nodes.py src/finch/idea/service.py
```
Expected: PASS（`test_position_gate_missing_position_needs_input` 保留但去掉复用门禁断言；`test_jobs.py` 里 `confirmed`/`position_source`/`position_fingerprint` 断言改为无这些字段）。

- [ ] **Step 6: 提交**

```bash
git add src/finch/content/jobs.py src/finch/graph/content_nodes.py src/finch/idea/service.py
git commit -m "refactor(inbox): drop position_fingerprint/confirmed/position_source + reuse gate"
```

---

## Self-Review 记录（已执行）

1. **Spec 覆盖**：spec §7.1 投影 → Task 1/2；§7.2 选择规则 → Task 2 `select_next`/`next_item`；§5.3 决策语义 → Task 3；§6 CLI → Task 4；§5.1/§5.2 存储 → Task 5；§5.1 立场机制 → Task 6。§11 迁移（alembic drop 表）→ Task 5 Step 1。无遗漏。
2. **占位符扫描**：alembic revision id 用 `<current-head>` 占位（属运行时生成，非 plan 缺陷）；无 TBD/TODO。
3. **类型一致性**：`InboxDecisionService.accept/skip` 返回 `DecisionRecord | InteractionCandidate`（测试与 CLI 解包一致）；`next_item` 返回 `dict`（含 `status`/`job_id` 兼容字段）；`original_score`/`build_original_item`/`build_engagement_item`/`select_next` 签名跨任务一致；`DecisionRecord` 去掉 `position_source`/`position_fingerprint` 后，Task 5 weekly 与 Task 4 CLI 均按新字段读写。

**范围说明（本阶段不做，留 Phase 2/3）**：互动 `decide revise`（LLM 回复重写）、条件 Critic（L0/L1）、Graph 收敛（select/write 合并）、`brief` 出图、`content_nodes.py` 拆分。均见 spec §10 阶段 2/3。
