# 外部调度：repo-discovery 每日窗口

Finch **不内置 cron**。用宿主调度（crontab / Codex Scheduled Task / launchd）调用同一 CLI。

## 推荐顺序

1. 若存在 `status=partial` 的最近 run → `uv run finch repos discover --resume <run_id>`
2. 否则 → `uv run finch repos discover --lookback-hours 24`
3. 可选：`uv run finch repos export --run <id> --format json --output var/outputs/repos-latest.json`

并发：同一 `run_id` 有 TTL 锁；勿并行多个 discover 写同一窗口。

配置见 `finch.yaml` → `repo_discovery`（与 `repository_discovery` 无关）。
