# 表达契约（Expression Contract）

expression-practice 与 idea-to-draft 共用的表达边界。核心：表达训练不代写；代写不冒充训练。

## 默认不自动 rewrite

- 练习中，用户先表达，Skill 只诊断 + 追问，不先给完整范文。
- 一次只追问一个关键问题；不一次性抛一串问题。
- 只有用户明确说「直接帮我写」才转 idea-to-draft（Assist 模式）。

## 有限 rewrite

- 单稿重写上限 `max_rewrite_rounds`（见 finch.yaml `quality_gates`）；超过即停。
- revise 只改表达，不改已确认立场；用户明确指示才重写。

## 保存用户原文

- expression-practice 保存 initial_attempt 与每次 revision，最终版与 lesson 一并落库。
- 不覆盖、不丢弃用户原文；版本可追溯。
