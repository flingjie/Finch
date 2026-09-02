# Finch 两周试运行（Phase 9 框架）

> 真实两周试运行由你手动执行。本文件给出流程与验收指标。框架以「脚本 + 记录机制」交付，不做自动发布。

## 前置

- `uv sync` 完成；`gh` 已登录且有目标仓库读取权限；`opencli` + Chrome 已登录 Twitter/X。
- 首次 `finch init` 初始化数据库。
- 校验环境：`finch diagnose`（分别报告 `gh` 与 `opencli`）。

## 每日流程（约 15 分钟以内）

1. 运行 `bash scripts/trial.sh`（等价于 `finch run daily` + `finch review list`）。
2. 若 Graph 停在 `WAITING_FOR_REVIEW`：`finch review show <DRAFT_ID>` 看全文，然后 `approve` / `revise` / `skip`。
   - `revise` 需要 `--file revised.md` 提供你的修订。
   - `skip` 需要 `--reason evidence_insufficient|not_relevant|low_quality|not_now|other`。
3. 批准后你在 Finch 外部手动发布，然后记录发布链接与互动数据：
   `finch review feedback <DRAFT_ID> --url <URL> --metrics '{"likes":N,"replies":N}'`。
4. **手动记录审核耗时**（从打开 Brief 到完成审核的分钟数）到你的运行日志。

## 每周流程

1. 运行 `finch run weekly`，得到批准率、跳过原因与已发布候选的汇总。
2. 人工补充分析：哪类 Commit 产生价值、哪类 Evidence 匹配精度高、哪些作者产生真实对话、下周该继续/减少/实验什么。
3. **只提配置建议，不自动修改质量门禁。**

## 记录机制（已由代码落库）

- 审核决策（approve/revise/skip + 理由 + diff + 时间戳）→ `ReviewRecord`。
- 发布链接与互动数据 → `FeedbackRecord`。
- 每日草稿 → `DraftRecord`。
- 审核耗时由你手动记入运行日志（每日流程第 4 步）。

## 验收指标（主 plan §12.4）

| 指标 | 目标 |
|---|---|
| engineering_event_precision | ≥ 80% |
| evidence_match_precision | ≥ 75% |
| valid_source_links | 100% |
| unsupported_personal_claims | 0 |
| sensitive_content_leaks | 0 |
| twitter_write_actions | 0 |
| draft_approval_rate | ≥ 60% |
| daily_review_time | ≤ 15 分钟 |
| replay_success_rate | 100% |

## 后续演进（主 plan §16，满足后再开发 Web UI）

```yaml
productization_gate:
  real_usage: ">= 4 weeks"
  evidence_cards: ">= 100"
  weekly_approved_drafts: ">= 3"
  draft_approval_rate: ">= 60%"
  average_daily_review_time: "<= 15 minutes"
  repeated_cli_friction: true
```
