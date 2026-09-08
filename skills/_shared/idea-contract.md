# IdeaCandidate 契约

Skill 层（idea-discovery / 用户输入 / 对话线索）产出统一 `IdeaCandidate`，持久化复用 `ContentJob`（不新增 Idea 表）。领域服务见 `src/finch/ideas/`。

## YAML 结构

```yaml
id: idea_<sha256[:8]>
origin: commit | user | conversation
core_point: ""            # 单一中心主张
observation: ""           # 实际观察到了什么
reader_problem: ""        # 读者的问题/痛点
why_worth_saying: ""      # 为什么值得现在说
intent: stance            # stance | exploration（默认 stance）
open_question: ""         # 未解决的开放问题（可空）
author_position:
  claim: ""               # 主张
  decision: ""            # 决策
  tradeoff: ""            # 取舍
source_refs:              # 可追溯来源
  - type: commit|pr|issue|test|post|paper|conversation
    ref: ""
    summary: ""
boundaries:               # 证据边界，传递到 Draft 校验
  known: []               # 已验证
  inferred: []            # 推断
  unknown: []             # 未知
recommended_format: reply | quote | short_post | thread | dm | do_not_publish
communication_goal: continue_discussion | invite_counterexample | summarize_practice | find_collaborators
generator:
  skill: ""               # 产出 Skill 名
  version: ""             # Skill 版本
```

## 7 条约束

1. **单一中心主张**：`core_point` 只承载一个中心主张；多个主张拆成多个候选。
2. **可追溯**：每条 `source_refs` 必须 `type + ref + summary` 齐备，能反查到具体来源。
3. **自动生成立场一律 `proposed`**：任何 Skill/LLM 自动产出，候选状态（`ContentJob.status`）只能是 `PROPOSED`。
4. **`confirmed` 只来自用户明确确认**：`IdeaService.confirm_position`（`finch ideas confirm`）是唯一确认路径。
5. **外部作者观点保留引用**：对话中的对方观点不得改写成作者亲历，原文保留在 `source_refs`。
6. **边界传递**：`boundaries.known/inferred/unknown` 必须原样带入 Draft 校验，Draft 不得越界陈述。
7. **Skill 层只产契约，持久化复用 ContentJob**：Skill 不直接写库，经 `IdeaService.create_candidate` 落为 `ContentJob`（`origin`/`observation`/`intent`/`open_question`/`generation_key`/`generator_name`/`generator_version`/`content_fingerprint`）。

