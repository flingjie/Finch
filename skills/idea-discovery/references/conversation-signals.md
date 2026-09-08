# 对话 → Idea 的判据（conversation 来源）

输入是完整的 `ConversationThread`（`finch ideas create --conversation <thread_id>`），
不是孤立的 `ConversationEvidence`。对话型 Idea 必须可追溯到 `ConversationThread` 及其
原始互动（`InteractionRecord`）。

## 观点来源白名单

只有以下内容能形成用户自己的立场（进入 `author_position` 或 `core_point` 的「亲历」表述）：

1. **用户自己的经验**（个人实践、代码、实验）；
2. **用户明确表达的判断**（「我认为 X 比 Y 好」）；
3. **经过验证的对话结论**（agreements / possible_experiments 里已被确认的部分）。

其他内容只能作为**外部信号**：保留第三人称、保留引用，不得改写成用户亲历。

## 规则

- 外部作者的观点/问题保留在 `source_refs`，进入 `core_point` / `reader_problem` /
  `boundaries` 时中性化（剥离第一人称）。
- `boundaries.known` 只放已验证结论；未解决的分歧放 `boundaries.unknown`。
- 没有用户立场时保持 `PROPOSED`（`author_position` 不标记为 confirmed），不能直接生成
  可批准的草稿。
- `communication_goal` 明确这次内容要把对话推进到哪里：
  `continue_discussion` / `invite_counterexample` / `summarize_practice` / `find_collaborators`。
- `recommended_format` 只作建议（reply / quote / short post / thread / DM / do-not-publish）；
  真正生成草稿仍走 `idea-to-draft` 并经人工审核。

## 可追溯闭环

```
ConversationThread (id + topic + agreements/disagreements/experiments)
  └── InteractionRecord（原始互动）
        └── IdeaCandidate（origin=conversation，source_refs 含 thread + 互动）
              └── ContentJob（PROPOSED → 确认立场 → DRAFTED）
```
