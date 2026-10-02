---
name: repo-discovery
description: >
  抓取 X / Twitter 上分享的 GitHub 项目，去重后按透明 X 互动热度排序，支持 Agent 视图、
  分页与完整导出。用于「抓取今天推特分享的 GitHub 项目」「显示今天所有 Agent 项目」
  「按 stars 排序」「继续上次没抓完的」「导出完整榜单」类请求。不评估代码质量、不读
  Practice、不生成推荐追问或 ContentJob。
---

# repo-discovery

从公开 X 讨论中收集被分享的 GitHub 仓库，聚合互动与基础 GitHub 指标，输出**完整热度榜**。
分页只影响展示。无候选数 / 推荐数上限。确定性 Python 负责抓取与排序；本 Skill 只调 CLI 并呈现。

## CLI

```bash
uv run finch repos discover [--lookback-hours 24] [--resume <run_id>] [--json]
uv run finch repos list --run <run_id> [--sort x_heat|mentions|github_stars|latest] \
  [--topic all|agent] [--page 1] [--page-size 50] [--json]
uv run finch repos export --run <run_id> --format json|csv --output <path> [--sort …] [--topic …]
```

默认热度公式（opencli 当前仅返回 likes）：`x_heat_v1_likes_only` — 项目热度 = 去重推文 likes 之和。
`views` 可展示但不入热度；reposts/quotes/replies 标记为 unavailable。

## 向用户呈现

见 `references/presentation.md` 与 `_shared/agent-presentation.md`。

- 先给结果：窗口、刷新/公式、完成状态、仓库数；再给当前页列表。
- 明确「第 N 页，共 M 条」；**禁止把第一页说成完整结果**；提示可翻页或 `export`。
- `partial`：说明覆盖缺口，给出 `discover --resume <run_id>`。
- `failed`：不能说「今天没有项目」；说明来源失败。
- `completed` 且 0 仓库：可以说零结果。

完成后按 `_shared/dialogue-policy.md` 做一次延伸点检查（无价值就自然结束）。不要为榜单硬凑实践或回复草稿。

## 边界

- 不读取源代码 / README 做能力推断；简介用 GitHub description，空则「暂无简介」。
- 不调用 PracticeProfile、Opportunity、reaction、ContentJob。
- 不自动 Star / Follow / 回复。
- 与 `settings.repository_discovery`（用户自有仓库）无关；与 `connect daily` 人物发现无关。
- 外部调度（cron/Codex）应优先 `--resume` 未完成窗口，再开新窗口；Finch 进程内无 cron。

## 参考

- `references/presentation.md`
- `docs/superpowers/specs/2026-10-02-repo-discovery-design.md`
- `_shared/dialogue-policy.md`
