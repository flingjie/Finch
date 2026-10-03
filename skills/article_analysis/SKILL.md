---
name: article_analysis
description: >
  分析一篇文章的表达任务、目标读者与预期变化、方法拆解与达成情况，并提炼
  2–3 个可迁移方法。用于“为什么这样写”“面向谁、是否有效”“这篇想让读者
  变成什么”等请求。只生成 ArticleReport，不重写、不做 AI 检测、不更新 VoiceProfile。
  风格表面特点请用 writing-style-analysis。可选从「降低理解成本」视角标注
  clarity_cost_reductions（受 ASD-STE100-inspired / 清晰表达规则启发，见共享规则文件）。
---

# article_analysis

看懂一篇文章为什么这样写：表达任务 → 读者变化 → 方法与效果 → 可借鉴技巧。
只产出报告，不改写、不生成分享稿、不自动进入 expression-practice。

## 执行

`finch article analyze --text/--file/--url [--json]`

呈现固定五段：表达任务、读者与预期变化、表达特点、目标达成情况、可借鉴方法。
`inferred=true` 时在表达任务标明「根据文章推断」。

## 与 writing-style-analysis 的路由

| 意图 | Skill |
|---|---|
| 风格特点、节奏、用词、可借鉴句式 | `writing-style-analysis` |
| 表达任务、读者变化、是否达成目的 | `article_analysis` |

意图不清时先问一句澄清；默认不同时跑两个分析。

## 清晰表达透镜（可选）

报告可含 `clarity_cost_reductions`：文章如何用结构、用词、步骤划分降低读者的理解成本。
判据与 `skills/_shared/asd-ste100-inspired.md` 中的 ASD-STE100-inspired / 简化技术英语原则
对齐（启发式，非官方 STE 合规）。本 Skill 仍只产出分析，不改写你的 Draft；改写走 `finch drafts revise`。

## 边界

- 不判断是否 AI 创作；不推断作者性格；不评价观点对错。
- 不重写原文；不生成分享稿；不自动改 VoiceProfile / practice-profile。
- 不落库；无可执行目标时「可执行性」可为不适用，不因此判失败。

## 参考

- `references/analysis-steps.md` — 五步判据。
- `references/output-contract.md` — ArticleReport 契约。
