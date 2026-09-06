# 作者立场确认卡（Gate 交互层）设计文档

> 状态：已获用户批准（2026-09-06）。
> 目标：把 `position_gate` 的 HITL 从「理解 `NEEDS_INPUT` + 手动跑 2~4 条命令」压缩成
> 「一张作者立场确认卡，一次选择，自动继续运行」，同时保留底层机制与安全不变量。

## 0. 结论与原则

- **不取消 `NEEDS_INPUT`**：Graph 内部状态仍是 `NEEDS_INPUT`，`finch run resume` 仍是底层恢复机制。
  `finch run resolve` 是覆盖在其上的友好门面。
- **一次选择，自动 resume**：确认 / 修改 / 跳过 / 停止 之后由 CLI 自动调用 replay，用户不再手动编排命令。
- **Skill 只调用 Finch CLI，不复制业务逻辑**（沿用 `skills/finch/SKILL.md` 原则）。
- **安全不变量不变**：任何全新或发生实质变化的作者立场，进入 Draft 节点前必须得到明确确认。
- **确定性**：`position_fingerprint` 是纯哈希（非 LLM）；复用门禁是确定性谓词。

## 1. 范围

**本次（P0 + P1 + P2 一次性实现）**：

- P0：`position_gate` 输出完整 `input_request`；`finch run resolve` 命令；确认/修改/跳过/停止后自动 resume。
- P1：预填编辑器（`--edit`）+ 保留 `--file`；`--skip` 自动尝试下一个 job；`--json` 供 Agent/Skill 调用；Skill 渲染确认按钮。
- P2：`position_fingerprint` + `PositionApproval` 持久化；完全相同立场自动复用确认；立场变化时展示 diff；
  撤销后禁止复用。

**范围外（明确不做）**：

- LLM 判定「新证据与原立场冲突」：自然语言矛盾判定非确定性，违反 repo 的确定性不变量。
  用「`change_mind_if` 非空 → 强制重确认」的确定性代理替代（见 §8）。
- 对 `NEEDS_INPUT` 之外的阻塞（`BLOCKED`/`FAILED`）不做交互门面（仍走 `finch diagnose`）。
- 不改 `GraphRuntime` / `replay` 的底层恢复语义。

## 2. 背景与现状（已核实）

- `position_gate`（`src/finch/graph/content_nodes.py` 的 `make_position_gate_node`）当前在立场未确认时
  返回 `status="needs_input"`，`output["items"]` = primary job，`output["questions"]` = `missing_questions[:3]`。
- `author_position`（`claim`/`decision`/`tradeoff`/`change_mind_if`）已由 `expand_content_job` 生成并存库，
  只是 `confirmed=False`。故「建议立场」是**重打包现有字段**，不是新增数据。
- 阻塞请求已持久化：`NodeResult.output` 落在 `NodeRecord.output_json`，键为
  `run_id:node_name:idempotency_key`。`resolve` 可经 `store.find_node(run_id, "position_gate", "default")` 读回。
- `finch run resume` 已会重读最新 job（`position_gate` 调 `jobs_repo.get_job`），故 `--confirm` =
  写 `confirmed=True` + replay。
- `run_daily` 与 `run_resume` **重复** `daily_nodes(...)` 装配逻辑；`resolve` 会是第三份，需抽取共享助手。
- `$finch` Skill 把 4 条命令的恢复流程硬编码在 `skills/finch/SKILL.md:47`。

## 3. 架构决策

新增 `src/finch/gate/` 包承载交互层；`position_gate` 节点只增加一个结构化输出字段与可选复用门禁；
CLI 增加 `finch run resolve`。

### 3.1 文件布局

```
src/finch/gate/
  __init__.py
  models.py    # InputRequest / ProposedPosition / InputAction / PositionApproval
  render.py    # render_input_request / render_position_diff（纯函数，无 IO）
  resolve.py   # resolve_input(...)：读阻塞请求 → 应用决策 → 自动 resume（编排）
src/finch/content/jobs.py   # + position_fingerprint()（挨着 AuthorPosition）
src/finch/storage/repositories.py  # + PositionApprovalRecord / PositionApprovalRepository
src/finch/graph/content_nodes.py   # position_gate：+ input_request 输出 + 可选复用门禁
src/finch/cli.py            # + run resolve 子命令；抽取 _resume_nodes/_resume_and_echo 共享助手
skills/finch/SKILL.md       # 更新 NEEDS_INPUT 恢复段 → $finch resolve
```

### 3.2 关键决策

- **复用门禁放 `position_gate` 内**，而非 CLI：因为「是否停下」由节点决定，且节点只有它自己能阻止
  每日运行在立场已复用时继续到 Draft。`position_gate` 已经通过 `jobs_repo` 有状态，再加
  `approvals_repo`（默认 `None`）不破坏「无 approvals_repo 时行为不变」。
- **`input_request` 增补而非替换** `items`/`questions`：旧字段保留，兼容现有测试与下游。
- **`run_id` 可选，缺省取最近一个 `NEEDS_INPUT` 的 run**：这是真正把 `run_id` 从主流程移除的关键
  （`run_daily` 目前甚至不打印 run-id）。需给 `Store` 加 `find_latest_run(state)`。
- **`--stop` 标记全部 active job 为 `DO_NOT_WRITE` 后 resume**：产出干净的「不建议写」空 brief，
  不残留 `NEEDS_INPUT`。

## 4. 数据模型

```python
# gate/models.py
class InputAction(StrEnum):
    CONFIRM = "confirm"; EDIT = "edit"; SKIP = "skip"
    STOP = "stop"; SHOW_EVIDENCE = "show_evidence"

class ProposedPosition(BaseModel):
    # 立场缺失/不完整时对应字段为空串；只有三者非空才允许 --confirm。
    claim: str = ""; decision: str = ""; tradeoff: str = ""
    change_mind_if: str | None = None

class InputRequest(BaseModel):
    type: Literal["author_position_confirmation"]
    run_id: str
    job_id: str
    topic: str                        # core_message or reader_problem
    why_now: str
    proposed_position: ProposedPosition
    evidence_card_ids: list[str]      # = job.source_card_ids
    questions: list[str] = []         # = job.missing_questions（卡片内展示）
    actions: list[InputAction] = Field(default_factory=lambda: list(InputAction))

class PositionApproval(BaseModel):
    position_fingerprint: str         # 主键：按立场复用，而非 job
    source_job_id: str                # 仅溯源
    approved_at: datetime
    revoked_at: datetime | None = None
```

```python
# content/jobs.py
def position_fingerprint(position: AuthorPosition) -> str:
    """hash(claim + decision + tradeoff + change_mind_if)；不含 confirmed。"""
    import hashlib
    raw = "\n".join([
        position.claim, position.decision, position.tradeoff,
        position.change_mind_if or "",
    ])
    return hashlib.sha256(raw.encode()).hexdigest()
```

`PositionApprovalRepository`（`payload_json` + `session.merge` 模式，同 `ContentJobRepository`）：

- `approve(fingerprint, job_id)` — upsert，`revoked_at=None`，刷新 `approved_at`。
- `find_active(fingerprint)` — 返回 `revoked_at is None` 的 approval，否则 `None`。
- `revoke(fingerprint)` — 设 `revoked_at=now`（幂等）。

## 5. `position_gate` 变更

在 `needs_input` 分支追加（`author_position` 缺失/不完整时对应字段留空串，仍产出 `input_request`，
此时 `--confirm` 无效、需走 `--edit`/`--file`）：

```python
pos = primary.author_position
output["input_request"] = InputRequest(
    type="author_position_confirmation",
    run_id=ctx["run_id"],
    job_id=primary.id,
    topic=primary.core_message or primary.reader_problem,
    why_now=primary.why_now,
    proposed_position=ProposedPosition(
        claim=(pos.claim if pos else ""),
        decision=(pos.decision if pos else ""),
        tradeoff=(pos.tradeoff if pos else ""),
        change_mind_if=(pos.change_mind_if if pos else None),
    ),
    evidence_card_ids=primary.source_card_ids,
    questions=list(primary.missing_questions)[:3],
).model_dump(mode="json")
```

复用门禁（仅当 `approvals_repo is not None` 且 `jobs_repo is not None`，且 `author_position` 完整但
`confirmed=False`）：

```
fingerprint = position_fingerprint(position)
approval = approvals_repo.find_active(fingerprint)
if approval and not position.change_mind_if:
    # 立场逐字一致、未被撤销、且作者未写出「什么会改变判断」→ 复用
    position.confirmed = True
    jobs_repo.upsert_job(primary.model_copy(update={"author_position": position}))
    output["reused_approval"] = fingerprint
    return NodeResult(status="succeeded", output=output)
```

否则照旧进入 `needs_input`。工厂签名改为
`make_position_gate_node(jobs_repo=None, approvals_repo=None)`（默认 `None`，dev harness 与现有测试不受影响）。

**如实声明**：逐字指纹复用只在 LLM 跨天重新生成完全相同立场时触发，LLM 方差下偏稀有；这是安全优先的
便利，不是大幅降低每日确认次数的手段。

## 6. `resolve` 机制

```
finch run resolve [run-id] [--confirm | --edit | --file PATH | --skip --reason R | --stop] [--json]
```

1. 解析 run-id：缺省时 `store.find_latest_run(GraphState.NEEDS_INPUT)`。
2. 读 `position_gate` 节点 `output_json` → `input_request`（缺则报「该 run 无待确认立场」）。
3. 应用动作：
   - **交互（无 flag）**：渲染确认卡（含建议立场、证据摘要、`questions`），提示 `请选择 1-5`，读数字映射动作。
   - **`--confirm`**：校验立场完整（`claim`/`decision`/`tradeoff` 均非空，否则报错并引导 `--edit`）→
     `jobs_repo` 写 `confirmed=True` → `approvals_repo.approve(...)` → resume。
   - **`--edit`**：`click.edit(text=预填 YAML)`（`$EDITOR`/`$VISUAL`，回退 `vi`）→ 读回校验 → 确认 + resume。
     若新立场 fingerprint 与已批准记录不同 → `revoke` 旧 fingerprint。
   - **`--file PATH`**：同 `--edit` 但直接读文件，不开编辑器（自动化/可复现测试）。
   - **`--skip --reason R`**：reject primary（`DO_NOT_WRITE` + reason）→ resume → `select_primary_job`
     选下一个 active job（现有「递补一次」逻辑已兜底）。
   - **`--stop`**：全部 active job 标 `DO_NOT_WRITE` → resume → 空「不建议写」brief，干净 `COMPLETED`。
   - **`--json`**：输出 `input_request`（及 resolve 后的 run state）为 JSON，供 Skill/Agent 消费。
4. 自动 resume：调用共享 `_resume_and_echo(store, nodes, run_id)`（见 §7），不再要求用户手动 `run resume`。

## 7. CLI 表面与共享助手重构

抽取（`cli.py` 内部）：

```python
def _resume_nodes(settings, store) -> list[Node]:
    """run_resume 与 resolve 共用：空 groups_by_repo 的 daily_nodes 装配。"""

def _resume_and_echo(store, nodes, run_id) -> None:
    """replay + 打印 state + 持久化 run 输出 + 打印 brief（原 run_resume 尾部逻辑）。"""
```

`run_resume` 与 `resolve` 都复用这两个助手；`run_daily` 保留其独有的完整装配。

## 8. 复用门禁（P2）

| 情况 | 行为 |
|---|---|
| 全新立场 | 完整确认 |
| 立场内容变化 | 展示 diff（`render_position_diff`）后确认 |
| 立场逐字一致且已确认、未被撤销、`change_mind_if` 为空 | 自动复用确认 |
| 立场逐字一致但 `change_mind_if` 非空 | 强制重新确认（确定性代理「新证据可能触发」） |
| 该立场曾被撤销 | 禁止复用 |

`change_mind_if` 非空即重确认，是「新证据与原立场冲突 → 强制重新确认」的**确定性近似**；不做 LLM 冲突判定。

## 9. Skill 更新

替换 `skills/finch/SKILL.md:47` 的恢复段为 `$finch resolve` 模式：检测 `NEEDS_INPUT` →
`finch run resolve --json` 取 `input_request` → 渲染对话选项 → 依据用户选择调用
`finch run resolve [run-id] --confirm | --skip | --stop | --edit`。Skill 仍只调 CLI。

## 10. 不变量与错误处理

- **证据优先**：`resolve` 只操作立场与 job，不生成/改写任何草稿或证据。
- **不自动发布**：`resolve` 无任何发布路径；`opencli`/`gh` 仍只读。
- **确定性**：`position_fingerprint` 纯 sha256；复用门禁是纯谓词，无 LLM。
- **confirm 前不 Draft**：复用仅限「逐字一致 + 未被撤销 + `change_mind_if` 空」。
- **错误统一**：`resolve` 领域错误抛 `KeyError`/`ValueError`，CLI 转一行错误 + 退出码 1
  （沿用 `finch dev` 修复后的约定）。

## 11. 测试

- `tests/unit/test_jobs.py`（增）：`position_fingerprint` 确定性；`confirmed` 不参与指纹；`change_mind_if` 差异改指纹。
- `tests/unit/test_gate_render.py`：`render_input_request` 分支（完整/缺字段）；`render_position_diff`。
- `tests/unit/test_gate_models.py`：`InputRequest` 序列化（`model_dump(mode="json")`）。
- `tests/unit/test_position_approval_repository.py`：approve / find_active / revoke 幂等。
- `tests/graph/test_content_nodes.py`（增）：`position_gate` 输出 `input_request`；复用门禁四分支
  （复用 / `change_mind_if` 重确认 / 撤销禁止 / `approvals_repo=None` 行为不变）。
- `tests/unit/test_cli_run.py`（增）：`finch run resolve --confirm/--skip/--stop/--file` 各路径含
  自动 resume（mock `$EDITOR`）；`--json` 输出；run-id 缺省取最近 `NEEDS_INPUT`。
- 现有测试全绿（`approvals_repo=None` 与 `input_request` 为增补字段，均向后兼容）。

## 12. 范围外（明确不做）

- LLM 冲突判定节点（非确定性，违反不变量）。
- `BLOCKED`/`FAILED` 的交互门面。
- 修改 `GraphRuntime`/`replay` 恢复语义。
- 对 `finch jobs answer/confirm-position/reject` 的删除：仍保留，服务调试/脚本/高级用户。
