> **已过时（superseded）**：本文描述的 daily 编排（`run_daily` / `ideas search`）已移除。
> 当前每日入口是 `finch connect daily`（可选 `--refresh`）+ `finch connect today` /
> `finch conversations follow-up` + 按需 `finch ideas create` / `connect create`。
> 保留作历史参考，不反映当前架构。不要新建 `finch run daily`。

# Finch 每日任务（历史草案）

当前推荐节奏（Build-in-Public）：

1. `finch connect daily`（或 `today`）看跟进对话 + 交流机会 + 可分享素材。
2. 选中机会：`finch connect prepare --opportunity <id>`（默认提纲，不批量完整草稿）。
3. 或手动：`finch connect create --input <url> --note "..."` / `--from-idea <id>`。
4. 用户平台发送后：`finch connect approve` → `finch connect record --url …`。
5. `finch conversations follow-up` 恢复上下文。
6. 不运行任何 Twitter 写命令，不自动发布。
