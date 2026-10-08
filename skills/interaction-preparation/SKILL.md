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

- 选中机会：`finch connect prepare --opportunity <id> [--reaction "<用户原话>"]`（可重复；本批最多 5 =
  deep_prepare_limit；`--reaction` 只配一条机会）。**无任何反应时代码只允许准备澄清问题**；有反应时沿用
  机会的 `proposal.form`，正文中来自用户的句子标 `[reaction]`。对已 ready 的机会再传新 `--reaction`
  会重新生成。
- 本次风格指令：`--style-note "<如：再短一点>"`（优先级最高，覆盖默认简洁策略与声音画像）。
- 方法复用（可选）：`--method <id>`（可重复）/ `--methods-from-report <id>` /
  `--use-method-library`（只筛 `applicable_forms` 含 reply 的方法，最多 10；一次推理选中最多 1 个）。
- 回复草稿默认一条正文，一个重点；默认展示只突出正文，方法与判断经 `--json` 查看。
- 方法反馈：用户改完/发完后 `finch methods log-reply --method <id> --artifact <id>
  --verdict useful|mixed|not_fit [--note …]`（内容/事实/风格修正写进 note）。
- 查看机会：`finch connect daily`（首页 0-1 条首选）或 `finch connect daily --view browse`
  （50 人分层浏览）；选中前不要整表生成正文。
- 指定帖子评估：`finch connect assess --url <url> [--question …]`（入口 2，跳过全平台发现）
- 查看人物证据：`finch connect person <person_id>`（只读）。
- 演示/成果事实回填：`finch connect artifact-status --opportunity <id> --artifact <id>
  --execution ran_ok|ran_failed|unclear [--real-material] [--note …]`
  （唯一可将 `not_run` 改为 `ran_*` 的路径）。
- 用户已在平台发出后登记事实：`finch connections record --person <person_id> --url <url>
  --body "<正文>" --opportunity <id>`（记录真实互动，不等同于批准）。
- 获知回应后接续：`finch connections follow-up --opportunity <id> --reply-body "…"
  [--reply-url …]`（实质回应可生成关联的新 proposed 机会）。

## 输入回退（codex 触发）

用户未提供帖子链接时的回退规则见 `_shared/url-fallback.md`。

## 产出契约（Artifact）

- 正文是可审阅表达方案，不是作者已确认的立场；`source_refs` 回溯机会的证据引用。
- `execution_status`：默认 `not_run`；演示真正跑过后由用户经
  `finch connect artifact-status --execution ran_ok|ran_failed` 回填，
  代码不得把未运行标为已运行。可同时 `--real-material` 声明材料来自真实经历。
- 默认展示：对方具体问题、我能补充什么、来源 refs、正文、一个下一步。
- 机会若带 `problem`/`fit`/`next_action`（推荐参与机会的结构化字段），正文围绕 `problem`
  那一个具体点展开；缺少依据时按 `next_action.type=ask` 提问，不把交流变成产品推销。
- 正文中标 `[reaction]` 的句子只能复述用户对这条机会亲口说的话，不得外推；`[practice-id]` 句子只能来自
  confirmed practices。两者之外不得出现第一人称经历。
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

- 反应是用户在本次会话里亲口说的，可以第一人称写；但它不是 confirmed practice，不进
  `practice-profile.yaml`，Skill 不自动建议 `profile add`。
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
- `_shared/url-fallback.md` — 用户未提供帖子链接时，回退到当前对话中最近一次 URL（codex 触发）。
