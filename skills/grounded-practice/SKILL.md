---
name: grounded-practice
description: >
  接地气学习环：观察一个真实他人的具体处境 → 用户说出自己的理解 → 单次追问 →
  一个动作（observe/ask/help/pause）→ 真实反馈 → 前后判断对比。只关心理解他人处境
  （共情/帮助），与 ActiveProblem+PracticeAttempt（用户自己的实践尝试）不同。
  假设与追问由本 Skill 提出并传入领域命令；领域服务确定性落库、不调 LLM。
---

# grounded-practice

帮用户从**具体的人和处境**出发，理解现实约束，用对方能理解的语言交流，并通过真实反馈
修正判断。核心不是替用户发消息，而是让用户先说出自己的理解、再被现实纠正一次。

本 Skill 只做入口与编排（对话引导 + 提出假设/追问/草稿）；状态写入经 `finch grounded`
领域服务完成（确定性、幂等、无 LLM）。CLI 返回的是持久化后的实际状态，不要猜测成功。

## 流程（对应 CLI）

1. 拿到一条关于**他人**处境的具体素材 → `finch grounded start --text/--file/--url ...`
   （可选 `--person <id>` 关联联系人；可选 `--request-id` 稳定去重）。得到 `id`。
2. 让用户**自己**说理解（不替他填、不把 AI 草稿记为用户判断）→
   `finch grounded reflect <id> --understanding "<用户原话>"`。
3. 提出**一个**尚未证实的假设 + **一个**最值得确认的问题 + 一个动作 + 动作草稿 →
   `finch grounded action <id> --action observe|ask|help|pause --hypothesis "..." \
   --question "..." --action-draft "..."`。
   默认一次只问一个问题（`grounded.max_questions=1`），不变成问卷。
4. 用户**手动**执行动作（发送/交流/提供帮助）→ `finch grounded record-action <id>`。
   仅生成草稿不记为已执行；只有 `record-action` 才进入 waiting_feedback。
5. 真实反馈回来后 → `finch grounded feedback <id> --text "..." --evidence
   source_quote|user_reported|ai_inferred [--ref ...] [--revised-understanding "..."
   | --keep] [--basis "..."]`。
6. 展示前后对比 → `finch grounded show <id>`；中断/恢复 → `finch grounded pause|resume <id>`。

## 证据边界（硬规则）

- `--evidence` 三态：`source_quote`（对方原文）/ `user_reported`（用户转述）/
  `ai_inferred`（AI 推测）。对方自述 ≠ 独立验证的事实；模型模拟对话只是练习，永不写为真实反馈。
- 无思路时可给两个「可能解释」，明确标为假设，选择后记录的是**用户自己的**判断。
- 无回复保持未知，不记为拒绝、不催发消息、不推断对方需求或动机。

## 草稿复用

- `action_draft` 复用 `reply-crafting` 的简洁风格：一个重点、一条正文、短、0–1 个问题、
  禁纯赞美/假装使用过产品/补造经历。
- 复用 `interaction-preparation` 的「可审阅成果」契约：草稿是待审方案，不等于已执行。
- 第一人称亲历规则照抄 `_shared/evidence-policy.md`。

## 边界

- 不把 AI 猜测写成用户判断：`--understanding` / `--revised-understanding` 只能是用户原话
  或显式 `--keep`（暂不改变）。
- 不自动发送、不自动行动；发送由用户决定并执行，事后 `record-action` 登记。
- 私人交流默认不进公开草稿，发布前由用户选择可用内容。
- 区分两个 pause：`--action pause`（本轮动作选「暂时跳过」）≠ `finch grounded pause <id>`
  （中断会话，status=paused）。
- 素材只有一句抱怨 → 保留未知（约束 evidence=unknown），不编造职业/预算/动机/成本。
- 一条场景一个追问、一个动作；不整篇展开解释。

## 参考

- `reply-crafting` — 草稿风格与不发布边界。
- `interaction-preparation` — 可审阅成果契约。
- `_shared/evidence-policy.md` — 证据边界。
- `_shared/agent-presentation.md` — 调用 CLI 之后如何对用户说话。
- `_shared/dialogue-policy.md` — 任务后延伸点选择、授权边界与收束。
