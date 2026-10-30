---
name: problem-discovery
description: >
  从真实讨论中发现问题并帮用户选择下一步。输入已导入的公开讨论（GitHub issue / URL /
  文本），调用 Finch 抽取问题卡片，用确定性概览（卡片数 / 来源范围 / 缺失字段）做真源，
  再由本 Skill 挑最多 3 个重点问题，给出受阻场景 / 已知事实 / 可能影响 / 关键未知 /
  建议动作，并支持「展开第二个」「看剩下的」等自然语言连续查看。用于「这些讨论里有什么
  真实问题」「哪个问题值得先看」「下一步怎么验证」类请求。
---

# problem-discovery

从真实讨论中抽取「真实问题」卡片，帮用户看懂主要受阻场景、判断哪个值得先看、选择下一步验证。
事实（evidence）/ 假设（hypotheses）/ 未知（unknowns）在结构上分开；卡片由确定性 Python 抽取 +
门禁，本 Skill 只调 CLI 并呈现，不做抽取、不编造证据、不写总分。

## CLI

```bash
uv run finch problem-discovery add --repo <owner/repo> | --url <链接> | --text <正文>
uv run finch problem-discovery discover [--refresh] [--json]
uv run finch problem-discovery summary [--json]
uv run finch problem-discovery list [--json] [--full]
uv run finch problem-discovery show <problem_id> [--json]
uv run finch problem-discovery present <problem_id...> [--batch <id>]
uv run finch problem-discovery last-presented [--json]
```

- `add`：导入公开讨论（GitHub 仓库 / URL / 粘贴文本），返回 source_ref。
- `discover`：抽取问题卡片入库（`--refresh` 强制重抽，否则幂等复用缓存）。
- `summary`：确定性概览（卡片数 / 来源范围 / 缺证据 / 缺角色 / 最近一次运行）。本 Skill 的
  概览与计数一律以它为真源，不要自己数卡片。
- `list` / `show`：读卡片（`show` 需要 problem_id，正文不向用户展示）。
- `present` / `last-presented`：记录 / 读取「展示顺序 → problem_id」映射，供「展开第二个」恢复。

## 流程

1. 未导入来源时先 `add`；已导入则跳过。
2. `discover --json` 抽卡片；`summary --json` 取确定性概览。
3. 从卡片里挑最多 3 个重点问题（定性、有解释，不写总分，不生成精确无依据的排名）。
4. 按 `references/presentation.md` 组织返回，末尾 `present <本轮展示的有序 problem_id...>`
   记录映射，再按 `_shared/dialogue-policy.md` 做一次延伸点检查。

## 向用户呈现

见 `_shared/agent-presentation.md` 与 `references/presentation.md`。默认不逐条铺开卡片：
先给概览，再给最多 3 个重点问题（每项五件事），再给判断边界，最后给 2–3 个自然语言继续入口。

## 边界

- 只读；`gh` / `opencli` 只读，不外发、不私信、不评论。
- 角色不明不编造用户画像；来源缺失说明「来源链接缺失」，不拼造 issue 编号或链接。
- 单仓库公开 issue 覆盖有限：不自动称「普遍需求」「商业机会」；issue 关闭 ≠ 已修复，
  当前状态未核对时写「待核实」。
- 卡片数量 ≠ 需求数量；本 Skill 不给出「问题组数量」或「独立反馈者数」（尚未合并，P2 才落地）。
- 影响未知标「未知」；推荐访谈不等于发起访谈，对外发送必须获明确授权。

## 参考

- `references/presentation.md` — 默认返回结构与「展开 / 看剩下的 / 换一批」映射。
- `_shared/agent-presentation.md` — 调用 CLI 之后如何对用户说话（共享原则）。
- `_shared/dialogue-policy.md` — 任务后延伸点选择、授权边界与收束。
