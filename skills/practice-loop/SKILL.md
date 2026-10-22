---
name: practice-loop
description: >
  围绕用户自己的一个问题，走通「问题 → 发现可尝试的实践 → 选中 → 最小行动 → 真实反馈 →
  结果判定 → 可复用结论（知识卡）」主链路。只做入口与编排（对话引导 + 提出假设/候选），
  状态写入经 finch problems / practice discover / attempts / knowledge 领域服务完成
  （确定性、幂等、无 LLM）。与 grounded-practice（理解他人处境）不同：本技能关心用户
  自己的实践尝试。
---

# practice-loop

帮用户把一个正在面对的真实问题，推进到一次可执行的小实践，记录结果，并沉淀为之后能复用的
知识卡。核心不是替用户想答案，而是让用户自己选一个最小行动、做一次、带回真实反馈。

本 Skill 只做入口与编排；状态写入全部经 CLI 领域服务（确定性、幂等）。CLI 返回的是
持久化后的实际状态，不要猜测成功。

## 三个入口

| 用户说 | 动作 |
|---|---|
| 我遇到一个问题 | `finch problems add --title ... [--why ...] [--desired-outcome ...] [--constraints ...]` |
| 我看到一份资料 | `finch materials capture ...`（走 material-library，已有） |
| 我试了一次（效果不好） | `finch attempts add --problem ... --attempt ... --observation ...` |

## 问题主流程（对应 CLI）

1. 建立问题卡（缺信息才补问 1–2 个，其余字段允许空/unknown）→
   `finch problems add --title "<一句话>" [--why ...] [--desired-outcome ...] [--constraints ...]`。
   得到 `problem_xxxxxxxx`。
2. 先查已有记录，再（按需）查公开来源 → `finch practice discover --problem <id>`
   （默认只查已有 ExpressionMethod / KnowledgeCard / PracticeAttempt；要查公开来源加
   `--external`，有界且失败不阻断）。得到 ≤3 张卡，每张含 claim / 为什么相关 / 证据 /
   适用条件 / 最小行动。不足 3 条不凑数。
3. 用户选中一张 → `finch attempts add --problem-id <id> --problem "<问题>" \
   --attempt "<最小行动>" --planned-action "<最小行动>" --hypothesis "<假设>" \
   --observation "<预期观察/留空>"`。
   用户没执行就保持 `open`，不因超时或模型推断标记完成。
4. 用户带回真实反馈 → `finch attempts verify <id> --result "<结果>" \
   [--result-status useful|mixed|ineffective|inconclusive]`。
5. 沉淀可复用结论（可选）→ `finch knowledge add --claim "<结论>" \
   --basis source_claim|user_reported|inferred [--attempt <id>] [--ref <URL>] \
   [--applicability ...] [--limitations ...]`。
   在另一个问题上实际复用后 → `finch knowledge reuse <id> --problem <另一个问题 id>`。

## 证据边界（硬规则）

- 只有用户原话才算 `user_reported` / 实际观察；AI 草稿/推测不写为亲历（`_shared/evidence-policy.md`）。
- 外部来源的「作者说有效」= `source_claim`，不是用户已验证；推断标 `inferred`，不升格为事实。
- 无来源的假设/建议必须显式标为 proposed，不伪造链接。
- 用户说「没用」→ 记录 `mixed` 或 `ineffective`，不自动改写成正向成果。

## 边界

- 不自动执行实践、不自动发送/发布；实践与发布都由用户决定并执行。
- 三个入口可以独立使用；没有资料也允许用户自己提出方法并标记为待验证假设。
- 只做一条主链路，不在每个步骤新建技能；讨论类诉求转 `topic-dialogue`，表达类诉求转
  `expression-practice` / `finch drafts`。

## 参考

- `_shared/evidence-policy.md` — 证据边界。
- `_shared/agent-presentation.md` — 调用 CLI 之后如何对用户说话。
- `_shared/dialogue-policy.md` — 任务后延伸点选择、授权边界与收束。
- `grounded-practice` — 理解他人处境的相邻回路（与本技能的自我实践区分）。
- `material-library` — 「资料」入口。
