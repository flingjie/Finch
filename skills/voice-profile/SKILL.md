---
name: voice-profile
description: >
  初始化和更新个人表达画像（VoiceProfile）。输入历史发布内容、expression-practice 最终版、
  人工修改后的采用版本、明确拒绝的表达及理由，输出 preferred_patterns / avoid_phrases /
  rhythm_rules / approved_examples / rejected_examples，并展示可追溯 diff 后由用户确认写入。
  用于「更新我的声音画像」「把这篇采用稿记进我的风格」「这条为什么不像我」类请求。
---

# voice-profile

初始化和更新个人表达画像。领域代码已存在（`finch voice` + `voice-profile.yaml`），本 Skill
只做画像学习的判断：什么该学、什么该避免、什么证据够格推导一条规则。

## 输入

- 用户历史发布内容（`finch voice approve-example <draft_id>` 已存 approved_examples）。
- expression-practice 中用户写的最终版。
- 人工修改后的采用版本（decision.accept 后 `revised_body or body`）。
- 明确拒绝的表达及理由（`finch voice reject-example <draft_id> --reason "..."`）。

## 输出

`preferred_patterns` / `avoid_phrases` / `rhythm_rules` / `approved_examples` / `rejected_examples`。

## 边界

- 不生成内容。
- 不根据点赞量自动改风格。
- 不从单个样本推导全局规则（见 `references/extraction-rules.md`）。
- 不从未经确认的 AI 草稿学习（只有 decision.accept 后的文本才算采用稿）。
- 更新前展示 diff，用户确认后写入。

## 执行

- 查看画像：`finch voice show`（摘要；完整用 `--json`）
- 提出更新：`finch voice propose`
- 采用样例：`finch voice approve-example <draft_id>`
- 拒绝样例：`finch voice reject-example <draft_id> --reason "..."`

## 向用户呈现

见 `_shared/agent-presentation.md` 与 `references/presentation.md`。默认摘要 + 最多 3 条更新候选，不贴完整 YAML；确认后才写入。

## 参考

- `references/extraction-rules.md` — 单样本不推导全局规则的判据。
- `references/sample-format.md` — 正反样本格式。
- `_shared/agent-presentation.md` — 调用 CLI 之后如何对用户说话（共享原则）。
- `references/presentation.md` — 本 Skill 的形状与「采纳 / 看完整画像」映射。
