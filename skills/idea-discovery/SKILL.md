---
name: idea-discovery
description: >
  把个人证据（Commit/PR/测试）、零散思考（用户片段）、真实交流（已验证 ConversationEvidence）
  或社区信号（同行共同主题 + 未解问题/分歧）提炼成一个值得继续发展的 Idea（IdeaCandidate）。
  四种来源只是输入不同，判断同一件事：这里有没有「读者值得知道」的真实工程决策或问题。
  用于「把我最近的提交 / 这个片段 / 这次交流 / 这些社区信号变成一个可写的想法」类请求。
---

# idea-discovery

把个人证据、零散思考或真实交流提炼成一个值得继续发展的 Idea。三种来源共用一份判断：
有「读者值得知道的真实决策/问题」就产出**一个** `IdeaCandidate`；机械变化、新闻、纯情绪、
无明确结论的噪音 → 空。

本 Skill 只调用 Finch CLI（`finch ideas commit` / `finch ideas create`），不复制业务逻辑、
不直接改数据库、不猜测状态。

## 四种来源

- **commit 来源**：`finch ideas commit [--since 7d]`。默认当前 checkout 的 origin、近 7 天；
  只有用户点名别的仓库才传 `--repo`。见 `references/commit-signals.md`。
- **fragment 来源**：`finch ideas create --text "..."`，见 `references/fragment-signals.md`。
  无 commit 也可；产出会带 `source_kind` / `facts` / `interpretation` / `evidence_status`
  （`observed` / `externally_reported` / `unverified`）。外部文章描述实验 →
  `externally_reported`，不得写成第一人称亲历。
- 与讨论配对：`finch connect create --input <url> --from-idea <id>` 或 `--note "..."`
  （默认提纲，零贡献时「暂不回复」）。
- **conversation 来源**：`finch ideas create --conversation <thread_id>`，读取线索与
  `observation_notes`（problem / workaround / usage_feedback）作为**对方**报告，不得改写为
  作者亲历；本人实践须用户明确输入。见 `references/conversation-signals.md`。
- **signals 来源**：`finch ideas signals`，见 `references/signals-signals.md`。

## 向用户呈现

见 `_shared/agent-presentation.md` 与 `references/presentation.md`。本 Skill 做编辑式推荐，不贴 CLI 原文。

读完 `finch ideas commit` / `create` / `signals` 后，按 `references/presentation.md` 的形状回复：结论 → 最多 3 条卡 →「写 1 / 展开 2 / 比较 / 换一批」。内部保留序号 → `idea_id`。

## 产出契约（IdeaCandidate）

见 `_shared/idea-contract.md`，要点：

- `core_point` 单一中心主张；`observation` 是实际观察到的；`intent` 为 `stance`（有立场）
  或 `exploration`（无完整结论也允许输出）。
- `facts` 与 `interpretation` 分列；`evidence_status` 决定能否声称亲历。
- `source_refs` 可追溯；`author_position.status` 一律 `proposed`。
- `boundaries.known/inferred/unknown` 传递到 Draft 校验。
- 对方自述「更快了」不得改写为精确节省百分比；无付款证据不得写「用户愿意付费已验证」。

## 边界

- 外部帖子不能直接变成个人观点（见 `_shared/evidence-policy.md`）。
- 不生成草稿（→ `idea-to-draft` / `expression-practice`）。
- 不负责搜索交流对象（→ `peer-discovery`）。

## 参考

- `references/commit-signals.md` — 机械变化 vs 真实决策的判据。
- `references/fragment-signals.md` — 零散思考是否够格成为 Idea 的判据。
- `references/conversation-signals.md` — 交流信号中性化提炼的判据。
- `references/signals-signals.md` — 社区信号（同行主题 + 未解问题/分歧）综合的判据。
- `_shared/idea-contract.md` — IdeaCandidate 契约。
- `_shared/evidence-policy.md` — 证据优先、外部帖 ≠ 个人证据。
- `_shared/agent-presentation.md` — 调用 CLI 之后如何对用户说话（共享原则）。
- `references/presentation.md` — 本 Skill 的形状与「写 / 展开 / 换一批」映射。
