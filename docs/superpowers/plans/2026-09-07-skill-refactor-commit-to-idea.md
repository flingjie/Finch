# Skill 架构重构 Step 2（commit-to-idea）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 实现 `commit-to-idea` Skill：`commit_service.py` 把 Commit 分组/私有过滤/安全扫描/工程问题决策结果提炼迁进领域服务，产出 `IdeaCandidate`；`skills/commit-to-idea/SKILL.md` + reference + eval；`finch ideas commit` CLI。

**Architecture:** `finch ideas commit [--repo] [--since]` → `CommitReader`/`Ingestor`/`Extractor`（复用现有 github/evidence 适配器）→ `CommitService.to_ideas()` → `IdeaService.create_candidate()`（幂等落库为 ContentJob）。

**Tech Stack:** Python 3.12、Pydantic 2、SQLModel、typer、pytest、gh/opencli（只读）。

## Global Constraints

- 复用现有 `github/commit_reader.py`（CommitReader.filter_noise）、`github/change_grouper.py`、`evidence/extractor.py`（Extractor.extract + build_cards）、`evidence/safety.py`；**不重写**这些，只做迁移调用。
- 私有仓库内容 → 不可发布（过滤）。
- 推断性立场 → `proposed`；只有用户明确确认才是 `confirmed`。
- 相同输入 → 相同结果（`idea_generation_key` 幂等）。
- 不自动发布；分数由代码算。
- 中文/英文 docstring 均可。

---

### Task 1: CommitService（commit → IdeaCandidate）

**Files:** `src/finch/ideas/commit_service.py`（新建）
**Test:** `tests/unit/test_commit_service.py`

**Interfaces:**
- `CommitService(reader: CommitReader, extractor: Extractor)`：
  - `to_ideas(commits: list[CommitDetail], *, repo: str) -> list[IdeaCandidate]`：对每个 commit（或 group）提取 EngineeringEvent → EvidenceCard → 构造 IdeaCandidate（`origin="commit"`、`source_refs` 用 commit URL、`boundaries` 从事件置信度映射、`generator={skill:"commit-to-idea", version:"1.0.0"}`、`author_position.status="proposed"`）。
  - 机械变化/私有内容/无可提炼观点 → 空结果（过滤）。

- [ ] **Step 1: 写失败测试**（有决策的 commit → 1 个 Idea；机械变化 → 空；私有 → 空；推断立场 → proposed；相同输入 → 相同结果）。
- [ ] **Step 2: 实现**（迁移调用现有 reader/extractor/safety）。
- [ ] **Step 3: 跑测试 + 全量回归**。
- [ ] **Step 4: 提交** `git commit -m "feat: add commit-to-idea skill"`。

---

### Task 2: SKILL.md + reference + eval

**Files:** `skills/commit-to-idea/SKILL.md`、`skills/commit-to-idea/references/commit-analysis.md`、`skills/commit-to-idea/evals/cases.yaml`（新建）

- [ ] **Step 1:** SKILL.md（职责=从 Commit/PR/Issue/测试变化提炼 Idea；引用 `_shared/idea-contract.md` 等；引用 `finch ideas commit`）。
- [ ] **Step 2:** reference + eval cases（有决策 commit / 机械变化 / 私有内容 / 推断立场 / 相同输入）。
- [ ] **Step 3: 提交** `git commit -m "docs: commit-to-idea skill"`。

---

### Task 3: `finch ideas commit` CLI + 回归

**Files:** `src/finch/cli.py`（新增 `ideas` 子命令组 + `commit`）
**Test:** `tests/unit/test_cli_ideas.py`

- [ ] **Step 1:** 加 `ideas_app` typer 子组 + `finch ideas commit [--repo] [--since] --json`（复用 `github_reflect` 的 load_commit_details + CommitService + IdeaService，落库并输出 `--json`）。
- [ ] **Step 2: 全量回归** `uv run pytest -q` + `ruff` + `mypy`。
- [ ] **Step 3: 提交** `git commit -m "feat: finch ideas commit"`。

---

## Self-Review

1. **覆盖**：plan §8 Step 2 全部；§5 幂等 → Task 1；§6 skills 结构 → Task 2。
2. **占位符**：无。
3. **类型一致**：`CommitService.to_ideas` → `list[IdeaCandidate]`；`IdeaService.create_candidate(IdeaCandidate)` 复用 Task 2 Step 1 已建的。
