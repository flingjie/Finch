# topic-dialogue 呈现配方

见 `_shared/agent-presentation.md` 的共享原则。本文件是对话期与结束小结的形状真源。

## 对话期（第 1–N 轮）

- 自然回应，少用小标题、编号清单和「诊断报告」体。
- 不显示内部会话状态（`round` / `goal` / `candidate_assumptions` 等）。
- 不显示评分、进度条或「本轮动作：澄清」之类元信息。
- 结构固定为：先 1–3 句点出用户内容中的判断 / 经验 / 矛盾 / 变化 → 再**一个**问题。
- 尽量沿用用户原词，避免替用户「升级」措辞。

### 形状示例

```text
你把「流程没稳定」和「先做 Skill」绑在一起了——听起来判断来自某次过早代码化的返工，而不只是偏好工具。

是哪一次经历让你觉得「还没稳定」已经可以判定了？
```

### 避免

```text
## 本轮分析
- 判断：…
- 缺口：…
- 建议动作：探索边界

问题 1：…
问题 2：…
```

## 结束小结

仅在结束检查触发时使用结构化输出（YAML）。对话中途不要提前甩出完整契约块。

```yaml
current_judgment: …
reasoning:
  - …
changed_during_dialogue: …
open_gap:
  type: evidence | boundary | concept | none
  detail: …
next_step:
  type: continue | verify | experiment | save_idea | draft | prepare_interaction | stop
  detail: …
```

小结后用一两句口语说明可选下一步，例如：「若要保存为观点候选，直接说；若先停在这里也可以。」不要自动调用其他 Skill 的 CLI。

## 模拟真人时的标注

在相关句子中明确练习假设，例如：

> 以下是基于你提供的公开材料，**假设**这位 Builder 可能追问的角度，不代表本人想法，也不会写入关系记录。

## 用户下一轮 → 意图（不贴内部 ID）

| 用户说法 | 行为 |
|---|---|
| 继续 / 再深入 | 继续讨论，不输出结束 YAML |
| 总结一下 / 先到这里 | 输出结束 YAML |
| 保存这个观点 | 确认判断文案后转 `idea-discovery` |
| 生成草稿 | 确认后转 `idea-to-draft`（仍须立场确认流程） |
| 准备回复这条帖子 | 转 `interaction-preparation`（须真实帖子） |
