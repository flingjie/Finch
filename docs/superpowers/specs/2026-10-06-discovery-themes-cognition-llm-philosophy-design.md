# 对齐发现主题：新增「个人认知 + LLM 哲学」方向

日期：2026-10-06
状态：已确认（待写实现计划）

## 1. 背景与问题

Finch 的北极星是「每周新增/加深多少条可接续的同行关系」。发现端（`finch connect daily`）靠
`finch.yaml` 里的 `interests` 与 `sources.*.queries` 决定「找谁」。当前配置**只有 agent 工程一个方向**：
`long_term_interests` 清一色 agent reliability / evals / harness / 生产实践，`current_questions` 围绕
生产卡点，各平台查询全部是 agent 可靠性、故障复盘、OCR、文档审校。

但用户 2025 下半年在 X 上真正高频、高共鸣的内容是**「个人认知 + LLM 哲学」**（读书、成长、认知科学，
如「从认知科学重新思考 LLM」「人和 AI 一样，先行动才逐渐对齐世界」）。这个方向是用户的真实智力身份，
却完全不在发现配置里。结果：Finch 只会找到一类 agent 工程师，找不到用户在认知科学 / 学习科学 / 心智
哲学方向真正想对话的同行。这正是「跨行业连接」本该覆盖、现在缺失的一块。

本次改动是**配置层对齐**：只改 `finch.yaml`，不新增领域服务、不碰推荐算法、不新增外发能力。

## 2. 目标与非目标

**目标（本轮）**

- 在发现配置里新增一条与 agent 工程**并列**的「个人认知 + LLM 哲学」方向，两类人每天同时进入候选池。
- 覆盖两类目标人物（用户确认「两者都要」）：
  - **一手实践者**——把认知科学 / 学习科学用于自身做事方式，并分享一手效果与边界。
  - **论述作者**——认知科学家、AI 研究者，围绕 LLM 哲学 / 心智哲学写具体论点、例子或反例。
- 收紧兴趣关键词为「核心身份词」，把具体方法词下沉到搜索层（先扩大搜索覆盖，谨慎扩大档案加分）。

**非目标（YAGNI）**

- 不新增「LLM 是否理解」独立查询（先看 twitter 桥接查询是否已覆盖，留第二轮）。
- 不新增 `excluded_content` 硬排除（`认知升级` / `训练营` 会误伤真实学习记录与课程复盘；现有实现只支持
  子串硬排除，无法区分「招募付费」与「真实学习记录」）。
- 不改 `exploration_topics`（方案 A 是固定查询并列，不走按天轮换）、`github`（固定登录名）、
  `v2ex`（hot 无关键词）、`xiaohongshu`（disabled）、`usage_queries`（死配置）。
- 不把认知问题设为默认发现意图；`current_questions` 首条仍为 agent 工程，agent 方向保持默认入口。
- 不初始化 `voice-profile`、不补 `practice-profile`（本次范围只限发现主题对齐，另案处理）。

## 3. 已核实的代码事实（决定改动边界）

评审前先核实了两个会改变设计的事实，均落在代码里：

1. **排序 boost 是「按兴趣类别命中一次」，不是「按词累加」**（`peers/recommendations.py`）。
   `_rank_score` 只做 `"question" in c.hit_categories` 的布尔判断，`_QUESTION_BOOST = 0.1` 固定加一次；
   `long_term` / `adjacent` / `explore` 命中**不加数值分**，只影响 `direction` 标签
   （peer / adjacent / serendipity）。因此往 `current_questions` 加认知问题不会因词多而放大某个候选的
   boost；agent 问题与认知问题同属 `"question"` 类别，排序权重对等，正是「并列」要的效果。但
   `long_term_interests` 加词会扩大「peer」方向命中面，宽词仍会污染方向标签，故关键词要收窄。

2. **`interests.usage_queries` 是死配置**（注释已标「当前主路径未消费」，代码无引用），本轮不碰。
   真驱动字段是 `sources.*.queries`（每天固定查询）、`interests.long_term_interests /
   current_questions / explore_directions / adjacent_queries`（子串匹配 + 排序 + 默认意图）。

3. **Reddit 查询是纯短语直传**（`reddit/opencli_client.py`：`opencli reddit search <query>`，无引号 /
   布尔解析）。现有 reddit 查询全是平铺短语。因此 reddit 新查询不写引号和 `OR`，保持平铺短语。

## 4. 改动内容（`finch.yaml`）

### 4.1 `interests` 关键词 — 收紧为「核心身份词」

`long_term_interests` 追加 6 词（子串匹配，≥3 字符；去掉方法词 `predictive processing / spaced
repetition / 具身认知` 与弱信号 `mental models / 心智模型 / 个人成长`）：

```yaml
  - cognitive science
  - learning science
  - philosophy of mind
  - 认知科学
  - 学习科学
  - 心智哲学
```

`current_questions` 追加 3 条（「找什么人、聊什么」形态；放在现有 agent 问题之后，默认意图不变）：

```yaml
  - 谁持续公开自己的学习实践，能说清用了什么方法、观察到什么变化，以及哪些地方没有奏效？
  - 谁围绕「LLM 是否理解」提出过明确论点、具体例子或反例，值得进一步交流？
  - 谁把认知科学或学习科学用于真实工作，并分享了我可以复现的小实验？
```

`explore_directions`（探索方向，去重；方法词放这里而非 long_term）：

```yaml
  - predictive processing
  - embodied cognition
  - philosophy of mind
  - machine understanding
  - 认知科学
  - 学习科学
  - 心智哲学
```

`adjacent_queries`（相邻探索，避免与 explore 重复，留出真正的相邻面）：

```yaml
  - metacognition
  - self-regulated learning
  - learning in public
  - 认知心理学
  - 自我调节学习
  - 个人知识管理
```

### 4.2 `sources` 搜索查询 — 首轮只加 4 条（6 → 10）

```yaml
twitter.queries 追加:
  - '("spaced repetition" OR "learning science" OR "deliberate practice") (experiment OR results OR practice)'
  - '("cognitive science" OR "philosophy of mind" OR "predictive processing" OR "embodied cognition") (LLM OR "language models")'

reddit.queries 追加:        # 平铺短语，无引号/布尔
  - "spaced repetition experience results"

weixin.queries 追加:
  - "认知科学 学习 实践"
```

**不动的字段**：`v2ex.queries`（hot 模式）、`github.users`（固定登录名）、`exploration_topics`、
`interests.usage_queries`、`excluded_content`、`sources.*.urls`。

## 5. 行为影响与代价

- 每日固定查询从 6 条（twitter 2 + reddit 2 + weixin 2）增到 10 条，opencli 只读调用量约 1.67 倍。
  实际总调用数与耗时还受分页、缓存、并发、候选补充抓取影响，需首轮实测，不能直接等同。
- 新增认知候选会进入同一 50 人分层池，与 agent 候选在排序上对等竞争（见 §3.1）；是否挤掉有价值的
  agent 候选，列入首轮验证观察项。

## 6. 首轮验证门槛

小样本跑通后，人工看**前 20 位新增候选**，逐项记录：

1. 有几位你确实愿意关注或回复？
2. 有几位提供了具体实践、论点或反例？
3. 有几位只是泛成长内容或课程营销？
4. 新增查询耗时多少，是否挤掉了有价值的 agent 候选？

结果决定第二轮是否：把「LLM 是否理解」拆成独立查询、把反复有效的方法词补进 `long_term_interests`、
或回收无效查询。调用成本与耗时以实测为准。

## 7. 不变量

- 不改 `gh` / `opencli` 只读边界，不新增外发能力，不碰发布门禁。
- 不新增领域服务或编排逻辑；纯 `finch.yaml` 配置变更。
- 不改推荐算法、不改 50 人分层、不改 `opportunity_assess_limit`。
