---
name: expression-practice
description: >
  Finch 核心表达训练 Skill：通过实际表达提升能力。选择 Idea → 用户先表达 → 诊断最大问题
  → 追问一个问题 → 用户重新表达 → 对比前后 → 保存最终版。绝不先给范文、一次只问一个
  关键问题、默认不自动 rewrite、保存用户原文和每次修改。用于「帮我练一下这个想法的表达」
  「我想自己写、你来追问」「帮我练习清晰表达」「按 ASD-STE100-inspired 练一句」类请求；
  清晰表达 / Simplified Technical English（简化技术英语）判据见 `_shared/asd-ste100-inspired.md`，
  用户必须先写，Skill 只诊断与追问。
---

# expression-practice

让用户通过实际表达提升能力。核心：用户先表达，Skill 只诊断 + 追问，不代写。

## 流程

**按 Idea 练表达（默认）**

1. 选 Idea（`finch ideas list` 挑选）。
2. 用户先表达 → `finch practice start --idea <id> --attempt "..."`。
3. 诊断最大问题 → `finch practice diagnose <session-id>`。
4. 用户重新表达 → `finch practice save <session-id> --revision "..."`。
5. 重复 3-4 直到满意，或用户说「直接帮我写」→ 转 `idea-to-draft`。
6. 保存最终版 → `finch practice finish <session-id> --final "..."`。

**按表达方法练（可选分支）**

1. （可选）`finch methods list` / `show` 选一条已保存方法。
2. 用户先表达 → `finch practice start --method <id> --attempt "..."`（可同时 `--idea <id>`）。
3. 诊断 → `finch practice diagnose <session-id>`（与 Idea 流程相同）。
4. 修订 → `finch practice save <session-id> --revision "..."`（不变）。
5. 结束并反馈方法 → `finch practice finish <session-id> --final "..." --verdict worth_reuse|practice_again|not_for_me [--note "..."]`。
6. 反馈优先问「这条方法是否值得再用」，不检查「有没有用上技巧」或代写达标范文。

## 检查维度

是否真正理解 / 有自己的判断 / 说明因果关系 / 与目标同行有关 / 具体 / 像用户自己 /
留下值得回应的空间（见 `references/diagnosis-rules.md`）。

## 强制规则

- 不先给完整范文。
- 一次只追问一个关键问题。
- 默认不自动 rewrite（只有用户明确要求代写才转 idea-to-draft）。
- 保存用户原文和每次修改（PracticeSession）。

## 清晰表达（ASD-STE100-inspired）

练习「句子是否够清楚、可操作」时，对照 `_shared/asd-ste100-inspired.md`（ASD-STE100-inspired，
中文适配，非官方合规声明）。用户始终先写首稿与修订；本 Skill 不代写完整范文。已有 Draft 的
句子级润色走 `finch drafts revise`，不走 practice 会话。

## 参考

- `references/diagnosis-rules.md` — 诊断维度。
- `references/session-output.md` — 会话输出 schema。
- `_shared/asd-ste100-inspired.md` — 清晰表达 / 简化技术英语共享规则（与 drafts revise 共用）。
