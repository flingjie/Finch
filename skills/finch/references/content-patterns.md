# Content Patterns

## 英文回复（reply）结构

1. **切入**：一句话点出这条讨论与我们证据的关联点。
2. **证据**：用一张 Evidence Card 支持的事实陈述（可追溯）。
3. **增量**：我们这边比讨论多出来的那点东西（一个具体结果、一个坑、一个测试）。
4. **（可选）问题**：一个具体、真实的问题，而不是礼貌性收尾。

示例骨架：

> We hit the same thing in `<repo>`: `<claim>`（见 `<source>`）。一个差别是 `<increment>`——`<具体结果>`。你们有没有遇到 `<具体问题>`？

## 中文日记（original）结构

1. **事件**：这组 commit 解决了什么问题（problem）。
2. **决策**：为什么这么做（decision，标注 INFERRED）。
3. **结果**：代码证明的结果（result，VERIFIED）。
4. **反思**：missing_context / 踩的坑 / 下回改进。

示例骨架：

> 这次 `<repo>` 里遇到 `<problem>`。我选择 `<decision>`（这是我的推断，不是直接证据）。结果 `<result>`。还没想清楚的是 `<missing_context>`。

## 通用约束

- 每条事实主张 → 一个 `evidence_card_id`。
- 没有证据卡的内容不写。
- 推断显式标注，不冒充事实。
- 回复 ≤ 一小段；日记可以稍长，但仍以"一条增量讲清楚"为界。
