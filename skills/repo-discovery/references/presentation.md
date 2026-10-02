# repo-discovery 呈现配方

见 `_shared/agent-presentation.md`。本文件是形状与命令映射。

## 形状（榜单首页）

```text
今天（Asia/Shanghai 近 24h）抓到 80 个仓库 / 300 条去重推文，状态 completed。
热度公式：x_heat_v1_likes_only（tweet_heat = likes；unavailable: reposts, quotes, replies）。

第 1 页，共 80 条（共 2 页）。这不是完整结果——可说「下一页」或导出。

1. acme/widget  tag=agent  heat=42  mentions=3  stars=1200
   Agent harness for tool timeouts
   https://github.com/acme/widget
…
```

## 用户下一轮 → CLI

| 用户说 | 动作 |
|---|---|
| 抓取 / 刷新今天 | `uv run finch repos discover` |
| 继续上次 | `uv run finch repos discover --resume <run_id>` |
| 下一页 / 第 N 页 | `uv run finch repos list --run <id> --page N` |
| 只要 Agent | `… list --run <id> --topic agent` |
| 按 stars | `… list --run <id> --sort github_stars` |
| 导出全部 | `uv run finch repos export --run <id> --format csv --output …` |

内部保留 `run_id`；换日后不能用错 run。
