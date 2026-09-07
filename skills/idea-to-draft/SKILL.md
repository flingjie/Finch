---
name: idea-to-draft
description: >
  把已确认的 Idea（ContentJob）写成一篇中文原创草稿（Draft）。只依据 job 语境（读者问题 /
  作者立场 / 核心主张 / scope）写正文，不搜索新来源、不绑定证据卡；草稿过 Critic（7 检查器，
  Safety 硬门禁）+ 有限 rewrite 后落库为 Draft + CriticReport。用于「把这个已确认的想法变成
  一篇可进入人工审核的草稿」类请求。
---

# idea-to-draft

把已确认的 Idea 写成草稿。职责单一：从 `ContentJob`（`status=confirmed`）生成一篇**进入人工审核**的 `Draft`，并落库一轮 Critic 报告。未确认的 idea 拒绝生成（`needs_confirmation`）。

本 Skill 只调用 Finch CLI（`finch drafts create`），不复制业务逻辑、不直接改数据库、不猜测状态。

## 职责

- 输入：一个已确认的 `ContentJob`（由 commit-to-idea / search-to-idea 产出并人工确认）。
- 输出：一篇 `Draft`（`kind` 随 job 的 `recommended_format`，`claims` 恒为空）+ 一轮 `CriticReport`。
- 未确认 / 不存在的 idea → 报错，不生成。
- 草稿只依据 job 语境写，**不搜索新来源、不绑定证据卡**。

## 执行

用 `finch drafts create <idea-id> [--json]`：

1. 加载 job；不存在抛 `KeyError`，`status != confirmed` 抛 `needs_confirmation`。
2. `draft_generation_key(idea 指纹, idea-to-draft 版本, 格式, voice 版本)` → `draft_id`；命中已有 Draft 直接返回（幂等）。
3. `write_original_from_job(runner, job)`：只依据 job 语境写正文（`claims=[]`）。
4. Critic（`idea_checker_suite`，去掉 EvidenceChecker，Safety 是硬门禁）+ 有限 rewrite（`max_rewrite_rounds`）。
5. `pass` / rewrite 用尽 → `upsert_draft` + `upsert_report`；Safety 硬失败（`needs_input`）→ 丢弃。

## 产出契约（`Draft`）

- `id` 由 `draft_generation_key` 决定（`draft_<key[:16]>`），同 idea + 同生成配置重复创建命中同一 `id`。
- `kind` ← job 的 `recommended_format`；`position_statement` ← job 的 `author_position.decision`。
- `claims` 恒为空（idea 草稿不绑定证据卡）；`language="zh"`；`run_id="idea"`。

## 强制规则

1. **不改变 author position**：`claim`/`decision`/`tradeoff` 只原样表达，不推翻、不改写已确认立场。
2. **不搜索新来源**：正文只依据 job 语境，不发起新的 commit / 讨论 / 检索，不加载额外证据。
3. **不补造事实**：不编造数字、指标、经历或来源；没有依据就不写。
4. **INFERRED·UNKNOWN 不写成亲历事实**：推断须带边界语言（「看起来/可能/在这次实现下」），未知不写；禁止把推断写成第一人称亲历。
5. **Evidence·Safety hard fail 不被平均分掩盖**：聚合去向由代码算（`aggregate_checks`），Evidence 的 `hard_fail` 与 Safety 的 `requires_human_input` 独立于平均分，fail-closed（命中即丢弃草稿）。
6. **不自动发布**：草稿只进入人工审核；`gh`/`opencli` 只读，发布由人工在 Finch 外部完成。

## 草稿写法

见 `references/draft-patterns.md`。

## 参考

- `references/draft-patterns.md` — 边界 / 口吻 / scope / 立场的写作判据。
- `_shared/author-position.md` — proposed vs confirmed，不改变立场。
- `_shared/evidence-policy.md` — 证据优先、推断不写成亲历、hard-fail 不被平均分掩盖。
- `_shared/voice-guide.md` — 作者口吻与篇幅。
- `_shared/quality-policy.md` — 有限 rewrite、不自动发布、分数由代码算。
