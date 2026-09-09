# 社区信号 → Idea 判据

`signals` 来源聚合多源社区信号，判断是否值得综合成一个观点。与 commit / fragment /
conversation 共用同一份「读者值得知道」的判断。

## 输入信号

- `PeerProfile.shared_topics` / `current_interests`：同行反复讨论的主题。
- `ConversationThread.open_questions` / `disagreements`：未解问题与分歧（必要张力）。

## 判据

**值得综合**（产出 `origin=synthesis`）：

- 多位同行反复讨论同一问题，但存在分歧或未解机制。
- 一个具体的未解问题/分歧，用户有实践视角可补充。

**跳过**（返回空）：

- 只有共同主题、没有未解问题/分歧（无张力，不调用 LLM）。
- 新闻、融资、纯情绪、机械变化、无明确结论的噪音。
- 无法归属到具体同行或对话的泛泛「行业趋势」。

## 边界

- 外部信号 ≠ 个人证据：`author_position.status` 一律 `proposed`，不得写成亲历。
- 不生成草稿（→ `idea-to-draft` / `expression-practice`）。
- 只读，不自动发布。
