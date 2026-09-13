# 跟进判定与下一步建议

## 跟进判定（确定性，代码计算）

`needs_follow_up(thread)` 返回 True 当且仅当（时间陈旧 **alone 不触发**）：

- 线索状态不是 CLOSED；DEFERRED 且未到期则不跟进；且
- 有 `pending_triggers`（`new_reply` / `own_commitment` / `new_evidence` / `related_update`）；或
- 有开放的明确 `Commitment`；或
- 有未解问题（`open_questions` / `open_question_notes`）。

## 线程笔记（observation_notes）

导入真实回复后，可在同一线索记录带来源的笔记：

| kind | 含义 |
|---|---|
| `problem` | 对方的具体任务与问题 |
| `workaround` | 当前解决方法 |
| `usage_feedback` | 使用尝试、结果或阻碍 |

必填 `source_ref`（指向 InteractionRecord）。「看起来不错」只能记兴趣；「有空试试」
不能升级为已开始使用；「付费后再说」不能记为付款。用户报告有效与独立观测有效分开。

## 下一步建议（语义判断，本 Skill 补充）

- 有未解问题或 observation 未知 → 一条有上下文的提问或帮助。
- 有开放承诺到期 → 履行或确认，不把对方承诺自动变成催促任务。
- 有新 usage_feedback（如需人工配置）→ 准备自然跟进，不自动重试推销。
- 明确拒绝或不希望继续 → 停止相关试用建议。
- 只过了若干天、无新事实 → **不**建议催促试用或发送消息。

## 边界

- 只读恢复上下文，不自动回复。
- 对话结论只有在用户亲自验证或明确表达后才可升级为个人观点。
- 外部作者观点保留引用，不写成用户亲历。
- 礼貌兴趣 ≠ accepted / paid；沉默 ≠ declined。
