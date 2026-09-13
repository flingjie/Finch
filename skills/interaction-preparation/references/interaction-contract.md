# 互动建议契约

`InteractionProposal` 是待批准的候选，不是已发生的互动。

- `contribution_type`：experience / question / addition / counterexample / resource。
- `draft` 是纯正文字符串；`intent` / `source_summary` / `factual_risks` 记录意图、所回应
  的帖子片段摘要与事实风险。
- `generation_key` 编码 peer + source + action + prompt_version，用于幂等（相同 key 不重复
  调用 LLM 或创建 Proposal）。

## 理解经历与试用意图（复用 ContributionType，不新建销售枚举）

一条建议聚焦**一个**自然的下一步，不一次追问完整商业问卷。没有自己的相关经验时，
不编造「我也遇到过」。准备邀请不是发送；批准也不是已发生互动。

| 意图 | `contribution_type` | 优先内容 |
|---|---|---|
| 了解问题 | `question` | 最近一次发生在什么任务中？现在怎样处理？有何约束？ |
| 提供帮助 | `experience` / `resource` | 给出相关案例、样例或经验，并说明适用限制 |
| 请求样本 | `question` | 说明用途，只请求必要材料，允许对方拒绝 |
| 邀请试用 | `resource` | 仅在已有相关工具且情境匹配时；说明能做什么和已知限制；先给有用信息 |

第一次互动优先给对方有用的信息或提出具体问题，**不要**开场推销。

## 草稿约束

- 互动轨道不携带作者个人证据：草稿只允许提问或明确标注推测，禁止虚构案例/代码/实验。
- 外部帖子只是信号，不是个人证据；不把别人的经历写成用户经历。
- 批准只创建发布意图，不等于已发布；真正发送前经人工 + `guard.evaluate_execution`。

## 三事实分离

`InteractionProposal`（待批准建议）、`InteractionRecord`（已发生互动）、
`FeedbackSnapshot`（结果反馈）是三个不同事实，不得互相替代。
