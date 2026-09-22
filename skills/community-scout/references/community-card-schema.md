# 社区行动卡 schema

`finch community save --file <card.yaml>` 吃这个 YAML。顶层 `community` 对应模型字段 `name`；
`id` / `week` / `created_at` 由 CLI 自动补全，不需要手写。

```yaml
community: Temporal Community
platforms:
  - GitHub Discussions
  - public_forum
fit_score: 86
why_fit:
  - 与 durable execution 和 Agent reliability 高度相关
  - 核心成员持续讨论真实生产问题
recent_evidence:
  - topic: workflow failure recovery
    relevance: 与 FDE-Gym 的 failure replay 方向相关
    url: https://example.com/discussion/1
people:
  - name: Core Builder A
    reason: 持续分享 durable execution 实践
entry_point:
  discussion: 一个仍在活跃的具体讨论
  suggested_angle: 分享 FDE-Gym 中的失败回放设计
first_contribution:
  type: example
  proposal: 提供一个 Agent workflow failure replay 示例
risks:
  - 概念讨论可能偏基础设施
  - 英文交流成本中等
evidence_urls:
  - https://example.com/discussion/1
```

## 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `community` | str | 社区名（对应模型 `name`，`id` 由它内容寻址） |
| `platforms` | list[str] | 社区所在平台 |
| `fit_score` | int 0–100 | 四维加权总分（见 scoring-rubric.md） |
| `why_fit` | list[str] | 为什么现在适合你 |
| `recent_evidence` | list[{topic, relevance, url}] | 近期公开证据（硬门槛 3） |
| `people` | list[{name, reason}] | 值得关注的核心 Builder |
| `entry_point` | {discussion, suggested_angle} | 可切入的具体讨论 + 建议角度 |
| `first_contribution` | {type, proposal} | 你能贡献的代码/案例/工具 |
| `risks` | list[str] | 参与成本与风险 |
| `evidence_urls` | list[str] | 公开证据链接（去重后的 `recent_evidence[].url`） |
