---
name: interaction-preparation
description: >
  基于具体同行、帖子和用户证据生成互动建议（回复 / 引用 / 私信 / 最小贡献）。输入
  PeerProfile 与选中的 Opportunity，产出待批准的 InteractionProposal（含草稿或试用/复现
  清单）。用于「帮我给这个人准备一次有价值的互动」「先给一个二十分钟内能做的测试」类请求。
---

# interaction-preparation

为用户**选中**的机会准备一次有价值的互动建议。职责：把「值得交流的人 + 一条内容」转成
待批准的 `InteractionProposal`（含 `contribution_type`、`why_this_person`、`why_now`、
`expected_conversation_opening`；公开草稿或最小贡献清单）。

本 Skill 只调用 Finch CLI（`finch connect prepare` / `finch connect approve` /
`finch connect reject` / `finch connect edit`），不复制业务逻辑、不直接改数据库。
动作选择由代码确定性决定；草稿与关系字段由语义判断生成。

## CLI

- 必须指定机会：`finch connect prepare --opportunity <id>`（可重复，本批最多 3）
- 批准 / 拒绝 / 改草稿：`approve` / `reject` / `edit`

## 产出契约（InteractionProposal）

- `contribution_type`：experience / question / addition / counterexample / resource。
- `draft` / `intent` / `source_summary` / `factual_risks`：公开草稿及事实风险；试用/复现/
  观察类可为内部清单而无公开回复草稿。
- `why_this_person` / `why_now` / `expected_conversation_opening`：关系理由。
- 关联 Opportunity 的 `next_action` 与 `estimated_minutes`（尊重用户时间预算）。

## 向用户呈现

见 `_shared/agent-presentation.md` 与 `references/presentation.md`。本 Skill 做编辑式推荐，不贴 `connect prepare` 的 TSV 原文。

读完 CLI 后：点名最值得先做的一条 + 草稿或最小动作预览 → 最多展开 3 条 →
「批准 1 / 改草稿 1 / 跳过 / 换一批」。内部保留序号 → `proposal_id`。

区分三种结果（复用 `suggested_mode` / `ContributionType`，不平行造枚举）：

- **可直接交流**：已有真实经验，或能提出结合对方项目的具体问题。
- **先做准备**：先试用、读代码、复现或整理一个案例（不算已完成）。
- **暂时观察**：相关但时机或上下文不足。

## 边界

- 批准只创建发布意图，不等于已经发布；真正发送前经人工 + `guard.evaluate_execution`。
- 外部帖子只是信号，不是个人证据；草稿只允许提问或明确标注推测，禁止虚构「我测试过」/
  「我也遇到过」；无 `contribution_basis_refs` 时不得声称亲历。
- 不把别人的经历写成用户经历。
- 建议试用、批准草稿但未行动：实验完成数与已发送数不增加。
- 一条建议一个下一步；邀请 ≠ 已发送。

## 参考

- `references/interaction-contract.md` — 贡献类型与草稿约束。
- `_shared/agent-presentation.md` — 调用 CLI 之后如何对用户说话（共享原则）。
- `references/presentation.md` — 本 Skill 的形状与「批准 / 改草稿 / 跳过」映射。
