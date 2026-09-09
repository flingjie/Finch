# idea-to-draft 呈现配方

见 `_shared/agent-presentation.md` 的共享原则。本文件是形状真源与命令映射。

## 形状

```text
草稿已生成，质检通过，等待你审核。

> 把编排器改成确定性图后，失败可以重放——问题从「猜现场」变成「重跑同一条路径」。

状态：未发布。Critic 细节默认不展开；你说「看质检」再补。

回复「采用」「改：收紧开头」或「跳过」。
```

素材来自 `finch drafts create` / `show`（正文 + 质检结论 + 卡末审核命令）。主文不贴 `draft_*` / `critic_rounds`。

## 用户下一轮 → CLI

- `采用` → `uv run finch review approve {draft_id}`
- `改：…` → `uv run finch drafts revise {draft_id} --instruction "..."`（或 `review revise`）
- `跳过` → `uv run finch review skip {draft_id} --reason "..."`
- `看质检` → `uv run finch review show {draft_id}`（可含 critic 明细）
