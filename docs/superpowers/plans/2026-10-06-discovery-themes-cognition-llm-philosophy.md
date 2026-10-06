# 对齐发现主题：新增「个人认知 + LLM 哲学」方向 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `finch.yaml` 里新增一条与 agent 工程并列的「个人认知 + LLM 哲学」发现方向，让每日发现同时覆盖两类人（一手实践者 + 论述作者）。

**Architecture:** 纯配置变更，只改 gitignored 的本地 `finch.yaml`（`interests` 关键词 + `sources.*.queries`）。不新增代码、领域服务或算法。排序 boost 是「按兴趣类别命中一次」（`_QUESTION_BOOST=0.1`），不是按词累加，所以并列两个方向在排序上对等。

**Tech Stack:** YAML 配置；Pydantic 2 `Settings` 加载校验；pytest 回归；`build_discovery_plan` 只读投影验证。

## Global Constraints

- `finch.yaml` 在 `.gitignore` 中被忽略（本地配置，含凭据/仓库清单）；**本计划不产生 git commit**。变更记录以已提交的 spec 文档 `docs/superpowers/specs/2026-10-06-discovery-themes-cognition-llm-philosophy-design.md` 为准。
- 只改 `finch.yaml`，不碰 `finch.example.yaml`（模板已与真实配置严重分叉，同步它属独立决策，见末尾）。
- 不新增 `excluded_content`、不改 `exploration_topics`/`github`/`v2ex`/`xiaohongshu`/`usage_queries`。
- `interests` 关键词须 ≥3 字符（`match_interests` 跳过 `<3` 字符的词）。
- 查询语法按适配器能力：twitter 支持引号 OR 组 + 圆括号过滤；reddit 是**纯短语直传**（不写引号/布尔）；weixin 是平铺关键词。
- 全部命令从仓库根 `/Users/lingjiefan/underway/Finch` 运行。

---

### Task 1: `interests` 关键词（核心身份词 + 问题 + 探索/相邻方向）

**Files:**
- Modify: `finch.yaml`（`interests.long_term_interests` / `current_questions` / `explore_directions` / `adjacent_queries` 四个段）

**Interfaces:**
- Consumes: 无（直接改现有配置文件）。
- Produces: 更长的四个 list，供 `candidate_pool.match_interests` 命中标签 + `recommendations._direction_for` 分类 + `query_plan.build_discovery_plan` 默认意图使用。

- [ ] **Step 1: `long_term_interests` 追加 6 词**

把当前这段：

```yaml
  long_term_interests:
    - agent reliability
    - agent evals
    - agent harness
    - production agents
    - 可靠性
    - 评测
    - 生产实践
```

改为（在末尾追加 6 词，其余不动）：

```yaml
  long_term_interests:
    - agent reliability
    - agent evals
    - agent harness
    - production agents
    - 可靠性
    - 评测
    - 生产实践
    - cognitive science
    - learning science
    - philosophy of mind
    - 认知科学
    - 学习科学
    - 心智哲学
```

- [ ] **Step 2: `current_questions` 追加 3 条**（保持 agent 首条不变，认知问题放后面）

把当前这段：

```yaml
  current_questions:
    - 谁解决过与我相似的真实卡点，有什么证据与可借鉴的做法
    - 如何把生产失败变成可复现的回归测试
    - 如何减少文档审校误报，同时保住严重错误召回率
    - Skill 流程在什么条件下值得代码化
    - 如何通过一个小实验判断产品是否解决真实问题
```

改为：

```yaml
  current_questions:
    - 谁解决过与我相似的真实卡点，有什么证据与可借鉴的做法
    - 如何把生产失败变成可复现的回归测试
    - 如何减少文档审校误报，同时保住严重错误召回率
    - Skill 流程在什么条件下值得代码化
    - 如何通过一个小实验判断产品是否解决真实问题
    - 谁持续公开自己的学习实践，能说清用了什么方法、观察到什么变化，以及哪些地方没有奏效？
    - 谁围绕「LLM 是否理解」提出过明确论点、具体例子或反例，值得进一步交流？
    - 谁把认知科学或学习科学用于真实工作，并分享了我可以复现的小实验？
```

- [ ] **Step 3: `explore_directions` 追加 7 词**

把当前这段：

```yaml
  explore_directions:
    - durable execution
    - failure replay
    - agent observability
    - human-in-the-loop
    - trajectory evaluation
    - 故障恢复
    - 人工接管
```

改为：

```yaml
  explore_directions:
    - durable execution
    - failure replay
    - agent observability
    - human-in-the-loop
    - trajectory evaluation
    - 故障恢复
    - 人工接管
    - predictive processing
    - embodied cognition
    - philosophy of mind
    - machine understanding
    - 认知科学
    - 学习科学
    - 心智哲学
```

- [ ] **Step 4: `adjacent_queries` 追加 6 词**

把当前这段（`工作流` 是最后一行）：

```yaml
    - 故障复盘
    - 产品验证
    - 用户反馈
    - 文档解析
    - 出版审校
    - 工作流
```

改为：

```yaml
    - 故障复盘
    - 产品验证
    - 用户反馈
    - 文档解析
    - 出版审校
    - 工作流
    - metacognition
    - self-regulated learning
    - learning in public
    - 认知心理学
    - 自我调节学习
    - 个人知识管理
```

- [ ] **Step 5: 验证配置可加载 + 字段长度正确**

Run:

```bash
uv run python -c "from finch.settings import load_settings; s=load_settings(); print(len(s.interests.long_term_interests), len(s.interests.current_questions), len(s.interests.explore_directions), len(s.interests.adjacent_queries))"
```

Expected: 打印 `13 8 14 20`（分别是 7+6、5+3、7+7、14+6）。若抛 `pydantic.ValidationError` 或打印数字不符，检查 YAML 缩进/引号。

- [ ] **Step 6: 跑 interest 命中 smoke check**（确认新词能被 `match_interests` 命中）

Run:

```bash
uv run python -c "
from finch.settings import load_settings
from finch.discovery.candidate_pool import match_interests
from finch.peers.models import PeerProfile
p = PeerProfile(id='smoke', platform='x', display_name='Cognitive Scientist', current_interests=['cognitive science', 'philosophy of mind'])
s = load_settings()
print(sorted({h.label for h in match_interests(p, settings=s)}))
"
```

Expected: 输出含 `cognitive science` 与 `philosophy of mind`。

- [ ] **Step 7: 跑全量回归**

Run: `uv run pytest -q`
Expected: 全部通过（测试不读 `finch.yaml`，改用 `finch.example.yaml` 或内存对象，故不受本改动影响）。

---

### Task 2: `sources` 搜索查询（首轮 4 条，6 → 10）

**Files:**
- Modify: `finch.yaml`（`sources.twitter.queries` / `sources.reddit.queries` / `sources.weixin.queries`）

**Interfaces:**
- Consumes: Task 1 已完成的 `finch.yaml` 其余字段不变。
- Produces: 各源 `queries` list，经 `query_plan._payload` → `build_discovery_plan.source_queries` → `opencli` 只读搜索。

- [ ] **Step 1: `twitter.queries` 追加 2 条**

把当前这段：

```yaml
    queries:
      - '("agent reliability" OR "agent evals") (failure OR production OR lessons)'
      - '("agent harness" OR "tool calling") (shipped OR timeout OR incident)'
    urls: []
```

改为：

```yaml
    queries:
      - '("agent reliability" OR "agent evals") (failure OR production OR lessons)'
      - '("agent harness" OR "tool calling") (shipped OR timeout OR incident)'
      - '("spaced repetition" OR "learning science" OR "deliberate practice") (experiment OR results OR practice)'
      - '("cognitive science" OR "philosophy of mind" OR "predictive processing" OR "embodied cognition") (LLM OR "language models")'
    urls: []
```

- [ ] **Step 2: `reddit.queries` 追加 1 条（平铺短语，无引号/布尔）**

把当前这段：

```yaml
    queries:
      - "LLM agent production failure lessons"
      - "agent evaluation tool timeout workaround"
    urls: []
```

改为：

```yaml
    queries:
      - "LLM agent production failure lessons"
      - "agent evaluation tool timeout workaround"
      - "spaced repetition experience results"
    urls: []
```

- [ ] **Step 3: `weixin.queries` 追加 1 条**

把当前这段：

```yaml
    queries:
      - "大模型应用 失败复盘"
      - "AI 落地 实践 评估"
    urls: []
```

改为：

```yaml
    queries:
      - "大模型应用 失败复盘"
      - "AI 落地 实践 评估"
      - "认知科学 学习 实践"
    urls: []
```

- [ ] **Step 4: 验证查询计划包含 10 条源查询**

Run:

```bash
uv run python -c "from finch.settings import load_settings; from finch.sources.query_plan import build_discovery_plan; p=build_discovery_plan(load_settings()); print(p.source_queries['twitter']); print(p.source_queries['reddit']); print(p.source_queries['weixin'])"
```

Expected: 三段各打印 4 / 3 / 3 条查询，twitter 含 `("spaced repetition" ...)` 与 `("cognitive science" ...)` 两条新查询；总数 4+3+3=10。

- [ ] **Step 5: 跑全量回归 + 类型检查**

Run:

```bash
uv run pytest -q && uv run mypy src
```

Expected: pytest 全部通过；mypy 无新错误（本改动不触碰 Python 源码）。

---

## 验证 / 手尾

- 首轮人工验证（不阻塞实现）：跑一次 `finch connect daily` 后，人工看前 20 位新增候选，记录愿关注/回复数、有具体实践/论点/反例数、泛成长/课程营销数、新增耗时，以及是否挤掉有价值的 agent 候选。结果决定第二轮是否拆分「LLM 是否理解」查询、是否把反复有效的方法词补进 `long_term_interests`。
- **是否同步 `finch.example.yaml`**：本计划默认不动它。若你希望模板也反映新方向，可作为独立小改（改 `finch.example.yaml` 的 `interests` + `sources` 两段，风格对齐现有示例），需你确认后再做——模板已与真实配置分叉，单同步 interests/queries 会留下其余 staleness。
