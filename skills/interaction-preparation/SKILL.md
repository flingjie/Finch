---
name: interaction-preparation
description: >
  为已选中的交流机会制作一份可审阅的贡献（方法卡 / 回复草稿 / 澄清问题 / 案例 / 演示说明）。
  输入选中的 Opportunity（`finch connect daily` 中由用户选中），产出带正文与来源的 Artifact，
  状态 ready。用于「帮我给这个人准备一次有价值的互动」「先给一个二十分钟内能做的测试」类请求。
---

# interaction-preparation

为用户**选中**的机会制作一次有价值的贡献。职责：把「值得交流的人 + 一条内容」转成一份
**可审阅成果**（`Artifact`：正文 + `source_refs` + `execution_status`）。机会里的
`proposal.form` 决定形式（`reply_draft` / `method_card` / `clarifying_question` / `case` /
`demo`），正文由代码落库后经 `connect prepare` 直接返回，不复制业务逻辑、不直接改数据库。

没有单独的审批状态机：`connect prepare` 把机会从 `selected` 走到 `ready` 并返回正文；
用户是否采用 / 修改 / 跳过由对话决定，不落 `InteractionProposal`。

## CLI

- 选中机会：`finch connect prepare --opportunity <id>`（可重复；本批最多 5 = deep_prepare_limit）
- 查看机会：`finch connect daily`（首页 0-1 条首选）或 `finch connect daily --view browse`
  （50 人分层浏览）；选中前不要整表生成正文。
- 查看人物证据：`finch connect person <person_id>`（只读）。
- 用户已在平台发出后登记事实：`finch connections record --person <person_id> --url <url>
  --body "<正文>" --opportunity <id>`（记录真实互动，不等同于批准）。

## 产出契约（Artifact）

- 正文是可审阅表达方案，不是作者已确认的立场；`source_refs` 回溯机会的证据引用。
- `execution_status`：`not_run`（方法卡/回复草稿未运行）；演示是否真正运行由用户后续更新，
  代码不得把未运行标为已运行。
- 默认展示：对方具体问题、我能补充什么、来源 refs、正文、一个下一步。
- **允许零结果**：无具体观察/问题/贡献时输出「暂不回复」及原因，不强行生成空泛回复。
  **具体观察 + 诚实提问**即可作为有效建议，无需个人经验；只有声称亲历（我用过/测试过）
  才要求个人证据。

## 向用户呈现

见 `_shared/agent-presentation.md` 与 `references/presentation.md`。本 Skill 做编辑式推荐，
不贴 CLI 原文。先给正文与来源，再给「采用 / 改 / 跳过」。两个用户决策始终分开——
**投入方向**（值不值得做）与**公开表达**（是否代表我）；正文完成不等于作者认领或已发布。

内部保留序号 → `opportunity_id`。完成后按 `_shared/dialogue-policy.md` 做一次延伸点检查
（无有效点就自然结束），延伸不得抢占交付物。

## 边界

- 正文只是待审成果，真正发送前经人工确认；发送后经 `finch connections record` 登记事实。
- `topic-dialogue` 模拟讨论不是真实互动，不得当作已发生交流或关系进展；准备回复须有真实
  帖子且用户明确要求。
- 外部帖子只是信号，不是个人证据；正文只允许提问或明确标注推测，禁止虚构「我测试过」/
  「我也遇到过」；仅 `evidence_status=observed` 时可声称亲历。
- 建议试用、生成正文但未行动：实验完成数与已发送数不增加。
- 一条建议一个下一步；邀请 ≠ 已发送。

## 参考

- `references/interaction-contract.md` — 贡献形式与正文约束。
- `_shared/agent-presentation.md` — 调用 CLI 之后如何对用户说话（共享原则）。
- `references/presentation.md` — 本 Skill 的形状与「采用 / 改 / 跳过」映射。
- `_shared/evidence-policy.md` — 回复提纲 vs 原创草稿的证据要求。
- `_shared/dialogue-policy.md` — 任务后延伸点选择、授权边界与收束
