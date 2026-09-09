---
name: interaction-preparation
description: >
  基于具体同行、帖子和用户证据生成互动建议（回复 / 引用 / 私信）。输入 PeerProfile 与
  InteractionProposal 需要的关系字段（contribution_type / why_this_person / why_now /
  expected_conversation_opening），产出待批准的 InteractionProposal（含草稿）。用于「帮我
  给这个人准备一次有价值的互动」类请求。
---

# interaction-preparation

为具体同行与帖子准备一次有价值的互动建议。职责单一：把一个「值得交流的人 + 一条帖子」
转成一个待批准的 `InteractionProposal`（含 `contribution_type`、`why_this_person`、
`why_now`、`expected_conversation_opening` 与草稿）。

本 Skill 只调用 Finch CLI（`finch connect prepare` / `finch connect approve` /
`finch connect reject` / `finch connect edit`），不复制业务逻辑、不直接改数据库。
动作选择（bookmark / observe / reply / quote）由代码确定性决定；草稿与关系字段由本
Skill 的语义判断生成。

## 产出契约（InteractionProposal）

- `contribution_type`：experience / question / addition / counterexample / resource。
- `draft` / `intent` / `source_summary` / `factual_risks`：草稿及事实风险。
- `why_this_person` / `why_now` / `expected_conversation_opening`：关系理由。

## 向用户呈现

见 `_shared/agent-presentation.md` 与 `references/presentation.md`。本 Skill 做编辑式推荐，不贴 `connect prepare` 的 TSV 原文。

读完 CLI 后：点名最值得先发的一条 + 草稿预览 → 最多展开 3 条 →「批准 1 / 改草稿 1 / 跳过 / 换一批」。内部保留序号 → `proposal_id`。

## 边界

- 批准只创建发布意图，不等于已经发布；真正发送前经人工 + `guard.evaluate_execution`。
- 外部帖子只是信号，不是个人证据；草稿只允许提问或明确标注推测，禁止虚构案例/代码/实验。
- 不把别人的经历写成用户经历。

## 参考

- `references/interaction-contract.md` — 贡献类型与草稿约束。
- `_shared/agent-presentation.md` — 调用 CLI 之后如何对用户说话（共享原则）。
- `references/presentation.md` — 本 Skill 的形状与「批准 / 改草稿 / 跳过」映射。
