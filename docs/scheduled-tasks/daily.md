# Finch 每日任务（Codex Scheduled Task 草案）

> 将本文件内容作为 Codex Scheduled Task（或 ChatGPT 自动化）的每日提示词。Finch 只通过 CLI 执行，不自动发布。

在 Finch 项目中执行 Skill 架构的每日流程：

1. `finch ideas commit` 从最近 Commit 提炼 idea 候选（落库为 `ContentJob`，状态 `proposed`）。
2. `finch ideas search --topic <话题>` 从公开讨论提炼 idea 候选（不把外部帖子写成作者亲历）。
3. `finch ideas list` 查看候选；用 `finch ideas confirm <id>` 确认立场（`proposed → confirmed`），
   不写的用 `finch ideas skip <id> --reason <理由>` 标为 `skipped`。
4. `finch drafts create <id>` 从已确认 idea 生成草稿（`idea-to-draft` → `DraftService` + Critic，不自动发布）。
5. 用 `finch review list` 列出待审草稿，`finch review approve <draft_id>` 完成人工审核。
6. 不运行任何 Twitter 写命令，不发布内容。
7. 返回当日候选与草稿摘要。

## 说明

- **手动验收**：正式接入调度前，先在项目里手动连续运行 3 次，确认三次都能稳定产出可审核候选与草稿、且幂等不重复计费。
- **部分完成**：Twitter（opencli Bridge/登录）不可用时，`finch ideas search` 会失败，但 `finch ideas commit`
  照常完成——GitHub 来源的 idea 候选不因 Twitter 掉线而丢失。可用 `finch diagnose` 分别确认 `gh` 与 `opencli` 状态。
- **待审**：草稿生成后由 `finch review` 处理，不进入自动发布。
