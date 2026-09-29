# community-scout 薄 Loop + 反馈闭环设计

日期：2026-09-29
状态：已确认（待写实现计划）

## 1. 背景与目标

community-scout 目前是「薄持久化 + 散文驱动」：`src/finch/communities/` 只做确定性持久化与反馈记录
（`CommunityProfile` / `CommunityFeedback` / `CommunityContext`，append-only JSONL），判断与流程全在
`skills/community-scout/SKILL.md` 的散文里，由当前对话里的 agent 每次现读现执行。代码里**没有 loop、
没有状态机、没有决策记录**，且反馈只记录不回灌——`SKILL.md` 显式把「反馈→评分闭环」推迟到「验证后」。

本设计给 community-scout 补上两件事：

1. **一个薄 Loop**：有界、确定性的 Python 编排 loop，Codex 只在固定判断点单发调用（不选动作、不碰
   「LLM agent loop」、不恢复 Graph Runtime）。
2. **反馈闭环**：把已记录的 `CommunityFeedback` 确定性回灌到下一次推荐（硬门禁 + 软排序）。

目标产物：`finch community run --intent weekly|question|revisit` 跑一趟发现，产出有公开证据的 ≤3 张
社区卡，并留下可复盘的 per-step 决策记录；反馈能真实影响下一趟的候选选择与框架。

## 2. 范围与非目标

**做：**

- 一个 `CommunityLoop`（Python 编排、有界、固定序动作机）。
- append-only 决策记录（`CommunityRun` + `RunStep`）。
- `derive_feedback_facts` 反馈回灌纯函数（硬门禁 + 软摘要）。
- `search` 复用现有只读适配器（见 §7）。
- 预算参数从 SKILL 散文提升到 `finch.yaml` 配置。

**不做（YAGNI）：**

- 通用动作规划 / 让 LLM 选下一步（这是被禁的「LLM agent loop」）。
- `ask_user`、`find_related_people` 动作（人是 loop 之间的断点；Builder 归 peer-discovery）。
- 学习权重 / 自动调权；新数据库；自动加入/发言/外发。
- 改变现有 `CommunityProfile` / `CommunityFeedback` 的持久化兼容（新字段均可空）。

## 3. 核心设计

### 3.1 组件与文件

| 组件 | 位置 | 职责 |
|---|---|---|
| `RunIntent`（枚举） | `communities/models.py` | `weekly` / `question` / `revisit`（对齐 SKILL 三入口） |
| `ScoutAction`（枚举） | `communities/models.py` | `search` / `inspect` / `propose` / `finish` |
| `ScoutObservation` | `communities/models.py` | 每步动作的结构化观察（候选池 / 已核验候选 + 淘汰理由 / 最终卡） |
| `RunStep` | `communities/models.py` | 决策记录一行：`run_id/action/observation/decision/outcome/at/elapsed_ms/llm_calls` |
| `CommunityRun` | `communities/models.py` | 一次 run 头：`run_id/intent/goal/week/status/预算消耗/最终卡` |
| `FeedbackFacts` | `communities/models.py` | 从 feedback 派生的确定性事实（§3.3） |
| 仓库扩展 | `communities/repository.py` | append-only `runs.jsonl` + `steps.jsonl`（沿用现有 JSONL 风格） |
| `CommunityLoop` | `communities/scout.py`（新） | 有界确定性 loop，注入只读适配器 + Codex runner + 预算 |
| `derive_feedback_facts` | `communities/scout.py`（新） | 纯函数，feedback → 硬门禁 + 软摘要 |
| CLI | `cli.py` | `finch community run` / `runs` / `run <id>`（复盘 trace） |
| 配置 | `settings.py` + `finch.yaml` | `community_scout`：预算 + `suppress_window`（镜像 `quality_gates` 模式） |

### 3.2 动作机与决策记录

- **固定序**：`search → inspect → propose → finish`。**Python 决定下一步**，LLM 只在判断点被调。
- **唯一有界分支**：`inspect` 后若**通过核验的候选为 0**（本批全部被淘汰）且预算未耗尽，允许**再
  `inspect` 下一批一次**（提案的「证据不足，再查一次」），由 config 限 1 轮。注意：有 `observe` 候选但
  无 `actionable` **不触发**重查——那是有据可读的正常结果，不凑数。
- **LLM 只出现在两个判断点**：
  - `inspect`：逐候选判定证据可核验性 / 入口开放性 / 可参与性，结构化输出，淘汰无公开证据或入口的候选。
  - `propose`：写 ≤3 张 `CommunityProfile` 卡（`recommendation_state` + `why_fit` + `evidence_urls` +
    一个下一步）。
  - `search`、`finish` 无 LLM。
- **每步写一条 `RunStep`**，`decision` 记录淘汰/入选理由（如「候选 X 在 inspect 淘汰：`entry_point.url`
  为空」），`outcome` 记录该步产物。因此能复盘「抓取范围太窄 / 判断标准有偏 / 切入话题差」三类问题。

### 3.3 反馈闭环（`derive_feedback_facts`，硬门禁 + 软排序）

纯函数，读 `feedback.jsonl`，按 `identity_key` 取每个社区**最新一条** `result` / `reason_kind` / `at` /
`interaction_ref`：

**硬门禁（Python 强制，可单测）：**

1. `result == ignored` 且距今 `< suppress_window`（默认 4 周）→ 从候选池**排除**。
2. 最新 `result` ∈ `{joined, interacted, repeated, contributed}` → 该社区在 `revisit` 时**强制「继续」
   框架**，`propose` 不得给「首次加入」下一步。
3. `reason_kind == no_time` **永不硬门禁**（当次约束，不永久过滤）。

**软排序（注入 LLM judge 作 observation）：** 每个 identity 的 `最新 result + reason_kind + note` 摘要，
让 LLM 权衡排序与解释（如「上次 `saved` + `deep_but_later`」）。

### 3.4 数据流（一次 run）

```
run(intent, goal)
  → derive_feedback_facts（硬门禁过滤候选池）
  → search：只读适配器收候选（有界），按 identity_key 去重 → 候选池 ≤20（默认预算，可配）
  → inspect：对前 6（默认预算）逐候选 LLM 判定 → 已核验候选 + 淘汰理由
  → [本批通过核验为 0 且预算在 → 再 inspect 下一批一次]
  → propose：LLM 写 ≤3 卡 + state → 最终卡
  → finish：save 卡片、写 CommunityRun + RunStep → 返回 CommunityRun
```

## 4. 数据模型（新增字段均可空，旧数据继续可读）

- `CommunityRun`：`run_id`、`intent`、`goal`、`week`、`status`（`running`/`done`/`failed`/`stopped`）、
  `candidates_found`、`cards_proposed`、`budget_used`、`started_at`、`finished_at`。
- `RunStep`：`run_id`、`action`、`observation`、`decision`、`outcome`、`at`、`elapsed_ms`、`llm_calls`。
- `FeedbackFacts`：`excluded`（被硬门禁排除的 identity 列表 + 理由）、`continue_framing`（回访须用「继续」
  框架的 identity 列表）、`summaries`（{identity: 摘要字符串}，供软排序）。
- `ScoutObservation`：按 `action` 不同而异的字段（`search` → 候选池；`inspect` → 已核验候选 + 淘汰；
  `propose` → 最终卡；`finish` → 空）。

## 5. 错误处理与不变量

- 分源失败 → 返回部分结果 + 披露缺口，不伪造全网结论（沿用 SKILL 边界）。
- 预算耗尽 → 停，`status=stopped` 并记录；LLM 调用失败 → Codex runner 重试后仍失败则
  `status=failed`，trace 保留已写步骤供诊断。
- **不自动加入/发言/外发**；`search` 只读；`finish` 只 `save` 建议卡，用户亲自执行下一步。
- 确定性总量：预算、去重、门禁、状态、trace 全在 Python；LLM 输出不带 `total`、不选动作。

## 6. 测试

- `derive_feedback_facts` 逐条硬门禁单测：`ignored` 4 周内抑制 / 超窗不抑制 / `no_time` 不过滤 /
  `contributed`→继续框架 / `interacted`→回访优先 / 多 feedback 取最新一条。
- `CommunityLoop`：注入 fake runner + fake 只读适配器，断言动作序、trace 完整、预算执行、re-inspect
  分支（本批全部淘汰时触发、有 `observe` 时不触发）。
- 持久化：`runs.jsonl` / `steps.jsonl` 往返；旧卡可读（现有 `test_communities.py` /
  `test_cli_community.py` 不破坏）。
- CLI：`run --json`、`runs`、`run <id>` 输出与错误路径。

## 7. search 动作的真实能力（已核对）

可用只读适配器：

- `finch sources` 统一发现（`src/finch/sources/`：twitter / reddit / github / v2ex / weixin /
  xiaohongshu，经 opencli gateway）。
- `finch twitter search`。
- `RedditOpenCliClient`。
- `WebFetcher`（公开网页、公开论坛、公开 Discord/Slack 主页或归档；无 JS 渲染，登录墙 fail-closed）。
- GitHub commit/PR/issue 读取（`src/finch/github/`，走 `gh`）。

**缺口（须如实披露，不伪造）：** GitHub **Discussions** 在 `src/finch/github/` 里**未实现**
（无 graphql/Discussions 支持）。`search` 遇到 Discussions 需求时，回退到 repo/issue 搜索 +
`WebFetcher` 抓公开讨论页，并在结果里标注覆盖缺口。

## 8. 文件范围

- 新增：`src/finch/communities/scout.py`。
- 修改：`src/finch/communities/models.py`、`repository.py`、`service.py`、`src/finch/cli.py`、
  `src/finch/settings.py`、`finch.yaml`、`skills/community-scout/SKILL.md`（把「试运行参数」改为指向
  config）、`skills/community-scout/references/presentation.md`（run 产物的呈现）。
- 测试：`tests/unit/test_community_scout.py`（新）、`tests/unit/test_cli_community.py`（扩展）。
