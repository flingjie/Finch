---
name: reply-crafting
description: >
  生成公开回复草稿：具体观察 → 自己的真实经验/证据 → 一个可继续讨论的问题。
  禁止纯赞美、假装使用、补造经历；无贡献点返回 SKIP。只生成草稿，不发布。
---

# reply-crafting

输入：ConnectionOpportunity + 观察/经验/问题要点。
输出：ReplyDraft（full_text 或 SKIP）。

## 质量规则

- 禁止纯赞美
- 禁止假装使用过对方产品
- 禁止模型补造用户经历
- 没有可贡献内容时返回 SKIP
- 只生成草稿，不调用 OpenCLI 的 reply/post

## 用户流程

用户复制草稿到原平台亲自发布，再执行 `finch connections record --person …`。
