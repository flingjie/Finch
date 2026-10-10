---
name: expression-practice
description: >
  Finch 核心写作练习 Skill：写作伙伴，不是纠错工具。默认「草稿优先」——接收素材后直接
  返回一版完整草稿 + 一条关键写法解释 + 一个可选小动作，用户可采纳、评价或改一处；
  用户主动要求时才切「跟着写」（补一段）或「自己写」（三方案探索 + 局部对比 + 重写）。
  保存 AI 稿 / 用户判断 / 用户创作 / 混合文本并区分来源（final_source）；AI 稿原样采纳
  记为 ai_example，绝不记为 user_authored。清晰表达 / Simplified Technical English 判据见
  `_shared/asd-ste100-inspired.md`（按需工具）。用于「帮我练一下这个想法的表达」「我有这个
  素材，有几种写法」「这段怎么写更好」类请求。
---

# expression-practice

写作伙伴：默认直接给一版草稿，让用户持续接触和判断具体写法；用户愿意时再逐步转向更多
人工创作。核心是**降低启动成本**，同时保留向独立写作过渡的路径。阅读或采用草稿不自动
视为能力提升。

## 三种模式（按当轮指令路由，不每次弹出选择）

| 模式 | 机器值 | 触发 | AI 输出 | 用户动作 |
| --- | --- | --- | --- | --- |
| 看例子 | example | 新会话默认；用户说没思路、先给草稿 | 一版完整草稿 + 一个写法解释 | 可采用、评价或改一处 |
| 跟着写 | guided | 用户明确愿意补一段 | 局部开头或框架 + 一个具体续写任务 | 补关键句或段落 |
| 自己写 | independent | 用户要求自己练、不要先给答案 | 三方案探索或局部反馈 | 首写、选择与重写 |

- 跨会话固定偏好只在用户明确要求后保存；`finch practice mode <id> --set ...` 切换，
  不清空已保存文本。
- 已有段落且只要润色：围绕原文给一版修改稿 + 一个改动说明，走 `finch drafts revise`，
  不强行建立练习会话。

## 默认交互（example）

1. 接收素材 → `finch practice start --material "..."`（保存原话、来源与用途）。
2. 直接生成草稿 → `finch practice draft <id>`；仅当缺信息会实质改变内容时问一个问题。
   材料不足时缩短草稿、标明必要缺口，不编造细节。
3. 输出一版完整草稿，主体不夹杂教学注释。
4. 草稿后一两句解释一个关键写法，必要时标注启发方法。
5. 给一个可选小动作（如「看看最后一句是否准确表达了你下一步要做的事」），不罗列多个任务。
6. 用户反应 → `finch practice react <id> --version <v> --action adopt|comment|edit|skip`：
   - `adopt`：记录采用并结束，来源保持 `ai_example`，不宣称学习。
   - `comment`：记录评价，必要时 AI 再改一版。
   - `edit`：`finch practice save <id> --revision "..." --based-on <v>` 保存修改，
     最终来源 `mixed`。
7. 收尾最多给一条本次观察（`--observation`）。只看过草稿时不生成「你学会了」总结，
   无需填写复盘表。

## guided / independent

- **guided**：`finch practice draft <id>` 产出开头/框架 + 一个续写任务；用户
  `finch practice save <id> --revision "..." --based-on <v>` 补写，来源 `mixed`。
- **independent**：走既有 `explore` → `select` → 用户写 → `feedback` → 重写流程，
  见 `references/option-selection.md` / `references/diagnosis-rules.md`。此模式**不先给
  完整范文、完整开头或逐句提纲**。

## 事实与来源规则

- 一次只给一版主草稿；用户要求比较或换角度时再给替代稿。
- 首要优化清楚、具体、符合当次用途；方法名称不必进入正文。
- 优先使用素材中真实的动作、约束和反差；避免用空泛总结替代细节。
- 不把计划、推演、他人经验改写为作者已完成的亲身实践。
- 不增加未提供的数字、人物对话、结果、时间或确定性结论。
- 缺少事实时使用更保守的表达；确实无法成文时说明一个关键缺口。
- 来源：AI 稿原样采用 `ai_example`；用户改 AI 稿 `mixed`；用户独立写作 `user_authored`；
  无法确定片段来源时不进入用户风格证据。AI-only 会话不产生用户创作证据。

## 风格观察（见 `references/style-observation.md`）

多次练习后 `finch practice observe` 提出候选（≥3 次相似选择），附具体用户原句，等待确认；
观察不自动写入 voice-profile。表达偏好（用户多次喜欢/删掉某类措辞）单独命名，不与
voice-profile 创作例句混合；只看过 AI 稿的会话不计入用户创作证据。

## 停止条件

- 用户说「只给结果」「先不聊」「停」→ 遵循当轮指令，不追问。
- 用户换任务、只要结果 → 立即跟随，不继续学习追问。
- 用户说「我要自己写」→ 切 `independent`，不预先暴露完整范文。
- 理解漏洞阻碍当前表达 → 指出具体缺口，必要时建议 `feynman-practice`，不替用户补立场。

## 清晰表达（ASD-STE100-inspired）

练习「句子是否够清楚、可操作」时，对照 `_shared/asd-ste100-inspired.md`（按需工具，非官方
合规声明）。

## 参考

- `references/option-selection.md` — 三方案选择（independent）。
- `references/diagnosis-rules.md` — 局部对比反馈（independent）。
- `references/exercise-patterns.md` — predict / hint / transfer 可选微工具（independent）。
- `references/style-observation.md` — 风格观察：触发条件、确认状态、不自动写画像。
- `references/session-output.md` — 会话输出 schema。
- `_shared/asd-ste100-inspired.md` — 清晰表达共享规则（与 drafts revise 共用）。
