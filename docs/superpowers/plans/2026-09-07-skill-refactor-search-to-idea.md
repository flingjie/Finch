# Skill 架构重构 Step 3（search-to-idea）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 实现 `search-to-idea` Skill：`search_service.py` 把 OpenCLI 搜索/召回/去重/机会提炼迁进领域服务，产出 `IdeaCandidate`；`skills/search-to-idea/SKILL.md` + reference + eval；`finch ideas search` CLI。

**Architecture:** `finch ideas search [--topic]` → `QueryBuilder`/`OpenCliClient`（复用 twitter 适配器）→ `SearchService.to_ideas()` → `IdeaService.create_candidate()`。与 Commit 无关，OpenCLI 故障不影响 Commit Skill。

**Tech Stack:** Python 3.12、Pydantic 2、SQLModel、typer、pytest、opencli（只读）。

## Global Constraints

- 复用 `twitter/query_builder.py`（QueryBuilder）、`twitter/opencli_client.py`（OpenCliClient.search）、`twitter/normalizer.py`（normalize_tweets）；**不重写**。
- 外部作者亲历 → 不变成用户亲历；立场一律 `proposed`。
- 最近已表达内容 → 去重；新闻/融资/纯情绪 → 空列表。
- 相同输入 → 相同结果（幂等）。
- 不自动发布；分数由代码算。

---

### Task 1: SearchService（search → IdeaCandidate）

**Files:** `src/finch/ideas/search_service.py`（新建）
**Test:** `tests/unit/test_search_service.py`

**Interfaces:**
- `SearchService(opencli: OpenCliClient, builder: QueryBuilder)`：
  - `to_ideas(posts: list, *, topic: str) -> list[IdeaCandidate]`：真实问题/反例/工程缺口 → IdeaCandidate（`origin="search"`、`source_refs` 用帖子 URL、立场 proposed）；新闻/融资/纯情绪 → 空；最近已表达 → 去重。

- [ ] **Step 1: 写失败测试**（真实问题→Idea；新闻→空；外部亲历不写成本人亲历；position 恒 proposed）。
- [ ] **Step 2: 实现**（迁移调用 twitter 适配器）。
- [ ] **Step 3: 跑测试 + 全量回归**。
- [ ] **Step 4: 提交** `git commit -m "feat: add search-to-idea skill"`。

---

### Task 2+3: SKILL.md + reference + eval + `finch ideas search` CLI

**Files:** `skills/search-to-idea/`（SKILL.md/references/evals，新建）、`src/finch/cli.py`（`ideas_app` 加 `search`）、`tests/unit/test_cli_ideas.py`（追加）

- [ ] **Step 1:** SKILL.md + reference + eval（真实问题/反例/工程缺口 → Idea；新闻/融资/纯情绪 → 空；最近已表达 → 去重；外部亲历不写成本人；position 恒 proposed）。
- [ ] **Step 2:** `finch ideas search [--topic] --json`（复用 ideas_app；SearchService → IdeaService 落库）。
- [ ] **Step 3: 全量回归** `uv run pytest -q` + `ruff` + `mypy`。
- [ ] **Step 4: 提交** `git commit -m "feat: finch ideas search"`。

---

## Self-Review

1. **覆盖**：plan §8 Step 3 全部；§5 幂等；§6 skills 结构。
2. **占位符**：无。
3. **类型一致**：`SearchService.to_ideas` → `list[IdeaCandidate]`；复用已建的 `IdeaService.create_candidate`。
