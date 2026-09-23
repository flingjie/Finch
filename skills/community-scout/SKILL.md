---
name: community-scout
description: >
  每周从公开信息中找到 3 个与用户当前实践高度相关的小型活跃社区（GitHub Discussions、Reddit、V2EX、X、
  公开论坛），并给出可执行的"进入第一步"（具体讨论、建议角度、首次贡献、成本与风险）。社区是"关系发生的场"：
  核心 Builder 进 peer-discovery/People 流程，社区问题进 idea-discovery。输出每周 3 张"社区行动卡"，
  每张回答：这是什么社区 / 为什么现在适合 / 近期公开证据 / 值得关注的核心 Builder / 可切入的问题 /
  能贡献什么 / 参与成本与风险。用于「这周有哪些值得参与的社区」「帮我找出这周最值得参与的 3 个社区」
  「找一个能讨论 Agent 可靠性的社区」类请求。
---

# community-scout

从公开信息中发现值得长期出现的社区。职责：读取 Finch 已掌握的"当前实践上下文"，从公开来源发现候选社区，
按四维适配评分过滤出 **每周 3 个**最值得进入的社区，产出可执行的第一步。社区是场域，不是人：
社区里的核心 Builder 交给 `peer-discovery` 继续深挖，社区里的具体问题交给 `idea-discovery`。

本 Skill 只调用 Finch CLI 与只读数据源，不复制业务逻辑。评分是 LLM 按 `references/scoring-rubric.md`
的判断，不在 Python 里；Python 只负责持久化（`finch community`）与反馈记录。不因为成员多或消息多就推荐。

## 输入

先用 `finch community discover` 快照当前实践上下文（写入 `var/communities/profile.yaml`）：

- 最近 GitHub commits 和正在解决的问题（`finch context` / `finch github reflect`）
- 已确认的关注主题（`profile.yaml` 的 interests / current_questions）
- Finch 发现的高价值内容与同行（`profile.yaml` 的 active_peers / recent_ideas）
- 用户明确输入的探索方向

## CLI

- 快照上下文 + 看本周周报：`finch community discover [--week 2026-W39]`
- 保存一张社区卡：`finch community save --file <card.yaml> [--week 2026-W39]`
- 查看单张卡：`finch community inspect <community_id>`
- 记录跟进：`finch community feedback <community_id> --result <ignored|saved|joined|interacted|repeated|contributed>`
- 列出候选与状态：`finch community list [--week 2026-W39]`

数据采集只读入口（发现阶段用，不是新命令）：

- `gh api graphql`（GitHub Discussions / `search repositories`，只读 GraphQL/REST）
- `finch twitter search`（X）
- `finch sources sync --source reddit --query …` / `--source v2ex`（Reddit / V2EX）
- `WebFetcher`（公开论坛、Discord/Slack 公开主页或公开归档；无 JS 渲染，登录墙 fail-closed）

## 产出契约

- `CommunityProfile`（见 `references/community-card-schema.md`）：id、name、platforms、fit_score、
  why_fit、recent_evidence[]、people[]、entry_point{discussion,suggested_angle}、
  first_contribution{type,proposal}、risks[]、evidence_urls[]、week

## 向用户呈现

见 `references/presentation.md` 与 `_shared/agent-presentation.md`。
完成后按 `_shared/dialogue-policy.md` 做一次延伸点检查（无有效点就自然结束），延伸不得抢占交付物。

- 每周输出 **3 张社区行动卡**，少而深，不做信息流；没有合格社区就如实说"本轮无推荐"，不凑数。
- 每张卡回答七个问题（见 `references/presentation.md`）。
- 推荐必须引用公开证据（`evidence_urls`）；成员数/消息量不是核心排序依据。

## 边界

- 不接入私有 Discord/Slack 消息；Discord/Slack 只处理公开主页、公开归档、公开邀请信息。
- 不自动加入社区、不自动发言；`first_contribution` 只是建议，由用户自己执行。
- 外部帖/公开讨论 ≠ 个人证据（见 `_shared/evidence-policy.md`）。
- 评分是判断不是代码；本轮不把权重写进 Python（验证 4 周后再代码化）。
- 不因为成员多、消息多就当作"活跃"或"质量"。
- 社区里的"人"不是本 Skill 的产出 → 交给 `peer-discovery`；社区里的"问题" → 交给 `idea-discovery`。
- 跟进结果只记录，不自动改变下次评分（反馈→评分闭环留待验证后）。

## 参考

- `references/scoring-rubric.md`
- `references/community-card-schema.md`
- `references/presentation.md`
- `_shared/agent-presentation.md`
- `_shared/evidence-policy.md`
- `_shared/dialogue-policy.md` — 任务后延伸点选择、授权边界与收束
