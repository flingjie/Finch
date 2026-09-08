# 互动建议契约

`InteractionProposal` 是待批准的候选，不是已发生的互动。

- `contribution_type`：experience / question / addition / counterexample / resource。
- `draft` 是纯正文字符串；`intent` / `source_summary` / `factual_risks` 记录意图、所回应
  的帖子片段摘要与事实风险。
- `generation_key` 编码 peer + source + action + prompt_version，用于幂等（相同 key 不重复
  调用 LLM 或创建 Proposal）。

## 草稿约束

- 互动轨道不携带作者个人证据：草稿只允许提问或明确标注推测，禁止虚构案例/代码/实验。
- 外部帖子只是信号，不是个人证据；不把别人的经历写成用户经历。
- 批准只创建发布意图，不等于已发布；真正发送前经人工 + `guard.evaluate_execution`。

## 三事实分离

`InteractionProposal`（待批准建议）、`InteractionRecord`（已发生互动）、
`FeedbackSnapshot`（结果反馈）是三个不同事实，不得互相替代。
