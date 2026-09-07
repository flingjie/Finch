# 收件箱深度重构 Phase 2（Graph 收敛）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 10 节点 Graph 收敛为 7 节点：`preflight → extract → collect → recall → match → select → write`。`select` 合并 define_jobs+position_gate（先选后写、不再阻塞），`write` 合并 draft+critique（L0 必跑 + L1 条件），`brief` 出图，`NEEDS_INPUT` 从 daily 路径消失，`gate/` 模块删除。

**Architecture:** `select` 节点 = 规划主题 → 确定性排序 → 只展开 Top K（K = `max_daily_original_posts`，默认 1）→ `ready_jobs`（单一份，不再有 content_jobs/ready_jobs 两份，不再阻塞）。`write` 节点 = 写稿 → L0 确定性（`validate_draft`）必跑 → L1 LLM（`default_checker_suite` 8 检查器）按需跑（L0 失败 / `llm_critique_mode=always`）。`brief` 与 `gate/` 交互层删除。

**Tech Stack:** Python 3.12、Pydantic 2、SQLModel、typer、pytest。Phase 1 已删除 review/ 模块与立场机制（position_fingerprint/confirmed/position_source/复用门禁）；`ContentJob.author_position` 现只有 claim/decision/tradeoff/change_mind_if。

## Global Constraints

- Python 3.12+；Pydantic 2；SQLModel `payload_json` + `session.merge`（幂等）。
- Ruff `E,F,I,B,UP`，行宽 100，py312；mypy 跑 `src`；测试 `uv run pytest tests/... -v`。
- **不变量**：证据链 `Commit → EngineeringEvent → EvidenceCard → Draft` 不废除；不自动发布；分数由代码算（`weighted_total`、主题排序）；双轨每轮都跑、故障隔离。
- **GraphRuntime 顺序/幂等/重放语义不改**；只改节点拓扑（节点少几个、节点内部合并）。
- **不改互动五维打分公式**；互动候选继续由 engagement 轨道产出，`provenance=external` 永不变 `EvidenceCard`。
- 中文/英文 docstring 均可；与相邻模块风格一致。

---

## File Structure

| 文件 | 职责 | 本阶段动作 |
|---|---|---|
| `src/finch/graph/content_nodes.py` | 节点工厂 | 删 `make_define_jobs_node`/`make_position_gate_node`/`make_draft_node`/`make_critique_node`/`make_brief_node`；新增 `make_select_node`/`make_write_node`；保留 `default_checker_suite`/`_run_checks`/`_format_*` 等 |
| `src/finch/graph/daily.py` | 组装 | `daily_nodes` 改为 7 节点（select/write 替换 define_jobs/position_gate/draft/critique，删 brief） |
| `src/finch/settings.py` | 配置 | `QualityGates.max_rewrite_rounds` 默认 2→1；新增 `llm_critique_mode: Literal["on_fail_or_gate","always"] = "on_fail_or_gate"` |
| `src/finch/cli.py` | CLI | 删 `_finish_daily`/`_read_input_request`/`_cards_for`/`_mark_stopped`/`_resume_and_echo` 及 gate 交互收尾逻辑；`run_daily` 不再处理 NEEDS_INPUT |
| `src/finch/gate/` | 旧交互层 | **删除整个模块**（`models.py`/`render.py`/`interactive.py`/`resolve.py`） |
| `src/finch/content/writer.py` | 写稿 | 保留 `write_original`/`write_reply`/`rewrite`（`rewrite_with_instruction` 供 decide revise 用，不动） |
| `tests/graph/test_daily.py`、`tests/unit/test_nodes.py`、`tests/unit/test_cli_run.py` 等 | 测试 | 更新为 7 节点；删 gate/resume/resolve 相关断言 |

---

### Task 1: select 节点（先选后写 + 不阻塞）

**Files:**
- Modify: `src/finch/graph/content_nodes.py`（新增 `make_select_node` + `_topic_sort_key` helper）
- Modify: `src/finch/graph/daily.py`（`daily_nodes` 用 select 替换 define_jobs + position_gate）
- Test: `tests/graph/test_daily.py`、`tests/unit/test_nodes.py`

**Interfaces:**
- Consumes: `plan_content_topics`、`expand_content_job`、`select_planning_evidence`、`TopicProposal`（均 `finch.content.jobs`）；`parse_items`/`items_payload`（`finch.graph.context`）
- Produces: `make_select_node(plan_runner, expand_runner, *, expand_concurrency=4, jobs_repo=None, budget=None, gates=None) -> Node`；节点名 `select`，`writes="ready_jobs"`，`succeeds_to="JOBS_SELECTED"`

- [ ] **Step 1: 写失败测试**

在 `tests/graph/test_daily.py` 追加（复用现有 fake runner/卡片）：

```python
def test_daily_has_seven_nodes_and_no_brief_or_gate():
    nodes = daily_nodes(...)  # 用现有 test_daily 的装配
    names = [n.name for n in nodes]
    assert names == ["preflight", "extract", "collect", "recall", "match", "select", "write"]
```

- [ ] **Step 2: 实现 `_topic_sort_key` 与 `make_select_node`**

`select` 节点逻辑（合并 define_jobs 的规划/预过滤/展开 + position_gate 的选择，去掉阻塞与复用门禁）：

```python
def _topic_sort_key(
    topic: TopicProposal,
    cards_by_id: dict[str, EvidenceCard],
) -> tuple[bool, float]:
    """主题级确定性排序键（先选后写，展开前可用）：有讨论上下文 > 证据置信占比。

    立场/why_now 只在 expand 后可得，故不参与主题级排序（spec §5 B 的务实落地）。
    sort 稳定，id 顺序作为 tie-break。
    """
    ratio = 0.0
    cards = [cards_by_id[cid] for cid in topic.card_ids if cid in cards_by_id]
    if cards:
        ratio = sum(1 for c in cards if c.confidence in _STRONG_CONFIDENCE) / len(cards)
    return (topic.candidate_id is not None, ratio)
```

`make_select_node`：读 `match_results`/`evidence_cards`/`candidates`；无卡短路；`select_planning_evidence` 裁剪 → `plan_content_topics` → 预过滤（沿用 define_jobs 的 candidate/match 校验 + card_ids 越界过滤 + topic.id 去重）→ `sorted(kept_topics, key=_topic_sort_key, reverse=True)` → 只 `expand_content_job` 前 K 个（K = `gates.max_daily_original_posts`，默认 1）→ 校验去重（`validate_source_cards` + job.id 去重）→ upsert（若 jobs_repo）→ 输出 `ready_jobs`（单一份）+ `output["unexpanded_topics"]`（未展开主题标题，调试用）。展开失败递补下一个主题（最多一次）。

- [ ] **Step 3: 改 daily.py**

`daily_nodes` 的 return 列表改为：

```python
    return [
        make_preflight_node(gh, opencli),
        make_extract_node(...),
        make_collect_node(collect_fn),
        make_recall_node(settings.quality_gates),
        make_match_node(_resolve("match_evidence"), settings.quality_gates, settings.twitter),
        make_select_node(
            _resolve("plan_topics"),
            _resolve("expand_job"),
            expand_concurrency=settings.llm.for_node("expand_job").max_concurrency,
            jobs_repo=jobs_repo,
            budget=settings.daily_budget,
            gates=settings.quality_gates,
        ),
        make_write_node(...),  # Task 2
    ]
```

（`make_position_gate_node` 删除；`make_write_node` 在 Task 2 定义前先占位或本任务暂留旧 draft+critique 两节点——建议本任务先只加 select 并保留 draft+critique 以隔离，Task 2 再合并。）

- [ ] **Step 4: 跑测试 + 提交**

Run: `uv run pytest tests/graph/test_daily.py tests/unit/test_nodes.py -v`，然后 `git commit -m "feat(graph): select node (select-then-write, no blocking)"`。

---

### Task 2: write 节点（写稿 + 条件 Critic L0/L1）

**Files:**
- Modify: `src/finch/graph/content_nodes.py`（新增 `make_write_node`，删 `make_draft_node`/`make_critique_node`）
- Modify: `src/finch/graph/daily.py`（write 替换 draft+critique）
- Modify: `src/finch/settings.py`（`max_rewrite_rounds` 默认 1；新增 `llm_critique_mode`）
- Test: `tests/graph/test_content_nodes.py`、`tests/unit/test_checkers.py`

**Interfaces:**
- Consumes: `write_original`/`write_reply`（`finch.content.writer`）、`validate_draft`（`finch.content.claims`）、`default_checker_suite`/`_run_checks`/`aggregate_checks`、`rewrite`
- Produces: `make_write_node(runner, write_reply, write_original, rewrite, gates, *, checkers=None, voice_profile=None) -> Node`；节点名 `write`，`reads=["ready_jobs","evidence_cards","candidates","match_results"]`，`writes="drafts"`，`succeeds_to="DRAFTED"`

- [ ] **Step 1: 实现 L0/L1 分层**

`write` 节点：Phase 1 写稿（沿用旧 `make_draft_node` 的 reply/original 路由与 cap），Phase 2 逐草稿 critic：

```python
mode = gates.llm_critique_mode  # "on_fail_or_gate" | "always"
for draft in written (非 None):
    card_ids = ...  # reply 取 match.card_ids，original 取全部
    l0_violations = validate_draft(draft, card_ids=card_ids)
    if mode == "always" or l0_violations:
        # 跑 L1（8 检查器）+ 定向重写，最多 max_rewrite_rounds 轮
        current = draft
        for i in range(gates.max_rewrite_rounds + 1):
            checks = _run_checks(suite, CheckContext(draft=current, cards=cards, job=job))
            outcome = aggregate_checks(checks)
            if outcome != AggregateOutcome.REWRITE:
                break
            if i == gates.max_rewrite_rounds:
                break
            current = rewrite(runner, current, [c for c in checks if not c.passed], cards_by_id, job)
        if outcome in {AggregateOutcome.PASS, AggregateOutcome.REWRITE} and current is not None:
            kept.append(current)  # pass 或 rewrite 用尽都保留（与旧 critique 一致）
        # reject（hard_fail）丢弃；needs_input 不再停图，改为记 warning 到 reports
    else:
        kept.append(draft)  # L0 全过且非 always：不进 L1
```

- [ ] **Step 2: 改 settings**

`QualityGates`：`max_rewrite_rounds: int = 1`；新增 `llm_critique_mode: Literal["on_fail_or_gate", "always"] = "on_fail_or_gate"`。

- [ ] **Step 3: 改 daily.py**

`make_write_node(_resolve("critique") or runner, write_reply, write_original, rewrite, settings.quality_gates, checkers=default_checker_suite(_resolve("critique"), voice_profile), voice_profile=voice_profile)`。

- [ ] **Step 4: 跑测试 + 提交**

Run: `uv run pytest tests/graph/test_content_nodes.py tests/unit/test_checkers.py -v`；`git commit -m "feat(graph): write node with conditional critic (L0 always, L1 on trigger)"`。

---

### Task 3: brief 出图 + NEEDS_INPUT 消失 + gate 删除

**Files:**
- Modify: `src/finch/graph/content_nodes.py`（删 `make_brief_node` 与 300+ 行 `_render_daily_brief`/`_*_section` 渲染函数）
- Modify: `src/finch/cli.py`（删 `_finish_daily`/`_read_input_request`/`_cards_for`/`_mark_stopped`/`_resume_and_echo`/`_echo_daily_brief` 及其调用；`run_daily` 删 NEEDS_INPUT 分支；`_echo_dual_track_result` 不再打印 brief；`_persist_run_outputs` 保留）
- Delete: `src/finch/gate/`（整个模块：`models.py`/`render.py`/`interactive.py`/`resolve.py`/`__init__.py`）
- Modify: `src/finch/inbox/render.py`（可选：加 `render_daily_summary(items)` 承载「今天 N 条待决定」，从 `next_item` 投影派生）
- Test: `tests/unit/test_cli_run.py`（删 daily NEEDS_INPUT 交互测试）、`tests/unit/test_gate_*`（删）

**Interfaces:**
- `run_daily` 不再有 `GraphState.NEEDS_INPUT` 分支；`--interactive`/`--non-interactive`/`--verbose` flag 与 `_finish_daily` 相关逻辑删除。
- `_persist_run_outputs`/`persist_critique_reports` 保留（write 节点的 reports 仍持久化 DraftVersion/CriticReport）。

- [ ] **Step 1: 删 gate/ 模块 + brief 渲染函数**

`rm -rf src/finch/gate`；删 `content_nodes.py` 里 `make_brief_node` 及其依赖的 `_render_daily_brief`/`_conclusion`/`_primary_section`/`_why_now_section`/`_position_section`/`_evidence_section`/`_risk_section`/`_draft_section`/`_commands_section`/`_not_now_section`/`_funnel_section`/`_pick_primary_draft`/`_job_title`/`_format_position`/`_format_evidence`/`_legacy_warning`/`_collect_draft_warnings` 等（先 grep 确认无他处引用）。

- [ ] **Step 2: 改 cli.py**

删 `_finish_daily`、`_read_input_request`、`_cards_for`、`_mark_stopped`、`_resume_and_echo`、`_echo_daily_brief`、`_latest_needs_input_run_id`（若 Phase 1 未删）；`run_daily` 删 `use_interactive` 计算与 NEEDS_INPUT 的 `_finish_daily` 分支；`_echo_dual_track_result` 删 brief 打印；删 gate 相关 import（`edit_position_inline`/`select_action`/`InputAction`/`InputRequest`/`ProposedPosition`/`render_*`/`resolve_input` 等）。

- [ ] **Step 3: 跑测试 + 提交**

Run: `uv run pytest -q`；删/改 gate/resume/resolve 测试；`git commit -m "refactor(graph): brief out of graph + gate removal (NEEDS_INPUT gone)"`。

---

### Task 4: 全量回归 + 清理

- [ ] **Step 1: 全量回归**

Run:
```bash
uv run pytest -q
uv run ruff check src/finch
uv run mypy src/finch
```
Expected：全绿；`test_daily.py` 断言 7 节点顺序；`tests/graph/test_replay.py` 全绿（验收标准）。

- [ ] **Step 2: 清理死代码 + 提交**

删 `ContentJob.needs_input()`（若已无调用）、`ContentJobStatus.NEEDS_INPUT`（若 daily 不再产生）、`GraphState.NEEDS_INPUT` 的 CLI 使用；`git commit -m "refactor(graph): phase 2 regression + dead-code cleanup"`。

---

## Self-Review 记录

1. **Spec 覆盖**：spec §4.1 select → Task 1；§4.2 write + §7 条件 Critic → Task 2；§4.3 brief 出图 + NEEDS_INPUT 消失 → Task 3；§11 迁移（`llm_critique_mode`/`max_rewrite_rounds`）→ Task 2 Step 2。§9 测试（replay 全绿）→ Task 4。
2. **占位符扫描**：无 TBD/TODO。
3. **类型一致性**：`make_select_node`/`make_write_node` 签名跨任务一致；`_topic_sort_key` 返回值 `tuple[bool, float]` 用于 sorted key。

**范围说明（留 Phase 3）**：`content_nodes.py` 拆分（select/write 各自成文件）、`cli.py` 进一步瘦身、`_STRONG_CONFIDENCE` 等重复常量去重。
