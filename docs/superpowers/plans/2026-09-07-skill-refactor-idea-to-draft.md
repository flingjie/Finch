# Skill 架构重构 Step 4（idea-to-draft）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 实现 `idea-to-draft` Skill：补全 `DraftService`（加载 Idea → require_confirmed → draft_generation_key → 命中返回 → 生成草稿 → 证据/事实边界校验 → Critic → 有限 rewrite → 保存 Draft/CriticReport）；`skills/idea-to-draft/SKILL.md` + reference + eval；`finch drafts create` CLI。

**Architecture:** `finch drafts create <idea-id>` → `IdeaService.require_confirmed` → `DraftService.create`（复用 `content/writer.write_original` + `content/critic` Critic 套件）→ 落库 Draft + CriticReport。

**Tech Stack:** Python 3.12、Pydantic 2、SQLModel、typer、pytest、LLM（`create_runner`/`CodexRunner`）。

## Global Constraints

- 不改变 author position；不搜索新来源；不补造事实；`INFERRED/UNKNOWN` 不写成亲历事实。
- Evidence/Safety hard fail 不被平均分掩盖（`aggregate_checks` 已有）。
- 不自动发布；幂等（`draft_generation_key`）。
- 中文/英文 docstring 均可。

---

### Task 1: DraftService 完整流程

**Files:** `src/finch/drafts/service.py`（补全 `create` 的生成 + Critic）
**Test:** `tests/unit/test_drafts_service.py`

**Interfaces:**
- `DraftService(drafts, critic_reports, jobs: ContentJobRepository, runner)`：
  - `create(idea_id, *, version, format, voice_version) -> Draft`：加载 job → `require_confirmed`（复用 IdeaService）→ `draft_generation_key` → `drafts.get_draft(id)` 命中返回 → 否则 `write_original`（复用 `content/writer`）→ `critique`/Critic 套件 → 有限 rewrite（`max_rewrite_rounds`）→ `drafts.upsert_draft` + `critic_reports.upsert_report` → 返回 Draft。非法状态（未确认）抛 ValueError。

- [ ] **Step 1: 写失败测试**（未确认抛错；幂等：同键返回同 Draft 不重复生成；通过 Critic 落库 Draft + CriticReport）。
- [ ] **Step 2: 实现**（复用 content/writer + content/critic）。
- [ ] **Step 3: 跑测试 + 全量回归**。
- [ ] **Step 4: 提交** `git commit -m "feat: add idea-to-draft skill"`。

---

### Task 2+3: SKILL.md + reference + eval + `finch drafts create` CLI

**Files:** `skills/idea-to-draft/`（SKILL.md/references/evals）、`src/finch/cli.py`（新增 `drafts_app` + `create`）、`tests/unit/test_cli_drafts.py`

- [ ] **Step 1:** SKILL.md（执行顺序 + 6 条强制规则）+ reference + eval。
- [ ] **Step 2:** `finch drafts create <idea-id> [--json]`（DraftService.create → 输出）。
- [ ] **Step 3: 全量回归** `uv run pytest -q` + `ruff` + `mypy`。
- [ ] **Step 4: 提交** `git commit -m "feat: finch drafts create"`。

---

## Self-Review

1. **覆盖**：plan §8 Step 4 全部；§4 Draft/Review 状态；§5 draft_generation_key。
2. **占位符**：无。
3. **类型一致**：`DraftService.create` 签名在 Step 1 Task 3 定为 `(idea, *, version, format, voice_version)`，本 Step 改为 `(idea_id, *, ...)` 需同步更新调用处与测试。
