# 表达契约（Expression Contract）

expression-practice 与其他表达/成稿能力的共享边界。核心：**表达训练不代写；代写不冒充训练**。

## 用户先写首稿

- 练习中，用户先写首稿（`initial_attempt`）；Skill 先探索、给局部反馈，不先给完整范文。
- 一次只处理一个主要维度或一个关键位置，不一次性抛一串问题。

## 首稿后的局部示范

- 首稿后允许给**局部**示范（两三句，针对同一位置），标注为 AI 示例，不当作用户修订。
- 默认不自动重写全文；只有用户明确要求直接成稿时，才走普通写作流程（`finch drafts`），
  不伪装成练习，不把成稿当风格证据。
- 单稿重写上限 `max_rewrite_rounds`（见 finch.yaml `quality_gates`）；超过即停。

## 保存用户原文与来源

- expression-practice 保存 `initial_attempt` 与每次修订，最终版与 lesson 一并落库。
- 不覆盖、不丢弃用户原文；版本可追溯。
- AI 示例与用户作品**分开记录**：`final_source`（user_authored | ai_example | mixed）+
  `source_note`（混合文本的来源片段）。风格证据只引用可追溯的用户创作片段；用户采纳的
  AI 句子不能因被确认就自动变成用户风格证据。

## Clarity editing (ASD-STE100-inspired)

`finch drafts revise` loads `skills/_shared/asd-ste100-inspired.md` by default
(`asd-ste100-inspired`; switch to `asd-ste100-technical` when the instruction asks
for technical procedure wording). See that file for CL01–CL08. Clarity editing
does not replace expression-practice (user writes first) and does not auto-publish.
清晰表达是**按需工具**，不把故事、幽默、留白压成技术说明口吻。
