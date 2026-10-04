---
name: reply-crafting
description: >
  生成公开回复草稿：一个重点、一条简洁正文，具体贡献优先，可选复用表达方法。
  只生成草稿，不发布；禁止纯赞美、假装使用、补造经历。
---

# reply-crafting

给一条原帖 + 可选个人材料，生成一条简洁、具体、有依据的公开回复草稿。
实现落在 `finch connect prepare --opportunity <id> [--reaction]`，产出 `Artifact`
（`kind=reply_draft`，字段 `body`）。本 Skill 不建独立的 ReplyDraft 模型；「暂不回复」
由上游 opportunity 判断，无贡献点时不硬编造。

## 输入 / 输出

- 输入：选中的 Opportunity（`finch connect daily` 选中）+ 观察/经验/问题要点，可选本次风格指令。
- 输出：`Artifact`（正文 `body` + `source_refs` + 可选 `method_ref`/`response_focus` 等元数据）。

## 简洁风格（默认策略）

- 一个重点、一条回复、通常 1–3 句；中文目标 40–100 字，软上限 120。
- 直接说判断/问题，少铺垫；用具体条件、例子、取舍，避免抽象术语堆叠。
- 不重复原帖完整观点、不附总结；不默认赞美开头；不强制反问/三段式/行动号召/以问句结尾。
- 问题 0–1 个，且必须有明确信息需求；单条信息完整时允许更短。
- 优先级：本次用户指令（`--style-note`）→ VoiceProfile → 默认策略。
- 机会带 `problem`/`fit`/`next_action` 时，回复围绕 `problem` 那一个具体点展开；缺少依据时
  按 `next_action.type=ask` 提问，不把交流变成产品推销。

## 质量规则

- 禁止纯赞美、假装使用过对方产品、补造用户经历。
- 第一人称只能来自 confirmed practice（`[practice-id]`）或用户本次原话（`[reaction]`）。
- 方法出处不是事实证据，不进入 `source_refs`。

## 可选方法复用

- 显式指定：`finch connect prepare --opportunity <id> --method <method-id>`
- 从报告取：`--methods-from-report <report-id>`
- 自动推荐：`--use-method-library`（只筛 `applicable_forms` 含 reply，最多 10）
- 一次推理选中最多 1 个方法；不匹配就用直接回复，不硬套方法。

## 反馈

用户改完/发完一条回复后，可对所用方法留反馈：

```
finch methods log-reply --method <id> --artifact <id> --verdict useful|mixed|not_fit [--note …]
```

内容修正 / 事实修正 / 风格修正写进 `note`；「没发出去」或「没人回」不等于方法无效。
想练：`finch practice start --method <id> --attempt "改写后的回复"`。

## 用户流程

用户复制草稿到原平台亲自发布，再执行 `finch connections record --person …`。
不调用 OpenCLI 的 reply/post，不自动更新 `voice-profile.yaml`。
