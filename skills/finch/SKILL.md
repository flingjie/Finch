---
name: finch
description: Evidence-driven builder companion. Use $finch when the user asks to run the daily graph, decide on candidate drafts (accept/revise/skip), reflect on GitHub engineering changes, manage the author voice profile, review engagement candidates, or diagnose the gh/opencli environment.
---

# Finch

Finch 是一个证据驱动的 Builder 伙伴。它通过 `gh` 只读读取 GitHub，通过 `opencli` 只读搜索/读取 Twitter/X，把工程实践与公共技术讨论匹配，生成必须经人工「采用」才可用的候选草稿。

Finch 的每一步都落地为 Finch CLI（`finch ...`）。**Skill 只调用 Finch CLI，不复制业务逻辑、不直接改数据库、不猜测节点状态。**

## 执行环境

Finch 的读取与生成都依赖真实网络：`gh` 读 GitHub，`opencli` 读 Twitter/X，`codex exec` 调 LLM。沙盒会阻断这些网络访问（典型报错 `connect: operation not permitted`）。运行需要读取数据的 Finch CLI（`finch github ...`、`finch twitter ...`、`finch run daily`、`finch diagnose`）时，要在主机环境执行，不要在沙盒中执行；只有不涉及网络的本地操作（如查看文件、纯本地 DB 的 `finch review list/show`）可以在沙盒中运行。

## 每日编排循环（$finch daily）

用户说 `$finch daily` 时，Codex 依次：

1. `finch run daily --json`（主机环境）→ `{"run_id","status","n_review","n_engagement_drafts"}`。
2. 若 `status == "completed"`：告知「今天没有需要决策的草稿」，结束。
3. 循环：
   a. `finch next --json` → 决策卡或 `{"status":"none"}`。
   b. 若 `none`：告知「全部决策完成」，结束。
   c. 把决策卡译成人话展示：主题、为什么值得说、可能的立场（主张/方案/取舍）、证据、草稿正文。
   d. 若 `must_ask` 非空（`position_conflict`/`safety_risk`）或 Codex 判断需要，先向用户提问关键判断；否则直接给出「采用/修改/跳过」选项。
   e. 把用户回复映射到一次决策：
      - 采用 → `finch decide <job-id> --action accept --json`
      - 修改 → `finch decide <job-id> --action revise --instruction "<用户原话>" --json`（展示新正文 + diff，回到步骤 e 让用户决定采用/继续改/跳过）
      - 跳过 → `finch decide <job-id> --action skip --reason not_now --json`
   f. 回到步骤 a。
4. 全部采用/跳过完成后，提醒：发布仍由用户在 Finch 外部手动完成（Finch 不自动发布）。

## 模式

| 模式 | 行为 | CLI |
|---|---|---|
| `$finch daily` | 运行每日 Graph + 决策循环（采用/修改/跳过） | `finch run daily --json` → `finch next --json` → `finch decide ... --json` |
| `$finch reflect` | 只用 `gh` 分析某仓库的工程变化 | `finch github reflect` |
| `$finch voice` | 管理作者声音画像（本地） | `finch voice show` / `approve-example` / `reject-example` |
| `$finch engagement` | 审核互动候选（人工批准后才执行） | `finch engagement list` / `show` / `approve` / `reject` / `edit` / `metrics` |
| `$finch weekly` | 汇总最近 7 天批准率、修改/跳过原因、效果指标 | `finch run weekly` |
| `$finch diagnose` | 检查 gh/opencli/认证/schema | `finch diagnose` |

调试接口（高级用户/脚本，不进入正常交互）：`finch jobs ...`、`finch run resume`、`finch run resolve`、`finch review ...`。

## 强制规则（不可违反）

- 不直接调用 GitHub HTTP API，必须通过 `gh` adapter（`finch github ...`）。
- 不直接控制 Twitter 页面，必须通过 `opencli` adapter（`finch twitter ...`）。
- 不运行 Twitter 写命令（不 post / reply / like / retweet / follow）。
- 没有 Evidence Card 不生成草稿；每条对外主张必须绑定 `evidence_card_id` 且可追溯。
- 不把推断写成用户亲历事实：`INFERRED` / `UNKNOWN` 不得写成第一人称亲历，也不得作为可发布主张。
- 不公开私有仓库内容（私有仓库 → `publishable=false`）。
- 不自动发布：发布只能由用户在 Finch 外部手动完成。候选草稿在用户「采用」前绝不视为已发布或已确认立场。
- 遇到敏感信息（密钥 / token / 私有内容）立即停止该候选内容。

## 错误恢复

- 环境异常：`finch diagnose`；不自动安装/改浏览器/代填凭据。
- `finch decide --action revise` 返回结构化错误 JSON（`{"status":"error","message":...}`）→ 把错误译成人话，让用户重试或改用其它动作；不要自行重跑 rewrite。
- Graph 停在 `BLOCKED`/`FAILED`：`finch diagnose` 排查；不改质量门禁数值。
- 发布后用户手动填链接与互动数据：`finch review feedback <DRAFT_ID> --url <URL> --metrics '<json>'`。

## 质量门禁（来自 finch.yaml，不要硬编码更改）

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
