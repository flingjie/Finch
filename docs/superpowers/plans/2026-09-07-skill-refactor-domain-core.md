# Skill 架构重构 Step 1（领域核心）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 建立 Skill 架构的领域核心：`IdeaCandidate` 契约、`ideas/`（IdeaService）+ `drafts/`（DraftService）领域服务、ContentJob 字段扩展（origin/generation_key/generator_name/generator_version/content_fingerprint/idea_candidate_json）、领域状态机与幂等键、`skills/_shared/*.md`。本阶段**不删 Graph**（Step 6 才删），只新增领域核心并与现有代码共存。

**Architecture:** `commit-to-idea` / `search-to-idea` Skill → `IdeaService` → `ContentJobRepository` → `idea-to-draft` → `DraftService` → Critic → Review。Skill 层产出统一 `IdeaCandidate`；持久化复用 `ContentJob`（不新增 Idea 表）。

**Tech Stack:** Python 3.12、Pydantic 2、SQLModel、typer、pytest、alembic。

## Global Constraints

- Python 3.12+；Pydantic 2；SQLModel `payload_json` + `session.merge`（幂等）。
- Ruff `E,F,I,B,UP`，行宽 100，py312；mypy `src`；测试 `uv run pytest -v`。
- **不自动发布**；**分数由代码算**；Adapter 负责超时与传输重试，领域 Service 不透明重试。
- **IdeaCandidate 契约**（plan §3）：`core_point` 单一中心主张；`source_refs` 可追溯；自动生成立场一律 `proposed`；只有用户输入或命中已批准 fingerprint 才是 `confirmed`；Search 来源不得写成亲历；`known/inferred/unknown` 传递到 Draft 校验。
- 中文/英文 docstring 均可；与相邻模块一致。

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/finch/ideas/__init__.py` | 包 docstring | 新建 |
| `src/finch/ideas/models.py` | `IdeaCandidate` / `SourceRef` / `IdeaBoundaries` / `IdeaGenerator` / `IdeaPosition` | 新建 |
| `src/finch/ideas/service.py` | `IdeaService`（状态转换 + 幂等键 + `ContentJob` 往返） | 新建 |
| `src/finch/drafts/__init__.py` / `drafts/service.py` | `DraftService`（draft generation key + 生成/校验骨架） | 新建 |
| `src/finch/content/jobs.py` | `ContentJob` 加 idea 候选流字段；`ContentJobStatus` 状态机改 `PROPOSED/CONFIRMED/DRAFTED/SKIPPED` | 修改 |
| `src/finch/storage/repositories.py` | `ContentJobRecord` 加 `origin`/`generation_key` 投影列 + `find_by_generation_key` | 修改 |
| `alembic/versions/XXXX_add_idea_fields.py` | contentjobrecord 加 origin/generation_key 列 | 新建 |
| `skills/_shared/*.md` | idea-contract / evidence-policy / author-position / voice-guide / quality-policy | 新建 |

---

### Task 1: IdeaCandidate 契约 + ContentJob 字段 + 状态机

**Files:** `ideas/models.py`（新建）、`content/jobs.py`（改）、`storage/repositories.py`（改）、alembic migration（新建）
**Test:** `tests/unit/test_ideas_models.py`、`tests/unit/test_repositories.py`

**Interfaces:**
- `IdeaCandidate`（`id`、`origin: Literal["commit","search","user"]`、`core_point`、`reader_problem`、`why_worth_saying`、`author_position: IdeaPosition`、`source_refs: list[SourceRef]`、`boundaries: IdeaBoundaries`、`recommended_format: Literal["original","reply","thread"]`、`generator: IdeaGenerator`）。
- `IdeaPosition`（`claim`/`decision`/`tradeoff`/`status: Literal["proposed","confirmed"]`）。
- `SourceRef`（`type: Literal["commit","pr","issue","test","post","paper"]`、`ref`、`summary`）。
- `IdeaBoundaries`（`known`/`inferred`/`unknown` 均为 `list[str]`）。
- `IdeaGenerator`（`skill`、`version`）。
- `ContentJobStatus` 改为 `PROPOSED/CONFIRMED/DRAFTED/SKIPPED`（删 `READY`/`DO_NOT_WRITE`/`NEEDS_INPUT`——NEEDS_INPUT 已在 Phase 3 删）。同步改 `learn/weekly.py`、`inbox/service.py`、`idea/service.py`、`graph/select_nodes.py`、`graph/write_nodes.py` 里引用 `DO_NOT_WRITE`/`READY` 的地方（`DO_NOT_WRITE`→`SKIPPED`、`READY`→`CONFIRMED` 或 `PROPOSED`，按语义）。
- `ContentJob` 加 6 字段：`origin`、`generation_key`、`generator_name`、`generator_version`、`content_fingerprint`、`idea_candidate_json`（均 `str|None=None`，`origin` 用 `Literal[...]`）。
- `ContentJobRecord` 加 `origin`/`generation_key` 两列（`index=True`）+ `find_by_generation_key(generation_key) -> ContentJob | None`；`upsert_job`/`upsert_jobs` 写入这两列。
- alembic migration：contentjobrecord `add_column` origin/generation_key（各建 index）。

- [ ] **Step 1: 写失败测试**（IdeaCandidate round-trip + ContentJobStatus 新枚举 + find_by_generation_key）。
- [ ] **Step 2: 实现**（models + jobs + repositories + migration）。
- [ ] **Step 3: 跑测试** `uv run pytest tests/unit/test_ideas_models.py tests/unit/test_repositories.py -v`；全量 `uv run pytest -q`（改枚举后回归）。
- [ ] **Step 4: 提交** `git commit -m "feat: add IdeaCandidate domain contract"`。

---

### Task 2: IdeaService + 幂等键 + 状态转换

**Files:** `ideas/service.py`（新建）
**Test:** `tests/unit/test_ideas_service.py`

**Interfaces:**
- `idea_generation_key(skill, skill_version, canonical_source_refs, input_fingerprint) -> str`（sha256 拼接）。
- `IdeaService(jobs: ContentJobRepository)`：
  - `create_candidate(idea: IdeaCandidate) -> ContentJob`（`generation_key` 命中则返回已有 job；否则生成 `id="idea_"+sha256(...)[:8]` 的 ContentJob，`author_position.status="proposed"`，`status=PROPOSED`）。
  - `confirm_position(idea_id) -> ContentJob`（PROPOSED→CONFIRMED，`author_position.status="confirmed"`）。
  - `revise_position(idea_id, position: IdeaPosition) -> ContentJob`。
  - `require_confirmed(idea_id) -> ContentJob`（未 CONFIRMED 抛领域错误，含 `status="needs_confirmation"` 结构化信息）。
  - `mark_drafted(idea_id) -> ContentJob`（CONFIRMED→DRAFTED）。
  - `skip(idea_id, reason) -> ContentJob`（→SKIPPED，记 reject_reason）。
- 状态转换只经 IdeaService；非法转换抛明确 ValueError。

- [ ] **Step 1: 写失败测试**（create 幂等、confirm、require_confirmed 未确认抛错、skip、非法转换）。
- [ ] **Step 2: 实现**。
- [ ] **Step 3: 跑测试** + 全量回归。
- [ ] **Step 4: 提交** `git commit -m "feat: add domain idempotency and state transitions"`。

---

### Task 3: DraftService（draft generation key + 骨架）

**Files:** `drafts/service.py`（新建）
**Test:** `tests/unit/test_drafts_service.py`

**Interfaces:**
- `draft_generation_key(idea_fingerprint, idea_to_draft_version, format, voice_profile_version) -> str`。
- `DraftService(drafts: DraftRepository, critic_reports: CriticReportRepository)`：
  - `create(idea: ContentJob, *, version, format, voice_version) -> Draft`（`draft_generation_key` 命中返回已有 Draft；否则返回占位/生成骨架——本任务只落键与幂等，生成调用留给 Step 4 的 idea-to-draft Skill）。

- [ ] **Step 1: 写失败测试**（幂等：同键返回同一 draft）。
- [ ] **Step 2: 实现**（若生成逻辑依赖未建的 Skill，先做幂等键 + 空生成占位并注明）。
- [ ] **Step 3: 跑测试**。
- [ ] **Step 4: 提交** `git commit -m "feat: add draft idempotency key + DraftService skeleton"`。

---

### Task 4: skills/_shared/*.md + 回归

**Files:** `skills/_shared/idea-contract.md`、`evidence-policy.md`、`author-position.md`、`voice-guide.md`、`quality-policy.md`（新建）
**Test:** 无单测（文档）；全量回归。

- [ ] **Step 1: 写 5 个 shared 文档**（内容对应 plan §3 契约与不变量；`_shared` 是不可触发 Skill，只存规则）。
- [ ] **Step 2: 全量回归** `uv run pytest -q` + `uv run ruff check src/finch` + `uv run mypy src/finch`。
- [ ] **Step 3: 提交** `git commit -m "docs: add shared skill rules"`。

---

## Self-Review 记录

1. **覆盖**：plan §3（IdeaCandidate 契约）→ Task 1；§4（状态机 + IdeaService）→ Task 1/2；§5（幂等键）→ Task 2/3；§6（skills/_shared）→ Task 4；§8 Step 1（领域核心）→ 全部。
2. **占位符扫描**：Task 3 的「生成骨架」明确标注为幂等键优先，生成留给 Step 4。
3. **类型一致性**：`IdeaCandidate`/`IdeaPosition`/`SourceRef`/`IdeaBoundaries`/`IdeaGenerator` 命名跨任务一致；`ContentJobStatus` 新枚举在 Task 1 定，Task 2 使用。

**范围外（后续 Step）**：commit-to-idea / search-to-idea / idea-to-draft 三个 Skill 的 SKILL.md + eval（Step 2–4）、CLI 切换（Step 5）、删 Graph/RunRecord（Step 6）、文档（Step 7）。
