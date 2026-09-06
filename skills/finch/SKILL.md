---
name: finch
description: Evidence-driven builder companion. Use $finch when the user asks to run the daily graph (dual-track: evidence-backed replies + original diary, plus a bounded engagement track), resume a run, reflect on GitHub engineering changes, review pending drafts, manage content jobs or the author voice profile, review engagement candidates, or diagnose the gh/opencli environment.
---

# Finch

Finch 是一个证据驱动的 Builder 伙伴。它通过 `gh` 只读读取 GitHub Commit/PR/Issue，通过 `opencli` 只读搜索/读取 Twitter/X 内容，把工程实践与公共技术讨论匹配，生成必须经人工审核的回复与原创内容。

Finch 的每一步都落地为 Finch CLI（`finch ...`）。**Skill 只调用 Finch CLI，不复制业务逻辑。**

## 执行环境

Finch 的读取与生成都依赖真实网络：`gh` 读 GitHub，`opencli` 读 Twitter/X，`codex exec` 调 LLM。沙盒会阻断这些网络访问（典型报错 `connect: operation not permitted`）。运行需要读取数据的 Finch CLI（`finch github ...`、`finch twitter ...`、`finch run daily`、`finch diagnose`）时，要在主机环境执行，不要在沙盒中执行；只有不涉及网络的本地操作（如查看文件、纯本地 DB 的 `finch review list/show`）可以在沙盒中运行。

## 模式

| 模式 | 行为 | CLI |
|---|---|---|
| `$finch daily` | 运行完整每日 Graph（双轨），生成 Daily Brief | `finch run daily` |
| `$finch resume` | 从 run-id 恢复，复用已完成节点、从 position_gate 继续 | `finch run resume <RUN_ID>` |
| `$finch resolve` | 处理当前 author-position 阻塞点（确认/修改/跳过/停止），完成后自动 resume | `finch run resolve [RUN_ID] --confirm` / `--edit` / `--skip --reason` / `--stop` / `--json` |
| `$finch reflect` | 只用 `gh` 分析某仓库/某时间段的工程变化 | `finch github reflect` |
| `$finch review` | 处理待审核草稿与反馈 | `finch review list` / `show` / `approve` / `revise` / `skip` / `confirm-position` / `feedback` |
| `$finch jobs` | 查看/回答/确认/拒绝 Content Jobs（人工立场） | `finch jobs list` / `show` / `answer` / `confirm-position` / `reject` |
| `$finch voice` | 管理作者声音画像（本地，不自动发布） | `finch voice show` / `approve-example` / `reject-example` |
| `$finch engagement` | 审核互动候选（bookmark/observe/reply/quote，人工批准后才执行） | `finch engagement list` / `show` / `approve` / `reject` / `edit` / `metrics` |
| `$finch weekly` | 汇总最近 7 天批准率、修改/跳过原因、内容效果指标 | `finch run weekly` |
| `$finch diagnose` | 检查 `gh`、`opencli`、认证和数据 schema | `finch diagnose` |

`finch run daily` 是双轨运行：原创证据轨（Commit → EvidenceCard → Draft，只深处理 `daily_budget` 预算内的 group，其余进 backlog）+ 互动 engagement 轨（按兴趣搜索、提出有界互动），两轨故障隔离、共享同一 `run_id`。

## 强制规则（不可违反）

- 不直接调用 GitHub HTTP API，必须通过 `gh` adapter（`finch github ...`）。
- 不直接控制 Twitter 页面，必须通过 `opencli` adapter（`finch twitter ...`）。
- 不运行 Twitter 写命令（不 post / reply / like / retweet / follow）。
- 没有 Evidence Card 不生成草稿；每条对外主张必须绑定 `evidence_card_id` 且可追溯。
- 不把推断写成用户亲历事实：`INFERRED` / `UNKNOWN` 不得写成第一人称亲历，也不得作为可发布主张。
- 不公开私有仓库内容（私有仓库 → `publishable=false`）。
- 不自动发布：发布只能由用户在 Finch 外部手动完成。
- 遇到敏感信息（密钥 / token / 私有内容）立即停止该候选内容。

## 错误恢复与人工输入

- 环境异常：运行 `finch diagnose` 分别报告 `gh` 与 `opencli` 状态。不要自动安装、不要修改浏览器配置、不要代填凭据。
- 每日 Graph 停在 `WAITING_FOR_REVIEW`：运行 `finch review list` 查看待审草稿，用 `finch review show <ID>` 看全文，再 `approve` / `revise` / `skip`。
- 每日 Graph 停在 `NEEDS_INPUT`（position_gate 需要作者立场）：运行 `finch run resolve --json` 取结构化 `input_request`，把建议立场渲染成对话选项，依据用户选择调用 `finch run resolve [RUN_ID] --confirm | --edit | --skip --reason | --stop`（完成后自动 resume）。底层 `finch jobs answer/confirm-position/reject` 与 `finch run resume` 保留给脚本与高级用户。
- Graph 停在 `BLOCKED` / `FAILED`：用 `finch diagnose` 排查；不要自行修改质量门禁数值。
- `revise` 需要用户提供修订文本（`--file revised.md`）；`skip` 需要标准跳过原因（`--reason evidence_insufficient|not_relevant|low_quality|not_now|other`）。
- 发布后由用户手动填写发布链接与互动数据：`finch review feedback <DRAFT_ID> --url <URL> --metrics '<json>'`。

## 质量门禁（来自 `finch.yaml`，不要硬编码更改）

```yaml
quality_gates:
  max_daily_replies: 5
  max_daily_original_posts: 1
  min_candidate_score: 0.65
  min_evidence_score: 0.75
  min_quality_score: 0.75
  min_discussability: 0.50
  max_rewrite_rounds: 2
  match_top_k: 10
  timing_default: 0.3
```

## 参考

- `references/voice-guide.md` — 英文回复与中文日记的语气。
- `references/quality-policy.md` — 门禁、证据绑定与安全策略。
- `references/content-patterns.md` — 回复与日记的结构模式。
- `scripts/daily.sh` — `finch run daily` 的便捷包装。
