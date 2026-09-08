# Finch Skill 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Finch 的 Skill 从「按来源拆分」重构为「按认知任务拆分」的 8 个 Skill，并为其中需要状态/持久化/LLM 判断的新 Skill 补齐 Python 领域服务与 CLI。

**Architecture:** 8 个 Skill（6 核心 + 2 辅助）覆盖 idea-discovery / conversation-scout / expression-practice / idea-to-draft / voice-profile / weekly-reflection / feynman-practice / sticky-message。确定性状态机、存储、幂等、评分、Critic 门禁继续留在 Python 领域服务；Skill 层只做「认知任务 + 调 CLI」。本次新增 `Opportunity`（conversation-scout 产物）、`PracticeSession`（expression-practice）两个持久化模型，扩展 `IdeaCandidate`/`ContentJob` 契约，并把 `finch weekly` 从确定性指标表替换为定性 LLM 复盘（指标计算保留作为输入）。

**Tech Stack:** Python 3.12+、Pydantic 2、SQLModel/SQLite、typer、codex exec（结构化子进程）、pytest、ruff。

**Spec:** `docs/superpowers/specs/2026-09-08-skill-restructuring-design.md`

## Global Constraints

- Python 3.12+；Pydantic 2 模型（`StrEnum`/`Literal`/`Field`）。
- SQLModel records 存 `payload_json` + 用 `session.merge` 幂等 upsert。
- 领域服务确定性、单线程；LLM 输出经 Pydantic 校验，`total`/指标只由代码算。
- 不自动发布；`gh`/`opencli` 只读，write 命令在拒绝名单。
- 子进程：args 数组、每调用超时、JSON 输出经 Pydantic 校验。
- 外部帖 ≠ 个人证据：只有验证过的 `ConversationEvidence` 经 `promote_to_personal` 提升。
- Ruff 选 `E,F,I,B,UP`；line-length 100；alembic 迁移脚本不 lint；`uv run mypy src` 通过。
- 测试命令：`uv run pytest tests/unit/test_<module>.py -k <name>`；提交前跑 `uv run ruff check .` 与 `uv run mypy src`。

---

## File Structure

- `skills/_shared/*.md` — 重组为 5 个共享策略文件（Task 1）。
- `skills/{idea-discovery,conversation-scout,expression-practice,voice-profile,weekly-reflection}/` — 新建/改造的 skill 目录（Tasks 1,3,4,5,6,7）。
- `skills/idea-to-draft/SKILL.md` — 降为 Assist 模式（Task 1）。
- `src/finch/ideas/models.py` — `IdeaCandidate`/`SourceRef` 契约扩展（Task 2）。
- `src/finch/content/jobs.py` — `ContentJob` 扩展 + origin 枚举（Task 2）。
- `src/finch/ideas/service.py` — `IdeaService.create_candidate` 映射新字段（Task 2）。
- `src/finch/ideas/commit_service.py` — generator.skill 改名 `idea-discovery`（Task 3）。
- `src/finch/ideas/fragment_service.py` — 新增：user/conversation/opportunity 来源结构化（Tasks 3,4）。
- `src/finch/ideas/opportunity.py` — 新增：`Opportunity` 模型 + `OpportunityService`（Task 4）。
- `src/finch/storage/repositories.py` — 新增 `OpportunityRepository`、`PracticeSessionRepository`、`ConversationEvidenceRepository.get`（Tasks 3,4,6）。
- `src/finch/practice/{models,service}.py` — 新增 expression-practice 领域服务（Task 6）。
- `src/finch/learn/reflection.py` — 新增定性周复盘服务（Task 7）。
- `src/finch/learn/weekly.py` — 移除确定性 narrative，保留指标函数（Task 7）。
- `src/finch/cli.py` — 新增 `finch ideas create`、`finch scout`、`finch practice`；改 `finch weekly`；删 `finch ideas search`（Tasks 3,4,6,7）。

---

## Task 1: `_shared` 重组 + `idea-to-draft` 降 Assist（纯 md）

**Files:**
- Create: `skills/_shared/expression-contract.md`, `skills/_shared/publication-safety.md`
- Modify: `skills/idea-to-draft/SKILL.md`
- Delete: `skills/_shared/quality-policy.md`, `skills/_shared/voice-guide.md`

**Interfaces:**
- Consumes: 无（纯文档）。
- Produces: `_shared/` 5 文件集合，后续各 SKILL.md 引用。

- [ ] **Step 1: 创建 `expression-contract.md`**

```bash
mkdir -p skills/_shared
```

Write `skills/_shared/expression-contract.md`:

```markdown
# 表达契约（Expression Contract）

expression-practice 与 idea-to-draft 共用的表达边界。核心：表达训练不代写；代写不冒充训练。

## 默认不自动 rewrite

- 练习中，用户先表达，Skill 只诊断 + 追问，不先给完整范文。
- 一次只追问一个关键问题；不一次性抛一串问题。
- 只有用户明确说「直接帮我写」才转 idea-to-draft（Assist 模式）。

## 有限 rewrite

- 单稿重写上限 `max_rewrite_rounds`（见 finch.yaml `quality_gates`）；超过即停。
- revise 只改表达，不改已确认立场；用户明确指示才重写。

## 保存用户原文

- expression-practice 保存 initial_attempt 与每次 revision，最终版与 lesson 一并落库。
- 不覆盖、不丢弃用户原文；版本可追溯。
```

- [ ] **Step 2: 创建 `publication-safety.md`**

Write `skills/_shared/publication-safety.md`:

```markdown
# 发布安全（Publication Safety）

## 不自动发布

- 候选草稿在用户「采用」前绝不视为已发布或已确认立场。
- 发布只能由用户在 Finch 外部手动完成；`gh`/`opencli` 适配器只读，写命令在拒绝名单。

## 分数由代码算

- 加权/总分只由代码计算（`weighted_total` 是唯一出处）；LLM 输出不携带 `total`。
- Evidence/Safety 是 hard-fail，命中即停，不被平均分掩盖。

## 证据优先

- 对外主张必须能回溯到证据；没有 Evidence Card 不生成内容。
- 推断（inferred）显式标注；unknown 不写；禁止把推断写成第一人称亲历。
```

- [ ] **Step 3: 删除被吸收的共享文件**

```bash
git rm skills/_shared/quality-policy.md skills/_shared/voice-guide.md
```

- [ ] **Step 4: 重写 `idea-to-draft/SKILL.md` 为 Assist 模式**

覆盖写 `skills/idea-to-draft/SKILL.md`（完整内容）：

```markdown
---
name: idea-to-draft
description: >
  把已确认的 Idea（ContentJob）写成一篇中文原创草稿（Draft），仅作 Assist 模式（代写）。
  用于「我没时间练习 / 已经想清楚，直接帮我写」类请求；只依据 job 语境（读者问题 / 作者
  立场 / 核心主张 / 边界）写正文，不搜索新来源、不绑定证据卡；草稿过 Critic（6 检查器，
  Safety 硬门禁）+ 有限 rewrite 后落库为 Draft + CriticReport，进入人工审核。
---

# idea-to-draft（Assist 模式）

把已确认的 Idea 写成草稿。职责单一：从 `ContentJob`（`status=confirmed`）生成一篇**进入人工审核**的 `Draft`，并落库一轮 Critic 报告。未确认的 idea 拒绝生成（`needs_confirmation`）。

这是**代写模式**，不是表达训练。如果用户想通过表达提升能力，先走 `expression-practice`。

## 职责

- 输入：一个已确认的 `ContentJob`。
- 输出：一篇 `Draft`（`kind` 随 job 的 `recommended_format`，`claims` 恒为空）+ 一轮 `CriticReport`。
- 未确认 / 不存在的 idea → 报错，不生成。
- 草稿只依据 job 语境写，**不搜索新来源、不绑定证据卡**。

## 执行

用 `finch drafts create <idea-id> [--json]`。

## 边界

- 不冒充表达训练（→ `expression-practice`）。
- 不改变已确认立场（claim/decision/tradeoff 只原样表达，见 `_shared/author-position.md`）。
- 不从外部信号补造个人经历（见 `_shared/evidence-policy.md`）。
- 不自动发布（见 `_shared/publication-safety.md`）。
- 不把 AI 生成文本直接加入 VoiceProfile（→ `voice-profile`）。

## 参考

- `references/draft-patterns.md` — 边界 / 口吻 / scope / 立场的写作判据。
- `_shared/author-position.md` — proposed vs confirmed，不改变立场。
- `_shared/evidence-policy.md` — 证据优先、推断不写成亲历。
- `_shared/expression-contract.md` — 有限 rewrite、默认不自动 rewrite。
- `_shared/publication-safety.md` — 不自动发布、分数由代码算。
```

- [ ] **Step 5: 提交**

```bash
git add skills/_shared skills/idea-to-draft/SKILL.md
git commit -m "docs(skills): reorganize _shared policies; demote idea-to-draft to assist mode"
```

---

## Task 2: `IdeaCandidate` / `ContentJob` 契约扩展

**Files:**
- Modify: `src/finch/ideas/models.py`, `src/finch/content/jobs.py`, `src/finch/ideas/service.py`
- Test: `tests/unit/test_idea_contract.py`

**Interfaces:**
- Consumes: 现有 `IdeaCandidate`、`ContentJob`、`IdeaService.create_candidate`。
- Produces: `IdeaCandidate.observation`/`intent`/`open_question`、`ContentJob.observation`/`intent`/`open_question`、`SourceRef.type` 含 `"conversation"`、`origin` 含 `"conversation"`；`create_candidate` 把三者映射到 `ContentJob`。

- [ ] **Step 1: 写失败测试**

Create `tests/unit/test_idea_contract.py`:

```python
"""契约扩展：IdeaCandidate / ContentJob 新增 observation/intent/open_question。"""

from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus
from finch.content.models import DraftKind
from finch.ideas.models import (
    IdeaBoundaries,
    IdeaCandidate,
    IdeaGenerator,
    SourceRef,
)
from finch.ideas.service import IdeaService


def _candidate() -> IdeaCandidate:
    return IdeaCandidate(
        id="idea_x",
        origin="user",
        core_point="中心主张",
        observation="实际观察",
        reader_problem="读者问题",
        why_worth_saying="为什么值得说",
        intent="exploration",
        open_question="尚未解决什么",
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        source_refs=[],
        boundaries=IdeaBoundaries(known=[], inferred=[], unknown=[]),
        recommended_format="original",
        generator=IdeaGenerator(skill="idea-discovery", version="1.0.0"),
    )


class _Jobs:
    def __init__(self):
        self.saved = None

    def find_by_generation_key(self, key):
        return None

    def upsert_job(self, job):
        self.saved = job


def test_new_fields_default_to_sane_values():
    idea = IdeaCandidate(
        id="idea_y", origin="commit", core_point="cp", reader_problem="rp",
        why_worth_saying="w", author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        source_refs=[], boundaries=IdeaBoundaries(), recommended_format="original",
        generator=IdeaGenerator(skill="idea-discovery", version="1.0.0"),
    )
    assert idea.observation == ""
    assert idea.intent == "stance"
    assert idea.open_question == ""


def test_source_ref_type_allows_conversation():
    ref = SourceRef(type="conversation", ref="ev_1", summary="s")
    assert ref.type == "conversation"


def test_create_candidate_maps_new_fields():
    jobs = _Jobs()
    service = IdeaService(jobs)  # type: ignore[arg-type]
    job = service.create_candidate(_candidate())
    assert job.observation == "实际观察"
    assert job.intent == "exploration"
    assert job.open_question == "尚未解决什么"
    assert job.origin == "user"
    assert job.status == ContentJobStatus.PROPOSED


def test_content_job_new_fields_default():
    job = ContentJob(
        id="j", source_card_ids=[], reader_problem="rp",
        recommended_format=DraftKind.ORIGINAL, status=ContentJobStatus.PROPOSED,
    )
    assert job.observation == ""
    assert job.intent == "stance"
    assert job.open_question == ""
    assert job.origin is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_idea_contract.py -v`
Expected: FAIL（`TypeError: IdeaCandidate.__init__() got an unexpected keyword argument 'observation'` 或类似）。

- [ ] **Step 3: 扩展 `IdeaCandidate` 与 `SourceRef`**

Edit `src/finch/ideas/models.py` — 改 `SourceRef.type` 与 `IdeaCandidate`：

```python
class SourceRef(BaseModel):
    """来源引用：类型 + 引用标识 + 一句话摘要（保证可追溯）。"""

    type: Literal["commit", "pr", "issue", "test", "post", "paper", "conversation"]
    ref: str
    summary: str
```

```python
class IdeaCandidate(BaseModel):
    """Idea 候选：Skill 层产出的统一输入契约。"""

    id: str
    origin: Literal["commit", "search", "user", "conversation"]
    core_point: str
    observation: str = ""
    reader_problem: str
    why_worth_saying: str
    intent: Literal["stance", "exploration"] = "stance"
    open_question: str = ""
    author_position: AuthorPosition
    source_refs: list[SourceRef]
    boundaries: IdeaBoundaries
    recommended_format: Literal["original", "reply", "thread"]
    generator: IdeaGenerator
```

- [ ] **Step 4: 扩展 `ContentJob`**

Edit `src/finch/content/jobs.py` — 改 `origin` 与新增三字段：

```python
    # ---- idea 候选流字段（Skill 架构 Step 1）----
    origin: Literal["commit", "search", "user", "conversation"] | None = None
    observation: str = ""
    intent: Literal["stance", "exploration"] = "stance"
    open_question: str = ""
    generation_key: str | None = None
    generator_name: str | None = None
    generator_version: str | None = None
    content_fingerprint: str | None = None
```

（`observation`/`intent`/`open_question` 放在 `origin` 之后、`generation_key` 之前，保持 idea 候选流字段分组。）

- [ ] **Step 5: `create_candidate` 映射新字段**

Edit `src/finch/ideas/service.py` — 在 `job = ContentJob(...)` 构造中加入三个新字段映射（`origin=idea.origin` 之后）：

```python
        job = ContentJob(
            id=f"idea_{fingerprint[:8]}",
            source_card_ids=[],
            reader_problem=idea.reader_problem,
            author_position=idea.author_position,
            recommended_format=_FORMAT_MAP[idea.recommended_format],
            status=ContentJobStatus.PROPOSED,
            core_message=idea.core_point,
            why_now=idea.why_worth_saying,
            origin=idea.origin,
            observation=idea.observation,
            intent=idea.intent,
            open_question=idea.open_question,
            generation_key=key,
            generator_name=idea.generator.skill,
            generator_version=idea.generator.version,
            content_fingerprint=fingerprint,
        )
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_idea_contract.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 7: 全量回归 + 提交**

```bash
uv run ruff check . && uv run mypy src
git add src/finch/ideas/models.py src/finch/content/jobs.py src/finch/ideas/service.py tests/unit/test_idea_contract.py
git commit -m "feat(ideas): extend IdeaCandidate/ContentJob with observation/intent/open_question"
```

---

## Task 3: `idea-discovery` 合并 + `finch ideas create`

**Files:**
- Create: `src/finch/ideas/fragment_service.py`, `skills/idea-discovery/SKILL.md`, `skills/idea-discovery/references/{commit-signals,fragment-signals,conversation-signals}.md`
- Modify: `src/finch/ideas/commit_service.py`, `src/finch/storage/repositories.py`, `src/finch/cli.py`
- Move: `skills/commit-to-idea` → `skills/idea-discovery`（含 evals）
- Test: `tests/unit/test_fragment_service.py`, `tests/unit/test_cli_ideas.py`（追加）

**Interfaces:**
- Consumes: `IdeaCandidate`（Task 2 扩展后）、`ConversationEvidence`、`StructuredInferenceRunner`。
- Produces: `FragmentService.from_text(text) -> IdeaCandidate`、`FragmentService.from_conversation(evidence) -> IdeaCandidate`、`ConversationEvidenceRepository.get(evidence_id) -> ConversationEvidence | None`、CLI `finch ideas create --text/--conversation`。

- [ ] **Step 1: `ConversationEvidenceRepository.get`**

Edit `src/finch/storage/repositories.py` — 在 `ConversationEvidenceRepository` 类内、`list_unverified` 之前加入：

```python
    def get(self, evidence_id: str) -> ConversationEvidence | None:
        """按 id 获取证据，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(ConversationEvidenceRecord, evidence_id)
            if record is None:
                return None
            return ConversationEvidence.model_validate_json(record.payload_json)
```

- [ ] **Step 2: 新建 `fragment_service.py`**

Create `src/finch/ideas/fragment_service.py`:

```python
"""FragmentService：把用户片段 / 已验证 ConversationEvidence 结构化为一 IdeaCandidate。

（idea-discovery 的 user / conversation 来源；opportunity 来源在 Task 4 加入。）

本模块是纯领域逻辑：不访问 DB、不落库。``IdeaService.create_candidate`` 负责后续幂等落库。
"""

import hashlib
from typing import Literal, cast

from pydantic import BaseModel, Field

from finch.content.jobs import AuthorPosition
from finch.engagement.models import ConversationEvidence
from finch.ideas.models import IdeaBoundaries, IdeaCandidate, IdeaGenerator, SourceRef
from finch.llm.base import StructuredInferenceRunner

_GENERATOR_SKILL = "idea-discovery"
_GENERATOR_VERSION = "1.0.0"

_IDEA_DRAFT_PROMPT = """\
You turn a raw fragment (or a verified conversation signal) into a single publishable
engineering Idea candidate. Return JSON matching the schema.

Rules:
- core_point is ONE central claim. Split multiple claims into multiple candidates.
- observation states what was actually observed; do not invent personal experience.
- intent is "stance" (you have a clear position) or "exploration" (open-ended, no
  complete conclusion yet). Choose "exploration" when there is no complete conclusion.
- author_position carries claim / decision / tradeoff; mark nothing as confirmed.
- boundaries: known (verified), inferred (with hedging), unknown (do not assert).
- recommended_format: original | reply | thread.

## Source type

{source_type}

## Source text

{source_text}
"""


def _to_candidate(
    out: "IdeaDraftOutput",
    *,
    origin: Literal["commit", "search", "user", "conversation"],
    source_refs: list[SourceRef],
) -> IdeaCandidate:
    core = out.core_point
    return IdeaCandidate(
        id=f"idea_{hashlib.sha256(core.encode('utf-8')).hexdigest()[:8]}",
        origin=origin,
        core_point=core,
        observation=out.observation,
        reader_problem=out.reader_problem,
        why_worth_saying=out.why_worth_saying,
        intent=out.intent,
        open_question=out.open_question,
        author_position=out.author_position,
        source_refs=source_refs,
        boundaries=out.boundaries,
        recommended_format=out.recommended_format,
        generator=IdeaGenerator(skill=_GENERATOR_SKILL, version=_GENERATOR_VERSION),
    )


class IdeaDraftOutput(BaseModel):
    """LLM 结构化输出：IdeaCandidate 中非 id/origin/source_refs/generator 的部分。"""

    core_point: str
    observation: str = ""
    reader_problem: str
    why_worth_saying: str
    intent: Literal["stance", "exploration"] = "stance"
    open_question: str = ""
    author_position: AuthorPosition
    boundaries: IdeaBoundaries = Field(default_factory=IdeaBoundaries)
    recommended_format: Literal["original", "reply", "thread"] = "original"


class FragmentService:
    """把用户输入 / ConversationEvidence 提炼为 IdeaCandidate（纯领域逻辑）。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def from_text(self, text: str) -> IdeaCandidate:
        """用户片段（一句话/模糊判断）→ IdeaCandidate，origin=user，无外部 source_refs。"""
        out = cast(
            IdeaDraftOutput,
            self.runner.run(
                _IDEA_DRAFT_PROMPT.format(source_type="user fragment", source_text=text),
                IdeaDraftOutput,
            ),
        )
        return _to_candidate(out, origin="user", source_refs=[])

    def from_conversation(self, evidence: ConversationEvidence) -> IdeaCandidate:
        """已验证 ConversationEvidence → IdeaCandidate，origin=conversation。"""
        out = cast(
            IdeaDraftOutput,
            self.runner.run(
                _IDEA_DRAFT_PROMPT.format(
                    source_type=f"conversation evidence ({evidence.kind})",
                    source_text=evidence.statement,
                ),
                IdeaDraftOutput,
            ),
        )
        source_refs = [
            SourceRef(type="conversation", ref=evidence.id, summary=evidence.statement)
        ]
        return _to_candidate(out, origin="conversation", source_refs=source_refs)
```

（`IdeaDraftOutput` 用到的 `Literal`/`BaseModel`/`Field` 已在文件顶部 import；此处无需再 import。）

- [ ] **Step 3: 写失败测试**

Create `tests/unit/test_fragment_service.py`:

```python
"""FragmentService：user / conversation 来源 → IdeaCandidate。"""

from finch.content.jobs import AuthorPosition
from finch.ideas.fragment_service import IdeaDraftOutput, FragmentService
from finch.ideas.models import IdeaBoundaries
from finch.engagement.models import ConversationEvidence


class FakeRunner:
    def __init__(self, ret):
        self.calls = 0
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.ret


def _out() -> IdeaDraftOutput:
    return IdeaDraftOutput(
        core_point="中心主张",
        observation="观察",
        reader_problem="读者问题",
        why_worth_saying="为什么",
        intent="exploration",
        open_question="问题",
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        boundaries=IdeaBoundaries(known=[], inferred=[], unknown=[]),
        recommended_format="original",
    )


def test_from_text_origin_user_no_source_refs():
    svc = FragmentService(FakeRunner(_out()))
    idea = svc.from_text("我最近有个模糊想法")
    assert idea.origin == "user"
    assert idea.source_refs == []
    assert idea.generator.skill == "idea-discovery"
    assert idea.intent == "exploration"


def test_from_conversation_origin_and_source_ref():
    evidence = ConversationEvidence(
        id="ev_1", interaction_id="i1", post_id="p1", kind="question",
        statement="某个机制到底怎么工作", verified=True,
    )
    svc = FragmentService(FakeRunner(_out()))
    idea = svc.from_conversation(evidence)
    assert idea.origin == "conversation"
    assert idea.source_refs[0].type == "conversation"
    assert idea.source_refs[0].ref == "ev_1"
```

- [ ] **Step 4: 跑测试确认失败 → 通过**

Run: `uv run pytest tests/unit/test_fragment_service.py -v`
Expected: PASS（2 passed）。若 Step 2 代码无误则直接通过。

- [ ] **Step 5: 改 generator.skill 为 `idea-discovery`**

Edit `src/finch/ideas/commit_service.py` — 改常量：

```python
_GENERATOR_SKILL = "idea-discovery"
```

（`search_service.py` 的 `_GENERATOR_SKILL` 在 Task 4 随文件删除，无需改。）

- [ ] **Step 6: 新增 `finch ideas create` CLI**

Edit `src/finch/cli.py` — 在 import 区加入 `FragmentService` 与 `ConversationEvidenceRepository`（后者已在 repositories import 中）。在 `@ideas_app.command("search")` 之前插入命令：

```python
@ideas_app.command("create")
def ideas_create(
    text: str = typer.Option(None, "--text", help="用户输入的一句话/片段"),
    conversation: str = typer.Option(None, "--conversation", help="已验证 ConversationEvidence id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """把用户片段或已验证 ConversationEvidence 结构化为 IdeaCandidate 并落库。"""
    if (text is None) == (conversation is None):
        typer.echo("exactly one of --text / --conversation is required")
        raise typer.Exit(code=1)
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    service = FragmentService(runner)
    if text is not None:
        idea = service.from_text(text)
    else:
        evidence = ConversationEvidenceRepository(store).get(conversation)
        if evidence is None:
            typer.echo(f"conversation evidence not found: {conversation}")
            raise typer.Exit(code=1)
        if not evidence.verified:
            typer.echo(f"conversation evidence not verified: {conversation}")
            raise typer.Exit(code=1)
        idea = service.from_conversation(evidence)
    job = IdeaService(ContentJobRepository(store)).create_candidate(idea)
    if as_json:
        typer.echo(
            json.dumps(
                {"id": job.id, "origin": job.origin, "status": job.status.value},
                ensure_ascii=False, indent=2,
            )
        )
    else:
        typer.echo(f"{job.id}\t{job.status.value}\t{job.core_message}")
```

Add import: 在 `from .ideas.commit_service import CommitService` 之后加 `from .ideas.fragment_service import FragmentService`。

- [ ] **Step 7: 追加 CLI 测试到 `tests/unit/test_cli_ideas.py`**

在文件末尾追加（`_candidate()` / `_settings()` / `CliRunner` 已在该文件顶部定义，直接复用）：

```python
class _FakeFragmentService:
    def __init__(self, runner):
        self.runner = runner

    def from_text(self, text):
        return _candidate()


def _patch_create_cli(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "FragmentService", _FakeFragmentService)


def test_ideas_create_requires_exactly_one_source(monkeypatch, tmp_path):
    settings = _settings(tmp_path, [])
    _patch_create_cli(monkeypatch, settings)
    r = CliRunner().invoke(app, ["ideas", "create"])
    assert r.exit_code == 1
    assert "exactly one of" in r.output


def test_ideas_create_text_persists(monkeypatch, tmp_path):
    settings = _settings(tmp_path, [])
    store = Store(settings.paths.db_path)
    store.init()
    _patch_create_cli(monkeypatch, settings)
    r = CliRunner().invoke(app, ["ideas", "create", "--text", "hello", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["status"] == "proposed"
    # _FakeFragmentService 复用 _candidate()（origin="commit"），仅验证落库链路。
    assert ContentJobRepository(store).list_jobs()[0].core_message == CORE_POINT
```

- [ ] **Step 8: 重命名 skill 目录并写 SKILL.md**

```bash
git mv skills/commit-to-idea skills/idea-discovery
git mv skills/idea-discovery/references/commit-analysis.md skills/idea-discovery/references/commit-signals.md
```

Write `skills/idea-discovery/SKILL.md`:

```markdown
---
name: idea-discovery
description: >
  把个人证据（Commit/PR/测试）、零散思考（用户片段）或真实交流（已验证 ConversationEvidence）
  提炼成一个值得继续发展的 Idea（IdeaCandidate）。三种来源只是输入不同，判断同一件事：
  这里有没有「读者值得知道」的真实工程决策或问题。用于「把我最近的提交 / 这个片段 /
  这次交流变成一个可写的想法」类请求。
---

# idea-discovery

把个人证据、零散思考或真实交流提炼成一个值得继续发展的 Idea。三种来源共用一份判断：
有「读者值得知道的真实决策/问题」就产出**一个** `IdeaCandidate`；机械变化、新闻、纯情绪、
无明确结论的噪音 → 空。

本 Skill 只调用 Finch CLI（`finch ideas commit` / `finch ideas create`），不复制业务逻辑、
不直接改数据库、不猜测状态。

## 三种来源

- **commit 来源**：`finch ideas commit [--repo R] [--since 7d]`，见 `references/commit-signals.md`。
- **fragment 来源**：`finch ideas create --text "..."`，见 `references/fragment-signals.md`。
- **conversation 来源**：`finch ideas create --conversation <evidence-id>`，见 `references/conversation-signals.md`。

## 产出契约（IdeaCandidate）

见 `_shared/idea-contract.md`，要点：

- `core_point` 单一中心主张；`observation` 是实际观察到的；`intent` 为 `stance`（有立场）
  或 `exploration`（无完整结论也允许输出）。
- `source_refs` 可追溯；`author_position.status` 一律 `proposed`。
- `boundaries.known/inferred/unknown` 传递到 Draft 校验。

## 边界

- 外部帖子不能直接变成个人观点（见 `_shared/evidence-policy.md`）。
- 不生成草稿（→ `idea-to-draft` / `expression-practice`）。
- 不负责搜索交流对象（→ `conversation-scout`）。

## 参考

- `references/commit-signals.md` — 机械变化 vs 真实决策的判据。
- `references/fragment-signals.md` — 零散思考是否够格成为 Idea 的判据。
- `references/conversation-signals.md` — 交流信号中性化提炼的判据。
- `_shared/idea-contract.md` — IdeaCandidate 契约。
- `_shared/evidence-policy.md` — 证据优先、外部帖 ≠ 个人证据。
```

Write `skills/idea-discovery/references/fragment-signals.md`:

```markdown
# 零散思考 → Idea 的判据（fragment 来源）

用户输入一句话、片段或模糊判断时，先问：

- 有没有可观察的具体事实/现象，而不只是感想？
- 同行会不会也遇到同样的问题（reader_problem）？
- 用户有没有一个判断/取舍（decision/tradeoff）可表达？

无结论、纯感想、无法具体化 → 输出 `exploration`（intent=exploration，open_question 记下未解决点）。
```

Write `skills/idea-discovery/references/conversation-signals.md`:

```markdown
# 交流信号 → Idea 的判据（conversation 来源）

输入是已验证的 ConversationEvidence（verified=True）。提炼时：

- 外部作者的经历不写成用户经历；进入 core_point/reader_problem/boundaries 的表述中性化。
- 原文只保留在 source_refs.summary。
- 未经验证的证据拒绝提炼（`finch ideas create --conversation` 会拒绝 verified=False）。
```

- [ ] **Step 9: 回归 + 提交**

把 `tests/unit/test_commit_service.py:96` 的断言从 `IdeaGenerator(skill="commit-to-idea", ...)` 改为 `IdeaGenerator(skill="idea-discovery", version="1.0.0")`（`_GENERATOR_SKILL` 已改名；其余测试里的 `"commit-to-idea"` 是任意字符串 fixture，不受影响）。

```bash
uv run ruff check . && uv run mypy src
uv run pytest tests/unit/test_fragment_service.py tests/unit/test_cli_ideas.py tests/unit/test_commit_service.py -q
git add -A
git commit -m "feat(ideas): merge commit-to-idea into idea-discovery; add finch ideas create"
```

---

## Task 4: `conversation-scout`（Opportunity 模型 + Service + `finch scout`）

**Files:**
- Create: `src/finch/ideas/opportunity.py`, `skills/conversation-scout/SKILL.md`, `skills/conversation-scout/references/{opportunity-signals,audience-profile}.md`
- Modify: `src/finch/storage/repositories.py`（`OpportunityRecord` + `OpportunityRepository`）, `src/finch/cli.py`, `src/finch/ideas/fragment_service.py`（`from_opportunity`）
- Move: `skills/search-to-idea` → `skills/conversation-scout`（含 evals）
- Delete: `src/finch/ideas/search_service.py`, `tests/unit/test_search_service.py`
- Test: `tests/unit/test_opportunity.py`, `tests/unit/test_cli_scout.py`

**Interfaces:**
- Consumes: `Tweet`、`StructuredInferenceRunner`、Task 2 的 `SourceRef`/`IdeaCandidate`。
- Produces: `Opportunity`、`SourcePostRef`、`OpportunityDraft`、`OpportunityService.to_opportunities(posts, topic) -> list[Opportunity]`、`OpportunityRepository.{upsert,get,list_all}`、`FragmentService.from_opportunity(opportunity) -> IdeaCandidate`、CLI `finch scout search/list/show` 与 `finch ideas create --opportunity`。

- [ ] **Step 1: 新建 `opportunity.py`**

Create `src/finch/ideas/opportunity.py`:

```python
"""conversation-scout 领域服务：从公开讨论提炼「交流机会」（Opportunity），非 IdeaCandidate。

与已删除的 SearchService 不同：本服务产出的是交流机会（由用户决定是否转成自己的 Idea），
且机会提炼本身是开放性判断，故注入 LLM runner；确定性去重 + 噪音预过滤仍走代码。

外部帖子只是信号，不是个人证据；Opportunity 不是 ContentJob、不是证据，是独立中转记录。
"""

import hashlib
from typing import Literal, cast

from pydantic import BaseModel, Field

from finch.llm.base import StructuredInferenceRunner
from finch.twitter.models import Tweet

# 机会信号（与旧 search_service 相同的轻量启发式，用作 LLM 前的确定性预过滤）。
_OPPORTUNITY_SIGNALS = frozenset({
    "doesn't work", "does not work", "not working", "broken", "broke", "breaks",
    "bug", "fails", "failed", "failing", "failure", "error", "crash", "crashing",
    "problem", "issue", "struggling", "struggle", "painful", "frustrating",
    "hard to", "too hard", "can't", "cannot", "won't", "too slow",
    "workaround", "hack", "missing", "lacking", "gap", "no one", "nobody",
    "unexpected", "in practice", "turns out",
    "不行", "坏了", "崩溃", "问题", "坑", "踩坑", "反例", "缺口", "翻车",
})

_NOISE_SIGNALS = frozenset({
    "announces", "announced", "launches", "launched", "release", "released",
    "shipping", "shipped", "new version",
    "raised", "raises", "funding", "series a", "series b", "seed", "valuation",
    "acquires", "acquired", "ipo", "investment",
    "amazing", "awesome", "incredible", "wow",
    "新闻", "融资", "发布", "上线", "估值", "收购",
})


def _signal_text(post: Tweet) -> str:
    return " ".join(post.text.split())


def _classify(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in _OPPORTUNITY_SIGNALS):
        return "opportunity"
    if any(k in lowered for k in _NOISE_SIGNALS):
        return "noise"
    return "other"


_OPPORTUNITY_PROMPT = """\
You scout public technical discussion for genuine conversation opportunities.
Given a post, decide whether it exposes a real problem, disagreement, failure case,
or unresolved mechanism worth engaging — not merely a launch, funding, or sentiment.

Rules:
- Do not treat the author's first-person experience as your own or as a fact.
- Do not recommend merely because the post is popular.
- Prefer real problems, disagreements, failure cases, and unresolved mechanisms.

## Topic
{topic}

## Post
{post}

Respond with JSON matching the schema:
- is_opportunity: true only if genuinely worth engaging.
- shared_tension: the tension both sides share.
- why_relevant: why it matters to practitioners now.
- response_angles: 1-3 of share_experience / ask_mechanism / challenge_assumption.
- knowledge_gap: what is unresolved or under-explained.
- relationship_value: what a useful exchange would build.
"""


class OpportunityDraft(BaseModel):
    """LLM 结构化输出：机会判断 + 交流机会字段。"""

    is_opportunity: bool
    shared_tension: str = ""
    why_relevant: str = ""
    response_angles: list[
        Literal["share_experience", "ask_mechanism", "challenge_assumption"]
    ] = Field(default_factory=list)
    knowledge_gap: str = ""
    relationship_value: str = ""


class SourcePostRef(BaseModel):
    """机会的来源帖子引用（外部信号，可追溯）。"""

    url: str
    author: str
    text: str


class Opportunity(BaseModel):
    """交流机会：值得交流的人、问题和切入口。"""

    id: str
    source_post: SourcePostRef
    shared_tension: str
    why_relevant: str
    response_angles: list[str]
    knowledge_gap: str
    relationship_value: str


class OpportunityService:
    """把一组公开帖子提炼为 Opportunity 列表（LLM 精判 + 确定性预过滤）。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def to_opportunities(self, posts: list[Tweet], *, topic: str) -> list[Opportunity]:
        opportunities: list[Opportunity] = []
        seen: set[str] = set()
        for post in posts:
            signal = _signal_text(post)
            if not signal:
                continue
            key = signal.lower()
            if key in seen:
                continue
            seen.add(key)
            if _classify(signal) != "opportunity":
                continue
            out = cast(
                OpportunityDraft,
                self.runner.run(
                    _OPPORTUNITY_PROMPT.format(topic=topic, post=signal),
                    OpportunityDraft,
                ),
            )
            if not out.is_opportunity:
                continue
            opportunities.append(
                Opportunity(
                    id=f"opp_{hashlib.sha256((post.url + signal).encode('utf-8')).hexdigest()[:8]}",
                    source_post=SourcePostRef(url=post.url, author=post.author, text=signal),
                    shared_tension=out.shared_tension,
                    why_relevant=out.why_relevant,
                    response_angles=out.response_angles,
                    knowledge_gap=out.knowledge_gap,
                    relationship_value=out.relationship_value,
                )
            )
        return opportunities
```

- [ ] **Step 2: 写失败测试**

Create `tests/unit/test_opportunity.py`:

```python
"""OpportunityService：帖子 → Opportunity（LLM 精判 + 确定性预过滤）。"""

from finch.ideas.opportunity import OpportunityDraft, OpportunityService
from finch.twitter.models import Tweet

POST_URL = "https://x.com/acme/status/1"


class FakeRunner:
    def __init__(self, ret):
        self.calls = 0
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.ret


def _post(text, id="1"):
    return Tweet(id=id, author="acme", text=text, url=POST_URL)


def _draft(is_opportunity=True):
    return OpportunityDraft(
        is_opportunity=is_opportunity,
        shared_tension="tension",
        why_relevant="relevant",
        response_angles=["ask_mechanism"],
        knowledge_gap="gap",
        relationship_value="value",
    )


def test_opportunity_yields_one():
    svc = OpportunityService(FakeRunner(_draft()))
    opps = svc.to_opportunities([_post("The scheduler has a nasty race condition bug.")], topic="sched")
    assert len(opps) == 1
    assert opps[0].source_post.url == POST_URL
    assert opps[0].id.startswith("opp_")


def test_noise_skipped_without_llm():
    runner = FakeRunner(_draft())
    svc = OpportunityService(runner)
    opps = svc.to_opportunities([_post("Acme launches v2 of their framework.")], topic="agents")
    assert opps == []
    assert runner.calls == 0


def test_llm_rejects_skips():
    svc = OpportunityService(FakeRunner(_draft(is_opportunity=False)))
    opps = svc.to_opportunities([_post("The scheduler has a nasty race condition bug.")], topic="sched")
    assert opps == []


def test_dedup():
    svc = OpportunityService(FakeRunner(_draft()))
    posts = [_post("Our agent keeps failing on long context.", id="1"),
             _post("Our agent keeps failing on long context.", id="2")]
    opps = svc.to_opportunities(posts, topic="agents")
    assert len(opps) == 1
```

- [ ] **Step 3: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_opportunity.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 4: `OpportunityRepository`**

Edit `src/finch/storage/repositories.py` — 在 `ConversationEvidenceRecord` 之前插入 record + repository：

```python
class OpportunityRecord(SQLModel, table=True):
    """Opportunity（交流机会）持久化模型。"""

    id: str = Field(primary_key=True)  # = opp_<sha256[:8]>
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OpportunityRepository:
    """交流机会仓储：按 id merge 幂等 upsert。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def upsert(self, opportunity: Opportunity) -> None:
        record = OpportunityRecord(
            id=opportunity.id,
            payload_json=opportunity.model_dump_json(),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def get(self, opportunity_id: str) -> Opportunity | None:
        with Session(self.store.engine) as session:
            record = session.get(OpportunityRecord, opportunity_id)
            if record is None:
                return None
            return Opportunity.model_validate_json(record.payload_json)

    def list_all(self) -> list[Opportunity]:
        with Session(self.store.engine) as session:
            records = list(session.exec(select(OpportunityRecord).order_by(col(OpportunityRecord.id))))
            return [Opportunity.model_validate_json(r.payload_json) for r in records]
```

Add import: 在 `from finch.engagement.models import (...)` 之后加 `from finch.ideas.opportunity import Opportunity`。

- [ ] **Step 5: `finch scout` CLI + 删 `ideas search`**

Edit `src/finch/cli.py`：

1. 加 import：`from .ideas.opportunity import OpportunityService`；在 repositories import 块加 `OpportunityRepository`。
2. 删除 `from .ideas.search_service import SearchService` import。
3. 删除整个 `@ideas_app.command("search")` 函数（`ideas_search`，约 40 行）。
4. 在 `app.add_typer(engagement_app, name="engagement")` 之后加：

```python
scout_app = typer.Typer(help="从公开讨论侦察交流机会（conversation-scout）")
app.add_typer(scout_app, name="scout")
```

5. 在文件末尾（`engagement_metrics` 之后、`if __name__` 之前）加三个命令：

```python
@scout_app.command("search")
def scout_search(
    topic: str = typer.Option(None, "--topic", help="搜索话题（默认 settings.twitter.queries[0]）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """从公开讨论搜索交流机会并落库（不生成 Idea，不落 ContentJob）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    builder = QueryBuilder(settings.twitter.queries, per_query_limit=settings.twitter.per_query_limit)
    opencli = OpenCliClient()
    if topic is None:
        if not builder.configs:
            typer.echo("--topic is required (no twitter queries configured)")
            raise typer.Exit(code=1)
        topic = builder.configs[0].text
    tweets = opencli.search(topic, product="top", limit=builder.per_query_limit)
    posts = normalize_tweets(tweets)
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    opps = OpportunityService(runner).to_opportunities(posts, topic=topic)
    repo = OpportunityRepository(store)
    for opp in opps:
        repo.upsert(opp)
    if as_json:
        typer.echo(json.dumps([o.model_dump(mode="json") for o in opps], ensure_ascii=False, indent=2))
    else:
        for opp in opps:
            typer.echo(f"{opp.id}\t{opp.source_post.url}\t{opp.shared_tension}")


@scout_app.command("list")
def scout_list(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """列出全部交流机会。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    opps = OpportunityRepository(store).list_all()
    if as_json:
        typer.echo(json.dumps([o.model_dump(mode="json") for o in opps], ensure_ascii=False, indent=2))
        return
    if not opps:
        typer.echo("no opportunities")
        return
    for opp in opps:
        typer.echo(f"{opp.id}\t{opp.source_post.url}\t{opp.shared_tension}")


@scout_app.command("show")
def scout_show(
    opportunity_id: str = typer.Argument(..., help="opportunity id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示单个交流机会。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    opp = OpportunityRepository(store).get(opportunity_id)
    if opp is None:
        typer.echo(f"opportunity not found: {opportunity_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(opp.model_dump_json(indent=2))
    else:
        typer.echo(f"{opp.id}\t{opp.source_post.url}")
        typer.echo(f"shared_tension: {opp.shared_tension}")
        typer.echo(f"why_relevant: {opp.why_relevant}")
        typer.echo(f"response_angles: {', '.join(opp.response_angles)}")
        typer.echo(f"knowledge_gap: {opp.knowledge_gap}")
        typer.echo(f"relationship_value: {opp.relationship_value}")
```

- [ ] **Step 6: `finch ideas create --opportunity`**

Edit `src/finch/ideas/fragment_service.py` — 顶部 import 加 `from finch.ideas.opportunity import Opportunity`；模块级加 `_FROM_OPPORTUNITY_PROMPT`；在**现有** `FragmentService` 类内追加 `from_opportunity` 方法（不要新建类，`__init__`/`from_text`/`from_conversation` 已在 Task 3 写好）：

```python
_FROM_OPPORTUNITY_PROMPT = """\
You turn a scouted conversation opportunity into a single Idea candidate. The opportunity
came from external public discussion, so it must stay external: never write the external
author's experience as the user's own first-person experience.

## Opportunity
{opportunity}

Return JSON matching the same schema as before (core_point / observation / reader_problem /
why_worth_saying / intent / open_question / author_position / boundaries / recommended_format).
"""


class FragmentService:
    def from_opportunity(self, opportunity: Opportunity) -> IdeaCandidate:
        """scout 机会 → IdeaCandidate，origin=search；外部信号强制中性化归入 inferred。"""
        out = cast(
            IdeaDraftOutput,
            self.runner.run(
                _FROM_OPPORTUNITY_PROMPT.format(opportunity=opportunity.model_dump_json()),
                IdeaDraftOutput,
            ),
        )
        neutral = " ".join(opportunity.source_post.text.split())
        # 外部信号未经作者一手验证：强制 known 为空、inferred 承载中性化信号（不信任 LLM 边界）。
        boundaries = IdeaBoundaries(known=[], inferred=[neutral], unknown=[])
        return _to_candidate(
            out, origin="search",
            source_refs=[
                SourceRef(type="post", ref=opportunity.source_post.url, summary=opportunity.source_post.text)
            ],
        ).model_copy(update={"boundaries": boundaries})
```

Add import: `from finch.ideas.opportunity import Opportunity`（顶部）。

Edit `src/finch/cli.py` — `ideas_create` 增加 `--opportunity` 选项与分支：

```python
def ideas_create(
    text: str = typer.Option(None, "--text", help="用户输入的一句话/片段"),
    conversation: str = typer.Option(None, "--conversation", help="已验证 ConversationEvidence id"),
    opportunity: str = typer.Option(None, "--opportunity", help="Opportunity id（由 finch scout 产出）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """把用户片段 / ConversationEvidence / Opportunity 结构化为 IdeaCandidate 并落库。"""
    provided = sum(x is not None for x in (text, conversation, opportunity))
    if provided != 1:
        typer.echo("exactly one of --text / --conversation / --opportunity is required")
        raise typer.Exit(code=1)
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    service = FragmentService(runner)
    if text is not None:
        idea = service.from_text(text)
    elif conversation is not None:
        evidence = ConversationEvidenceRepository(store).get(conversation)
        if evidence is None:
            typer.echo(f"conversation evidence not found: {conversation}")
            raise typer.Exit(code=1)
        if not evidence.verified:
            typer.echo(f"conversation evidence not verified: {conversation}")
            raise typer.Exit(code=1)
        idea = service.from_conversation(evidence)
    else:
        opp = OpportunityRepository(store).get(opportunity)
        if opp is None:
            typer.echo(f"opportunity not found: {opportunity}")
            raise typer.Exit(code=1)
        idea = service.from_opportunity(opp)
    job = IdeaService(ContentJobRepository(store)).create_candidate(idea)
    if as_json:
        typer.echo(json.dumps(
            {"id": job.id, "origin": job.origin, "status": job.status.value},
            ensure_ascii=False, indent=2,
        ))
    else:
        typer.echo(f"{job.id}\t{job.status.value}\t{job.core_message}")
```

（Step 6 的 CLI 分支替换 Task 3 Step 6 中写的 `ideas_create`，以单一来源校验支持三种来源。）

- [ ] **Step 7: 加 `from_opportunity` 测试**

Append to `tests/unit/test_fragment_service.py`:

```python
from finch.ideas.opportunity import Opportunity, SourcePostRef


def test_from_opportunity_external_neutralized():
    opp = Opportunity(
        id="opp_1",
        source_post=SourcePostRef(url="https://x.com/a/status/9", author="a", text="I spent weeks debugging this"),
        shared_tension="t", why_relevant="w", response_angles=["ask_mechanism"],
        knowledge_gap="g", relationship_value="v",
    )
    svc = FragmentService(FakeRunner(_out()))
    idea = svc.from_opportunity(opp)
    assert idea.origin == "search"
    assert idea.boundaries.known == []
    assert idea.boundaries.inferred  # 外部信号强制归入 inferred
    assert idea.source_refs[0].type == "post"
```

- [ ] **Step 8: 重命名 skill 目录 + 写 SKILL.md**

```bash
git mv skills/search-to-idea skills/conversation-scout
git mv skills/search-to-idea/evals skills/conversation-scout/evals 2>/dev/null || true
git mv skills/conversation-scout/evals/cases.yaml skills/conversation-scout/evals/cases.yaml
git mv skills/conversation-scout/references/opportunity-signals.md skills/conversation-scout/references/opportunity-signals.md
```

Write `skills/conversation-scout/SKILL.md`:

```markdown
---
name: conversation-scout
description: >
  从公开讨论（Twitter/X 搜索）中寻找值得交流的人、问题和切入口，产出「交流机会」
  （Opportunity），由用户决定是否转成自己的 Idea。判断一条帖子是否承载真实问题 / 分歧 /
  失败案例 / 未解决机制；新闻 / 融资 / 纯情绪 / 最近已表达的内容跳过。用于「帮我看看这个话题
  里有什么值得回应/交流」类请求。
---

# conversation-scout

从公开讨论中寻找交流机会。职责单一：判断一条帖子是否承载「值得交流的真实问题 / 分歧 /
失败案例 / 未解决机制」，有就产出**一个** `Opportunity`；没有就产出空列表。

本 Skill 只调用 Finch CLI（`finch scout search/list/show`），不复制业务逻辑、不直接改数据库。

## 产出契约（Opportunity）

- `source_post`：帖子引用（url / author / text）。
- `shared_tension` / `why_relevant` / `response_angles` / `knowledge_gap` / `relationship_value`。

## 边界

- 不替用户形成观点（→ `idea-discovery` 才把机会转成 Idea）。
- 不把别人的经历写成用户经历。
- 不直接生成完整回复（→ `expression-practice` / `idea-to-draft`）。
- 不因为帖子热门就推荐；优先真实问题、分歧、失败案例、未解决机制。

## 参考

- `references/opportunity-signals.md` — 真实问题 / 分歧 / 失败案例 vs 新闻 / 融资 / 情绪的判据。
- `references/audience-profile.md` — 什么样的交流对象值得投入。
```

Write `skills/conversation-scout/references/audience-profile.md`:

```markdown
# 值得投入的交流对象

优先：真实提问者（未解决机制）、分歧中的实践者、公开失败案例的作者。
跳过：纯转发、纯情绪、纯推广、与自身实践无关的话题。
```

- [ ] **Step 9: 更新 evals 断言（IdeaCandidate → Opportunity）**

Edit `skills/conversation-scout/evals/cases.yaml` — 把 `expected_output.ideas`/`origin: search`/`core_point` 断言改为 `opportunities`/`source_post.url`；`author_position.status` 断言删除。保留「真实问题产出一个机会 / 新闻融资情绪产出空 / 确定性」的 case 结构，`expected_output` 字段改为：

```yaml
    expected_output:
      opportunities: 1
      source_post_url: https://x.com/acme/status/1
```

- [ ] **Step 10: 删旧文件 + 删 `ideas search` 测试 + 回归 + 提交**

先删除 `tests/unit/test_cli_ideas.py` 中 `# ---- finch ideas search ----` 段到下一个 `# ----` 段之前的全部测试（命令 `finch ideas search` 已删除）。

```bash
git rm src/finch/ideas/search_service.py tests/unit/test_search_service.py
uv run ruff check . && uv run mypy src
uv run pytest tests/unit/test_opportunity.py tests/unit/test_fragment_service.py tests/unit/test_cli_ideas.py -q
git add -A
git commit -m "feat(scout): conversation-scout produces Opportunity; retire search-to-idea IdeaCandidate path"
```

---

## Task 5: `voice-profile` skill 包装（无代码）

**Files:**
- Create: `skills/voice-profile/SKILL.md`, `skills/voice-profile/references/{extraction-rules,sample-format}.md`

**Interfaces:**
- Consumes: 现有 `finch voice` CLI（show/approve-example/reject-example）与 `voice-profile.yaml`。
- Produces: 无代码变更。

- [ ] **Step 1: 写 SKILL.md**

Write `skills/voice-profile/SKILL.md`:

```markdown
---
name: voice-profile
description: >
  初始化和更新个人表达画像（VoiceProfile）。输入历史发布内容、expression-practice 最终版、
  人工修改后的采用版本、明确拒绝的表达及理由，输出 preferred_patterns / avoid_phrases /
  rhythm_rules / approved_examples / rejected_examples，并展示可追溯 diff 后由用户确认写入。
  用于「更新我的声音画像」「把这篇采用稿记进我的风格」「这条为什么不像我」类请求。
---

# voice-profile

初始化和更新个人表达画像。领域代码已存在（`finch voice` + `voice-profile.yaml`），本 Skill
只做画像学习的判断：什么该学、什么该避免、什么证据够格推导一条规则。

## 输入

- 用户历史发布内容（`finch voice approve-example <draft_id>` 已存 approved_examples）。
- expression-practice 中用户写的最终版。
- 人工修改后的采用版本（decision.accept 后 `revised_body or body`）。
- 明确拒绝的表达及理由（`finch voice reject-example <draft_id> --reason "..."`）。

## 输出

`preferred_patterns` / `avoid_phrases` / `rhythm_rules` / `approved_examples` / `rejected_examples`。

## 边界

- 不生成内容。
- 不根据点赞量自动改风格。
- 不从单个样本推导全局规则（见 `references/extraction-rules.md`）。
- 不从未经确认的 AI 草稿学习（只有 decision.accept 后的文本才算采用稿）。
- 更新前展示 diff，用户确认后写入。

## 执行

- 查看画像：`finch voice show`
- 采用样例：`finch voice approve-example <draft_id>`
- 拒绝样例：`finch voice reject-example <draft_id> --reason "..."`

## 参考

- `references/extraction-rules.md` — 单样本不推导全局规则的判据。
- `references/sample-format.md` — 正反样本格式。
```

Write `skills/voice-profile/references/extraction-rules.md`:

```markdown
# 画像提取规则

- 一条规则至少需要 ≥3 个独立样本才能写入 preferred_patterns / avoid_phrases / rhythm_rules。
- 单样本只记为正/反样例（approved_examples / rejected_examples），不推导全局规则。
- 不从点赞量推导风格；点赞是分发信号，不是风格证据。
- 未经确认的 AI 草稿不进入画像（只学 decision.accept 后的文本）。
```

Write `skills/voice-profile/references/sample-format.md`:

```markdown
# 正反样本格式

- approved_example: `{id: draft_id, text: 采用后的最终文本}`。
- rejected_example: `{id: draft_id, reason: 拒绝理由}`。
```

- [ ] **Step 2: 提交**

```bash
git add skills/voice-profile
git commit -m "docs(skills): add voice-profile skill wrapping finch voice"
```

---

## Task 6: `expression-practice`（PracticeSession + Service + `finch practice`）

**Files:**
- Create: `src/finch/practice/__init__.py`, `src/finch/practice/models.py`, `src/finch/practice/service.py`, `skills/expression-practice/SKILL.md`, `skills/expression-practice/references/{diagnosis-rules,session-output}.md`
- Modify: `src/finch/storage/repositories.py`（`PracticeSessionRecord` + `PracticeSessionRepository`）, `src/finch/cli.py`
- Test: `tests/unit/test_practice_service.py`, `tests/unit/test_cli_practice.py`

**Interfaces:**
- Consumes: `StructuredInferenceRunner`。
- Produces: `PracticeSession`、`PracticeDiagnosis`、`PracticeLesson`、`PracticeService.{start,diagnose,save_revision,finish}`、`PracticeSessionRepository.{upsert,get}`、CLI `finch practice start/diagnose/save/finish/show`。

- [ ] **Step 1: 新建 `practice/models.py`**

Create `src/finch/practice/models.py`:

```python
"""expression-practice 领域模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PracticeSession(BaseModel):
    """一次表达练习会话：首稿 + 诊断 + 追问 + 修订 + 最终版 + 经验。"""

    id: str
    idea_id: str | None = None
    opportunity_id: str | None = None
    initial_attempt: str = ""
    diagnosis: str = ""
    questions_asked: list[str] = Field(default_factory=list)
    revisions: list[str] = Field(default_factory=list)
    final_expression: str = ""
    lesson: str = ""
    status: Literal["started", "finished"] = "started"
    created_at: datetime
    updated_at: datetime


class PracticeDiagnosis(BaseModel):
    """LLM 诊断输出：最大问题 + 一个追问。"""

    diagnosis: str
    question: str


class PracticeLesson(BaseModel):
    """LLM 经验总结输出。"""

    lesson: str
```

- [ ] **Step 2: 新建 `practice/service.py`**

Create `src/finch/practice/service.py`:

```python
"""PracticeService：expression-practice 领域服务（skill 对话驱动 + 一次性 CLI 落库）。

状态机：started →（diagnose / save_revision 可多轮）→ finished。每步幂等落库
（``PracticeSessionRepository.upsert``）。诊断与经验总结是 LLM 开放性判断；其余是确定性状态。
"""

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from finch.llm.base import StructuredInferenceRunner
from finch.practice.models import PracticeDiagnosis, PracticeLesson, PracticeSession
from finch.storage.repositories import PracticeSessionRepository

_DIAGNOSE_PROMPT = """\
You are a writing coach. Diagnose the user's latest expression of an idea and identify the
single biggest problem, then ask exactly ONE question to guide the next revision.

Check dimensions: real understanding / own judgment / causality / relevance to the peer /
specificity / sounds like the author / leaves room to respond.

## Idea context
{context}

## Initial attempt
{initial}

## Revisions so far
{revisions}

## Latest expression
{latest}

Respond with JSON matching the schema: diagnosis (the single biggest problem) and
question (exactly one guiding question).
"""

_LESSON_PROMPT = """\
Compare the user's initial attempt with their final expression and write one lesson learned
about their expression, not a list of generic advice.

## Initial attempt
{initial}

## Final expression
{final}

Respond with JSON matching the schema: lesson (one specific lesson).
"""


class PracticeService:
    """驱动一次表达练习会话。"""

    def __init__(
        self,
        sessions: PracticeSessionRepository,
        runner: StructuredInferenceRunner,
    ) -> None:
        self.sessions = sessions
        self.runner = runner

    def start(
        self,
        *,
        idea_id: str | None = None,
        opportunity_id: str | None = None,
        initial_attempt: str,
    ) -> PracticeSession:
        """创建会话，记 initial_attempt。"""
        now = datetime.now(UTC)
        session = PracticeSession(
            id=f"practice_{uuid4().hex[:8]}",
            idea_id=idea_id,
            opportunity_id=opportunity_id,
            initial_attempt=initial_attempt,
            created_at=now,
            updated_at=now,
        )
        self.sessions.upsert(session)
        return session

    def diagnose(self, session_id: str, *, context: str = "") -> PracticeSession:
        """LLM 诊断最大问题 + 追问一个问题；追加到 questions_asked。"""
        session = self._get(session_id)
        out = cast(
            PracticeDiagnosis,
            self.runner.run(
                _DIAGNOSE_PROMPT.format(
                    context=context,
                    initial=session.initial_attempt,
                    revisions="\n---\n".join(session.revisions),
                    latest=session.revisions[-1] if session.revisions else session.initial_attempt,
                ),
                PracticeDiagnosis,
            ),
        )
        session = session.model_copy(
            update={
                "diagnosis": out.diagnosis,
                "questions_asked": [*session.questions_asked, out.question],
                "updated_at": datetime.now(UTC),
            }
        )
        self.sessions.upsert(session)
        return session

    def save_revision(self, session_id: str, revision: str) -> PracticeSession:
        """追加一次修订。"""
        session = self._get(session_id)
        session = session.model_copy(
            update={
                "revisions": [*session.revisions, revision],
                "updated_at": datetime.now(UTC),
            }
        )
        self.sessions.upsert(session)
        return session

    def finish(self, session_id: str, final_expression: str) -> PracticeSession:
        """记最终版 + LLM 生成 lesson，置 finished。"""
        session = self._get(session_id)
        lesson = cast(
            PracticeLesson,
            self.runner.run(
                _LESSON_PROMPT.format(
                    initial=session.initial_attempt, final=final_expression
                ),
                PracticeLesson,
            ),
        )
        session = session.model_copy(
            update={
                "final_expression": final_expression,
                "lesson": lesson.lesson,
                "status": "finished",
                "updated_at": datetime.now(UTC),
            }
        )
        self.sessions.upsert(session)
        return session

    def _get(self, session_id: str) -> PracticeSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session
```

Create `src/finch/practice/__init__.py`（空文件）:

```python
"""expression-practice 领域服务。"""
```

- [ ] **Step 3: `PracticeSessionRepository`**

Edit `src/finch/storage/repositories.py` — 加 import `from finch.practice.models import PracticeSession`，并在文件末尾加：

```python
class PracticeSessionRecord(SQLModel, table=True):
    """PracticeSession（表达练习会话）持久化模型。"""

    id: str = Field(primary_key=True)
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PracticeSessionRepository:
    """表达练习会话仓储：按 id merge 幂等 upsert。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def upsert(self, session: PracticeSession) -> None:
        record = PracticeSessionRecord(
            id=session.id,
            payload_json=session.model_dump_json(),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session_ctx:
            session_ctx.merge(record)
            session_ctx.commit()

    def get(self, session_id: str) -> PracticeSession | None:
        with Session(self.store.engine) as session_ctx:
            record = session_ctx.get(PracticeSessionRecord, session_id)
            if record is None:
                return None
            return PracticeSession.model_validate_json(record.payload_json)
```

- [ ] **Step 4: 写失败测试**

Create `tests/unit/test_practice_service.py`:

```python
"""PracticeService：start / diagnose / save_revision / finish。"""

from finch.practice.models import PracticeDiagnosis, PracticeLesson
from finch.practice.service import PracticeService
from finch.storage.database import Store
from finch.storage.repositories import PracticeSessionRepository


class FakeRunner:
    def __init__(self, diagnosis, lesson):
        self.diagnosis = diagnosis
        self.lesson = lesson

    def run(self, prompt, output_model, **kw):
        if output_model is PracticeDiagnosis:
            return self.diagnosis
        if output_model is PracticeLesson:
            return self.lesson
        raise AssertionError(output_model)


def _service(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    return PracticeService(
        PracticeSessionRepository(store),
        FakeRunner(
            PracticeDiagnosis(diagnosis="最大问题是空泛", question="具体发生在哪一步？"),
            PracticeLesson(lesson="先讲具体场景再下判断"),
        ),
    )


def test_full_session(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(idea_id="idea_1", initial_attempt="初稿")
    assert s.status == "started"
    s = svc.diagnose(s.id, context="idea context")
    assert s.questions_asked == ["具体发生在哪一步？"]
    s = svc.save_revision(s.id, "修订1")
    assert s.revisions == ["修订1"]
    s = svc.finish(s.id, "最终版")
    assert s.status == "finished"
    assert s.final_expression == "最终版"
    assert s.lesson == "先讲具体场景再下判断"


def test_diagnose_missing_session_raises(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    svc = PracticeService(
        PracticeSessionRepository(store),
        FakeRunner(PracticeDiagnosis(diagnosis="d", question="q"), PracticeLesson(lesson="l")),
    )
    try:
        svc.diagnose("nope")
    except KeyError:
        return
    raise AssertionError("expected KeyError")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_practice_service.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 6: `finch practice` CLI**

Edit `src/finch/cli.py` — 加 import `from .practice.service import PracticeService`、repositories 加 `PracticeSessionRepository`；`app.add_typer(scout_app, ...)` 之后加：

```python
practice_app = typer.Typer(help="表达练习（expression-practice，skill 驱动 + 一次性 CLI 落库）")
app.add_typer(practice_app, name="practice")
```

文件末尾加五个命令：

```python
@practice_app.command("start")
def practice_start(
    idea: str = typer.Option(None, "--idea", help="关联 idea id"),
    opportunity: str = typer.Option(None, "--opportunity", help="关联 opportunity id"),
    attempt: str = typer.Option(..., "--attempt", help="用户首稿"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """开始一次表达练习（记 idea/opportunity + 首稿）。"""
    if (idea is None) == (opportunity is None):
        typer.echo("exactly one of --idea / --opportunity is required")
        raise typer.Exit(code=1)
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    session = PracticeService(PracticeSessionRepository(store), runner).start(
        idea_id=idea, opportunity_id=opportunity, initial_attempt=attempt
    )
    if as_json:
        typer.echo(session.model_dump_json(indent=2))
    else:
        typer.echo(session.id)


@practice_app.command("diagnose")
def practice_diagnose(
    session_id: str = typer.Argument(..., help="session id"),
    context: str = typer.Option("", "--context", help="可选 idea 语境"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """诊断最大问题 + 追问一个问题（LLM）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    try:
        session = PracticeService(PracticeSessionRepository(store), runner).diagnose(
            session_id, context=context
        )
    except KeyError:
        typer.echo(f"session not found: {session_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(session.model_dump_json(indent=2))
    else:
        typer.echo(f"diagnosis: {session.diagnosis}")
        typer.echo(f"question: {session.questions_asked[-1]}")


@practice_app.command("save")
def practice_save(
    session_id: str = typer.Argument(..., help="session id"),
    revision: str = typer.Option(..., "--revision", help="修订后的表达"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """追加一次修订。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    try:
        session = PracticeService(PracticeSessionRepository(store), runner).save_revision(
            session_id, revision
        )
    except KeyError:
        typer.echo(f"session not found: {session_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(session.model_dump_json(indent=2))
    else:
        typer.echo(f"revisions: {len(session.revisions)}")


@practice_app.command("finish")
def practice_finish(
    session_id: str = typer.Argument(..., help="session id"),
    final: str = typer.Option(..., "--final", help="最终表达"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """记最终版 + LLM 经验总结，置 finished。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    try:
        session = PracticeService(PracticeSessionRepository(store), runner).finish(
            session_id, final
        )
    except KeyError:
        typer.echo(f"session not found: {session_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(session.model_dump_json(indent=2))
    else:
        typer.echo(f"lesson: {session.lesson}")


@practice_app.command("show")
def practice_show(
    session_id: str = typer.Argument(..., help="session id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """展示会话（首稿 / 诊断 / 追问 / 修订 / 最终版 / lesson）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    session = PracticeSessionRepository(store).get(session_id)
    if session is None:
        typer.echo(f"session not found: {session_id}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(session.model_dump_json(indent=2))
    else:
        typer.echo(f"id: {session.id}")
        typer.echo(f"status: {session.status}")
        typer.echo(f"initial_attempt: {session.initial_attempt}")
        typer.echo(f"diagnosis: {session.diagnosis}")
        typer.echo(f"questions_asked: {json.dumps(session.questions_asked, ensure_ascii=False)}")
        typer.echo(f"revisions: {json.dumps(session.revisions, ensure_ascii=False)}")
        typer.echo(f"final_expression: {session.final_expression}")
        typer.echo(f"lesson: {session.lesson}")
```

- [ ] **Step 7: 写 SKILL.md**

Write `skills/expression-practice/SKILL.md`:

```markdown
---
name: expression-practice
description: >
  Finch 核心表达训练 Skill：通过实际表达提升能力。选择 Idea → 用户先表达 → 诊断最大问题
  → 追问一个问题 → 用户重新表达 → 对比前后 → 保存最终版。绝不先给范文、一次只问一个
  关键问题、默认不自动 rewrite、保存用户原文和每次修改。用于「帮我练一下这个想法的表达」
  「我想自己写、你来追问」类请求。
---

# expression-practice

让用户通过实际表达提升能力。核心：用户先表达，Skill 只诊断 + 追问，不代写。

## 流程

1. 选 Idea（`finch ideas list` 挑选，或关联一个 Opportunity）。
2. 用户先表达 → `finch practice start --idea <id> --attempt "..."`。
3. 诊断最大问题 → `finch practice diagnose <session-id>`。
4. 用户重新表达 → `finch practice save <session-id> --revision "..."`。
5. 重复 3-4 直到满意，或用户说「直接帮我写」→ 转 `idea-to-draft`。
6. 保存最终版 → `finch practice finish <session-id> --final "..."`。

## 检查维度

是否真正理解 / 有自己的判断 / 说明因果关系 / 与目标同行有关 / 具体 / 像用户自己 /
留下值得回应的空间（见 `references/diagnosis-rules.md`）。

## 强制规则

- 不先给完整范文。
- 一次只追问一个关键问题。
- 默认不自动 rewrite（只有用户明确要求代写才转 idea-to-draft）。
- 保存用户原文和每次修改（PracticeSession）。

## 参考

- `references/diagnosis-rules.md` — 诊断维度。
- `references/session-output.md` — 会话输出 schema。
```

Write `skills/expression-practice/references/diagnosis-rules.md`:

```markdown
# 诊断维度

- 理解：是否说清了机制，而非堆术语。
- 判断：是否有自己的取舍（decision/tradeoff）。
- 因果：是否说明「为什么」。
- 相关：是否与目标同行的问题有关。
- 具体：是否有场景、后果、依据，而非空泛概括。
- 像本人：是否符合 voice-profile。
- 留白：是否留下值得回应的空间。

每次只挑「最大」的一个问题追问，不列清单。
```

Write `skills/expression-practice/references/session-output.md`:

```markdown
# 会话输出

PracticeSession：initial_attempt / diagnosis / questions_asked[] / revisions[] /
final_expression / lesson，关联 idea_id 或 opportunity_id。
```

- [ ] **Step 8: 回归 + 提交**

```bash
uv run ruff check . && uv run mypy src
uv run pytest tests/unit/test_practice_service.py tests/unit/test_repositories.py -q
git add -A
git commit -m "feat(practice): expression-practice session service and finch practice CLI"
```

---

## Task 7: `weekly-reflection`（定性复盘替换确定性周报）

**Files:**
- Create: `src/finch/learn/reflection.py`, `skills/weekly-reflection/SKILL.md`, `skills/weekly-reflection/references/reflection-contract.md`
- Modify: `src/finch/learn/weekly.py`（移除 narrative，保留指标函数）, `src/finch/cli.py`（`run_weekly`）
- Test: `tests/unit/test_reflection.py`；Modify `tests/unit/test_weekly.py`

**Interfaces:**
- Consumes: Task 2 后 `ContentJob`/`Draft`；`WeeklyReport`（指标容器）；`VoiceProfile`；`Feedback`；`ConversationEvidence`。
- Produces: `WeeklyReflection`、`WeeklyReflectionService.reflect(...)`、`render_reflection`；`finch weekly` 输出定性复盘。

- [ ] **Step 1: 新建 `reflection.py`**

Create `src/finch/learn/reflection.py`:

```python
"""WeeklyReflectionService：把一周的表达/修改/讨论/结果转成下一周一个训练重点（LLM 定性复盘）。

确定性指标仍由 ``weekly_analysis``（learn/weekly.py）计算，本服务只做「解读」：
指标与数据是输入，结论由 LLM 判断，但 LLM 输出不含任何 total / 分数。
"""

from typing import cast

from pydantic import BaseModel, Field

from finch.content.voice import VoiceProfile
from finch.engagement.models import ConversationEvidence
from finch.learn.models import Feedback
from finch.learn.weekly import WeeklyReport
from finch.llm.base import StructuredInferenceRunner

_REFLECT_PROMPT = """\
You write a weekly reflection that turns the past week's expression, revisions, discussions,
and results into ONE training focus for next week. Do not produce a list of generic advice.

Answer only four questions:
1. What did the user actually figure out this week?
2. Which expression sounded most like themselves?
3. Which exchange produced a new connection or a new question?
4. What ONE expression problem should they train next week?

## Deterministic metrics (computed in code)
{metrics}

## Published feedback
{feedbacks}

## Conversation evidence
{conversations}

## Voice profile
{voice}

Respond with JSON matching the schema: insight, strongest_expression,
meaningful_connection, next_practice, stop_doing, voice_update_candidate,
new_idea_candidates (list).
"""


class WeeklyReflection(BaseModel):
    """定性周复盘：四个问题的答案 + 一个训练重点。"""

    insight: str
    strongest_expression: str
    meaningful_connection: str
    next_practice: str
    stop_doing: str
    voice_update_candidate: str
    new_idea_candidates: list[str] = Field(default_factory=list)


class WeeklyReflectionService:
    """从 WeeklyReport（指标）+ 相关数据生成定性复盘。"""

    def __init__(self, runner: StructuredInferenceRunner) -> None:
        self.runner = runner

    def reflect(
        self,
        report: WeeklyReport,
        *,
        feedbacks: list[Feedback] | None = None,
        conversation_evidence: list[ConversationEvidence] | None = None,
        voice_profile: VoiceProfile | None = None,
    ) -> WeeklyReflection:
        metrics = {
            "reviewed_drafts": report.reviewed_drafts,
            "approved": report.approved,
            "skipped": report.skipped,
            "approval_rate": report.approval_rate,
            "evidence_coverage": report.evidence_coverage,
            "decision_density": report.decision_density,
            "generic_sentence_rate": report.generic_sentence_rate,
            "human_correction_rate": report.human_correction_rate,
            "job_completion_rate": report.job_completion_rate,
            "useful_reply_rate": report.useful_reply_rate,
        }
        profile = voice_profile if voice_profile is not None else VoiceProfile()
        return cast(
            WeeklyReflection,
            self.runner.run(
                _REFLECT_PROMPT.format(
                    metrics=_render_metrics(metrics),
                    feedbacks=_render_feedbacks(feedbacks or []),
                    conversations=_render_conversations(conversation_evidence or []),
                    voice=profile.model_dump_json(),
                ),
                WeeklyReflection,
            ),
        )


def _render_metrics(metrics: dict) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in metrics.items())


def _render_feedbacks(feedbacks: list[Feedback]) -> str:
    if not feedbacks:
        return "(none)"
    return "\n".join(
        f"- {fb.draft_id}: learning={fb.learning or '(none)'}"
        for fb in feedbacks
    )


def _render_conversations(conversations: list[ConversationEvidence]) -> str:
    if not conversations:
        return "(none)"
    return "\n".join(
        f"- [{c.kind}] {c.statement}" for c in conversations
    )


def render_reflection(reflection: WeeklyReflection) -> str:
    """把 WeeklyReflection 渲染为 Markdown。"""
    lines = [
        "# Finch Weekly Reflection",
        "",
        "## 本周想清楚了什么",
        f"- {reflection.insight or '(none)'}",
        "",
        "## 最像自己的表达",
        f"- {reflection.strongest_expression or '(none)'}",
        "",
        "## 有意义的交流",
        f"- {reflection.meaningful_connection or '(none)'}",
        "",
        "## 下周训练重点",
        f"- {reflection.next_practice or '(none)'}",
        "",
        "## 停止做",
        f"- {reflection.stop_doing or '(none)'}",
        "",
        "## 声音画像更新候选",
        f"- {reflection.voice_update_candidate or '(none)'}",
    ]
    if reflection.new_idea_candidates:
        lines += ["", "## 新 Idea 候选"]
        lines += [f"- {cand}" for cand in reflection.new_idea_candidates]
    return "\n".join(lines)
```

- [ ] **Step 2: 精简 `weekly.py`（移除 narrative，保留指标）**

Edit `src/finch/learn/weekly.py`：

1. 删除 `NextWeekPlan` 类。
2. 从 `WeeklyReport` 删除字段 `weekly_insight` 与 `next_week`（含注释行）。
3. 删除 `_build_narrative`、`_MetricSignal`、`_HEALTHY`、`_WEAK`、`_HIGHER_IS_BETTER`、`_DIRECTIONAL_METRICS`、`_EXPERIMENT_HYPOTHESES`、`_STOP_ACTIONS`、`_classify`、`render_weekly`、`_METRIC_LABELS`。
4. 保留：`_do_not_write_rate`、`_rewrite_rounds`、`_find_check`、`_reports_for`、`_evidence_coverage`、`_decision_density`、`_generic_sentence_rate`、`_human_correction_rate`、`_job_completion_rate`、`_useful_reply_rate`、`weekly_analysis`。
5. 在 `weekly_analysis` 末尾的 `return WeeklyReport(...)` 中删除 `weekly_insight=weekly_insight` 与 `next_week=next_week` 两行，并删除函数体内的 `weekly_insight, next_week = _build_narrative(...)` 调用块。

- [ ] **Step 3: 写失败测试**

Create `tests/unit/test_reflection.py`:

```python
"""WeeklyReflectionService：定性复盘（指标输入 + LLM 输出校验）。"""

from finch.content.voice import VoiceProfile
from finch.learn.reflection import (
    WeeklyReflection,
    WeeklyReflectionService,
    render_reflection,
)
from finch.learn.weekly import WeeklyReport


class FakeRunner:
    def __init__(self, ret):
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        return self.ret


def _report() -> WeeklyReport:
    return WeeklyReport(reviewed_drafts=2, approved=1, approval_rate=0.5)


def test_reflect_returns_reflection():
    svc = WeeklyReflectionService(FakeRunner(WeeklyReflection(
        insight="想清楚了 X", strongest_expression="那篇回复", meaningful_connection="一次追问",
        next_practice="先讲场景再下判断", stop_doing="堆术语",
        voice_update_candidate="少用'本质上'", new_idea_candidates=["idea_x"],
    )))
    out = svc.reflect(_report(), voice_profile=VoiceProfile())
    assert out.insight == "想清楚了 X"
    assert out.new_idea_candidates == ["idea_x"]


def test_render_reflection_contains_sections():
    reflection = WeeklyReflection(
        insight="i", strongest_expression="s", meaningful_connection="m",
        next_practice="n", stop_doing="x", voice_update_candidate="v",
    )
    text = render_reflection(reflection)
    assert "本周想清楚了什么" in text
    assert "下周训练重点" in text
```

- [ ] **Step 4: 改 CLI `run_weekly`**

Edit `src/finch/cli.py` — 加 import `from .learn.reflection import WeeklyReflectionService, render_reflection` 与 `from .content.voice import load_voice_profile`（后者已在顶部 import）。替换 `run_weekly`：

```python
@app.command("weekly")
def run_weekly(as_json: bool = typer.Option(False, "--json", help="输出 JSON")) -> None:
    """周复盘：确定性指标 + LLM 定性解读（一个训练重点）。"""
    settings = load_settings()
    store = Store(settings.paths.db_path)
    store.init()
    since = datetime.now(UTC) - timedelta(days=7)
    report = weekly_analysis(
        DraftRepository(store),
        DecisionRecordRepository(store),
        FeedbackRepository(store),
        ContentJobRepository(store),
        CriticReportRepository(store),
        since=since,
    )
    runner = cast(CodexRunner, create_runner(settings.llm, "critique") or CodexRunner())
    reflection = WeeklyReflectionService(runner).reflect(
        report,
        feedbacks=FeedbackRepository(store).list_feedbacks(),
        conversation_evidence=ConversationEvidenceRepository(store).list_all(),
        voice_profile=load_voice_profile(settings.paths.voice_profile_path),
    )
    if as_json:
        typer.echo(reflection.model_dump_json(indent=2))
    else:
        typer.echo(render_reflection(reflection))
```

Remove the now-unused `from .learn.weekly import render_weekly, weekly_analysis` — change to `from .learn.weekly import weekly_analysis`.

- [ ] **Step 5: 更新 `test_weekly.py`**

Edit `tests/unit/test_weekly.py` — 删除 `render_weekly` 相关断言：
- `test_weekly_analysis_empty` 中删除 `assert "evidence insufficient" in render_weekly(report)`，改为 `assert report.reviewed_drafts == 0`。
- 顶部 import 改为 `from finch.learn.weekly import weekly_analysis`。

- [ ] **Step 6: 写 SKILL.md**

Write `skills/weekly-reflection/SKILL.md`:

```markdown
---
name: weekly-reflection
description: >
  把一周的表达、修改、讨论和结果转成下一周一个训练重点（LLM 定性复盘）。输入首稿与最终稿、
  Critic 报告、人工修改记录、VoiceProfile 变化、有意义的回复、ConversationEvidence 与发布
  后效果数据（含确定性指标）；只回答四个问题并给出一个下周训练重点。用于「帮我做本周复盘」
  「这周表达上练什么」类请求。
---

# weekly-reflection

把一周转成下一周一个训练重点。只回答四个问题：本周想清楚了什么 / 哪次表达最像自己 /
哪次交流产生新连接或新问题 / 下周只练哪一个表达问题。

## 执行

`finch weekly [--json]`（指标由代码算，解读由 LLM 做）。

## 边界

- 不自动更新 VoiceProfile（输出 voice_update_candidate，由用户确认后走 `voice-profile`）。
- 不输出十几条泛泛建议；每周只选一个表达实验。
- 不以发帖数量、点赞量为主要目标。

## 参考

- `references/reflection-contract.md` — 输出 schema 与四问。
```

Write `skills/weekly-reflection/references/reflection-contract.md`:

```markdown
# 复盘输出

insight / strongest_expression / meaningful_connection / next_practice / stop_doing /
voice_update_candidate / new_idea_candidates[]。

不变量：指标（evidence_coverage 等）由代码算，LLM 只解读、不产出 total。
```

- [ ] **Step 7: 回归 + 提交**

```bash
uv run ruff check . && uv run mypy src
uv run pytest tests/unit/test_reflection.py tests/unit/test_weekly.py -q
git add -A
git commit -m "feat(learn): replace deterministic weekly narrative with LLM qualitative reflection"
```

---

## 完成后的全量校验

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
```

预期：全绿。若 `test_cli_ideas.py` 里既有 `ideas search` 相关断言因删除命令而失败，删除对应断言（该命令已退役）。

## 范围说明（spec §8 的 evals 缺口）

spec §8 要求为 `expression-practice` 与 `voice-profile` 新增 `evals/cases.yaml`。这两个是**交互式 skill**（诊断只问一个问题 / 不先给范文 / diff 确认等行为），其 eval 依赖 skill-eval harness（`skills/*/evals/` 由该 harness 消费，非 pytest），本计划的 pytest 单测已覆盖对应的领域服务行为（`PracticeService` 每步状态转换、`WeeklyReflectionService` 输出校验）。这两个 evals 文件的编写不在本代码计划内，待 skill-eval harness 接线时单独补充。
