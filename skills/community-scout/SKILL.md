---
name: community-scout
description: >
  帮用户围绕具体问题发现并持续参与合适的公开社区（GitHub Discussions、Reddit、V2EX、X、公开论坛），
  解释其中实际讨论与可参与空间，用户选择后深入阅读，参与后按真实结果决定继续、观察或离开。
  支持本周探索、围绕问题、指定社区回访三种入口。社区是"关系发生的场"：核心 Builder 进
  peer-discovery/People 流程，社区问题进 idea-discovery。用于「这周有哪些值得参与的社区」
  「哪里适合讨论 X」「Temporal Community 最近值得回去吗」类请求。
---

# community-scout

从公开信息中发现值得进入并持续参与的社区。职责：读取 Finch 已掌握的"当前实践上下文"，从公开来源发现候选社区，
按证据与情境分层（观察 `observe` / 可参与 `actionable`），产出可执行的下一步。社区是场域，不是人：
社区里的核心 Builder 交给 `peer-discovery` 继续深挖，社区里的具体问题交给 `idea-discovery`。

本 Skill 只调用 Finch CLI 与只读数据源，不复制业务逻辑。判断是 LLM 按 `references/scoring-rubric.md`
进行；Python 只负责持久化与反馈记录。不因为成员多或消息多就推荐。

## 输入

先用 `finch community context` 快照当前实践上下文（写入 `var/communities/profile.yaml`）：

- 最近 GitHub commits 和正在解决的问题（`finch context` / `finch github reflect`）
- 已确认的关注主题（`profile.yaml` 的 interests / current_questions）
- Finch 发现的高价值内容与同行（`profile.yaml` 的 active_peers / recent_ideas）
- 用户明确输入的探索方向

## 三种入口

| 用户意图 | 示例 | 首轮交付 |
|---|---|---|
| 本周探索 | "这周有哪些值得参与的社区？" | 最多 3 个不同参与价值的社区，点名首选及原因；合格不足如实给实际数量 |
| 围绕问题 | "哪里适合讨论 FDE-Gym 的失败回放？" | 优先 1 个能回应此问题的当前讨论 + 1 个不同路径的备选；没有现成讨论就提出观察/贡献路径 |
| 指定社区/回访 | "Temporal Community 最近值得回去吗？" | 读历史选择、实际反馈和新公开证据，给继续/观察/暂缓建议 |

当前问题、实践引用、历史互动和可投入时间只在有据可查时使用。用户一句话给出的临时意图只影响本次搜索，
不静默写入长期兴趣。用户没给时间预算时不假定其有时间写代码；默认建议轻量、真实的一步。

> **当前限制（MVP）**：`question` / `revisit` 目前与 `weekly` 走同一趟 loop——唯一真实搜索后端是
> WebFetcher（多源社区发现尚未接入，见 `finch.yaml` 的 `community_scout.search_urls`），`revisit` 暂不
> 自动重找历史社区。反馈回灌（`derive_feedback_facts` 硬门禁）对三种入口都生效。待搜索后端补强后再区分意图。

## 候选分层（而非一次打分淘汰）

1. 公开发现：来源、规范 URL、最近观察时间、可访问范围可记录；来源失败披露覆盖缺口。
2. 可观察候选（`observe`）：有具体可核验证据，但未必有此刻适合参与的帖子；可推荐阅读，不杜撰切入点。
3. 当前可参与（`actionable`）：有仍相关话题，且能说明用户可提问、分享经证实经验或贡献什么。
4. 持续参与：用户确实加入/发言/收到回应/再次互动/贡献；回访优先读真实未完成讨论与承诺。

排序先看问题匹配、可带入材料、具体入口、互动开放度、交流成本；再考虑实践差异与意外发现。
`fit_score` 只作旧卡读取/诊断，不是用户面前的结论。

## CLI

- 快照上下文：`finch community context [--json]`
- 保存一张社区卡：`finch community save --file <card.yaml> [--week 2026-W39]`
- 查看单张卡：`finch community inspect <community_id> [--json]`（`--json` 含反馈历史）
- 记录跟进：`finch community feedback <community_id> --result <...> [--ref <链接>] [--reason-kind <...>] [--note <...>]`
- 列出候选与状态（默认去重）：`finch community list [--week ...] [--all] [--json]`
- 跑一趟发现（有界 loop + 决策记录）：`finch community run --intent weekly|question|revisit [--goal …]`
- 列出历史 run：`finch community runs [--json]`
- 复盘一次 run 的 per-step 决策：`finch community run-trace <run_id> [--json]`

数据采集只读入口（发现阶段用，不是新命令）：

- `gh api graphql`（GitHub Discussions / `search repositories`，只读 GraphQL/REST）
- `finch twitter search`（X）
- `finch sources sync --source reddit --query …` / `--source v2ex`（Reddit / V2EX）
- `WebFetcher`（公开论坛、Discord/Slack 公开主页或公开归档；无 JS 渲染，登录墙 fail-closed）

## 搜索预算（试运行参数）

搜索预算现在可配：`finch.yaml` 的 `community_scout`（max_candidates / inspect_batch / max_cards /
max_reinspect_rounds / suppress_window_weeks / search_urls）。周探索默认 20 → 6 → 3；
问题模式优先 1 + 备选 1。分源失败返回部分结果与缺口，不因一处超时伪造全网结论。
对规范 URL 与作品指纹去重；重试计入预算。

## 向用户呈现

见 `references/presentation.md` 与 `_shared/agent-presentation.md`。
完成后按 `_shared/dialogue-policy.md` 做一次延伸点检查（无有效点就自然结束），延伸不得抢占交付物。

- 默认先给一句明确建议 + 必要公开来源 + 一个下一步，详情按需展开；不固定七字段。
- 推荐必须引用公开证据（`evidence_urls`）；成员数/消息量不是核心排序依据。

## 边界

- 不接入私有 Discord/Slack 消息；Discord/Slack 只处理公开主页、公开归档、公开邀请信息。
- 不自动加入社区、不自动发言；`first_contribution` 只是建议，由用户自己执行。草稿仅用户选中后准备。
- 外部帖/公开讨论 ≠ 个人证据（见 `_shared/evidence-policy.md`）。
- 无可核验公开证据不能作肯定推荐；不把"社区主页可访问"当成"近期讨论可访问"。
- 社区里的"人"不是本 Skill 的产出 → 交给 `peer-discovery`；社区里的"问题" → 交给 `idea-discovery`。
- 跟进结果确定性回灌下一次推荐：`derive_feedback_facts` 只做硬门禁（ignored 在 suppress_window 内排除、
  no_time 永不过滤、已互动进入「继续」框架）与软排序摘要；不自动训练权重。

## 参考

- `references/scoring-rubric.md`
- `references/community-card-schema.md`
- `references/presentation.md`
- `_shared/agent-presentation.md`
- `_shared/evidence-policy.md`
- `_shared/dialogue-policy.md` — 任务后延伸点选择、授权边界与收束