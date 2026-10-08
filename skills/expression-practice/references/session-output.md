# 会话输出

`PracticeSession`（YAML，`<var_dir>/practice/<session_id>.yaml`）：

- 叙事字段：`initial_attempt`（首稿，不可覆盖）/ `diagnosis`（最近诊断）/
  `questions_asked[]`（legacy，只追加 revise/predict/transfer 的 task）/
  `revisions[]`（正文修订，预测与迁移不写入）/ `final_expression` / `lesson`。
- 关联：`idea_id`（可空，无 Idea 也可训练）或 `method_id`（方法练习）。
- 状态与方法反馈：`status`（started|finished）/ `method_verdict` / `method_verdict_note`。
- 目标语境：`context.audience` / `context.goal`。
- 逐轮历史：`turns[]`，每轮含 `id` / `expression_snapshot`（本轮针对的原文）/
  `feedback`（`action` = revise|predict|hint|transfer|finish、`diagnosis`、`evidence_quote`、
  `task`、`hint_level`）/ `response` / `response_kind`（revision|prediction|transfer|skipped）/
  `created_at` / `responded_at`。
- 时间戳：`created_at` / `updated_at`。

旧会话没有 `context` / `turns`，读取时二者取默认值（空），`turns` 为空时回退展示 legacy 字段。

示例（一轮 revise 已响应）：

```yaml
id: practice_ab12cd34
idea_id: null
initial_attempt: 效率能提高
diagnosis: 空泛，没说谁少做了哪一步
questions_asked:
  - 把「效率提高」换成谁少做了哪一步？
revisions:
  - 审查由串行改成并行，工程师少等一轮
final_expression: ""
lesson: ""
status: started
method_id: null
method_verdict: null
method_verdict_note: ""
context:
  audience: Agent 开发者
  goal: 说清审查瓶颈
turns:
  - id: turn_9f00aa11
    expression_snapshot: 效率能提高
    feedback:
      action: revise
      diagnosis: 空泛，没说谁少做了哪一步
      evidence_quote: 效率能提高
      task: 把「效率提高」换成谁少做了哪一步？
      hint_level: 0
    response: 审查由串行改成并行，工程师少等一轮
    response_kind: revision
    created_at: 2026-10-08T00:00:00+00:00
    responded_at: 2026-10-08T00:01:00+00:00
created_at: 2026-10-08T00:00:00+00:00
updated_at: 2026-10-08T00:01:00+00:00
```
