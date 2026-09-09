# 互动反思事实录入设计（Interaction Reflection Fact-Entry）

日期：2026-09-09
状态：待评审

## 背景与结论

连接优先改造已把架构倒置（见 `docs/product-contract.md` 与
`2026-09-09-connection-first-gap-closure-design.md`），但连接主循环的「记忆」为空：
四组关系事实全无写入者，导致三项北极星指标结构性恒为 0、`from_signals` 永远找不到张力。

| 死字段 / 死模型 | 后果 |
|---|---|
| `FeedbackSnapshot` 无构造者 | `meaningful_interactions`、`meaningful_response_rate` 恒 0 |
| `relationship_stage` 无推进者 | `new_relevant_peers` 恒 0 |
| `ConversationThread.open_questions / disagreements / agreements / possible_experiments` 无写入者 | `collaboration_signals` 恒 0；`needs_follow_up` 的 open-questions 分支死；`from_signals` 无张力 |
| `InteractionRecord.reply_refs / follow_up_status / follow_up_at` 无写入者 | 无法判断「对方回了但没继续」 |
| `ConversationEvidence` 无生产者（`evidence_upgrade.extract_conversation_evidence` 零调用者） | 对话→观点证据链断开 |

本设计补上**写路径**：一次真实互动之后，把「发生了什么」记录下来，让上述字段与指标
真正运转。录入模型为「只读抓回 + LLM 提议 + 人工确认」——LLM 只产出**候选**，人工确认后
才落库，不违反「LLM 不直接决定关系状态」铁律。

## 范围

**In：**

- 新增 `InteractionReflection` 实体 + `ReflectionRepository` + `ReflectionService`
  （`scrape` → `propose` → `apply` 三段式）。
- 新增 CLI：`finch connect reflect <proposal-id>`、`connect reflect-approve <id>`、
  `connect reflect-reject <id>`。
- `apply` 原子写入四组事实：`FeedbackSnapshot`、`InteractionRecord` 跟进字段、
  `ConversationThread` 语义字段、`PeerProfile` 关系字段（merge 不覆写）、
  `ConversationEvidence`（`verified=False` 候选）。

**Out（非目标）：**

- 不做**跟进发现的读路径**：不改 `needs_follow_up`、不做 follow-up sweep 命令。等事实
  积累起来后单独设计。
- 不做 `promote_to_personal`（conversation → personal 证据升级）的自动触发；本设计只
  落 `verified=False` 候选，升级门禁后续单独做。
- 不新增 `PeerProfile` 字段（`possible_next_actions` 已在 Task 2 加过，本设计只写入）。
- 不改 twitter/gh adapter（只复用现有只读 `thread(url)` / 能力）。
- 不做跨平台 peer 合并。

---

## 设计 1：录入模型

一次真实互动的完整闭环（前两步已有，第三步新增）：

```text
connect record <proposal-id> --url ...        # 已有：记「我发了」，建 InteractionRecord + 挂 thread
        ↓ （几天后，等回复）
connect reflect <proposal-id>                 # 新增：抓回客观信号 → LLM 提议 → 存 PROPOSED + 打印
        ↓ 人工看打印
connect reflect-approve <reflection-id>       # 新增：确认 → 原子写四组事实
```

`reflect` 与 `record` 分离是因为时间上隔着几天（回复要等），不可能同一个命令完成。

---

## 设计 2：实体 `InteractionReflection`

新增到 `src/finch/engagement/models.py`，语义字段 1:1 映射现有死字段，**零新增 PeerProfile
字段**。

```python
class ReflectionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


class EvidenceCandidate(BaseModel):
    kind: Literal["question", "disagreement", "hypothesis", "experiment"]
    statement: str


class InteractionReflection(BaseModel):
    id: str                                   # 确定性：refl_<proposal_id>
    proposal_id: str                          # 回溯 InteractionProposal
    peer_id: str
    source_url: str
    captured_at: datetime                     # 抓回时间
    # —— 客观信号（确定性抓回，非 LLM）——
    replies: int = 0
    likes: int = 0
    peer_replied: bool = False
    reply_refs: list[str] = Field(default_factory=list)
    scrape_failed: bool = False
    # —— 语义字段（LLM 提议）——
    meaningful: bool = False
    follow_up_status: str = "none"            # none / pending / replied / closed
    follow_up_at: datetime | None = None
    relationship_stage: RelationshipStage | None = None   # None = 不推进
    next_context: str = ""
    possible_next_actions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    agreements: list[str] = Field(default_factory=list)
    possible_experiments: list[str] = Field(default_factory=list)
    evidence: list[EvidenceCandidate] = Field(default_factory=list)
    # —— 审批 ——
    status: ReflectionStatus = ReflectionStatus.PROPOSED
    reject_reason: str = ""
```

---

## 设计 3：三段式 `ReflectionService`（`src/finch/engagement/reflection.py`）

职责边界保持「确定性服务 vs LLM 候选」分离：

1. **`scrape(url)`** — 确定性：`OpenCliClient.thread(url)` 抓回回复数 / 点赞数 / 对方是否
   回复 / 回复 URL 列表。失败返回 `scrape_failed=True` + 全空客观字段，不阻断。
2. **`propose(...)`** — LLM：输入 `published_body` + 抓回回复 + peer/thread 上下文，输出
   一个 `InteractionReflection` 的语义部分。**只提议，不落任何事实。** 提示词存
   `prompts/reflect-interaction.md`。
3. **`apply(ws, reflection)`** — 确定性：把 APPROVED 反思原子写入四组事实（见设计 4）。

`apply` 是纯确定性函数，可独立单测；`propose` 依赖 runner，测试用 fake runner 注入。

---

## 设计 4：`apply` 字段映射

给定 APPROVED 的 `reflection`，解析并写入：

| 目标对象 | 写什么 | 来源 |
|---|---|---|
| `FeedbackSnapshot`（id=`snap_<proposal_id>`） | `replies` / `likes` / `meaningful` / `captured_at`，`interaction_id=<proposal_id>` | 抓回 + LLM |
| `InteractionRecord`（`rec_<proposal_id>`） | `reply_refs` / `follow_up_status` / `follow_up_at`；`outcome` 派生为 `peer_replied ? "replied" : "no_response"` | 抓回 / LLM |
| `ConversationThread`（含 `rec_<proposal_id>` 的 thread） | 四个语义列表 append + 去重；`last_activity_at=captured_at` | LLM / 抓回 |
| `PeerProfile`（**字段定向 merge，不整对象覆写**） | `relationship_stage`（仅当非 None）、`last_meaningful_interaction_at`（仅 meaningful）、`next_context`、`possible_next_actions` | LLM |
| `ConversationEvidence`（id=`ev_<proposal_id>_<i>`，`verified=False`） | 物化 `EvidenceCandidate`：`interaction_id=<proposal_id>`、`post_id=<proposal.post.id>` | LLM 候选 |

**对象解析规则：**

- `record`：按 `proposal_id` 查 `InteractionRecordRepository`（`rec_<proposal_id>`）。不存在
  则报错退出（reflect 只能反思已 record 的互动）。
- `thread`：找 `interaction_ids` 含 `rec_<proposal_id>` 的 thread。无（peer_id 为空时
  `connect record` 不建 thread）则跳过 thread 更新，其余照常。
- `peer`：按 `peer_id` 查 `PeerRepository`，不存在则跳过 peer 更新。更新方式为**字段定向 merge**：
  加载既有 profile 后 `model_copy(update={...})`，只覆盖 `relationship_stage`（非 None 时）、
  `next_context`、`possible_next_actions` 与 `last_meaningful_interaction_at`（meaningful 时），
  其余字段原样保留——与 `_persist_discovery` 的修复同源，绝不复现整对象覆写丢字段。

---

## 设计 5：命令面

```
finch connect reflect <proposal-id> [--json]
    # 抓回 + LLM 提议 → 存 PROPOSED 反思 + 打印（含「确认请跑 reflect-approve」提示）

finch connect reflect-approve <reflection-id> [--json]
    # 状态必须 PROPOSED；apply 四组事实 → 状态 APPROVED

finch connect reflect-reject <reflection-id> [--reason <str>]
    # 状态必须 PROPOSED；记录 reject_reason → 状态 REJECTED，不写任何事实
```

三个命令与现有 `connect approve / reject` 同构，人工确认是显式硬门禁。

---

## 幂等与防重复

- 反思 id 确定性派生 `refl_<proposal_id>`：重跑 `reflect` 覆盖 PROPOSED 反思，不堆叠。
- `reflect-approve` 对非 PROPOSED 反思报错退出，不二次写入。
- `FeedbackSnapshot` / `ConversationEvidence` id 确定性派生，upsert 覆盖，不重复计数。
- thread 语义列表合并去重；`PeerProfile` merge 不覆写其他已积累字段。

## 错误处理与边界

- 抓回失败 → `scrape_failed=True`、客观字段置 0/空，LLM 仍基于 `published_body` + 上下文
  提议；反思打印中标注「无抓回信号」。
- LLM 失败 → try/except 包裹，`Exit(1)`（与 `ideas signals` 一致）。
- 无 record / 无 peer / 非 PROPOSED 状态 → 明确报错退出。
- 枚举校验：`follow_up_status` 四值、`relationship_stage` 六值，非法值由 Pydantic 拒绝。

## 测试策略

- 单元测试 `apply`：四组事实正确写入；thread 合并去重；peer 不覆写其他字段（复用
  `merge_discovered` 回归）；已 APPROVED 再 approve 报错。
- 单元测试 `scrape`：thread 空 / 失败 → 客观字段默认、不崩。
- 单元测试 `propose`：fake runner 返回非法枚举 → Pydantic 拒绝。
- CLI 测试：`reflect` 存 PROPOSED + 打印；`reflect-approve` 写四组事实；`reflect-reject`
  不写事实。
- 回归：全量 `uv run pytest` + `uv run ruff check .` + `uv run mypy src`。

## 非目标（YAGNI）

- 不恢复 Graph Runtime、不改存储层抽象。
- 不做跟进发现读路径、不做 follow-up sweep 命令。
- 不做 conversation → personal 证据升级的自动触发。
- 不新增 PeerProfile 字段、不改 adapter、不重命名现有命令。
