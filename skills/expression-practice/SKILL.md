---
name: expression-practice
description: >
  Finch 核心表达训练 Skill：通过实际表达提升能力。选择 Idea（或无需 Idea）→ 用户先表达
  → 诊断一个最大问题 → 选一个下一步动作（revise / predict / hint / transfer）→ 用户响应
  → 对比前后 → 保存最终版。可练预测读者理解、逐级提示、迁移到新受众或篇幅；正文已清楚时
  允许零问题结束。绝不先给范文、一次只处理一个障碍、默认不自动 rewrite、保存用户原文和
  每次修改。用于「帮我练一下这个想法的表达」「我想自己写、你来追问」「帮我练习清晰表达」
  「按 ASD-STE100-inspired 练一句」类请求；清晰表达 / Simplified Technical English（简化
  技术英语）判据见 `_shared/asd-ste100-inspired.md`，用户必须先写，Skill 只诊断与追问。
---

# expression-practice

让用户通过实际表达提升能力。核心：用户先表达，Skill 只诊断 + 追问（或给出最小帮助），
不代写。

## 流程

**一轮训练（循环）**

1. 用户先表达 → `finch practice start [--idea <id>] [--method <id>] [--audience "..."] [--goal "..."] --attempt "..."`。
   **无 Idea 也可训练**，跳过选 Idea 步骤；有上下文时推断受众与目标，仅在缺信息会改变诊断时问一个必要问题。
2. 诊断 → `finch practice diagnose <session-id> [--exercise auto|revise|predict|hint|transfer]`。
   每次只挑「最大」的一个障碍；反馈引用用户一处原文，解释它为何妨碍当前读者，不列七维分数表。
3. 用户响应：
   - 修订 → `finch practice save <session-id> --revision "..."`（或 `respond --kind revision`）。
   - 预测 → `finch practice respond <session-id> --turn <id> --kind prediction --text "..."`。
   - 迁移 → `finch practice respond <session-id> --turn <id> --kind transfer --text "..."`。
   - 跳过该轮 → `finch practice respond <session-id> --turn <id> --skip`。
4. 重复 2-3，直到收尾。

**收尾**

- 用户说结束 / 目标已达 / 连续两轮无新信息 → 结束（正文已清楚时允许零问题结束）。
- `finch practice finish <session-id> --final "..." [--verdict worth_reuse|practice_again|not_for_me --note "..."]`。
- 收尾包含：最终表达 + 首稿到终稿的一处具体变化 + 一条可复用经验。没做迁移练习就写「迁移尚未检验」，不宣称已掌握。

**按表达方法练（可选分支）**

1. （可选）`finch methods list` / `show` 选一条已保存方法。
2. 用户先表达 → `finch practice start --method <id> --attempt "..."`（可同时 `--idea <id>`）。
3. 诊断 / 响应 / 修订与 Idea 流程相同。
4. 结束并反馈方法 → `finch practice finish ... --verdict ... [--note "..."]`。
5. 反馈优先问「这条方法是否值得再用」，不检查「有没有用上技巧」或代写达标范文。

## 可选练习动作

- **预测（predict）**：用户先预测「读者看完会认为你在建议什么」；AI 再比较意图与文本，
  给一种**有文本依据**的可能误读（明确标注为模拟阅读，不是用户调研结果）。默认只给一种重要误读。
- **提示（hint）**：仅用户卡住或明确求助时启用，逐级给最小帮助（见下）。
- **迁移（transfer）**：正文已讲清楚后，改**一个**条件（受众 / 篇幅 / 例子）重新表达；
  用户先写，AI 再比较是否保留核心意思与边界。练习可跳过。

## 逐级帮助（仅按需）

1. 问题提示（例：把「效率提高」换成谁少做了哪一步）。
2. 局部骨架（例：原来需要___，现在___，但新增了___）。
3. 局部参考：只示范一两句，标注为 AI 参考，不当作用户修订。

不因回答慢或时间经过自动升级提示；用户要求完整代写时转 `idea-to-draft`。

## 检查维度

是否真正理解 / 有自己的判断 / 说明因果关系 / 与目标同行有关 / 具体 / 像用户自己 /
留下值得回应的空间（见 `references/diagnosis-rules.md`）。

## 强制规则

- 不先给完整范文。
- 一次只处理一个最大障碍，不列清单、不每轮检查所有维度。
- 默认不自动 rewrite（只有用户明确要求代写才转 idea-to-draft）。
- 保存用户原文和每次修改（PracticeSession）：首稿与每次修订不被覆盖；预测与迁移回复
  不写入正文，只记在对应轮次里。

## 停止条件

- 用户说「只给结果」「先不聊」「停」→ 遵循当轮指令，不追问。
- 用户换任务 → 立即跟随。
- 用户说「直接帮我写」→ 转 `idea-to-draft`，不继续训练。
- 事实或理解不清 → 指出具体缺口，必要时建议 `feynman-practice` / `topic-dialogue`，不替用户补立场。

## 清晰表达（ASD-STE100-inspired）

练习「句子是否够清楚、可操作」时，对照 `_shared/asd-ste100-inspired.md`（ASD-STE100-inspired，
中文适配，非官方合规声明）。用户始终先写首稿与修订；本 Skill 不代写完整范文。已有 Draft 的
句子级润色走 `finch drafts revise`，不走 practice 会话。

## 参考

- `references/diagnosis-rules.md` — 诊断维度与逐轮规则。
- `references/exercise-patterns.md` — predict / hint / transfer 的少量例子与不适用情形。
- `references/session-output.md` — 会话输出 schema。
- `_shared/asd-ste100-inspired.md` — 清晰表达 / 简化技术英语共享规则（与 drafts revise 共用）。
