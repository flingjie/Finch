# 单一决策点 — Plan 3（must_ask 信号 + Codex 编排）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐单一决策点最后两块：(1) `next --json` 的确定性 `must_ask`/`ask_reasons`/`risks` 信号（`position_conflict` + `safety_risk`）；(2) 把 `$finch` skill 从旧的 `run daily → run resolve → review approve` 多命令编排，重写为 `daily --json → next --json → decide --json` 的单循环编排。

**Architecture:** 信号是纯函数（`gate/render.py`），`next --json` 读 job 的 `change_mind_if` 与 draft 的 critic 报告（safety 检查）填充。Codex 编排只调用 Finch 的三个 `--json` 命令，把结构化状态译成人话、在 `must_ask` 之上叠加 LLM 判断、把自然语言修改转成 `decide --instruction`。

**Tech Stack:** Python 3.12+，Pydantic 2，typer，pytest，ruff（E,F,I,B,UP 行长 100），mypy；skill 为 Markdown。

## Global Constraints

- Python 3.12+；Pydantic 2；ruff `E,F,I,B,UP` 行长 100；mypy 通过。
- 确定性：`must_ask`/`ask_reasons`/`risks` 由确定性规则计算，绝不 LLM 判定；LLM 判断只存在于 Codex 编排层。
- `next --json` 只读，不改变状态。
- Codex 编排只调用 Finch CLI（`finch run daily --json` / `finch next --json` / `finch decide ... --json`），不改数据库、不猜测节点状态。
- 不变量不变：No auto-publish、Evidence first、External ≠ evidence、Deterministic totals。

---

### Task 1: must_ask 信号组装（`build_ask_reasons` / `build_risks`）

**Files:**
- Modify: `src/finch/gate/render.py`
- Modify: `src/finch/cli.py`（`next` 命令填充 `must_ask`/`ask_reasons`/`risks`）
- Test: `tests/unit/test_gate_render.py`、`tests/unit/test_cli_run.py`

**Interfaces:**
- Produces:
  - `build_ask_reasons(job: ContentJob | None, critic_reports: list[dict]) -> list[str]`
  - `build_risks(critic_reports: list[dict]) -> list[str]`

- [ ] **Step 1: 写失败测试**

`tests/unit/test_gate_render.py` 追加：

```python
from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus, IntendedEffect, SuccessCriterion
from finch.content.models import DraftKind
from finch.gate.render import build_ask_reasons, build_risks


def _job_with_change_mind_if(change_mind_if=None):
    return ContentJob(
        id="j1", source_card_ids=["ev1"], reader_problem="r", audience="a",
        intended_effect=IntendedEffect(understand="u"),
        author_position=AuthorPosition(
            claim="c", decision="d", tradeoff="t", change_mind_if=change_mind_if
        ),
        success_criteria=[SuccessCriterion(id="c1", description="d", measurement="critic")],
        recommended_format=DraftKind.ORIGINAL, status=ContentJobStatus.NEEDS_INPUT,
    )


def test_build_ask_reasons_position_conflict():
    reasons = build_ask_reasons(_job_with_change_mind_if("条件改变"), [])
    assert reasons == ["position_conflict"]


def test_build_ask_reasons_safety_risk():
    reports = [{"round": 0, "checks": [{"checker": "safety", "passed": False, "issues": ["risk"]}], "outcome": "rewrite"}]
    assert build_ask_reasons(_job_with_change_mind_if(), reports) == ["safety_risk"]


def test_build_ask_reasons_empty():
    assert build_ask_reasons(_job_with_change_mind_if(), []) == []


def test_build_risks():
    reports = [{"round": 0, "checks": [
        {"checker": "safety", "passed": False, "issues": ["risk"]},
        {"checker": "voice", "passed": True, "issues": []},
    ], "outcome": "rewrite"}]
    assert build_risks(reports) == ["safety: ['risk']"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_gate_render.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_ask_reasons'`。

- [ ] **Step 3: 实现信号函数**

`src/finch/gate/render.py` 顶部 import `ContentJob`，末尾新增：

```python
def build_ask_reasons(job: ContentJob | None, critic_reports: list[dict]) -> list[str]:
    """确定性 must_ask 信号：position_conflict（change_mind_if 非空）+ safety_risk（safety 检查失败）。"""
    reasons: list[str] = []
    if (
        job is not None
        and job.author_position is not None
        and job.author_position.change_mind_if
    ):
        reasons.append("position_conflict")
    if critic_reports:
        for check in critic_reports[-1].get("checks", []):
            if check.get("checker") == "safety" and not check.get("passed", True):
                reasons.append("safety_risk")
                break
    return reasons


def build_risks(critic_reports: list[dict]) -> list[str]:
    """返回最后一轮 Critic 的失败检查项（checker: issues）。"""
    if not critic_reports:
        return []
    return [
        f"{c['checker']}: {c.get('issues') or 'failed'}"
        for c in critic_reports[-1].get("checks", [])
        if not c.get("passed", True)
    ]
```

- [ ] **Step 4: 接进 `next --json`**

`src/finch/cli.py` 的 `next` 命令，在组装 card payload 时，读 critic 报告并填信号：

```python
    critic_reports = CriticReportRepository(store).list_reports(draft.id)
    ask_reasons = build_ask_reasons(job, critic_reports)
    ...
        "must_ask": bool(ask_reasons),
        "ask_reasons": ask_reasons,
        "risks": build_risks(critic_reports),
```

（`build_ask_reasons`/`build_risks` 从 `.gate.render` import；`CriticReportRepository` 从 `.storage.repositories` import——均已在 cli.py 其他命令可用。）

- [ ] **Step 5: 更新 `next` 测试断言**

`tests/unit/test_cli_run.py::test_next_json_returns_card` 已断言 `"must_ask": []`；保持通过即可（`_job` 无 change_mind_if、无 critic 报告 → 空）。如需覆盖非空，可另加一例（可选）。

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_gate_render.py tests/unit/test_cli_run.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/finch/gate/render.py src/finch/cli.py tests/unit/test_gate_render.py tests/unit/test_cli_run.py
git commit -m "feat(gate): must_ask signal assembly in next --json"
```

---

### Task 2: 重写 `$finch` skill（Codex 编排循环）

**Files:**
- Modify: `skills/finch/SKILL.md`

- [ ] **Step 1: 重写 SKILL.md**

用以下内容替换 `skills/finch/SKILL.md`（保留 frontmatter `name`/`description`，重写「模式」表与「错误恢复」节，新增「每日编排循环」节；「执行环境」「强制规则」「质量门禁」「参考」保持不变）：

```markdown
---
name: finch
description: Evidence-driven builder companion. Use $finch when the user asks to run the daily graph, decide on candidate drafts (accept/revise/skip), reflect on GitHub engineering changes, manage the author voice profile, review engagement candidates, or diagnose the gh/opencli environment.
---

# Finch

Finch 是一个证据驱动的 Builder 伙伴。它通过 `gh` 只读读取 GitHub，通过 `opencli` 只读搜索/读取 Twitter/X，把工程实践与公共技术讨论匹配，生成必须经人工「采用」才可用的候选草稿。

Finch 的每一步都落地为 Finch CLI（`finch ...`）。**Skill 只调用 Finch CLI，不复制业务逻辑、不直接改数据库、不猜测节点状态。**

## 执行环境

（保留原文：真实网络需主机环境，纯本地只读命令可沙盒。）

## 每日编排循环（$finch daily）

用户说 `$finch daily` 时，Codex 依次：

1. `finch run daily --json`（主机环境）→ `{"run_id","status","n_review","n_engagement_drafts"}`。
2. 若 `status == "completed"`：告知「今天没有需要决策的草稿」，结束。
3. 循环：
   a. `finch next --json` → 决策卡或 `{"status":"none"}`。
   b. 若 `none`：告知「全部决策完成」，结束。
   c. 把决策卡译成人话展示：主题、为什么值得说、可能的立场（主张/方案/取舍）、证据、草稿正文。
   d. 若 `must_ask` 非空（`position_conflict`/`safety_risk`）或 Codex 判断需要，先向用户提问关键判断；否则直接给出「采用/修改/跳过」选项。
   e. 把用户回复映射到一次决策：
      - 采用 → `finch decide <job-id> --action accept --json`
      - 修改 → `finch decide <job-id> --action revise --instruction "<用户原话>" --json`（展示新正文 + diff，回到步骤 e 让用户决定采用/继续改/跳过）
      - 跳过 → `finch decide <job-id> --action skip --reason not_now --json`
   f. 回到步骤 a。
4. 全部采用/跳过完成后，提醒：发布仍由用户在 Finch 外部手动完成（Finch 不自动发布）。

## 模式

| 模式 | 行为 | CLI |
|---|---|---|
| `$finch daily` | 运行每日 Graph + 决策循环（采用/修改/跳过） | `finch run daily --json` → `finch next --json` → `finch decide ... --json` |
| `$finch reflect` | 只用 `gh` 分析某仓库的工程变化 | `finch github reflect` |
| `$finch voice` | 管理作者声音画像（本地） | `finch voice show` / `approve-example` / `reject-example` |
| `$finch engagement` | 审核互动候选（人工批准后才执行） | `finch engagement list` / `show` / `approve` / `reject` / `edit` / `metrics` |
| `$finch weekly` | 汇总最近 7 天批准率、修改/跳过原因、效果指标 | `finch run weekly` |
| `$finch diagnose` | 检查 gh/opencli/认证/schema | `finch diagnose` |

调试接口（高级用户/脚本，不进入正常交互）：`finch jobs ...`、`finch run resume`、`finch run resolve`、`finch review ...`。

## 强制规则（不可违反）

（保留原文全部 8 条；在「不自动发布」条下补充：候选草稿在用户「采用」前绝不视为已发布或已确认立场。）

## 错误恢复

- 环境异常：`finch diagnose`；不自动安装/改浏览器/代填凭据。
- `finch decide --action revise` 返回结构化错误 JSON（`{"status":"error","message":...}`）→ 把错误译成人话，让用户重试或改用其它动作；不要自行重跑 rewrite。
- Graph 停在 `BLOCKED`/`FAILED`：`finch diagnose` 排查；不改质量门禁数值。
- 发布后用户手动填链接与互动数据：`finch review feedback <DRAFT_ID> --url <URL> --metrics '<json>'`。

## 质量门禁（来自 finch.yaml，不要硬编码更改）

（保留原文 YAML。）

## 参考

（保留原文参考列表。）
```

- [ ] **Step 2: 自查**

确认重写后的 SKILL.md：
- frontmatter 的 `name: finch` 不变；
- 「每日编排循环」只调用三个 `--json` 命令，不出现 `run_id`/`job_id` 的内部概念暴露给用户；
- 「调试接口」明确降级 `jobs`/`run resume`/`run resolve`/`review`；
- 「强制规则」「质量门禁」「参考」内容未删减。

- [ ] **Step 3: 提交**

```bash
git add skills/finch/SKILL.md
git commit -m "docs(skill): rewrite $finch daily orchestration around next/decide --json"
```

---

### Task 3: 全量回归

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -q`
Expected: PASS。

- [ ] **Step 2: lint + 类型**

Run: `uv run ruff check . && uv run mypy src`
Expected: 无错误。

- [ ] **Step 3: 提交（如有修正）**

```bash
git add -A && git commit -m "chore: regression fixes for single decision point Plan 3"
```

---

## Self-Review 结果

- **Spec 覆盖**：§3.2 `position_conflict`/`safety_risk` 信号 → Task 1；§7 Codex 编排（`$finch daily` 循环）→ Task 2；§5 职责边界（Codex 只调 CLI、不改 DB）→ Task 2 的编排循环与强制规则。
- **Placeholder 扫描**：无 TBD/TODO；SKILL.md 中以「保留原文」标注的节需保留现有内容（非占位）。
- **类型一致性**：`build_ask_reasons(job, critic_reports)`/`build_risks(critic_reports)` 在 Task 1 定义并在 `next` 调用处一致。
