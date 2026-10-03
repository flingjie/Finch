---
name: article_analysis
description: >
  分析一篇文章的表达任务、目标读者与预期变化、方法拆解与达成情况，并纳入
  写作风格七维（开头/结构/节奏/用词/立场/具体性/读者关系）与可迁移技巧。
  用于「为什么这样写」「分析风格」「面向谁、是否有效」「节奏和用词有什么特点」
  等请求。只生成 ArticleReport，不重写、不做 AI 检测、不更新 VoiceProfile。
  可选从「降低理解成本」视角标注 clarity_cost_reductions（受 ASD-STE100-inspired /
  清晰表达规则启发，见共享规则文件）。
---

# article_analysis

看懂一篇文章为什么这样写：表达任务 → 读者变化 → 表达特点 → 写作风格七维 → 方法与效果 → 可借鉴技巧。
只产出报告，不改写、不生成分享稿、不自动进入 expression-practice。

## 执行

`finch article analyze --text/--file/--url [--json]`

呈现固定段落：表达任务、读者与预期变化、表达特点、**写作风格**（七维 + 可迁移技巧等）、目标达成情况、可借鉴方法。
`inferred=true` 时在表达任务标明「根据文章推断」。`--json` 时 `style` 为嵌套 `StyleBlock`。

## 清晰表达透镜（可选）

报告可含 `clarity_cost_reductions`：文章如何用结构、用词、步骤划分降低读者的理解成本。
判据与 `skills/_shared/asd-ste100-inspired.md` 中的 ASD-STE100-inspired / 简化技术英语原则
对齐（启发式，非官方 STE 合规）。本 Skill 仍只产出分析，不改写你的 Draft；改写走 `finch drafts revise`。

## 边界

- 不判断是否 AI 创作；不推断作者性格；不评价观点对错。
- 不重写原文；不生成分享稿；不自动改 VoiceProfile / practice-profile。
- 不落库；无可执行目标时「可执行性」可为不适用，不因此判失败。

## 参考

- `references/analysis-steps.md` — 任务与风格判据。
- `references/output-contract.md` — ArticleReport 契约（含 `style`）。
