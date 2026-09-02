# Finch 每日任务（Codex Scheduled Task 草案）

> 将本文件内容作为 Codex Scheduled Task（或 ChatGPT 自动化）的每日提示词。Finch 只通过 CLI 执行，不自动发布。

在 Finch 项目中执行 `$finch daily`（即 `finch run daily`）。

1. 使用 `gh` 同步配置仓库最近 72 小时的 Commit、PR 和 Issue。
2. 提取工程事件并更新 Evidence Card。
3. 使用 `opencli` 的只读 Twitter 命令执行已配置查询。
4. 生成最多 5 条回复候选和最多 1 条原创候选。
5. 每条草稿必须绑定可追溯证据。
6. 不运行任何 Twitter 写命令，不发布内容。
7. 返回 Finch Daily Brief。

## 说明

- **手动验收**：正式接入调度前，先在项目里手动连续运行 3 次 `finch run daily`，确认三次都能稳定产出可审核 Brief、且重放不重复计费。
- **部分完成**：Twitter（opencli Bridge/登录）不可用时，Graph 会在收集推文阶段进入 `BLOCKED`，但此前的 GitHub 同步与 Evidence 提取已照常完成——GitHub 证据分支不因 Twitter 掉线而丢失。可用 `finch diagnose` 分别确认 `gh` 与 `opencli` 状态。
- **待审**：成功产出草稿后 Graph 停在 `WAITING_FOR_REVIEW`，用 `finch review list` 查看、`approve`/`revise`/`skip` 处理。
- **延迟项**：主 plan §6.2 的「证据不足则归档为学习材料」「最多提出 3 个问题 / `NEEDS_INPUT`」推迟到 Phase 9 之后，本草案不含这两条。
