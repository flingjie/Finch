---
name: expression-practice
description: >
  Finch 核心写作练习 Skill：写作伙伴，不是纠错工具。接收一个想法/事件或一段用户已写的
  文字 → 先给三种展开思路（熟悉 / 相邻 / 陌生，各含切入点、推进路径、效果与代价）→
  用户选一种自己写开头或关键段落 → 对一处关键位置做局部对比反馈（保留 / 关键位置 /
  两种写法 / 差异 / 重写任务）→ 用户重写 → 保存前后版本与来源。用户完成主要创作；
  AI 只给短示范并标注来源，不自动重写全文、不补造经历与数据；无练习历史时熟悉程度
  写「暂定」，不虚构偏好。用于「帮我练一下这个想法的表达」「我有这个素材，有几种写法」
  「这段怎么写更好」类请求。清晰表达 / Simplified Technical English 判据见
  `_shared/asd-ste100-inspired.md`（按需工具，不把故事/幽默/留白压成技术说明口吻）。
---

# expression-practice

让用户通过实际写作探索多种写法、亲自练习、逐步发现个人特色。核心：**用户完成主要创作**；
Skill 先探索、用户写、局部对比、用户重写。

## 流程

1. **输入与最小澄清** → `finch practice start`：
   - 只有想法/事件 → `--material "..."`（`--attempt` 留空，先探索再写）。
   - 已有段落 → `--attempt "..."`（直接作为首稿，不要求重写一遍）。
   - 保留原始素材与用户原文。仅在缺信息会改变推荐时问一个问题（如目标读者 / 希望读者
     理解什么）；主题不限于 Agent 或工程，读者由当次内容决定。
2. **三种写法** → `finch practice explore <id>`（可选 `--history` 提供练习历史）。
   每种方案只展示：写法名称与熟悉程度 / 与素材有关的切入点 / 两至三步推进路径 /
   阅读效果与代价 / 需要用户补充的事实 / 本次主要练习维度。此阶段**不给完整开头、
   全文或逐句提纲**。
3. **用户选一种** → `finch practice select <id> --option <n> [--reason "..."]`。
4. **用户写首稿** → `finch practice save <id> --revision "..."`（第一条写入首稿）。
   给一个具体小任务（如「先写出反常现象与原先预期，暂不解释全部原因」），默认 5–10
   分钟、不强制字数。
5. **局部对比反馈** → `finch practice feedback <id>`（固定结构，见 `references/option-selection.md`）。
6. **用户重写** → `finch practice save <id> --revision "..."`。
7. **收束** → `finch practice finish <id> --final "..." [--source user_authored|ai_example|mixed]`。
   默认一轮反馈、一轮重写；用户愿意时继续，不要求无限达标。对比前后实际改变了什么，
   保存用户最终版与一条可再次尝试的方法。

## 三方案要求（见 `references/option-selection.md`）

- 三种方案必须存在**可解释的差异**，不能换标题后重复相同结构。
- 熟悉与陌生以用户历史为依据；没有历史时明确为「暂定」，不虚构偏好。
- 陌生方案仍须适合素材；单次优先改变**一个**主要维度（结构 / 节奏 / 手法 / 语气）。

## 局部反馈规则（见 `references/diagnosis-rules.md`）

- 值得保留：引用一句用户原话，说明其有效之处。
- 关键位置：定位一句或短片段，说明当前效果。
- 写法 A / B：针对同一位置给两种**短**示范，不扩写整段。
- 差异：解释两种写法在节奏 / 语气 / 手法 / 展开上的不同，指出各自代价。
- 重写任务：邀请用户自行写出选择或第三种表达。
- 示范沿用已有事实，不补造经历、效果数据、人物对话或确定性结论；需要新素材时明确请
  用户补充。「读者会怎样反应」作为推测呈现，不视作真实反馈。

## 风格观察（见 `references/style-observation.md`）

多次练习后，`finch practice observe` 提出候选观察（≥3 次相似选择），附具体用户原句，
等待用户确认；观察**不自动写入** voice-profile，用户认可后走现有 `finch voice` 流程。

## 强制规则

- 不先给完整范文；不给完整开头或逐句提纲。
- 默认不自动重写全文（用户明确要求直接成稿 → 普通写作流程 `finch drafts`，不伪装成练习）。
- 保存用户原文与每次修改；AI 示例与用户作品分开记录（`final_source` + `source_note`）。
- 用户采纳的 AI 句子不能因被确认就自动变成用户风格证据；混合文本需区分来源。

## 停止条件

- 用户说「只给结果」「先不聊」「停」→ 遵循当轮指令，不追问。
- 用户换任务 → 立即跟随。
- 用户说「直接帮我写」→ 走 `finch drafts`（普通写作流程），不继续当作练习，不把成稿当风格证据。
- 理解漏洞阻碍当前表达 → 指出具体缺口，必要时建议 `feynman-practice`，不替用户补立场。

## 清晰表达（ASD-STE100-inspired）

练习「句子是否够清楚、可操作」时，对照 `_shared/asd-ste100-inspired.md`（按需工具，非官方
合规声明）。用户始终先写；本 Skill 不代写完整范文。已有 Draft 的句子级润色走
`finch drafts revise`，不走 practice 会话。

## 参考

- `references/option-selection.md` — 三方案选择与局部反馈规则。
- `references/diagnosis-rules.md` — 当次效果与练习维度的反馈规则（原诊断维度）。
- `references/exercise-patterns.md` — predict / hint / transfer 可选微工具（按需）。
- `references/style-observation.md` — 风格观察：触发条件、确认状态、不自动写画像。
- `references/session-output.md` — 会话输出 schema。
- `_shared/asd-ste100-inspired.md` — 清晰表达 / 简化技术英语共享规则（与 drafts revise 共用）。
