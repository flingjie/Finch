# 单一决策点：把「确认立场 + 批准草稿」合并为一次「采用/修改/跳过」设计文档

> 状态：已获用户批准（2026-09-06）。
> 目标：删除「每次写草稿前必须人工确认立场」的固定门禁，把两次人工确认（`confirm-position` + `review approve`）
> 合并为一次原子决策，并把正常使用从 `daily → 查看 job → 确认立场 → resume → 查看草稿 → approve` 缩短为
> `finch daily → 采用 / 修改 / 跳过`。

## 0. 结论与原则

- **真正不可逆的动作是「公开发布」，不是「生成草稿」**。发布仍由用户手动完成（`gh`/`opencli` 只读），
  因此生成一份可能不准确、但不会自动公开的候选草稿没有实质风险。
- **默认推断、按需提问**：`position_gate` 不再默认阻塞。立场字段可由证据推断时，直接生成候选草稿并标记
  `position_source=INFERRED`；只有无法可靠推断或存在确定性高风险信号时才 `needs_input`。
- **一次决策，原子落地**：`采用` 同时表示「这个立场是我的」「这份草稿可用」「把立场记录为可复用偏好」，
  等价于合并 `confirm-position` 与 `review approve`。
- **确定性边界不变**：Graph 仍确定性（顺序、状态、幂等、重放）；「是否值得打断用户」的 LLM 判断只存在于
  Codex 编排层，绝不进入 Graph。Finch 只输出确定性信号（`must_ask` + 理由），Codex 在其上叠加判断。
- **加性迁移**：新增 `DecisionRecord` 为权威记录；旧记录（`AuthorPosition.confirmed` / `PositionApproval` /
  `ReviewDecision`）保留可读，供 weekly/metrics/voice/reuse-gate 向后兼容；旧命令降级为调试接口。
- **范围**：只覆盖原创（content）轨道；互动（engagement）轨道的审批队列不变。

## 1. 范围

**本次（一次性实现，5 个子系统 A–E）**：

- A. **非阻塞 gate**：`position_gate` 停止要求 `confirmed`；推断立场通过并继续生成草稿。
- B. **统一决策模型**（加性）：`PositionSource` / `DecisionAction` / `DecisionRecord` 新表。
- C. **ask-vs-infer 策略**：确定性 `must_ask` 信号（hybrid 边界，Finch 发信号 + Codex 判断）。
- D. **新 CLI 面**：`finch daily --json` / `finch next --json` / `finch decide <job-id> ... --json`。
- E. **Codex 编排**：`$finch daily` 循环（读 `next`、渲染决策卡、按需提问、调 `decide`）。

**范围外（明确不做）**：

- 不改 `GraphRuntime` / `replay` / 证据链（`Commit → EngineeringEvent → EvidenceCard → Draft`）不变量。
- 不改互动轨道的 `InteractionCandidate` 审批队列（`finch engagement` 不变）。
- 不做破坏性数据迁移（不 drop 旧列/旧表）；旧命令保留为调试接口。
- `NEEDS_INPUT` 之外的阻塞（`BLOCKED`/`FAILED`）不新增交互门面（仍走 `finch diagnose`）。

## 2. 背景与现状（已核实）

- `position_gate`（`content_nodes.py:876-1010`）当前在 primary job 的 `author_position.confirmed=False` 时
  返回 `needs_input`，即使 `decision`/`tradeoff` 已由 `expand_content_job` 推断出来。立场字段本身已存在，
  gate 只要求「人类认领」，不是「生成立场」。
- `review/service.py` 中 `confirm_position`（`CONFIRM_POSITION`）与 `approve` 是两条独立 `ReviewDecision`；
  `list_pending` 明确把 `CONFIRM_POSITION` 排除在「已审核」之外。二者构成两次人工确认。
- `brief` 节点已把终态算好：`content_nodes.py:719` 在有草稿时输出 `WAITING_FOR_REVIEW`，否则 `COMPLETED`。
  因此去掉阻塞后，Graph 自然落在 `WAITING_FOR_REVIEW`（≈「待审核」）或 `COMPLETED`（「今天不值得写」），
  无需新增状态。
- `Draft`（`content/models.py`）当前**无 `job_id` / `run_id` 关联**，无法回溯到源 job/立场。
- 复用门禁（`position_fingerprint` + `PositionApproval`）已存在：逐字一致立场 + 无 `change_mind_if` 即复用。
- 仓库已有 alembic 迁移基础（`test_alembic.py`）。

## 3. 架构

```
src/finch/
  gate/models.py        # + PositionSource / DecisionAction / must_ask 信号字段
  gate/resolve.py       # 保留（调试）；decide 复用其确定性落地能力
  graph/content_nodes.py# position_gate 改为非阻塞 + must_ask；draft 节点写入 job_id/run_id
  review/models.py      # + DecisionRecord（新表，加性）
  review/service.py     # + accept/decide 服务（原子落地 + 向后兼容投影）
  cli.py                # + daily --json / next --json / decide；旧命令保留为调试
skills/finch/SKILL.md   # + $finch daily 编排循环（Codex 层）
```

### 3.1 Gate 变化（A）

`position_gate.run` 新逻辑：

- `decision` / `tradeoff` 任一为空 → `needs_input` + `must_ask`（`position_incomplete`，无法起草）。
- 立场完整但 `confirmed=False` → **通过**：把 primary 放入 `items`，标记 `position_source=INFERRED`；
  复用门禁命中则标记 `REUSED`（自动确认，保持现有行为）。
- 立场已确认 / 复用确认 → 原样通过。

### 3.2 确定性 must_ask 信号（C）

`next --json` 返回的决策卡携带 Finch 计算出的确定性信号（仅信号，不决定是否提问）：

| 字段 | 含义 | 确定性来源 |
|---|---|---|
| `must_ask` | 布尔 | 任一 `ask_reasons` 非空 |
| `ask_reasons` | 列表 | `position_incomplete` / `safety_risk` / `position_conflict` |
| `position_incomplete` | 字段缺失 | `decision`/`tradeoff` 空 |
| `safety_risk` | 高风险表达 | safety checker 命中负面/隐私/安全 |
| `position_conflict` | 与历史立场冲突 | `change_mind_if` 非空 + 新证据（复用门禁已强制重确认） |

Codex 层的 LLM 判断（「存在冲突的合理立场」「commit 疑似临时方案/实验」）只存在于编排层，不进 Graph。

## 4. 数据模型（B，加性）

```python
class PositionSource(StrEnum):
    INFERRED = "inferred"
    HUMAN_CONFIRMED = "human_confirmed"
    REUSED = "reused"

class DecisionAction(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    SKIP = "skip"

class DecisionRecord(BaseModel):          # 新表；幂等键 "dec_<job_id>"
    job_id: str
    draft_id: str
    action: DecisionAction
    position_source: PositionSource
    position_fingerprint: str
    approved_content_hash: str            # 采用时绑定最终正文；revise 改变 hash → 旧批准失效
    revised_body: str | None = None
    diff: str | None = None
    decided_at: datetime
```

- `Draft` 已含 `content_job_id`（writer 已填充 `job.id`）与 `position_statement`（已存 `author_position.decision`）；
  本次**新增 `run_id: str`** 使草稿可回溯到当次 run（`content_job_id` 无需新增，仅沿用）。
- `AuthorPosition` 增加 `position_source: PositionSource | None = None` 字段（加性，保留 `confirmed: bool`）：
  运行期推断立场标记 `INFERRED`，复用门禁命中标记 `REUSED`，采用时标记 `HUMAN_CONFIRMED`。
  `confirmed=True` 与 `position_source ∈ {HUMAN_CONFIRMED, REUSED}` 语义等价，旧代码读 `confirmed` 不受影响。
- 加性迁移：新增 `DecisionRecord` 表；不 drop `AuthorPosition.confirmed` / `PositionApproval` /
  `ReviewDecision`。旧命令（`jobs confirm-position` / `review approve`）降级为调试接口，仍可用。

### 4.1 原子落地与向后兼容投影（判断 call #2）

`decide --action accept` 一次性写入：

1. `DecisionRecord`（权威，`position_source=HUMAN_CONFIRMED`，`approved_content_hash=hash(final_body)`）。
2. 向后兼容投影：`ContentJob.author_position.confirmed=True` + `PositionApproval`（fingerprint 复用）
   + `ReviewDecision(APPROVE)`，使 weekly / metrics / voice / reuse-gate 无需重写即可继续工作。

## 5. 决策动作语义

| 用户动作 | Finch 后台操作 |
|---|---|
| 采用 | 确认立场（`HUMAN_CONFIRMED`）+ 批准草稿 + 存 fingerprint + 绑定 `approved_content_hash` |
| 修改 | 按自然语言指令重写 → 重跑 Critic → 返回 `{new_body, diff, critic}`（不落 `accept` 记录） |
| 跳过 | `ContentJob.status=DO_NOT_WRITE` + `reject_reason` → 递补下一个 primary |

`decide --action revise --instruction "<NL>"` 由 Finch 内部调用 Codex（rewrite 子进程，确定性编排：
Finch 负责顺序/状态/校验/Pydantic），复用现有「smart 节点 codex exec」模式（判断 call #3）。

## 6. CLI 契约（D）

```bash
finch daily --json
# → {"run_id", "status": "review_required"|"completed", "n_review": int, "n_engagement_drafts": int}

finch next --json
# → 决策卡: {job_id, topic, why_now, position{claim,decision,tradeoff,source},
#            evidence[], draft, draft_id, must_ask, ask_reasons[], risks[]}
#   无待决策项时 → {"status": "none"}

finch decide <job-id> --action accept|revise|skip [--instruction "<NL>"] --json
# → {"status": "accepted"|"revised"|"skipped", "draft_id", "diff"?, "critic"?}
```

- `item` = 确定性 primary `ContentJob`（判断：item identity）。
- 旧命令（`jobs show` / `run resume` / `review approve` / `review confirm-position`）保留为调试接口。

## 7. Codex 编排（E）

`$finch daily` 后台循环：

```
finch daily --json → 若 review_required：
  finch next --json → 渲染决策卡 →（must_ask 或 Codex 判断）在对话中提问
  → 把自然语言映射到 finch decide ... --json → 循环直到 next 返回 none
```

用户只接触 `采用` / `修改` / `跳过`；不再接触 `run_id` / `job_id` / `NEEDS_INPUT` / `run resume` / YAML。

## 8. 简化后的状态机

```
Running → Completed          （今天不值得写）
Running → ReviewRequired      （有候选草稿，≈ WAITING_FOR_REVIEW）
ReviewRequired → ReviewRequired （修改）
ReviewRequired → ReadyToPublish（采用，≈ APPROVED/PUBLISHED 前）
ReviewRequired → Completed      （跳过）
```

内部节点状态（`EXTRACTED` / `JOBS_DEFINED` / `POSITIONS_READY` / `CRITIQUED` …）保留但不暴露；
`NEEDS_INPUT` 缩窄为「无法可靠推断立场」的内部协议。

## 9. 错误处理

- `decide` 对不存在的 job / 不完整立场 / 缺 `--instruction`（revise 时）走 `typer.Exit(1)` 干净错误。
- `decide revise` 的 Codex 子进程失败 → 结构化错误 JSON（不复写、不落库），由编排层决定重试或上报。
- 非 TTY（Codex/CI）只走 `--json`，不弹交互选择器。

## 10. 测试

- `position_gate` 非阻塞：推断立场通过（不再 `needs_input`）；字段缺失仍 `needs_input` + `must_ask`。
- `DecisionRecord` 原子落地 + 向后兼容投影（weekly/metrics/reuse-gate 继续工作）。
- `decide accept/revise/skip` 三路径；`revise --instruction` 返回新版本 + diff。
- `next --json` 决策卡形状；无待决策项返回 `none`。
- CLI `--json` 契约 + 旧命令回归。

## 11. 迁移

- 加性 alembic 迁移：新增 `DecisionRecord` 表；`Draft` 增加 `job_id`/`run_id` 列（可空，向后兼容旧草稿）。
- 不删旧列/旧表；旧命令保持可用（调试）。

## 12. 与既有设计的关系

本设计是 [[2026-09-06-author-position-confirmation-design]] 与
[[2026-09-06-needs-input-interactive-step-design]] 之上的第三层：前两者把「立场确认」做成友好门面，
本设计进一步**取消其作为固定门禁的地位**，把确认合并进最终的「采用」决策。复用门禁、`resolve_input`
的确定性落地、决策卡渲染均沿用，不重复实现。
