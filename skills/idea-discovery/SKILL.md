---
name: idea-discovery
description: >
  把个人证据（Commit/PR/测试）、零散思考（用户片段）或真实交流（已验证 ConversationEvidence）
  提炼成一个值得继续发展的 Idea（IdeaCandidate）。三种来源只是输入不同，判断同一件事：
  这里有没有「读者值得知道」的真实工程决策或问题。用于「把我最近的提交 / 这个片段 /
  这次交流变成一个可写的想法」类请求。
---

# idea-discovery

把个人证据、零散思考或真实交流提炼成一个值得继续发展的 Idea。三种来源共用一份判断：
有「读者值得知道的真实决策/问题」就产出**一个** `IdeaCandidate`；机械变化、新闻、纯情绪、
无明确结论的噪音 → 空。

本 Skill 只调用 Finch CLI（`finch ideas commit` / `finch ideas create`），不复制业务逻辑、
不直接改数据库、不猜测状态。

## 三种来源

- **commit 来源**：`finch ideas commit [--repo R] [--since 7d]`，见 `references/commit-signals.md`。
- **fragment 来源**：`finch ideas create --text "..."`，见 `references/fragment-signals.md`。
- **conversation 来源**：`finch ideas create --conversation <evidence-id>`，见 `references/conversation-signals.md`。

## 产出契约（IdeaCandidate）

见 `_shared/idea-contract.md`，要点：

- `core_point` 单一中心主张；`observation` 是实际观察到的；`intent` 为 `stance`（有立场）
  或 `exploration`（无完整结论也允许输出）。
- `source_refs` 可追溯；`author_position.status` 一律 `proposed`。
- `boundaries.known/inferred/unknown` 传递到 Draft 校验。

## 边界

- 外部帖子不能直接变成个人观点（见 `_shared/evidence-policy.md`）。
- 不生成草稿（→ `idea-to-draft` / `expression-practice`）。
- 不负责搜索交流对象（→ `peer-discovery`）。

## 参考

- `references/commit-signals.md` — 机械变化 vs 真实决策的判据。
- `references/fragment-signals.md` — 零散思考是否够格成为 Idea 的判据。
- `references/conversation-signals.md` — 交流信号中性化提炼的判据。
- `_shared/idea-contract.md` — IdeaCandidate 契约。
- `_shared/evidence-policy.md` — 证据优先、外部帖 ≠ 个人证据。
