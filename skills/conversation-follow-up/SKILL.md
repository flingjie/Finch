---
name: conversation-follow-up
description: >
  找出值得继续的对话，恢复上下文并提出下一步。输入 ConversationThread（open_questions /
  agreements / disagreements / possible_experiments），输出需要跟进的理由与下一步建议。
  用于「有哪些对话该继续」「这条对话下一步怎么回」类请求。
---

# conversation-follow-up

找出值得继续的对话并恢复上下文。职责单一：判断一条 `ConversationThread` 是否需要跟进
（有未解问题 / 超期未活动），需要就恢复其上下文并提出下一步。

本 Skill 只调用 Finch CLI（`finch conversations list --needs-follow-up` /
`finch conversations show` / `finch conversations follow-up`），不复制业务逻辑、不直接
改数据库。跟进判定（`needs_follow_up`）由代码确定性计算；「下一步说什么」的语义建议
由本 Skill 补充。

## 产出契约（ConversationThread 跟进）

- `open_questions` / `agreements` / `disagreements` / `possible_experiments`：上下文。
- `next_step`：回答未解问题或提出实验。

## 边界

- 只读恢复上下文，不自动回复。
- 对话结论只有在用户亲自验证或明确表达后才可升级为个人观点（→ `idea-discovery`）。
- 外部作者观点保留引用，不写成用户亲历。

## 参考

- `references/follow-up-contract.md` — 跟进判定与下一步建议。
