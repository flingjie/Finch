# 跟进判定与下一步建议

## 跟进判定（确定性，代码计算）

`needs_follow_up(thread)` 返回 True 当且仅当：

- 线索状态不是 CLOSED；且
- 有未解问题（open_questions 非空）；或
- 超过 stale_days 未活动（last_activity_at 为空或过旧）。

## 下一步建议（语义判断，本 Skill 补充）

- 有未解问题 → 回答未解问题或提出实验。
- 无未解问题但超期 → 确认是否关闭或延续新主题。
- 有 agreements / possible_experiments → 可作为观点来源（→ `idea-discovery`）。

## 边界

- 只读恢复上下文，不自动回复。
- 对话结论只有在用户亲自验证或明确表达后才可升级为个人观点。
- 外部作者观点保留引用，不写成用户亲历。
