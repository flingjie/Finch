# Finch：让 AI 完成它的工作 —— 实现计划（SDD 分解）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 需求基线 / 领域设计：`docs/Finch-Effective-Content-Implementation-Plan.md`（以下简称 **Spec**）。本文件把 Spec 拆成 9 个可独立派发的任务（对应 Spec §11 的 PR 1–9），并落地精确契约。

**Goal:** 把 Finch 从「找到证据就写」升级为「先定义内容要完成的工作（Content Job）并确认作者判断，再写」；把单一 Critic 总分升级为可解释、可定向修复的 Checker Suite；把互动数据升级为「内容是否完成预定工作」的效果反馈。

**Architecture:** 确定性 Python + SQLModel + Pydantic v2 + Typer。新增 `content/jobs.py`（Content Job 模型）、`content/checkers/`（Checker Suite）、`content/voice.py`（Voice Profile）。Graph 在 `MatchEvidenceNode` 与 `DraftNode` 之间插入 `DefineContentJobsNode` 与 `PositionGateNode`（确定性校验，不加 LLM 路由或回边）。持久化接入 Alembic。

**Tech Stack:** Python 3.12、Pydantic v2、SQLModel、Typer、既有 `Store`/`Draft`/`MatchResult`/`GraphRuntime`、pytest、Ruff、mypy、Alembic。命令：`uv run pytest`、`uv run ruff check .`、`uv run mypy src`。

**Spec:** `docs/Finch-Effective-Content-Implementation-Plan.md`（§3 领域模型、§4 Graph、§5 Critic Suite、§6 Voice、§7 CLI、§8 Outcome、§9 持久化）。

## Global Constraints

- **不重构为多 Agent 平台**；不新增 LLM 路由或 Graph 回边（Spec §1、§4）。
- 每个正式 Draft 必须绑定一个 `READY` 且 position 已确认的 Content Job；未确认 position 只能生成「提纲建议」，不得进入正式 Draft/Review（Spec §3.1、§12）。
- `source_card_ids` 必须是 `MatchResult.card_ids` 的子集；原创内容必须来自本次可发布 Evidence Cards（Spec §3.1 硬规则）。
- `DO_NOT_WRITE` 是正常成功结果，不是失败（Spec §3.1）。
- Critic 汇总由**确定性聚合器**计算，不得直接相信模型返回的 `passed`（Spec §5.2）。
- 任一 `hard_fail` 拒绝；high 且 `requires_human_input=true` 进入 `NEEDS_INPUT`；其余失败项进入定向重写，重写 prompt 只接收失败检查器的具体指令，禁止无目标「整体润色」（Spec §5.2）。
- 安全与证据 hard fail 不能被质量均分覆盖（Spec §12）。
- 所有新增字段提供默认值，避免破坏现有审核历史；数据库仍用 payload JSON（Spec §3.2）。
- 不自动发布；发布仍需人工批准并在外部完成（Spec §7、§12）。
- 新增记录表通过 `Store.init()` 的 `create_all` 自动注册（沿用 Phase 4/6 的 `from finch.storage import repositories` 再 `create_all` 机制），并接入 Alembic（Spec §9）。
- 仓储方法避免 N+1，沿用最新 commit 的批量读取原则（Spec §9）。
- Python `>=3.12`；全量测试、Ruff、mypy 通过；恢复、重放、幂等与只读权限边界保持不变（Spec §12）。

## Parallel Dispatch

任务严格串行（Spec §13：先 PR 1–3 建 Content Job 契约，再拆 Critic）。无并行 Wave；每个任务在 feature branch `effective-content` 上顺序提交，任务间用 `review-package BASE HEAD` 审查。

| Task | 内容 | 依赖 |
|---|---|---|
| 1 | Content Job 模型、仓储、prompt、单测 | 无 |
| 2 | DefineContentJobsNode / PositionGateNode、状态与 replay | 1 |
| 3 | jobs CLI、HITL、Daily Brief、review 模型扩展 | 1, 2 |
| 4 | Checker protocol + Evidence/Decision checker | 1 |
| 5 | Specificity/Portability + 确定性聚合 + targeted rewrite | 4 |
| 6 | Voice profile + Voice/Structure/Actionability/Safety checker | 4, 5 |
| 7 | Draft versions、critic reports、Alembic 迁移 | 1, 4–6 |
| 8 | Outcome feedback 与 weekly metrics | 1, 3 |
| 9 | eval corpus、两周 trial 文档与阈值校准 | 全部 |

## File Map

| 路径 | 职责 | 独占任务 |
|---|---|---|
| `src/finch/content/jobs.py`（新增）+ `tests/unit/test_jobs.py` | Content Job 模型 + JobRepository | 1 |
| `prompts/define-content-jobs.md`（新增） | DefineContentJobsNode 的 prompt | 1 |
| `src/finch/graph/content_nodes.py`、`src/finch/graph/state.py`、`tests/graph/*` | 两个新节点、状态、replay | 2 |
| `src/finch/content/models.py` + `tests/unit/test_content_models.py` | `Draft.content_job_id` 等扩展 | 1 |
| `src/finch/review/models.py` + `tests/unit/test_review_models.py` | `ReviewAction`/`SkipReason`/`ReviewDecision` 扩展 | 3 |
| `src/finch/cli.py` + `tests/unit/test_cli_run.py`、`test_cli_review.py` | `finch jobs *`、`finch run resume`、`finch voice *` | 3, 6 |
| `src/finch/content/checkers/`（新增）+ `tests/unit/test_checkers.py` | Checker protocol + 全部 checker | 4, 5, 6 |
| `src/finch/content/critic.py` + `tests/unit/test_critic.py` | 聚合器 + targeted rewrite | 5 |
| `src/finch/content/voice.py`（新增）+ `voice-profile.yaml`（新增）+ `tests/unit/test_voice.py` | Voice Profile | 6 |
| `src/finch/storage/repositories.py` + `tests/unit/test_repositories.py` | ContentJob/CriticReport/DraftVersion Record + 仓储 | 1, 7 |
| `src/finch/review/feedback.py`、`src/finch/review/weekly.py` + `tests/unit/test_weekly.py` | OutcomeAssessment + weekly metrics | 8 |
| `alembic/`（新增）、`migrations/` | Alembic 基线 + 迁移 | 7 |
| `tests/evals/`（新增） | 固定 eval 数据集 | 9 |
| `docs/trial-run.md`、`scripts/trial.sh` | trial 文档更新 | 9 |

**禁止改：** `src/finch/graph/runtime.py` 的执行模型（不回边、不加 LLM 路由）、`src/finch/review/__init__.py`、`src/finch/storage/__init__.py`（除非新增 import 注册新表）。

## Shared Contracts

### C1. Content Job 模型（Task 1 实现，`src/finch/content/jobs.py`）

```python
from typing import Literal
from enum import StrEnum
from pydantic import BaseModel, Field

class IntendedEffect(BaseModel):
    understand: str
    believe: str | None = None
    action: str | None = None

class AuthorPosition(BaseModel):
    claim: str
    decision: str
    tradeoff: str
    change_mind_if: str | None = None
    confirmed: bool = False

class SuccessCriterion(BaseModel):
    id: str
    description: str
    measurement: Literal["critic", "human", "outcome"]

class ContentJobStatus(StrEnum):
    PROPOSED = "proposed"
    NEEDS_INPUT = "needs_input"
    READY = "ready"
    DO_NOT_WRITE = "do_not_write"

class ContentJob(BaseModel):
    id: str
    source_card_ids: list[str]
    candidate_id: str | None = None
    reader_problem: str
    audience: str
    intended_effect: IntendedEffect
    author_position: AuthorPosition | None = None
    success_criteria: list[SuccessCriterion]
    recommended_format: DraftKind
    status: ContentJobStatus
    missing_questions: list[str] = Field(default_factory=list, max_length=3)
```

硬规则（实现为 `ContentJob.validate_source_cards(cards: set[str]) -> bool` 或等价门禁）：
- `source_card_ids` ⊆ 本次 MatchResult 的 `card_ids`。
- 缺 `author_position.decision` 或 `tradeoff` → `NEEDS_INPUT`，`missing_questions` 最多 3 个。
- 证据不足 / 无增量 / 无适合读者的任务 → `DO_NOT_WRITE`（正常成功）。
- `recommended_format` 复用 `finch.content.models.DraftKind`。

### C2. Draft 扩展（Task 1 实现，改 `src/finch/content/models.py`）

```python
class Draft(BaseModel):
    id: str
    kind: DraftKind
    candidate_id: str | None = None
    language: str = "en"
    body: str
    claims: list[ClaimRef] = Field(default_factory=list)
    content_job_id: str | None = None        # 新增，默认 None（兼容历史草稿）
    position_statement: str = ""             # 新增，作者判断声明
    critic_report_id: str | None = None      # 新增
```

（新增字段全部带默认值；`content_job_id` 用 `str | None` 以满足 Spec §9「无 content_job_id 的历史草稿只读展示」。）

### C3. Checker 接口（Task 4 实现，`src/finch/content/checkers/base.py`）

```python
class CheckResult(BaseModel):
    checker: str
    passed: bool
    severity: Literal["low", "medium", "high", "hard_fail"]
    locations: list[str]
    issues: list[str]
    rewrite_instructions: list[str]
    requires_human_input: bool = False

class Checker(Protocol):
    name: str
    def check(self, ctx: CheckContext) -> CheckResult: ...
```

`CheckContext` 携带 draft body、claims、对应 Content Job、voice profile（Task 6 起）等。`CritiqueResult` 增加 `checks: list[CheckResult]`（Task 5 实现）。

### C4. Review 扩展（Task 3 实现，改 `src/finch/review/models.py`）

```python
class ReviewAction(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    SKIP = "skip"
    CONFIRM_POSITION = "confirm_position"     # 独立于最终发布批准

class SkipReason(StrEnum):
    # 现有项保留 …
    NO_CLEAR_POSITION = "no_clear_position"
    GENERIC_VOICE = "generic_voice"
    JOB_NOT_USEFUL = "job_not_useful"
    FACT_ERROR = "fact_error"

class ReviewDecision(BaseModel):
    # 现有字段保留 …
    position_correct: bool | None = None
    voice_match: int | None = None
    job_clear: bool | None = None
```

### C5. Outcome 模型（Task 8 实现，`src/finch/review/feedback.py`）

```python
class OutcomeAssessment(BaseModel):
    job_completed: Literal["yes", "partly", "no", "unknown"]
    reader_understood: bool | None = None
    desired_action_count: int | None = None
    useful_reply_count: int | None = None
    github_clicks: int | None = None
    notes: str | None = None
```

### C6. Graph 节点（Task 2 实现，`src/finch/graph/content_nodes.py`）

- `DefineContentJobsNode`：reads `match_results`, `evidence_cards`, `candidates`；writes `content_jobs`；状态 `JOBS_DEFINED`。
- `PositionGateNode`：reads `content_jobs`；writes `ready_jobs`；状态 `POSITIONS_READY` / `NEEDS_INPUT`（确定性校验，缺 position → 保存状态并停止）。
- 内容段链：`MatchEvidenceNode → DefineContentJobsNode → PositionGateNode → DraftNode → CritiqueNode → BriefNode`。
- `DraftNode` 改为读 `ready_jobs + evidence_cards + candidates`；空 job 或 `DO_NOT_WRITE` 返回成功空列表。
- `NEEDS_INPUT` 后，`finch run resume <run-id>` 利用现有 replay/resume 从 gate 继续（Task 3 CLI）。

### C7. 聚合器（Task 5 实现，`src/finch/content/critic.py`）

`passed` 由确定性聚合器计算：任一 `hard_fail` → 拒绝；任一 high 且 `requires_human_input=true` → `NEEDS_INPUT`；其余失败项 → 定向重写。每轮保存 checker report 与 draft version（Task 7 持久化）。

### C8. 持久化 Record（Task 1 + Task 7 实现，`src/finch/storage/repositories.py`）

`ContentJobRecord`（Task 1）、`CriticReportRecord`（Task 7）、`DraftVersionRecord`（Task 7）——均 SQLModel `table=True`，`payload_json: str` 存完整模型，`updated_at` 用 `datetime.now(UTC)`。`OutcomeAssessment` 存进现有 `FeedbackRecord.payload_json`（Task 8）。

---

## Task 1: Content Job 模型、仓储、prompt、单测（PR 1）

- [ ] 新增 `src/finch/content/jobs.py`：实现 C1 全部模型 + `ContentJobStatus`。
- [ ] 实现 `source_card_ids ⊆ card_ids` 的校验门禁（`validate_source_cards`）。
- [ ] 改 `src/finch/content/models.py`：`Draft` 增加 C2 三个字段（带默认值）。
- [ ] 新增 `ContentJobRecord` + `ContentJobRepository`（`upsert/get/list`，`model_validate_json` 往返，避免 N+1）。
- [ ] 在 `src/finch/storage/__init__.py` 注册新 Record（沿用 `create_all` 机制）。
- [ ] 新增 `prompts/define-content-jobs.md`：输入 Evidence Cards，输出 Content Job JSON（含 `DO_NOT_WRITE`/`NEEDS_INPUT` 分支指令）。
- [ ] 新增 `tests/unit/test_jobs.py`：模型往返、状态枚举、`validate_source_cards` 子集校验、`missing_questions` 上限 3、`NEEDS_INPUT`/`DO_NOT_WRITE` 判定。
- [ ] 更新 `tests/unit/test_content_models.py`：Draft 新字段默认值。

**验收：** 给定一组 Evidence Cards，模型能表达 `READY / NEEDS_INPUT / DO_NOT_WRITE`；缺 position 的 job 为 `NEEDS_INPUT`；`missing_questions` 不超过 3 条；Draft 新字段不破坏现有构造。`uv run pytest` / `ruff` / `mypy` 全绿。

## Task 2: Graph 节点、状态与 replay（PR 2）

- [ ] 在 `src/finch/graph/state.py` 增加 `JOBS_DEFINED`、`POSITIONS_READY`、`NEEDS_INPUT` 状态（纳入主链或等价推进）。
- [ ] 实现 `DefineContentJobsNode`：读 `match_results/evidence_cards/candidates`，调 Task 1 prompt 生成 `content_jobs`，写 `content_jobs`。
- [ ] 实现 `PositionGateNode`：确定性校验 `author_position`（decision + tradeoff + confirmed），写 `ready_jobs`；缺 position → `NEEDS_INPUT` 保存状态并停止。
- [ ] 改 `DraftNode`：读 `ready_jobs + evidence_cards + candidates`；空 job / `DO_NOT_WRITE` 返回成功空列表。
- [ ] 更新 `src/finch/graph/daily.py` / `pipeline.py` 组装新链路（C6）。
- [ ] 增加 replay/resume 测试：`NEEDS_INPUT` 后从 gate 继续，`DO_NOT_WRITE` 幂等空输出。

**验收：** 内容段链符合 C6；`DO_NOT_WRITE` 与空 job 幂等返回空；未确认 position 不进入 DraftNode；replay 不破坏现有恢复语义。`uv run pytest` / `ruff` / `mypy` 全绿。

## Task 3: jobs CLI、HITL、Daily Brief、review 模型扩展（PR 3）

- [ ] 改 `src/finch/review/models.py`：C4 的 `ReviewAction.CONFIRM_POSITION`、4 个 `SkipReason`、3 个 `ReviewDecision` 字段（带默认值）。
- [ ] 新增 `finch jobs list --status needs_input` / `jobs show` / `jobs answer --file` / `jobs confirm-position` / `jobs reject --reason`。
- [ ] 新增 `finch run resume <run-id>`（复用现有 replay/resume）。
- [ ] 更新 Daily Brief：每个候选展示 Spec §7 的 6 项（要完成的工作、目标读者与期望动作、证据来源、核心判断与取舍、Critic 未解决风险、推荐动作）。
- [ ] 保留「用户在 Finch 外手动发布」安全边界（不新增自动发布）。

**验收：** 6 个 CLI 命令可用；`confirm-position` 独立于最终发布批准；`jobs answer` 更新 Content Job 后可从 gate 恢复；Daily Brief 展示 6 项。`uv run pytest` / `ruff` / `mypy` 全绿。

## Task 4: Checker protocol + Evidence/Decision checker（PR 4）

- [ ] 新增 `src/finch/content/checkers/base.py`：C3 的 `CheckResult` + `Checker` Protocol + `CheckContext`。
- [ ] 实现 `EvidenceChecker`：复用现有 claims 校验 + LLM 蕴含；无证据/越界主张 → `hard_fail`。
- [ ] 实现 `DecisionChecker`：与 Content Job 对齐，确认正文表达已确认的选择与取舍。
- [ ] 迁移现有 `CritiqueResult` 结构为兼容汇总（`checks` 字段，Task 5 补聚合）。
- [ ] `tests/unit/test_checkers.py`：checker 协议、Evidence hard fail、Decision 对齐。

**验收：** 每次拒绝/重写能指出具体句子、失败原因、严重级别与修改指令；hard fail 不被总分覆盖。`uv run pytest` / `ruff` / `mypy` 全绿。

## Task 5: Specificity/Portability + 聚合 + targeted rewrite（PR 5）

- [ ] 实现 `SpecificityChecker`（抽象词规则 + LLM）：找删除修辞后无信息的句子。
- [ ] 实现 `PortabilityChecker`（LLM 反事实）：检测可套用于任何项目的内容。
- [ ] 实现确定性聚合器（C7）：`hard_fail`→拒绝、high+human→`NEEDS_INPUT`、其余→定向重写。
- [ ] 重写 prompt 只接收失败检查器的具体指令，禁止无目标整体润色；有界循环针对失败项。
- [ ] 保存每轮 draft version 与 checker report（与 Task 7 的 Record 对接）。

**验收：** 通用 AI 套话 → Portability/Specificity fail；有数字无证据 → hard fail；重写两轮仍失败 → 不进审核。`uv run pytest` / `ruff` / `mypy` 全绿。

## Task 6: Voice profile + 4 个 checker（PR 6）

- [ ] 新增 `src/finch/content/voice.py` + `voice-profile.yaml`（preferred_patterns / avoid_phrases / rhythm_rules / approved_example_ids / rejected_example_ids+reasons）。
- [ ] `finch voice show` / `voice approve-example <draft-id>` / `voice reject-example <draft-id> --reason`。
- [ ] 实现 `VoiceChecker`（voice profile + LLM）、`StructureChecker`（确定性统计 + LLM）、`ActionabilityChecker`（Content Job 对齐）、`SafetyChecker`（复用现有 safety/critic）。
- [ ] 只有人工批准且 `voice_match` 达标的内容进入 approved examples；人工修改文本优先于原始 AI 草稿。

**验收：** Finch 能区分「事实错」「观点不是我的」「不像我说话」「任务本身无价值」；正反样本管理可追溯。`uv run pytest` / `ruff` / `mypy` 全绿。

## Task 7: Draft versions、critic reports、Alembic（PR 7）

- [ ] 新增 `CriticReportRecord`、`DraftVersionRecord` + 对应仓储（C8）。
- [ ] 接入 Alembic：创建当前 schema baseline，再添加 content jobs / critic reports / draft versions 迁移。
- [ ] 对现有 Draft 兼容：无 `content_job_id` 的历史草稿只读展示，不参与新指标。
- [ ] 仓储方法沿用批量读取，避免 N+1。

**验收：** 三张新表可迁移与读回；历史草稿不崩溃；`uv run pytest` / `ruff` / `mypy` 全绿。

## Task 8: Outcome feedback 与 weekly metrics（PR 8）

- [ ] 扩展 `Feedback`：C5 的 `OutcomeAssessment` 存进 `FeedbackRecord.payload_json`。
- [ ] 扩展 `finch review feedback` 命令录入 outcome。
- [ ] 周报新增：`evidence_coverage`、`decision_density`、`generic_sentence_rate`、`human_correction_rate`、`job_completion_rate`、`useful_reply_rate`、`do_not_write_rate`（`do_not_write` 不视为失败）。
- [ ] 输出每周「继续/调整/停止」建议，不自动修改生产阈值。

**验收：** 周报能回答哪些内容任务有效/失败，失败发生在选题、证据、观点、表达还是分发。`uv run pytest` / `ruff` / `mypy` 全绿。

## Task 9: eval corpus、两周 trial 文档与阈值校准（PR 9）

- [ ] 在 `tests/evals/` 建立固定数据集，覆盖 Spec §10 Phase E 的 7 个场景（READY / NEEDS_INPUT / DO_NOT_WRITE / Portability-Specificity fail / 有数字无证据 hard fail / 取舍自然 pass / 重写两轮仍失败不进审核）。
- [ ] 更新 `docs/trial-run.md` 与 `scripts/trial.sh`：两周试运行流程与阈值校准。
- [ ] 每个 Phase 合并门槛写入：`pytest`、`ruff`、`mypy` 全绿；新增 prompt 有 contract test；Graph recovery/replay 测试不回退。

**验收：** eval 数据集可跑通全部 7 个场景；trial 文档反映新指标与阈值校准流程。`uv run pytest` / `ruff` / `mypy` 全绿。
