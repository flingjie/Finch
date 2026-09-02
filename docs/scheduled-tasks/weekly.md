# Finch 每周任务（Codex Scheduled Task 草案）

> 将本文件内容作为每周复盘的提示词。注意：`finch run weekly` 是 Phase 8/9 的交付物，当前尚未实现——接入调度前需先完成该命令。

在 Finch 项目中执行 `$finch weekly`（即 `finch run weekly`）。

分析本周：

1. 哪类 Commit 最容易形成有价值内容。
2. 哪类 Evidence 匹配精度最高。
3. 哪些草稿被频繁修改或跳过。
4. 哪些作者产生持续对话。
5. 下周应该继续、减少和实验什么。

## 说明

- **数据来源**：草稿批准/修改/跳过记录在 `ReviewRecord`，发布链接与互动数据在 `FeedbackRecord`（Phase 6 已落表）。周复盘分析读取这些表。
- **边界**：只提出配置调整建议，**不自动修改质量门禁**，**不发布内容**。
- **依赖**：本命令尚未实现（见 `finch run --help` 只有 `daily`），先由人工依据 `finch review list` + 仓储数据手工复盘，待 Phase 8/9 补齐命令。
