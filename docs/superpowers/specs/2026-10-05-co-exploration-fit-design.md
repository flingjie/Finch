# Co-Exploration Fit：让推荐以「共同问题」为核心

日期：2026-10-05
状态：已确认（待写实现计划）

## 1. 背景与问题

Finch 的北极星是「每周新增/加深多少条可接续的同行关系」。当前推荐按**对方这个人好不好**
排序——`PersonScoreBreakdown` 六项（sustained_creation / first_hand / sharing_willingness /
cross_domain / joint_practice / connection_opportunity）全是对方的质量信号，没有一项回答
「你俩是否在解决同一个具体问题」。共同问题只在中选**之后**（`Opportunity.problem` / `fit`）才
呈现，不决定谁排前面。

与此同时，上一轮 learning-loop 已经建了 `ActiveProblem`（≤3 个 open 的活跃问题，有生命周期、
可回链 PracticeAttempt），但它目前是「孤立」实体：只有 CLI 和 PracticeAttempt 回链，**还没接进
发现/机会评估**——而「我正在解决什么」恰恰是判断「这段内容是否值得现在聊」的第一信号。

本轮把两件事接起来：

1. **共同问题进机会评估**：把 `ActiveProblem` 注入 `assess_opportunity`，让 `fit` 能锚到活跃问题，
   `recommend` 门优先「能推进当前问题」的机会。（即 learning-loop spec §7.1，当时只写不实施。）
2. **三问统一输出**：把「他解决什么 / 与你什么相关 / 你能贡献什么」做成一个确定性渲染函数，
   让每次推荐都回答这三问。

## 2. 目标与非目标

**目标**

- `ActiveProblem` 成为机会评估的「我正在解决什么」结构化输入（≤3 open，`problem_refs` 可追溯）。
- 每次推荐（首选机会）统一呈现三问，回退到旧自由文本字段，不丢信息。
- 机会评估 `recommend` 门新增 problem-advance 信号：能推进活跃问题的内容 fit 更强。

**非目标（YAGNI）**

- 不改确定性 50 人分层（`PersonScoreBreakdown` / `candidate_pool`）——本轮「共同问题」只落 LLM
  机会评估，不做文本匹配重排。
- 不做反馈回路 #5（新视角/是否尝试/是否继续/双方贡献的标记与推荐调整）——另开一轮。
- 不动 `interests.current_questions`（继续作为 `user_context` 的旧输入，与 `ActiveProblem` 并存）。
- 不新增共同实践的机制（`NextAction`/`Artifact`/`MiniExperiment` 已覆盖）。

## 3. 数据模型

仅一处字段新增（`src/finch/opportunities/models.py`）：

```python
class Fit(BaseModel):
    """为什么与用户有关：reason + 已确认实践引用 + 活跃问题引用。"""
    reason: str
    practice_refs: list[str] = Field(default_factory=list)
    problem_refs: list[str] = Field(default_factory=list)   # 新增：活跃问题 id（如 problem_xxx）
```

`problem_refs` 与 `practice_refs` 同范式：结构化回链、可追溯，由 LLM 在 `fit` 中输出、
代码不生成总分。旧 YAML 无此字段 → 空列表，向后兼容。

## 4. 接入点

### 4.1 `render_active_problems`（新 `src/finch/problems/render.py`）

镜像 `profile/render.py::render_user_practices`：

```python
def render_active_problems(problems: list[ActiveProblem] | None) -> str:
    """渲染 open 的活跃问题；空/None 返回 '(none)'。"""
```

格式：`- [problem_xxx] 标题 — 为什么值得追`（只渲染 `status == "open"`）。

### 4.2 机会评估 `opportunities/assess.py`

- `assess_opportunity(..., active_problems: str = "")` 新增参数，默认 `"(none)"`。
- `prompts/opportunity.md` 在 `## User context` 后新增 `## User active problems` 块，注入
  `{active_problems}`；新增第 4 条信号 **problem-advance**：
  「这条内容能解释 / 挑战 / 验证某个活跃问题 → fit 更强，优于泛泛主题相关」。
- `fit` 字段指令新增：命中活跃问题时把其 id 写进 `problem_refs`（像 `practice_refs` 一样）。

### 4.3 接线（`discover.py` / `daily.py` / `from_url.py`）

- `discover_preferred_opportunity_outcome` 与 `discover_preferred_opportunity` 增加
  `active_problems: str = ""`，透传 `assess_opportunity`。
- `daily.py::run_daily_discovery` 加载 `ProblemRepository(ws).list_all()`，渲染 open 问题并传入。
- `from_url.py::assess_from_url` 同样透传。
- **skip 指纹**（`discover.py` 里 `parts = [person_ref, current_work, why_relevant, user_context]`）
  追加 `active_problems`，使注入活跃问题后旧 SKIP 缓存自然失效（与 `user_practices` 同处理）。

### 4.4 三问输出（新 `src/finch/opportunities/render.py`）

```python
def render_opportunity_questions(opp: Opportunity) -> str:
    """把机会渲染成三问块：他解决什么 / 与你什么相关 / 你能贡献什么。"""
```

映射与回退：

| 问 | 优先字段 | 回退 |
|---|---|---|
| 他解决什么 | `problem.statement`（标 `evidence_status`：author_stated/inferred） | `topic` |
| 与你什么相关 | `fit.reason` + `practice_refs` / `problem_refs` | `why_me` |
| 你能贡献什么 | `proposal.contribution` + `form` + `expected_output` | `(待准备)` |

输出形如：

```
他解决什么：工具超时后误报成功（作者明说）
与你有关：你在 Agent-100-Days 里也踩过（实践 [agent-100-days]；问题 [problem_重试]）
你能贡献：一张「错误分类后重试」的方法卡（method_card，产出可对比的决策表）
```

纯呈现层，不改字段语义。确定性、可单测回退逻辑。

### 4.5 呈现接线

- `finch connect daily` 首选机会展示调用 `render_opportunity_questions`。
- `skills/interaction-preparation` / `reply-crafting` 的呈现（`_shared/agent-presentation.md`）
  复用同一函数，保证 CLI 与 skill 三问一致。

## 5. 测试

- `tests/unit/test_problems_render.py`：`render_active_problems` 只渲染 open、空/None → `(none)`。
- `tests/unit/test_opportunity_assess.py` 扩展：有活跃问题时 prompt 含 problem 块与 id；空时与改动前一致。
- `tests/unit/test_opportunity_render.py`：`render_opportunity_questions` 三问映射 + 各字段 None 时的回退。
- `tests/unit/test_opportunity_fit.py`：`Fit.problem_refs` 默认空列表、旧 YAML 无字段可加载。
- prompt contract test：`opportunity.md` 占位符与 `.format()` 参数集合一致。
- `uv run pytest` / `uv run ruff check .` / `uv run mypy src` 全绿。

## 6. 验收

1. `finch problems add` 建 ≥1 个 open 问题后，`finch connect daily` 的机会评估 prompt 含
   `## User active problems` 块与问题 id。
2. 命中活跃问题时，`Opportunity.fit.problem_refs` 含对应 id；`finch connect daily` 展示三问块。
3. 无活跃问题 / 无 problem/fit 字段时，三问回退到 `topic`/`why_me`/`(待准备)`，不崩、不丢信息。

## 7. 后续（不在本 spec）

- 反馈回路 #5：新视角/是否尝试/是否继续/双方贡献的标记路径 + 据此调整推荐。
- 确定性 50 人分层的 problem-match 粗筛（若两周观察发现 LLM 评估预算不足）。
