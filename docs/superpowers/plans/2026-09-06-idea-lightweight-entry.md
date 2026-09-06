# finch idea：轻量入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `finch idea <想法/片段> [--json]` 命令——判断一个用户提交的想法能否发，能发时产出一篇进入现有 Review 的样稿，不新增 Graph、不新增数据库表、不做评分系统。

**Architecture:** 独立 typer 命令，复用现有 `ContentJob → Draft → Critic → Review` 后半段。两次结构化 LLM 调用（`assess-idea` 判断 + `write-idea` 写样稿），样稿走去掉 EvidenceChecker 的 7 检查器 Critic + `max_rewrite_rounds` 定向重写循环；只有 ready 且通过 Critic 才落库。`idea/` 是纯函数服务集合，CLI 负责 IO 与落库。

**Tech Stack:** Python 3.12、Pydantic 2、SQLModel、typer、`codex exec` / OpenAI 兼容 HTTP（`create_runner`）、pytest。

## Global Constraints

- Python 3.12+；Pydantic 2 模型（`StrEnum`/`Literal`/`Field`）；SQLModel 记录用 `payload_json` + `session.merge`（幂等）。
- Ruff 选择 `E,F,I,B,UP`，行宽 100，py312；mypy 跑 `src`。测试命令：`uv run pytest tests/unit/test_idea_service.py tests/unit/test_cli_idea.py -v`。
- **无自动发布**：`gh`/`opencli` 只读；idea 草稿与 daily 草稿一样，发布由人工 `review approve` 完成。
- **idea 草稿跳过 EvidenceChecker**（允许诚实表述的个人判断/假设）；其余 7 个检查器原样保留，Safety 仍是 hard_fail 硬门禁。
- **不新增数据库表、不新增 Graph 节点**：只复用 `ContentJobRecord`/`DraftRecord`/`CriticReportRecord`。
- **不接入 `run_daily`**：`idea` 是独立入口；`run_id` 用常量 `"idea"`，不污染 daily 摘要。
- 中文/英文 docstring 均可；新 `idea/` 模块 docstring 用中文，与相邻模块风格一致。
- 子进程调用：args 用数组（`CodexRunner` 内部已处理），每次调用有 timeout，JSON 输出经 Pydantic 校验。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `src/finch/idea/__init__.py` | 包 docstring |
| `src/finch/idea/models.py` | `IdeaAssessment` / `AssessIdeaOutput` / `WriteIdeaOutput` / `RewriteIdeaOutput` |
| `src/finch/idea/service.py` | 纯函数：`assess_idea` / `write_idea` / `build_content_job` / `build_draft` / `idea_checker_suite` / `run_idea_critic` / `rewrite_idea` / `critic_failure_reason` / `recent_author_posts` |
| `prompts/assess-idea.md` | 评估 prompt（判断 + 提取 job 语境） |
| `prompts/write-idea.md` | 写样稿 prompt |
| `src/finch/cli.py` | 新增 `idea` 命令 + 两个输出 helper + 新 import |
| `tests/unit/test_idea_service.py` | 服务纯函数测试 |
| `tests/unit/test_cli_idea.py` | CLI 命令测试（monkeypatch 掉 LLM） |

依赖方向：`cli.py → idea/service.py → (content/jobs, content/models, content/writer, content/voice, content/checkers, graph/content_nodes, evidence/models, author/models, llm/base)`。`idea/` 不依赖 `cli.py`，无循环。

---

### Task 1: idea 数据模型

**Files:**
- Create: `src/finch/idea/__init__.py`
- Create: `src/finch/idea/models.py`
- Test: `tests/unit/test_idea_service.py`

**Interfaces:**
- Produces:
  - `IdeaAssessment`（字段：`status: Literal["ready","not_ready"]`、`reason_code: str|None=None`、`reason: str=""`、`core_point: str|None=None`、`matched_evidence_ids: list[str]=[]`、`duplicate_post_url: str|None=None`、`draft_id: str|None=None`、`sample: str|None=None`）
  - `AssessIdeaOutput(IdeaAssessment)`（追加 `reader_problem`/`audience`/`understand`/`believe`/`action`/`claim`/`decision`/`tradeoff`/`change_mind_if`，均 `str|None=None`）
  - `WriteIdeaOutput`（`body: str`）
  - `RewriteIdeaOutput`（`body: str`）

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_idea_service.py`：

```python
"""Unit tests for the idea service."""

from finch.idea.models import AssessIdeaOutput, IdeaAssessment


def test_idea_assessment_round_trips():
    result = IdeaAssessment(
        status="ready",
        core_point="Graph 的价值是恢复与重放",
        matched_evidence_ids=["ev_1"],
        draft_id="draft_idea_abc",
        sample="很多 Agent 项目引入 Graph 只是为了画出来……",
    )
    back = IdeaAssessment.model_validate(result.model_dump(mode="json"))
    assert back == result
    assert back.status == "ready"
    assert back.reason_code is None


def test_assess_output_inherits_idea_assessment_fields():
    out = AssessIdeaOutput(
        status="not_ready",
        reason_code="TOO_BROAD",
        reason="没有具体问题",
        reader_problem=None,
        audience=None,
    )
    assert isinstance(out, IdeaAssessment)
    assert out.reason_code == "TOO_BROAD"
    assert out.sample is None
    assert out.matched_evidence_ids == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_idea_service.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'finch.idea'`）

- [ ] **Step 3: 实现模型**

创建 `src/finch/idea/__init__.py`：

```python
"""idea 轻量入口：判断用户想法能否发 + 生成样稿（复用现有 ContentJob → Draft → Critic → Review）。"""
```

创建 `src/finch/idea/models.py`：

```python
"""idea 数据模型（finch idea 轻量入口）。"""

from typing import Literal

from pydantic import BaseModel, Field


class IdeaAssessment(BaseModel):
    """公开 --json 输出：判断结论 +（ready 时）样稿与 draft_id。"""

    status: Literal["ready", "not_ready"]
    reason_code: str | None = None
    reason: str = ""
    core_point: str | None = None
    matched_evidence_ids: list[str] = Field(default_factory=list)
    duplicate_post_url: str | None = None
    draft_id: str | None = None
    sample: str | None = None


class AssessIdeaOutput(IdeaAssessment):
    """assess-idea 调用①内部输出：ready 时携带构建 ContentJob 的语境。"""

    reader_problem: str | None = None
    audience: str | None = None
    understand: str | None = None
    believe: str | None = None
    action: str | None = None
    claim: str | None = None
    decision: str | None = None
    tradeoff: str | None = None
    change_mind_if: str | None = None


class WriteIdeaOutput(BaseModel):
    """write-idea 调用②内部输出：样稿正文。"""

    body: str


class RewriteIdeaOutput(BaseModel):
    """rewrite 内部输出：只回传正文（idea 草稿 claims 恒为空）。"""

    body: str
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_idea_service.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/finch/idea/__init__.py src/finch/idea/models.py tests/unit/test_idea_service.py
git commit -m "feat(idea): assessment/draft output models"
```

---

### Task 2: idea 纯函数 helper（build job/draft + 最近帖子）

**Files:**
- Create: `src/finch/idea/service.py`（本任务只含 helper，后续任务追加 LLM 函数）
- Test: `tests/unit/test_idea_service.py`（追加）

**Interfaces:**
- Consumes（来自 Task 1）: `AssessIdeaOutput`
- Produces:
  - `recent_author_posts(posts: list[AuthorPost], limit: int = 25) -> list[AuthorPost]`（按 `published_at` 降序、`kind ∈ {"original","reply"}`、取前 `limit`）
  - `build_content_job(text: str, assessment: AssessIdeaOutput) -> ContentJob`（`id="idea_"+sha256(text)[:8]`、`source_card_ids=matched_evidence_ids`、`author_position.confirmed=True` + `position_source=HUMAN_CONFIRMED`、`success_criteria` 固定一条 `idea_human_review`）
  - `build_draft(job: ContentJob, body: str) -> Draft`（`id="draft_"+job.id`、`kind=ORIGINAL`、`language="zh"`、`claims=[]`、`run_id="idea"`）

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_idea_service.py` 顶部 import 与末尾测试：

```python
from datetime import UTC, datetime, timedelta

from finch.author.models import AuthorPost
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    ContentScope,
    IntendedEffect,
    PositionSource,
    SuccessCriterion,
)
from finch.content.models import DraftKind
from finch.idea.models import AssessIdeaOutput
from finch.idea.service import build_content_job, build_draft, recent_author_posts


def _assessment(**overrides) -> AssessIdeaOutput:
    data = dict(
        status="ready",
        core_point="Graph 的价值是恢复与重放",
        matched_evidence_ids=["ev_1"],
        reader_problem="很多工程师只把 Graph 理解成流程可视化",
        audience="正在构建生产 Agent 的工程师",
        understand="Graph 的核心价值是恢复与重放",
        believe="用可恢复性评价 Graph",
        action="重新审视 Graph 的选型",
        claim="Graph 的主要价值是支持恢复与重放",
        decision="用可恢复性而不是图的复杂度评价 Graph",
        tradeoff="需要持久化状态并处理副作用幂等",
        change_mind_if=None,
    )
    data.update(overrides)
    return AssessIdeaOutput(**data)


def test_build_content_job_confirms_human_position():
    job = build_content_job("我觉得 Agent Graph 的价值是恢复", _assessment())
    assert job.id.startswith("idea_")
    assert job.source_card_ids == ["ev_1"]
    assert job.candidate_id is None
    assert job.recommended_format == DraftKind.ORIGINAL
    assert job.status == ContentJobStatus.READY
    assert job.scope == ContentScope.BOUNDED_LESSON
    assert job.author_position is not None
    assert job.author_position.confirmed is True
    assert job.author_position.position_source == PositionSource.HUMAN_CONFIRMED
    assert job.author_position.decision == "用可恢复性而不是图的复杂度评价 Graph"
    assert job.intended_effect.understand == "Graph 的核心价值是恢复与重放"
    assert job.success_criteria[0].id == "idea_human_review"
    assert job.success_criteria[0].measurement == "human"


def test_build_content_job_deterministic_id():
    a = build_content_job("同一个想法", _assessment())
    b = build_content_job("同一个想法", _assessment())
    assert a.id == b.id


def test_build_draft_idea_fields():
    job = build_content_job("想法", _assessment())
    draft = build_draft(job, "样稿正文")
    assert draft.id == f"draft_{job.id}"
    assert draft.kind == DraftKind.ORIGINAL
    assert draft.language == "zh"
    assert draft.claims == []
    assert draft.content_job_id == job.id
    assert draft.run_id == "idea"
    assert draft.position_statement == "用可恢复性而不是图的复杂度评价 Graph"


def _post(remote_id, published_at, kind="original"):
    return AuthorPost(
        platform="x",
        remote_post_id=remote_id,
        author_account_id="acct",
        kind=kind,
        body=f"post {remote_id}",
        url=f"https://x.com/u/{remote_id}",
        published_at=published_at,
    )


def test_recent_author_posts_sorts_filters_limits():
    now = datetime.now(UTC)
    posts = [
        _post("a", now - timedelta(days=10)),
        _post("b", now - timedelta(days=1)),
        _post("c", now - timedelta(days=3), kind="quote"),
        _post("d", now - timedelta(days=2), kind="reply"),
    ]
    out = recent_author_posts(posts, limit=3)
    ids = [p.remote_post_id for p in out]
    assert ids == ["b", "d", "a"]
    assert "c" not in ids
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_idea_service.py -v`
Expected: FAIL（`ImportError: cannot import name 'build_content_job' from 'finch.idea.service'`）

- [ ] **Step 3: 实现 helper**

创建 `src/finch/idea/service.py`：

```python
"""idea 服务：纯函数集合（判断 + 写样稿 + Critic + 对象构建），不访问 DB。

CLI 负责 IO 与落库；本模块不依赖 finch.cli，避免循环导入。
"""

import hashlib

from finch.author.models import AuthorPost
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    ContentScope,
    IntendedEffect,
    PositionSource,
    SuccessCriterion,
)
from finch.content.models import Draft, DraftKind
from finch.idea.models import AssessIdeaOutput

_IDEA_SUCCESS_CRITERION = SuccessCriterion(
    id="idea_human_review",
    description="人工审核确认是否发布",
    measurement="human",
)


def recent_author_posts(posts: list[AuthorPost], limit: int = 25) -> list[AuthorPost]:
    """返回作者最近的原创/回复（按 published_at 降序，取前 limit）。"""
    filtered = [p for p in posts if p.kind in {"original", "reply"}]
    filtered.sort(key=lambda p: p.published_at, reverse=True)
    return filtered[:limit]


def build_content_job(text: str, assessment: AssessIdeaOutput) -> ContentJob:
    """把评估结果转换成已确认立场的 ContentJob（id 由文本哈希确定，幂等）。"""
    job_id = "idea_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return ContentJob(
        id=job_id,
        source_card_ids=list(assessment.matched_evidence_ids),
        candidate_id=None,
        reader_problem=assessment.reader_problem or "",
        audience=assessment.audience or "",
        intended_effect=IntendedEffect(
            understand=assessment.understand or "",
            believe=assessment.believe,
            action=assessment.action,
        ),
        author_position=AuthorPosition(
            claim=assessment.claim or "",
            decision=assessment.decision or "",
            tradeoff=assessment.tradeoff or "",
            change_mind_if=assessment.change_mind_if,
            confirmed=True,
            position_source=PositionSource.HUMAN_CONFIRMED,
        ),
        success_criteria=[_IDEA_SUCCESS_CRITERION],
        recommended_format=DraftKind.ORIGINAL,
        status=ContentJobStatus.READY,
        scope=ContentScope.BOUNDED_LESSON,
    )


def build_draft(job: ContentJob, body: str) -> Draft:
    """把样稿正文包成 idea 草稿（claims 恒为空，run_id 用常量 idea）。"""
    return Draft(
        id=f"draft_{job.id}",
        kind=DraftKind.ORIGINAL,
        candidate_id=None,
        language="zh",
        body=body,
        claims=[],
        content_job_id=job.id,
        position_statement=job.author_position.decision if job.author_position else "",
        run_id="idea",
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_idea_service.py -v`
Expected: PASS（全部通过，含 Task 1 的 2 个）

- [ ] **Step 5: 提交**

```bash
git add src/finch/idea/service.py tests/unit/test_idea_service.py
git commit -m "feat(idea): build content job/draft + recent posts helper"
```

---

### Task 3: assess_idea 与 write_idea（两次 LLM 调用 + prompts）

**Files:**
- Create: `prompts/assess-idea.md`
- Create: `prompts/write-idea.md`
- Modify: `src/finch/idea/service.py`（追加 `assess_idea` / `write_idea` / `_slim_cards` / `_slim_posts` / `_IDEA_REWRITE_PROMPT` / `rewrite_idea`）
- Test: `tests/unit/test_idea_service.py`（追加）

**Interfaces:**
- Consumes: `AssessIdeaOutput`（Task 1）、`EvidenceCard`、`AuthorPost`
- Produces:
  - `assess_idea(runner: StructuredInferenceRunner, text: str, cards: list[EvidenceCard], recent_posts: list[AuthorPost]) -> AssessIdeaOutput`
  - `write_idea(runner: StructuredInferenceRunner, text: str, assessment: AssessIdeaOutput, cards: list[EvidenceCard]) -> str`（返回正文）
  - `rewrite_idea(runner: StructuredInferenceRunner, draft: Draft, failed_checks: list[CheckResult], job: ContentJob | None) -> Draft`

- [ ] **Step 1: 写 prompt 文件**

创建 `prompts/assess-idea.md`：

```markdown
你判断一段用户提交的想法是否适合公开发布，并（适合时）提取写作所需的语境。按 schema 返回 JSON。
不要读取文件、运行命令或使用任何工具，只依据下方数据作答。

判断标准（四项全部满足才 status="ready"）：
1. 有明确观点（不是泛泛而谈）。
2. 对具体读者有新增价值。
3. 有证据支撑，或诚实表述为个人判断/假设（不把推断写成已验证事实）。
4. 与近期发布内容不高度重复。

不适合时（status="not_ready"）：
- 只给出一个最主要原因，reason_code 六选一：NO_CLEAR_POINT / NO_NEW_VALUE /
  INSUFFICIENT_EVIDENCE / DUPLICATE_CONTENT / TOO_BROAD / UNSAFE_TO_PUBLISH。
- DUPLICATE_CONTENT 时填 duplicate_post_url（取自 Recent posts 的 url）。
- core_point / matched_evidence_ids / reader_problem 等语境字段留空。

适合时（status="ready"）：
- core_point：一句话概括核心观点。
- matched_evidence_ids：从 Evidence cards 里选与观点最相关的卡 id（最多 5 个，可空）。
- reader_problem / audience / understand / believe / action：目标读者与其困惑、预期效果。
- claim / decision / tradeoff / change_mind_if：作者立场（change_mind_if 可空）。
- draft_id / sample 留空（由后续步骤生成）。

## 用户想法
{text}

## Evidence cards（精简：id / claim / topics）
{cards}

## Recent posts（作者近期原创与回复，用于查重）
{recent_posts}
```

创建 `prompts/write-idea.md`：

```markdown
你根据用户的原始想法写一篇中文短内容草稿。按 schema 返回 JSON（只有 body 字段）。
不要读取文件、运行命令或使用任何工具，只依据下方数据作答。
Instructions:
- 保持用户核心观点，用作者自己的口吻表达。
- 观点诚实表述为个人判断/假设，不把推断写成已验证事实。
- 只把 Evidence cards 当作参考上下文（提供具体案例），不编造来源或数字。
- 篇幅短：一段观点 + 一句具体例子或取舍，不写长文。

## 用户想法
{text}

## 核心观点
{core_point}

## 目标读者
{audience}

## 作者立场
claim: {claim}
decision: {decision}
tradeoff: {tradeoff}

## Evidence cards（参考上下文，可选）
{cards}
```

- [ ] **Step 2: 写失败测试**

追加到 `tests/unit/test_idea_service.py`：

```python
from finch.content.checkers.base import CheckResult
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.idea.models import RewriteIdeaOutput
from finch.idea.service import assess_idea, rewrite_idea, write_idea


class FakeRunner:
    def __init__(self, ret):
        self.calls = 0
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.ret


def _card(card_id="ev_1"):
    return EvidenceCard(
        id=card_id,
        event_id="evt",
        claim="replay 让失败可重放",
        sources=[],
        confidence=ClaimConfidence.SUPPORTED,
        publishable=True,
        topics=["graph"],
    )


def test_assess_idea_calls_runner_once():
    ret = AssessIdeaOutput(status="ready", core_point="p", matched_evidence_ids=["ev_1"])
    runner = FakeRunner(ret)
    out = assess_idea(runner, "想法", [_card()], [])
    assert runner.calls == 1
    assert out == ret


def test_write_idea_returns_body():
    runner = FakeRunner(WriteIdeaOutput(body="样稿正文"))
    body = write_idea(runner, "想法", _assessment(), [_card()])
    assert runner.calls == 1
    assert body == "样稿正文"


def test_rewrite_idea_keeps_draft_identity_updates_body():
    job = build_content_job("想法", _assessment())
    draft = build_draft(job, "旧正文")
    runner = FakeRunner(RewriteIdeaOutput(body="新正文"))
    out = rewrite_idea(
        runner,
        draft,
        [CheckResult(checker="specificity", passed=False, severity="medium",
                     issues=["vague"], rewrite_instructions=["be specific"])],
        job,
    )
    assert runner.calls == 1
    assert out.body == "新正文"
    assert out.id == draft.id
    assert out.claims == []
    assert out.content_job_id == job.id
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_idea_service.py -v`
Expected: FAIL（`ImportError: cannot import name 'assess_idea'`）

- [ ] **Step 4: 实现 LLM 函数**

修改 `src/finch/idea/service.py`，把 import 块替换为下面完整内容（追加 `json`/`Path`/`cast` 与 checkers/evidence/llm/writer 依赖），并追加函数：

```python
"""idea 服务：纯函数集合（判断 + 写样稿 + Critic + 对象构建），不访问 DB。

CLI 负责 IO 与落库；本模块不依赖 finch.cli，避免循环导入。
"""

import hashlib
import json
from pathlib import Path
from typing import cast

from finch.author.models import AuthorPost
from finch.content.checkers.base import CheckContext, CheckResult
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    ContentScope,
    IntendedEffect,
    PositionSource,
    SuccessCriterion,
)
from finch.content.models import Draft, DraftKind
from finch.content.writer import _render_failed_checks, _render_job_context
from finch.evidence.models import EvidenceCard
from finch.idea.models import (
    AssessIdeaOutput,
    RewriteIdeaOutput,
    WriteIdeaOutput,
)
from finch.llm.base import StructuredInferenceRunner

_ASSESS_PROMPT_PATH = Path("prompts/assess-idea.md")
_WRITE_PROMPT_PATH = Path("prompts/write-idea.md")
_MAX_ASSESS_CARDS = 50

_IDEA_SUCCESS_CRITERION = SuccessCriterion(
    id="idea_human_review",
    description="人工审核确认是否发布",
    measurement="human",
)

_IDEA_REWRITE_PROMPT = """\
You rewrite a draft to address specific critic check failures. Return JSON matching the schema.
Instructions:
- Keep the same voice and personal-judgment framing as the Original draft.
- Fix exactly the failures listed under Failed checks. Do NOT restyle, polish, or improve
  the rest of the draft — change only what is needed to resolve the listed failures.

{job_context}## Original draft
{body}

## Failed checks
{rewrite_instructions}
"""


def recent_author_posts(posts: list[AuthorPost], limit: int = 25) -> list[AuthorPost]:
    """返回作者最近的原创/回复（按 published_at 降序，取前 limit）。"""
    filtered = [p for p in posts if p.kind in {"original", "reply"}]
    filtered.sort(key=lambda p: p.published_at, reverse=True)
    return filtered[:limit]


def build_content_job(text: str, assessment: AssessIdeaOutput) -> ContentJob:
    """把评估结果转换成已确认立场的 ContentJob（id 由文本哈希确定，幂等）。"""
    job_id = "idea_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return ContentJob(
        id=job_id,
        source_card_ids=list(assessment.matched_evidence_ids),
        candidate_id=None,
        reader_problem=assessment.reader_problem or "",
        audience=assessment.audience or "",
        intended_effect=IntendedEffect(
            understand=assessment.understand or "",
            believe=assessment.believe,
            action=assessment.action,
        ),
        author_position=AuthorPosition(
            claim=assessment.claim or "",
            decision=assessment.decision or "",
            tradeoff=assessment.tradeoff or "",
            change_mind_if=assessment.change_mind_if,
            confirmed=True,
            position_source=PositionSource.HUMAN_CONFIRMED,
        ),
        success_criteria=[_IDEA_SUCCESS_CRITERION],
        recommended_format=DraftKind.ORIGINAL,
        status=ContentJobStatus.READY,
        scope=ContentScope.BOUNDED_LESSON,
    )


def build_draft(job: ContentJob, body: str) -> Draft:
    """把样稿正文包成 idea 草稿（claims 恒为空，run_id 用常量 idea）。"""
    return Draft(
        id=f"draft_{job.id}",
        kind=DraftKind.ORIGINAL,
        candidate_id=None,
        language="zh",
        body=body,
        claims=[],
        content_job_id=job.id,
        position_statement=job.author_position.decision if job.author_position else "",
        run_id="idea",
    )


def _slim_cards(cards: list[EvidenceCard]) -> list[dict]:
    slim: list[dict] = []
    for card in cards[:_MAX_ASSESS_CARDS]:
        slim.append({"id": card.id, "claim": card.claim, "topics": card.topics})
    return slim


def _slim_posts(posts: list[AuthorPost]) -> list[dict]:
    return [
        {
            "kind": p.kind,
            "body": p.body,
            "url": p.url,
            "published_at": p.published_at.isoformat(),
        }
        for p in posts
    ]


def assess_idea(
    runner: StructuredInferenceRunner,
    text: str,
    cards: list[EvidenceCard],
    recent_posts: list[AuthorPost],
) -> AssessIdeaOutput:
    """调用①：判断能否发 + 提取 job 语境。"""
    prompt = _ASSESS_PROMPT_PATH.read_text().format(
        text=text,
        cards=json.dumps(_slim_cards(cards), ensure_ascii=False),
        recent_posts=json.dumps(_slim_posts(recent_posts), ensure_ascii=False),
    )
    return cast(AssessIdeaOutput, runner.run(prompt, AssessIdeaOutput))


def write_idea(
    runner: StructuredInferenceRunner,
    text: str,
    assessment: AssessIdeaOutput,
    cards: list[EvidenceCard],
) -> str:
    """调用②：把用户原文 + 证据扩写成样稿正文。"""
    prompt = _WRITE_PROMPT_PATH.read_text().format(
        text=text,
        core_point=assessment.core_point or "",
        audience=assessment.audience or "",
        claim=assessment.claim or "",
        decision=assessment.decision or "",
        tradeoff=assessment.tradeoff or "",
        cards=json.dumps([c.model_dump(mode="json") for c in cards], ensure_ascii=False),
    )
    out = cast(WriteIdeaOutput, runner.run(prompt, WriteIdeaOutput))
    return out.body


def rewrite_idea(
    runner: StructuredInferenceRunner,
    draft: Draft,
    failed_checks: list[CheckResult],
    job: ContentJob | None,
) -> Draft:
    """按 Critic 失败项定向重写（只回传新正文，claims 恒为空）。"""
    prompt = _IDEA_REWRITE_PROMPT.format(
        body=draft.body,
        job_context=_render_job_context(job) if job is not None else "",
        rewrite_instructions=_render_failed_checks(failed_checks),
    )
    out = cast(RewriteIdeaOutput, runner.run(prompt, RewriteIdeaOutput))
    return draft.model_copy(update={"body": out.body})
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_idea_service.py -v`
Expected: PASS（含前两任务的测试）

- [ ] **Step 6: 提交**

```bash
git add src/finch/idea/service.py prompts/assess-idea.md prompts/write-idea.md tests/unit/test_idea_service.py
git commit -m "feat(idea): assess/write/rewrite LLM calls + prompts"
```

---

### Task 4: Critic 循环（去 EvidenceChecker + rewrite 循环 + 失败映射）

**Files:**
- Modify: `src/finch/idea/service.py`（追加 `idea_checker_suite` / `run_idea_critic` / `critic_failure_reason` / `_joined_issues`）
- Test: `tests/unit/test_idea_service.py`（追加）

**Interfaces:**
- Consumes: `default_checker_suite` / `_run_checks`（`finch.graph.content_nodes`）、`aggregate_checks` / `AggregateOutcome`（`finch.content.checkers`）、`VoiceProfile`、`Checker`
- Produces:
  - `idea_checker_suite(runner: StructuredInferenceRunner | None, voice_profile: VoiceProfile | None = None) -> list[Checker]`（`default_checker_suite` 去掉 `name == "evidence"`，共 7 个）
  - `run_idea_critic(runner, draft, job, cards, max_rewrite_rounds, *, checkers=None, voice_profile=None) -> tuple[str, list[CheckResult], Draft]`（返回 `(outcome, checks, final_draft)`；`outcome ∈ {"pass","rewrite","reject","needs_input"}`）
  - `critic_failure_reason(checks: list[CheckResult]) -> tuple[str, str]`（Safety hard_fail → `("UNSAFE_TO_PUBLISH", ...)`；其余 → `("NO_NEW_VALUE", ...)`）

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_idea_service.py`：

```python
from finch.content.checkers.aggregate import AggregateOutcome
from finch.idea.service import critic_failure_reason, idea_checker_suite, run_idea_critic


def _pass_check():
    return CheckResult(checker="scripted", passed=True, severity="low")


def _rewrite_check():
    return CheckResult(
        checker="scripted", passed=False, severity="medium",
        issues=["vague"], rewrite_instructions=["be specific"],
    )


def _reject_check():
    return CheckResult(
        checker="safety", passed=False, severity="hard_fail", issues=["unsafe"],
    )


class ScriptedChecker:
    name = "scripted"

    def __init__(self, results):
        self._results = list(results)
        self._i = 0

    def check(self, ctx):
        result = self._results[self._i]
        self._i = min(self._i + 1, len(self._results) - 1)
        return result


def _critic_draft_and_job(body="正文"):
    job = build_content_job("想法", _assessment())
    return build_draft(job, body), job


def test_idea_checker_suite_drops_evidence():
    suite = idea_checker_suite(runner=None)
    names = [c.name for c in suite]
    assert "evidence" not in names
    assert len(suite) == 7


def test_run_idea_critic_passes():
    draft, job = _critic_draft_and_job()
    suite = [ScriptedChecker([_pass_check()])]
    outcome, checks, final = run_idea_critic(
        None, draft, job, [], max_rewrite_rounds=1, checkers=suite
    )
    assert outcome == AggregateOutcome.PASS
    assert final == draft


def test_run_idea_critic_rewrites_then_passes():
    draft, job = _critic_draft_and_job("旧正文")
    suite = [ScriptedChecker([_rewrite_check(), _pass_check()])]
    runner = FakeRunner(RewriteIdeaOutput(body="新正文"))
    outcome, checks, final = run_idea_critic(
        runner, draft, job, [], max_rewrite_rounds=1, checkers=suite
    )
    assert outcome == AggregateOutcome.PASS
    assert final.body == "新正文"
    assert runner.calls == 1


def test_run_idea_critic_rejects_on_hard_fail():
    draft, job = _critic_draft_and_job()
    suite = [ScriptedChecker([_reject_check()])]
    outcome, checks, final = run_idea_critic(
        None, draft, job, [], max_rewrite_rounds=1, checkers=suite
    )
    assert outcome == AggregateOutcome.REJECT
    assert final == draft


def test_run_idea_critic_rewrite_exhausted():
    draft, job = _critic_draft_and_job()
    suite = [ScriptedChecker([_rewrite_check()])]
    runner = FakeRunner(RewriteIdeaOutput(body="还是不够"))
    outcome, checks, final = run_idea_critic(
        runner, draft, job, [], max_rewrite_rounds=1, checkers=suite
    )
    assert outcome == AggregateOutcome.REWRITE


def test_critic_failure_reason_maps_safety():
    code, reason = critic_failure_reason([_reject_check()])
    assert code == "UNSAFE_TO_PUBLISH"
    assert "safety" in reason


def test_critic_failure_reason_defaults_to_no_new_value():
    code, reason = critic_failure_reason([_rewrite_check()])
    assert code == "NO_NEW_VALUE"
    assert "scripted" in reason
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_idea_service.py -v`
Expected: FAIL（`ImportError: cannot import name 'run_idea_critic'`）

- [ ] **Step 3: 实现 Critic 循环**

修改 `src/finch/idea/service.py`，在 import 块追加：

```python
from finch.content.checkers.aggregate import AggregateOutcome, aggregate_checks
from finch.content.checkers.base import Checker
from finch.content.voice import VoiceProfile
from finch.graph.content_nodes import _run_checks, default_checker_suite
```

并在文件末尾追加：

```python
def idea_checker_suite(
    runner: StructuredInferenceRunner | None,
    voice_profile: VoiceProfile | None = None,
) -> list[Checker]:
    """Critic 套件去掉 EvidenceChecker（idea 允许个人判断/假设，不强制证据绑定）。"""
    return [c for c in default_checker_suite(runner, voice_profile) if c.name != "evidence"]


def run_idea_critic(
    runner: StructuredInferenceRunner | None,
    draft: Draft,
    job: ContentJob,
    cards: list[EvidenceCard],
    max_rewrite_rounds: int,
    *,
    checkers: list[Checker] | None = None,
    voice_profile: VoiceProfile | None = None,
) -> tuple[str, list[CheckResult], Draft]:
    """跑 Critic + 定向重写循环，返回 (outcome, checks, final_draft)。

    outcome ∈ {"pass","rewrite","reject","needs_input"}（与 aggregate_checks 对齐）。
    pass 才落库；rewrite 用尽 max_rewrite_rounds 后仍不 pass 即返回 "rewrite"。
    """
    suite = checkers if checkers is not None else idea_checker_suite(runner, voice_profile)
    current = draft
    checks: list[CheckResult] = []
    for i in range(max_rewrite_rounds + 1):
        ctx = CheckContext(draft=current, cards=cards, job=job)
        checks = _run_checks(suite, ctx)
        outcome = aggregate_checks(checks)
        if outcome != AggregateOutcome.REWRITE:
            return outcome, checks, current
        if i == max_rewrite_rounds:
            return AggregateOutcome.REWRITE, checks, current
        failed = [c for c in checks if not c.passed]
        current = rewrite_idea(runner, current, failed, job)
    return AggregateOutcome.REWRITE, checks, current


def _joined_issues(checks: list[CheckResult]) -> str:
    parts: list[str] = []
    for check in checks:
        if not check.passed:
            detail = "; ".join(check.issues) if check.issues else "failed"
            parts.append(f"{check.checker}: {detail}")
    return " | ".join(parts) or "critic failed"


def critic_failure_reason(checks: list[CheckResult]) -> tuple[str, str]:
    """把 Critic 失败映射到 (reason_code, reason)。"""
    failed = [c for c in checks if not c.passed]
    if any(c.checker == "safety" and c.severity == "hard_fail" for c in failed):
        return "UNSAFE_TO_PUBLISH", _joined_issues(failed)
    return "NO_NEW_VALUE", _joined_issues(failed)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_idea_service.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: 提交**

```bash
git add src/finch/idea/service.py tests/unit/test_idea_service.py
git commit -m "feat(idea): critic loop without EvidenceChecker + failure mapping"
```

---

### Task 5: CLI 命令

**Files:**
- Modify: `src/finch/cli.py`（新增 import + `idea` 命令 + `_echo_idea` / `_echo_idea_error` helper）
- Test: `tests/unit/test_cli_idea.py`（新建）

**Interfaces:**
- Consumes（服务层全部函数，Task 1–4）: `IdeaAssessment`、`assess_idea`、`write_idea`、`build_content_job`、`build_draft`、`run_idea_critic`、`critic_failure_reason`、`recent_author_posts`
- Produces: CLI 命令 `finch idea <text> [--json]`

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_cli_idea.py`：

```python
"""Unit tests for finch idea CLI command."""

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.content.checkers.aggregate import AggregateOutcome
from finch.content.voice import VoiceProfile
from finch.idea.models import AssessIdeaOutput
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import ContentJobRepository, DraftRepository


def _settings(tmp_path):
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


def _ready_assessment():
    return AssessIdeaOutput(
        status="ready",
        core_point="Graph 的价值是恢复与重放",
        matched_evidence_ids=[],
        reader_problem="很多人把 Graph 理解成流程可视化",
        audience="构建生产 Agent 的工程师",
        understand="Graph 的核心价值是恢复与重放",
        claim="Graph 的主要价值是恢复与重放",
        decision="用可恢复性评价 Graph",
        tradeoff="需要持久化状态",
    )


def _fake_pass_critic(runner, draft, job, cards, max_rounds, **kw):
    return (AggregateOutcome.PASS, [], draft)


def test_idea_ready_persists_and_outputs_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_voice_profile", lambda _p: VoiceProfile())
    monkeypatch.setattr(cli, "assess_idea", lambda *a, **k: _ready_assessment())
    monkeypatch.setattr(cli, "write_idea", lambda *a, **k: "样稿正文")
    monkeypatch.setattr(cli, "run_idea_critic", _fake_pass_critic)

    r = CliRunner().invoke(app, ["idea", "我觉得 Agent Graph 的价值是恢复", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["status"] == "ready"
    assert payload["draft_id"].startswith("draft_idea_")
    assert payload["sample"] == "样稿正文"

    draft = DraftRepository(store).get_draft(payload["draft_id"])
    assert draft is not None
    assert draft.run_id == "idea"
    jobs = ContentJobRepository(store).list_jobs()
    assert len(jobs) == 1
    assert jobs[0].author_position is not None
    assert jobs[0].author_position.confirmed is True


def test_idea_not_ready_no_persist(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_voice_profile", lambda _p: VoiceProfile())
    monkeypatch.setattr(
        cli, "assess_idea",
        lambda *a, **k: AssessIdeaOutput(status="not_ready", reason_code="TOO_BROAD",
                                         reason="没有具体问题"),
    )

    r = CliRunner().invoke(app, ["idea", "Agent 很重要", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["status"] == "not_ready"
    assert payload["reason_code"] == "TOO_BROAD"

    assert DraftRepository(store).list_drafts() == []
    assert ContentJobRepository(store).list_jobs() == []


def test_idea_critic_failure_maps_unsafe(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_voice_profile", lambda _p: VoiceProfile())
    monkeypatch.setattr(cli, "assess_idea", lambda *a, **k: _ready_assessment())
    monkeypatch.setattr(cli, "write_idea", lambda *a, **k: "样稿正文")

    def _fake_unsafe(runner, draft, job, cards, max_rounds, **kw):
        from finch.content.checkers.base import CheckResult
        return (
            AggregateOutcome.REJECT,
            [CheckResult(checker="safety", passed=False, severity="hard_fail",
                         issues=["unsafe"])],
            draft,
        )

    monkeypatch.setattr(cli, "run_idea_critic", _fake_unsafe)

    r = CliRunner().invoke(app, ["idea", "想法", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["status"] == "not_ready"
    assert payload["reason_code"] == "UNSAFE_TO_PUBLISH"
    assert DraftRepository(store).list_drafts() == []


def test_idea_empty_text_exits(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["idea", "   "])
    assert r.exit_code == 1
    assert "empty" in r.output
    assert DraftRepository(store).list_drafts() == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_cli_idea.py -v`
Expected: FAIL（`AttributeError: module 'finch.cli' has no attribute 'idea'`，或调用时 `No such command`）

- [ ] **Step 3: 实现 CLI**

修改 `src/finch/cli.py`。

（a）在现有 import 区追加三处（`cli.py` 顶层 import 已按 isort 排序，位置如下）：

在 `from .content.checkers.base import CheckResult` 之前新增：

```python
from .content.checkers.aggregate import AggregateOutcome
```

在 `from .graph.state import GraphState` 之后、`from .llm.openai_compatible import create_runner` 之前新增：

```python
from .idea.models import IdeaAssessment
from .idea.service import (
    assess_idea,
    build_content_job,
    build_draft,
    critic_failure_reason,
    recent_author_posts,
    run_idea_critic,
    write_idea,
)
```

在 `from .storage.repositories import (` 列表内，把 `AuthorPostRepository,` 加到列表首行（`CommitIngestionRepository,` 之前）：

```python
from .storage.repositories import (
    AuthorPostRepository,
    CommitIngestionRepository,
    ...
)
```

（b）在文件末尾 `if __name__ == "__main__":` 之前追加命令与 helper：

```python
@app.command()
def idea(
    text: str = typer.Argument(..., help="想法或片段"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),  # noqa: B008
) -> None:
    """判断一个想法能否发，能发时生成样稿进入 Review。"""
    if not text.strip():
        typer.echo("idea text is empty")
        raise typer.Exit(code=1)

    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()

    cards = EvidenceRepository(store).list_cards()
    cards_by_id = {card.id: card for card in cards}
    recent_posts = recent_author_posts(AuthorPostRepository(store).list())

    assess_runner = create_runner(settings.llm, "assess_idea") or CodexRunner()
    write_runner = create_runner(settings.llm, "write_idea") or CodexRunner()
    critic_runner = create_runner(settings.llm, "critique") or CodexRunner()
    voice_profile = load_voice_profile(settings.paths.voice_profile_path)

    try:
        assessment = assess_idea(assess_runner, text, cards, recent_posts)
    except (RuntimeError, StructuredOutputError) as exc:
        _echo_idea_error(exc, as_json)
        raise typer.Exit(code=1) from exc

    if assessment.status != "ready":
        _echo_idea(assessment, as_json)
        return

    matched_cards = [
        cards_by_id[cid] for cid in assessment.matched_evidence_ids if cid in cards_by_id
    ]
    try:
        body = write_idea(write_runner, text, assessment, matched_cards)
        job = build_content_job(text, assessment)
        draft = build_draft(job, body)
        outcome, checks, final_draft = run_idea_critic(
            critic_runner,
            draft,
            job,
            matched_cards,
            settings.quality_gates.max_rewrite_rounds,
            voice_profile=voice_profile,
        )
    except (RuntimeError, StructuredOutputError) as exc:
        _echo_idea_error(exc, as_json)
        raise typer.Exit(code=1) from exc

    if outcome != AggregateOutcome.PASS:
        reason_code, reason = critic_failure_reason(checks)
        _echo_idea(
            IdeaAssessment(
                status="not_ready",
                reason_code=reason_code,
                reason=reason,
                core_point=assessment.core_point,
                matched_evidence_ids=assessment.matched_evidence_ids,
            ),
            as_json,
        )
        return

    ContentJobRepository(store).upsert_job(job)
    DraftRepository(store).upsert_draft(final_draft)
    CriticReportRepository(store).upsert_report(
        final_draft.id, 0, checks, AggregateOutcome.PASS
    )

    _echo_idea(
        IdeaAssessment(
            status="ready",
            reason_code=None,
            reason="观点明确，且通过 Critic",
            core_point=assessment.core_point,
            matched_evidence_ids=assessment.matched_evidence_ids,
            draft_id=final_draft.id,
            sample=final_draft.body,
        ),
        as_json,
    )


def _echo_idea(assessment: IdeaAssessment, as_json: bool) -> None:
    if as_json:
        typer.echo(assessment.model_dump_json(indent=2))
        return
    if assessment.status == "ready":
        typer.echo("适合发。")
        typer.echo(f"\n原因\n{assessment.reason}")
        if assessment.core_point:
            typer.echo(f"\n核心观点\n{assessment.core_point}")
        if assessment.sample:
            typer.echo(f"\n样稿\n{assessment.sample}")
        if assessment.draft_id:
            typer.echo(f"\n采用：finch review approve {assessment.draft_id}")
            typer.echo(f"修改：finch review revise {assessment.draft_id} --file <path>")
            typer.echo(f"跳过：finch review skip {assessment.draft_id} --reason not_now")
    else:
        typer.echo("暂时不适合发。")
        typer.echo(f"\n原因\n{assessment.reason}")
        if assessment.reason_code:
            typer.echo(f"（{assessment.reason_code}）")


def _echo_idea_error(exc: Exception, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
    else:
        typer.echo(f"idea failed: {exc}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_cli_idea.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 全量回归 + lint + mypy**

Run:
```bash
uv run pytest tests/unit/test_idea_service.py tests/unit/test_cli_idea.py -v
uv run ruff check src/finch/idea src/finch/cli.py tests/unit/test_idea_service.py tests/unit/test_cli_idea.py
uv run mypy src/finch/idea
```
Expected: 全部通过，无 lint/type 错误。

- [ ] **Step 6: 提交**

```bash
git add src/finch/cli.py tests/unit/test_cli_idea.py
git commit -m "feat(idea): finch idea CLI command"
```

---

## Self-Review 记录（已执行）

1. **Spec 覆盖**：spec §4 数据模型 → Task 1；§5 流程 + §7 判断标准 → Task 3（assess/write）与 Task 4（critic 循环）；§6 Critic 复用 + reason_code 映射 → Task 4；§8 CLI 契约 → Task 5；§10 测试 → 各任务内嵌。无遗漏。
2. **占位符扫描**：无 TBD/TODO；所有代码步骤给出完整内容；无「写测试」空话。
3. **类型一致性**：`run_idea_critic` 返回 `tuple[str, list[CheckResult], Draft]`，Task 5 CLI 解包 `outcome, checks, final_draft` 一致；`critic_failure_reason(checks)` 签名 Task 4 定义与 Task 5 调用一致；`build_content_job(text, assessment)` / `build_draft(job, body)` 签名跨任务一致；`recent_author_posts(posts, limit=25)` 一致。
