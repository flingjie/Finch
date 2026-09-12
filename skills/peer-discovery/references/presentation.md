# peer-discovery 呈现配方

见 `_shared/agent-presentation.md` 的共享原则。本文件是形状真源与命令映射。

## 两种密度

| 密度 | 何时 | 内容 |
|---|---|---|
| **轻量列表** | 浏览「今天有哪些人」 | 8–12 张：谁 / 内容链接 / 为何推荐 / 切入点或值得了解；**无完整回复草稿** |
| **深度卡片** | 用户选中后准备互动 | 最多 **3** 条：完整草稿 + 风险 + 批准命令 |

已有对话区独立呈现，不占 8–12 发现名额。

## 形状（浏览）

```text
今天有 10 个新交流机会（另有 2 条需要继续的对话）：

1. @alice — 新实验：failure replay + diff
   内容：https://x.com/alice/status/…
   为何推荐：与你的 checkpoint 实践具体重叠，有新增数据
   切入点：问他们如何验证补偿在部分失败后仍成立
   → 准备：uv run finch connect prepare --opportunity opp_…

2. @dave — 跨领域：saga 补偿表
   …
（先了解也可：模式 learn，不必立刻回复）

回复「准备互动 1」「展开 2」「再给 5 位」或「今天只浏览」。
```

## 用户下一轮 → CLI

- `准备互动 N` → `uv run finch connect prepare --opportunity {opportunity_id}`（每次默认最多 3）
- `展开 N` → 补互补点/分歧/上下文；仍不自动生成 10 份草稿
- `再给 5 位` / `换一批` → `uv run finch connect more --snapshot {snapshot_id} --limit 5`（不重复、不调网络/LLM）
- `今天只浏览` → 只 `today`，不进入 prepare/approve
- 无快照或过期 → Skill 先 `uv run finch connect refresh`，再 `today`
