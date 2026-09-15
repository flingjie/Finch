---
name: interaction-preparation
description: >
  基于具体同行、帖子和用户证据生成互动建议（回复 / 引用 / 私信 / 最小贡献）。输入
  PeerProfile 与选中的 Opportunity，产出待批准的 InteractionProposal（默认提纲，完整草稿按需）。
  用于「帮我给这个人准备一次有价值的互动」「先给一个二十分钟内能做的测试」类请求。
---

# interaction-preparation

为用户**选中**的机会准备一次有价值的互动建议。职责：把「值得交流的人 + 一条内容」转成
待批准的 `InteractionProposal`（含 `contribution_type`、`why_this_person`、`why_now`、
`expected_conversation_opening`；**默认 outline**，完整 `draft` 仅在用户要求或 `--draft` 时）。

本 Skill 只调用 Finch CLI（`finch connect prepare` / `finch connect create` /
`finch connect approve` / `finch connect reject` / `finch connect edit` / `record`），
不复制业务逻辑、不直接改数据库。动作选择由代码确定性决定；提纲与关系字段由语义判断生成。

## CLI

- 选中机会：`finch connect prepare --opportunity <id>`（可重复；本批有上限）
- 手动帖子 + 笔记：`finch connect create --input <url> [--from-idea <id>|--note "..."] [--draft]`
- 批准 / 拒绝 / 改草稿：`approve` / `reject` / `edit`
- 用户已发送后登记：`finch connect record <proposal_id> --url <url>`（需先批准）

## 产出契约（InteractionProposal）

- 默认展示：对方具体问题、我能补充什么、证据 refs、2–3 条提纲、一个下一步。
- `outline` / `value_added` / `source_summary` / `factual_risks`：提纲路径。
- `draft`：仅按需；试用/复现/观察类可为内部清单而无公开回复。
- `contribution_type`：experience / question / addition / counterexample / resource。
- **允许零结果**：无贡献点时输出「暂不回复」及原因，不强行生成空泛回复。

## 向用户呈现

见 `_shared/agent-presentation.md` 与 `references/presentation.md`。本 Skill 做编辑式推荐，不贴 CLI 原文。

读完 CLI 后：点名最值得先做的一条 + **提纲**或最小动作预览 → 最多展开 3 条 →
「批准 1 / 要完整草稿 / 改提纲 1 / 跳过 / 换一批」。内部保留序号 → `proposal_id`。

区分三种结果（复用 `suggested_mode` / `ContributionType`，不平行造枚举）：

- **可直接交流**：已有真实经验，或能提出结合对方项目的具体问题。
- **先做准备**：先试用、读代码、复现或整理一个案例（不算已完成）。
- **暂时观察 / 暂不回复**：相关但时机不足，或缺少有效增量。

## 边界

- 批准只创建发布意图，不等于已经发布；真正发送前经人工 + `guard.evaluate_execution`。
- 外部帖子只是信号，不是个人证据；提纲/草稿只允许提问或明确标注推测，禁止虚构「我测试过」/
  「我也遇到过」；仅 `evidence_status=observed` 时可声称亲历。
- 修改正文后旧批准失效（`revision` 递增）。
- 建议试用、批准草稿但未行动：实验完成数与已发送数不增加。
- 一条建议一个下一步；邀请 ≠ 已发送。

## 参考

- `references/interaction-contract.md` — 贡献类型与草稿约束。
- `_shared/agent-presentation.md` — 调用 CLI 之后如何对用户说话（共享原则）。
- `references/presentation.md` — 本 Skill 的形状与「批准 / 改草稿 / 跳过」映射。
- `_shared/evidence-policy.md` — 回复提纲 vs 原创草稿的证据要求。
