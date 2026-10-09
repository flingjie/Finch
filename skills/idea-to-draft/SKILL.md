---
name: idea-to-draft
description: >
  把用户已想清楚的文本直接写成一篇中文原创草稿（Draft），按作者声音画像（voice-profile.yaml）
  写作，仅作 Assist 模式（代写）。用于「我没时间练习 / 已经想清楚，直接帮我写」类请求；只依据
  用户给的语境写正文，不搜索新来源、不绑定证据卡；草稿过 Critic（6 检查器，Safety 硬门禁）
  + 有限 rewrite 后落库为 Draft + CriticReport，进入人工审核。不强制先确认立场。成稿后的
  句子级清晰表达（ASD-STE100-inspired / 简化技术英语原则，中文适配）用 `finch drafts revise`
  与 `skills/_shared/asd-ste100-inspired.md`，不在本 Skill 内代做。
---

# idea-to-draft（Assist 模式）

把用户已想清楚的文本写成草稿。职责单一：从用户给的文本直接生成一篇**进入人工审核**的
`Draft`，并落库一轮 Critic 报告。这是直接写作短路，不强制先确认立场。

这是**代写模式**，不是表达训练。如果用户想通过表达提升能力，先走 `expression-practice`。

## 职责

- 输入：用户已想清楚的文本 / 想法。
- 输出：一篇 `Draft`（`claims` 恒为空）+ 一轮 `CriticReport`，状态为**待审**。
- 只依据用户给的语境写，**不搜索新来源、不绑定证据卡**。
- 正文按作者声音画像写（见「声音」），不生成空泛的助手腔调。
- 草稿完成不等于作者认领立场，也不等于已发布。

## 执行

用 `finch drafts write <text> [--json]`。用户已有一个 `status=confirmed` 的 idea 时，
也可用 `finch drafts create <idea-id> [--json]` 沿既有立场写。

## 声音

写作前读 `voice-profile.yaml`。`preferred_patterns` 是表达顺序，`approved_examples` 是
腔调参照，`avoid_phrases` 命中的表达不写，`rhythm_rules` 是节奏约束；画像为空则退回
`references/draft-patterns.md` 的默认口吻。`finch drafts write/create` 已把画像交给生成
与 Critic；若返回正文仍与画像腔调明显不符，先 `finch drafts revise <id> --instruction "..."`
对齐再呈现，不把「不像我」的稿子当最终交付。

## 向用户呈现

见 `_shared/agent-presentation.md` 与 `references/presentation.md`。先给正文与质检结论，再给「采用 / 改 / 跳过」；Critic 明细默认不展开。
完成后按 `_shared/dialogue-policy.md` 做一次延伸点检查（无有效点就自然结束），延伸不得抢占交付物。

## 边界

- 不冒充表达训练（→ `expression-practice`）。
- 完成讨论 ≠ 立场已确认；`drafts write` 只生成待审草稿，不自动认领立场（见 `_shared/author-position.md`）。
- 不改变已确认立场（claim/decision/tradeoff 只原样表达，见 `_shared/author-position.md`）。
- 不从外部信号补造个人经历（见 `_shared/evidence-policy.md`）。
- 不自动发布（见 `_shared/publication-safety.md`）。
- 不把 AI 生成文本直接加入 VoiceProfile（→ `voice-profile`）。
- 无来源的效果数字、付费「已验证」主张、以及把对方试用写成自己亲历 → Critic 硬失败。
- 成稿后的清晰表达润色 → `finch drafts revise`（默认走 ASD-STE100-inspired 共享规则，见 `_shared/asd-ste100-inspired.md`）。

## 参考

- `references/draft-patterns.md` — 边界 / 口吻 / scope / 立场的写作判据（默认口吻即作者声音画像）。
- `voice-profile.yaml` — 作者声音画像（preferred_patterns / approved_examples / avoid_phrases / rhythm_rules）。
- `_shared/author-position.md` — proposed vs confirmed，不改变立场。
- `_shared/evidence-policy.md` — 证据优先、推断不写成亲历。
- `_shared/expression-contract.md` — 有限 rewrite、默认不自动 rewrite。
- `_shared/publication-safety.md` — 不自动发布、分数由代码算。
- `_shared/agent-presentation.md` — 调用 CLI 之后如何对用户说话（共享原则）。
- `references/presentation.md` — 本 Skill 的形状与「采用 / 改 / 跳过」映射。
- `_shared/dialogue-policy.md` — 任务后延伸点选择、授权边界与收束
