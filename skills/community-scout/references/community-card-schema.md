# 社区行动卡 schema

`finch community save --file <card.yaml>` 吃这个 YAML。顶层 `community` 对应模型字段 `name`；
`id` / `week` / `created_at` 由 CLI 自动补全。新字段均可空，旧卡继续可读。

## 可参与态示例（actionable）

```yaml
community: Temporal Community
canonical_url: https://temporal.io/community
recommendation_state: actionable
intent: question
question: Agent workflow failure replay 的社区
practice_refs:
  - FDE-Gym failure replay 案例
platforms:
  - GitHub Discussions
fit_score: 86
why_fit:
  - 与 durable execution 和 Agent reliability 高度相关
recent_evidence:
  - topic: workflow failure recovery
    relevance: 与 FDE-Gym 的 failure replay 方向相关
    url: https://example.com/discussion/1
entry_point:
  discussion: 一个仍在活跃的具体讨论
  suggested_angle: 分享 FDE-Gym 中的失败回放设计
  url: https://example.com/discussion/1
  status: open
first_contribution:
  type: example
  proposal: 提供一个 Agent workflow failure replay 示例
risks:
  - 英文交流成本中等
evidence_urls:
  - https://example.com/discussion/1
```

## 观察态示例（observe）

```yaml
community: Some Slow-moving Open Source Project
canonical_url: https://github.com/example/project
recommendation_state: observe
recent_evidence:
  - topic: 上月的 release notes
    relevance: 方向相关但当前无线程可切入
    url: https://example.com/release
why_fit:
  - 方向高度相关，但近期无开放讨论
risks:
  - 暂无具体切入点，仅建议阅读
```

## 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `community` | str | 社区名（对应模型 `name`；`id` 由它内容寻址） |
| `canonical_url` | str = "" | 跨周稳定标识（主页/项目链接）；空则回退 name-hash id 去重 |
| `recommendation_state` | `observe`/`actionable`/空 | Finch 建议的状态；空=旧卡 |
| `intent` | str = "" | `weekly`/`question`/`revisit` |
| `question` | str = "" | 问题模式的具体问题 |
| `practice_refs` | list[str] = [] | 用户带入本次匹配的实践材料 |
| `source_checked_at` | datetime/空 | 资料最近核验时间 |
| `platforms` | list[str] | 社区所在平台 |
| `fit_score` | int 0–100 | 旧四维加权分（诊断用，不是结论） |
| `why_fit` | list[str] | 为什么现在适合你 |
| `recent_evidence` | list[{topic, relevance, url}] | 近期公开证据 |
| `people` | list[{name, reason}] | 值得关注的核心 Builder |
| `entry_point` | {discussion, suggested_angle, url, status} | 可切入讨论 + 链接 + 开放性（`open`/`closed`/`unknown`/空） |
| `first_contribution` | {type, proposal} | 你能贡献的代码/案例/工具 |
| `risks` | list[str] | 参与成本与风险 |
| `evidence_urls` | list[str] | 公开证据链接（去重后） |

## feedback 字段

| 字段 | 说明 |
|---|---|
| `result` | 六值不变：`ignored/saved/joined/interacted/repeated/contributed` |
| `reason_kind` | 选择/拒绝原因短标签（`no_time`/`too_general`/`language_barrier`/`deep_but_later` 等），可为空 |
| `interaction_ref` | 真实互动链接；空=未提供 |
| `ref_kind` | `public_url`/`user_stated`；无 `interaction_ref` 时为空 |

已发生状态（`interacted/repeated/contributed`）无 `interaction_ref` 时为自述（`user_stated`），未公开核验。
"准备/打算"不得记成 `interacted`。