---
name: idea-to-draft
description: >
  把已确认的 Idea（ContentJob）写成一篇中文原创草稿（Draft），仅作 Assist 模式（代写）。
  用于「我没时间练习 / 已经想清楚，直接帮我写」类请求；只依据 job 语境（读者问题 / 作者
  立场 / 核心主张 / 边界）写正文，不搜索新来源、不绑定证据卡；草稿过 Critic（6 检查器，
  Safety 硬门禁）+ 有限 rewrite 后落库为 Draft + CriticReport，进入人工审核。
---

# idea-to-draft（Assist 模式）

把已确认的 Idea 写成草稿。职责单一：从 `ContentJob`（`status=confirmed`）生成一篇**进入人工审核**的 `Draft`，并落库一轮 Critic 报告。未确认的 idea 拒绝生成（`needs_confirmation`）。

这是**代写模式**，不是表达训练。如果用户想通过表达提升能力，先走 `expression-practice`。

## 职责

- 输入：一个已确认的 `ContentJob`。
- 输出：一篇 `Draft`（`kind` 随 job 的 `recommended_format`，`claims` 恒为空）+ 一轮 `CriticReport`。
- 未确认 / 不存在的 idea → 报错，不生成。
- 草稿只依据 job 语境写，**不搜索新来源、不绑定证据卡**。

## 执行

用 `finch drafts create <idea-id> [--json]`。

## 边界

- 不冒充表达训练（→ `expression-practice`）。
- 不改变已确认立场（claim/decision/tradeoff 只原样表达，见 `_shared/author-position.md`）。
- 不从外部信号补造个人经历（见 `_shared/evidence-policy.md`）。
- 不自动发布（见 `_shared/publication-safety.md`）。
- 不把 AI 生成文本直接加入 VoiceProfile（→ `voice-profile`）。

## 参考

- `references/draft-patterns.md` — 边界 / 口吻 / scope / 立场的写作判据。
- `_shared/author-position.md` — proposed vs confirmed，不改变立场。
- `_shared/evidence-policy.md` — 证据优先、推断不写成亲历。
- `_shared/expression-contract.md` — 有限 rewrite、默认不自动 rewrite。
- `_shared/publication-safety.md` — 不自动发布、分数由代码算。
