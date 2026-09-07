# Finch 每周任务（Codex Scheduled Task 草案）

> 将本文件内容作为每周复盘的提示词。`finch weekly` 已实现（读取草稿批准/修改/跳过记录、发布链接与互动数据）。

在 Finch 项目中执行 `finch weekly`。

分析本周：

1. 哪类 Commit 或公开讨论最容易形成有价值内容。
2. 哪类 Evidence 匹配精度最高。
3. 哪些草稿被频繁修改或跳过。
4. 哪些作者产生持续对话。
5. 下周应该继续、减少和实验什么。

## 说明

- **数据来源**：草稿批准/修改/跳过记录在 `DecisionRecord`，发布链接与互动数据在 `FeedbackRecord`。周复盘分析读取这些表。
- **边界**：只提出配置调整建议，**不自动修改质量门禁**，**不发布内容**。
- **人工补充**：对照 `finch weekly` 输出，人工补充「继续 / 调整 / 停止」的判断，再决定是否调整 `finch.yaml` 的 `quality_gates` 或 `weekly.py` 的阈值常量。
