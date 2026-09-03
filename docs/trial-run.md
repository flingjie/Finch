# Finch 两周试运行（Phase 9 框架）

> 真实两周试运行由你手动执行。本文件给出流程与验收指标。框架以「脚本 + 记录机制」交付，不做自动发布。

## 前置

- `uv sync` 完成；`gh` 已登录且有目标仓库读取权限；`opencli` + Chrome 已登录 Twitter/X。
- 首次 `finch init` 初始化数据库。
- 校验环境：`finch diagnose`（分别报告 `gh` 与 `opencli`）。

## 每日流程（约 15 分钟以内）

1. 运行 `bash scripts/trial.sh`（等价于 `finch run daily` + `finch jobs list --status needs_input` + `finch review list`）。
2. 若 Graph 停在 `NEEDS_INPUT`（Content Jobs 人审门）：`finch jobs show <JOB_ID>` 看详情，然后
   `finch jobs answer <JOB_ID> --file answers.yaml` 补立场，再 `finch jobs confirm-position <JOB_ID>` 确认；
   不写的用 `finch jobs reject <JOB_ID> --reason <理由>` 标为 `do_not_write`。之后 `finch run resume <RUN_ID>` 继续。
3. 若 Graph 停在 `WAITING_FOR_REVIEW`：`finch review show <DRAFT_ID>` 看全文，然后 `approve` / `revise` / `skip`。
   - `revise` 需要 `--file revised.md` 提供你的修订。
   - `skip` 需要 `--reason evidence_insufficient|not_relevant|low_quality|not_now|other`。
4. 批准后你在 Finch 外部手动发布，然后记录发布链接与互动数据：
   `finch review feedback <DRAFT_ID> --url <URL> --metrics '{"likes":N,"replies":N}'`。
5. **手动记录审核耗时**（从打开 Brief 到完成审核的分钟数）到你的运行日志。

## 每周流程

1. 运行 `finch run weekly`，得到批准率、跳过原因与已发布候选的汇总。
2. 人工补充分析：哪类 Commit 产生价值、哪类 Evidence 匹配精度高、哪些作者产生真实对话、下周该继续/减少/实验什么。
3. **只提配置建议，不自动修改质量门禁。**

## 记录机制（已由代码落库）

- 审核决策（approve/revise/skip + 理由 + diff + 时间戳）→ `ReviewRecord`。
- 发布链接与互动数据 → `FeedbackRecord`。
- 每日草稿 → `DraftRecord`。
- 审核耗时由你手动记入运行日志（每日流程第 5 步）。

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

### 每周内容指标（Task 8 新增，`finch run weekly` 输出）

| 指标 | 含义 | 目标 |
|---|---|---|
| evidence_coverage | 证据检查通过的报告占比 | 越高越好（继续 ≥ 0.7，< 0.4 停止） |
| decision_density | 已发布草稿绑定 job 且含 decision+tradeoff 的占比 | 越高越好 |
| generic_sentence_rate | portability/specificity 失败的报告占比（套话率） | 越低越好 |
| human_correction_rate | 需人工改事实/立场的已审草稿占比 | 越低越好 |
| job_completion_rate | 结果评估 job_completed ∈ {yes, partly} 的占比 | 越高越好 |
| useful_reply_rate | 回复中 useful_reply_count>0 的占比 | 越高越好 |
| do_not_write_rate | DO_NOT_WRITE job 占比 | **信息性，不视为失败** |

- 每个指标按「继续 / 调整 / 停止」三档给出每周建议：健康（score ≥ 0.7）→ 继续；
  偏弱（0.4 ≤ score < 0.7）→ 调整；差（< 0.4）→ 停止。高者为优的指标直接用值，
  `generic_sentence_rate` / `human_correction_rate` 用 1−值；`do_not_write_rate` 恒为信息性。
- `do_not_write_rate` 只用于观察选题/证据是否过弱，**不触发任何失败判定**。

## 阈值校准（两周后）

试运行满两周后，回顾上述指标并**只提建议、不自动修改**：

1. 汇总两周的 `finch run weekly` 输出，逐项对照「继续 / 调整 / 停止」建议。
2. 对持续「停止」或「调整」的指标，给出对 `finch.yaml` 中 `quality_gates` 的
   **提议**（例如 `min_candidate_score`、`min_evidence_score`、`max_rewrite_rounds`），
   以及对 `weekly.py` 建议阈值常量（`_HEALTHY=0.7`、`_WEAK=0.4`）的**提议**。
3. 提案走正常评审，人工确认后再改；试运行期间任何阈值都不自动漂移。

## 合并门槛

每个 Phase 只在以下全绿时才合并：

- `uv run pytest`（含 `tests/evals/` 的 7 个场景）；
- `uv run ruff check .`；
- `uv run mypy src`。
- 每个新增 prompt 都有对应 contract test（prompt 模板 + 输出 schema 校验）。
- Graph 的恢复 / 重放 / 幂等测试（`tests/graph/test_replay.py` 等）不回退。

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
