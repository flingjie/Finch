# 会话输出

`PracticeSession`（YAML，`<var_dir>/practice/<session_id>.yaml`）：

- 叙事字段：`source_material`（原始素材，可空）/ `initial_attempt`（首稿，不可覆盖）/
  `diagnosis`（旧诊断，legacy）/ `questions_asked[]`（legacy）/ `revisions[]`（正文修订）/
  `final_expression` / `lesson`。
- 关联：`idea_id`（可空，无 Idea 也可训练）或 `method_id`（方法练习）。
- 状态与方法反馈：`status`（started|finished）/ `phase`（explore|drafting|feedback|revising|done，
  可空）/ `method_verdict` / `method_verdict_note`。
- 草稿优先模式：`mode`（example|guided|independent；旧会话读作 independent）。
- 目标语境：`context.audience` / `context.goal`。
- 写法探索：`options[]`（三种方案，各含 `name` / `familiarity` / `entry_point` / `progression[]` /
  `effect` / `cost` / `facts_needed` / `dimension` / `method_id` 可空）/ `selected_option`（下标）/
  `selection_reason` / `practice_dimension`。
- 局部对比：`feedback_rounds[]`，每轮含 `keep` / `key_location` / `alternative_a` /
  `alternative_b` / `difference` / `rewrite_task` / `user_rewrite` / `method_id` 可空。
- AI 草稿版本：`ai_drafts[]`，每版含 `id` / `text` / `parent_version_id`（可空）/ `method_ids[]` /
  `explanation` / `task` / `created_at`。
- 用户动作：`user_actions[]`，每条含 `action`（adopt|comment|edit|skip）/ `target_version_id` /
  `text` / `edit_scope` / `created_at`。
- 来源：`final_source`（user_authored|ai_example|mixed，默认 user_authored）/ `source_note`
  （混合文本的来源片段说明）/ `final_version_id`（最终版对应 AI 版本 id，可空；None=用户
  独立创作）/ `learning_observation`（本次轻量学习观察，非能力结论）。
- 旧逐轮历史：`turns[]`（旧诊断流程，仍保留），每轮含 `id` / `expression_snapshot` /
  `feedback` / `response` / `response_kind` / `created_at` / `responded_at`。
- 时间戳：`created_at` / `updated_at`。

旧会话没有新字段，读取时取默认值（空），不影响历史记录可读。

示例（新流程，已选择方案、一轮反馈后）：

```yaml
id: practice_ab12cd34
idea_id: null
initial_attempt: 反常的是，我们原以为审查越快越好，但更快反而让工程师更焦虑
diagnosis: ""
questions_asked: []
revisions: []
final_expression: ""
lesson: ""
status: started
phase: feedback
mode: independent
method_id: null
method_verdict: null
method_verdict_note: ""
context:
  audience: 一线工程师
  goal: 说清一个反直觉的审查现象
source_material: 审查速度与团队焦虑的反常关系
options:
  - name: 反常现象开头
    familiarity: 陌生
    entry_point: 先写与预期相反的现象，再回到预期
    progression: [写出反常现象, 补原预期, 落到一个待验证解释]
    effect: 读者先被反差抓住
    cost: 开头不给判断，读者要读到后面才知道结论
    facts_needed: 一个真实发生过的反差事例
    dimension: 结构
selected_option: 0
selection_reason: 想练「先现象后结论」
practice_dimension: 结构
feedback_rounds:
  - keep: 保留「反常的是」——它直接把反差标出来
    key_location: 「更快反而让工程师更焦虑」这一句当前只是断言，缺一个具体瞬间
    alternative_a: 把「更焦虑」落到一个可见动作，例如「开始反复确认同一处代码」
    alternative_b: 用对话呈现，例如「『这版真的没问题吗』成了当天问得最多的一句话」
    difference: A 用具体动作、节奏更稳；B 用对话、更有现场感但需要真实出处
    rewrite_task: 选 A、B 或你自己的一种，重写这一句
    user_rewrite: ""
turns: []
final_source: user_authored
source_note: ""
created_at: 2026-10-09T00:00:00+00:00
updated_at: 2026-10-09T00:03:00+00:00
```
