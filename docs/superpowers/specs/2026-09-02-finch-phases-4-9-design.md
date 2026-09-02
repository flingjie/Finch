# Finch Phase 4–9 设计文档

> 本文是 Phase 4–9 的跨阶段设计（spec）。逐阶段实现计划（task 级）另见 `docs/superpowers/plans/`。
> 需求与验收基线见 `docs/Finch-Codex-Development-Plan.md`（后称"主 plan"），本文不重复，只记录**新引入的设计决策**与**阶段分解**。

## 1. 范围

- Phase 4–9 全部实现；Phase 9 以「框架 + 试运行脚本 + 记录机制」交付，真实两周运行由用户手动执行。
- 内容类型：**英文回复 + 中文 Agent 实战日记**均实现（Phase 5 两套模板）。
- 主 plan §6.2 的「证据不足则归档为学习材料」「最多提出 3 个问题 / `NEEDS_INPUT`」**推迟到 Phase 9 之后**，本跨阶段不实现。空匹配的行为见 §3.8。

## 2. 阶段分解与依赖

| 期 | 交付物 | 依赖 |
|---|---|---|
| 4 Evidence Matching | GraphContext（含恢复）+ `QualityGates` + Recall（确定性）+ Match（judge + 公式 + 门禁）+ 确定性 safety + 领域仓储骨架 + 每日 Graph 节点 1–6 | 新基础 |
| 5 Writer/Critic/Daily Brief | `content/`（writer/critic/claims/models）+ `prompts/draft-reply.md` `draft-original.md` `critique-draft.md` + Daily Brief + 节点内最多 `max_rewrite_rounds` 轮重写 + 主张蕴含门禁 | 4 |
| 6 Review & 反馈 | `review/`（service/feedback/models）+ `finch review list/show/approve/revise/skip` + Diff 保存 + 跳过原因 + 发布链接 | 5 |
| 7 Codex Skill | `skills/finch/SKILL.md` + references + scripts | 6 |
| 8 Scheduled Tasks | 每日/每周任务草案（手动 3 次 daily 起） | 5（与 7 并行；周任务草案可引用 6 的反馈模型） |
| 9 试运行框架 | 两周运行脚本 + 记录机制 + 周复盘分析 | 6 与 8 |

Phase 7 不阻塞「先把 daily 跑起来」。Skill 只包装已有 CLI，不复制业务逻辑。

## 3. 跨阶段架构决策

### 3.1 GraphContext：产物投影 + 从 NodeRecord 恢复（Phase 4 落地）

**现状缺口**：`GraphRuntime.run()` 调 `node.run({})`；成功节点 `continue` 且不把 `NodeRecord.output_json` 灌回内存。进程内 `outputs` 在崩溃/重放后是空的，下游节点会读到空上下文。

**设计**：Runtime 维护 `outputs: dict[str, dict]`，key 是**产物名**（不是节点名）。`Node` 增加：

```python
class Node(BaseModel):
    name: str
    reads: list[str] = []   # 必读产物 key
    writes: str = ""        # 本节点写入的产物 key；空则不写入 GraphContext
    # 既有字段不变：timeout_seconds / max_retries / idempotency_key / side_effect
```

`reads` / `writes` 是主 plan §6.3 的 **Context policy**。Input/Output **schema** 仍是各节点 `NodeResult.output` 对应的 Pydantic 模型；Runtime 边界只传 `dict`，由节点（或薄包装）做校验。二者不可等同。

规范产物 key（Phase 4–5）：

| key | 写入节点 | 期 |
|---|---|---|
| `evidence_cards` | `ExtractEventsNode` | 4 |
| `candidates` | `CollectTweetsNode` | 4 |
| `ranked_candidates` | `RecallNode` | 4 |
| `match_results` | `MatchEvidenceNode` | 4 |
| `drafts` | `DraftNode` / `CritiqueNode` | 5 |
| `brief` | `BriefNode` | 5 |

Runtime 执行顺序：

1. **注水**：对每个已成功且被跳过的节点，若其 `writes` 非空，从 `NodeRecord.output_json` 填入 `outputs[writes]`。重放/恢复不得要求重跑上游。
2. 对节点 N：若 `N.reads` 中任一 key 不在 `outputs` 中 → 该节点 `failed`（fail-closed，禁止 `if k in outputs` 静默丢 key）。
3. `ctx = {k: outputs[k] for k in N.reads}`（只暴露声明读取的 key）。
4. `result = N.run(ctx)`。
5. 若成功且 `N.writes` 非空：`outputs[N.writes] = result.output`，并随 `NodeRecord` 落盘。

Judge / Writer / Critic 的 Codex 输出必须进 `NodeResult.output`，否则跳过成功节点时会丢分数、触发重复计费。

### 3.2 匹配分级（Phase 4）

按主 plan §8 拆成**两个节点**，对齐已有 `GraphState.CANDIDATES_RANKED` → `EVIDENCE_MATCHED`，并让确定性召回失败重试时不必重打 judge：

```
candidates × cards
  → RecallNode（确定性）
      ① 关键词 / topic 重叠，产出 candidate→[card_id]
      ② 预排序取 top-K（K = QualityGates.match_top_k，默认 10）
         确定性粗分 + 有无召回；无召回直接淘汰
      writes: ranked_candidates
  → MatchEvidenceNode
      ③ Codex judge：一次批量调用对 top-K 打分
         relevance / evidence_strength / incremental_value / discussability
      ④ Runtime 公式（与主 plan §8 一致，不含 discussability）：
         Score = 0.30*rel + 0.30*evid + 0.20*incr + 0.10*timing + 0.10*relation
      ⑤ 门禁：Score≥min_candidate_score、evidence_strength≥min_evidence_score、
         discussability≥min_discussability
      ⑥ 排序降序，截断 max_daily_replies
      writes: match_results
```

- **discussability**：judge 仍打分，但**不进加权公式**（主 plan 公式权重已满 1.0）。作为独立门禁，避免「相关且有证据但无可讨论空间」的公告贴混入。
- **Codex 调用次数**：匹配阶段 = **1 次** batch judge（top-K 一条调用，不是逐条）。全天 ~10–20 次是抽取 + 匹配 + 写作 + 审查的合计上限，不是匹配单独的配额。
- **timing**：`published_at` 缺失时用 `QualityGates.timing_default`（默认 `0.3`），模型不猜时间。
- **relationship_value**：Phase 4 默认 `0.5`；作者 ∈ `twitter.high_value_authors` → `1.0`；∈ `twitter.blocked_authors` → `0.0`。持续对话记录由 Phase 9 回填后升级。
- **MatchResult 约束**：每个 candidate 的 `card_ids` 必须是本次召回集合的子集。Draft 不得引用集合外的卡（见 §3.5）。

### 3.3 QualityGates 与作者名单（Phase 4 落地）

`settings.py` 的 `quality_gates: dict` 改为 Pydantic 模型 `QualityGates`。魔法数只从这里读：

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

作者名单放在既有 `twitter` 下，不塞进门禁数值模型：

```yaml
twitter:
  high_value_authors: []
  blocked_authors: []
```

缺省值即上述默认值。

### 3.4 领域仓储（Phase 4 骨架，Phase 5/6 填满）

`storage/repositories.py`（现占位）实现领域仓储。`Store` 仍只存 `RunRecord`/`NodeRecord`；新增表：

- `EvidenceCardRecord`（Phase 4 落表，支撑 Phase 9 周复盘；卡跨 run 累积，按 `id` 幂等 upsert）
- `DraftRecord`（Phase 5）
- `ReviewRecord`（Phase 6）
- `FeedbackRecord`（Phase 6）

SQLModel 模型 + 对应 repository 方法（`save_draft` / `get_draft` / `save_review` / …）。

### 3.5 Draft 绑定证据 + 蕴含（Phase 5）

`content/models.py` 的 `Draft` 每条主张：

```python
class ClaimRef(BaseModel):
    statement: str
    evidence_card_id: str
    confidence: ClaimConfidence
```

硬门禁（无卡、或不满足下列任一条 → 不生成、不进审核）：

1. `evidence_card_id` 非空，且 ∈ 该 candidate 的 `MatchResult.card_ids`（禁止从全库「另选一张更好看的卡」）。
2. **蕴含**：`statement` 必须被该卡的 `claim` + `sources` 支持，而不是只检查 ID 存在。Critic 做这项判定；不确定 → 不通过（fail-closed）。
3. 对外主张的 `confidence` 必须 `assertable`（`VERIFIED` / `SUPPORTED` / `USER_CONFIRMED`）。`INFERRED` / `UNKNOWN` 不得写成第一人称亲历，也不得作为可发布主张进入审核。

`required` 含 `evidence_card`（主 plan §11）在此落地为「ID ∈ 匹配集 + 蕴含 + assertable」，不是「有个字符串就算」。

外部 Tweet 文本只作为待分析数据字段进入 Writer/Critic 的 prompt **数据区**（明确分隔），不进系统指令、不触发工具调用。组装责任在 `content/`（Phase 5），不在 GraphContext。

### 3.6 安全扫描：确定性 hard_fail vs LLM 审查（职责切分）

`hard_fail`（主 plan §11）按失败模式拆开，避免把 Codex 偏宽松的判断当成硬失败的唯一判据。

**Phase 4 `evidence/safety.py`（确定性，失败即停）**

- `secret_detected`：正则扫密钥/token 形态。
- `private_repo_content`：`RepoInfo.is_private` 或卡上 `publishable=false`。
- `nonexistent_commit`：Evidence 的 source URL 无法反查到已同步 commit。
- `twitter_write_command`：允许名单之外命令即阻断（Phase 3 已实现，保持）。

扫描对象：Evidence Card 与即将进入 GraphContext 的结构化字段。Tweet 原文不当作可信指令。

**Phase 5 Critic（语义，fail-closed）**

- `invented_personal_experience`
- `unsupported_metric`
- §3.5 的蕴含检查

Codex 辅助；**不确定或不通过 = 该草稿不进审核**，记入 `CritiqueResult`，不把 LLM 分数当作 `hard_fail` 的唯一开关。确定性扫描仍可在草稿文本上再跑一遍（密钥/私有内容）。

### 3.7 Writer ↔ Critic：节点内有界循环，不是 Runtime 回边

现有 `GraphRuntime` 是线性节点列表，不表达主 plan mermaid 的 Critic→Writer 环。**不扩展 Runtime 为图循环。**

- `DraftNode`：对每个 `match_results` 项生成初稿，`writes: drafts`。
- `CritiqueNode`：内部循环，上限 `QualityGates.max_rewrite_rounds`。不达标则调用 `content.writer.rewrite()`（函数，不是 `DraftNode.run()`）。节点不调用节点；Codex 仍由 runner 调用。
- 超过轮次仍不达标：该草稿不进入 Brief / 审核，写入 warnings。
- Phase 6 `review revise` 从 CLI 重新进入 writer，不走每日 Graph 回边。

### 3.8 空匹配与每日 Graph 终态

- 召回/门禁后 `match_results` 为空：`DraftNode` / `CritiqueNode` 成功且 `drafts=[]`；`BriefNode` 仍渲染「无机会」Brief。
- **有 ≥1 条达标草稿**：每日 Graph 成功终态 = `WAITING_FOR_REVIEW`（暂停等人，不是 `GraphState.is_terminal`）。`COMPLETED` 只在 Phase 6 审核完成（及后续 MEASURED）之后。
- **零草稿**：每日 Graph 成功终态 = `COMPLETED`（无可审项）。
- Preflight 失败 → `BLOCKED`（与主 plan §5.1 一致）。节点失败 → `FAILED`。
- Runtime 每成功一节点按 §5 表推进 `RunRecord.state`（现状只写 COMPLETED/FAILED，Phase 4 补上）。

## 4. 数据模型新增汇总

| 模型 | 归属 | 期 |
|---|---|---|
| `GraphContext`（运行时累积，可从 NodeRecord 恢复） | `graph/context.py` | 4 |
| `QualityGates` | `settings.py` | 4 |
| `MatchResult`（candidate×cards 匹配 + 分数 + card_ids） | `evidence/models.py` | 4 |
| `SafetyReport` | `evidence/safety.py` | 4 |
| `Draft` / `ClaimRef` | `content/models.py` | 5 |
| `CritiqueResult` | `content/critic.py` | 5 |
| `DailyBrief` | `content/models.py` | 5 |
| `ReviewDecision` / `SkipReason` | `review/models.py` | 6 |
| `Feedback` | `review/feedback.py` | 6 |
| 各 `*Record`（SQLModel 表） | `storage/repositories.py` | 4–6 |

## 5. 真实节点清单（替代 Noop/Failing）

对应主 plan §6.2 每日 Graph（线性 Runtime + Critique 内循环）。Phase 4 实现 1–6；Phase 5 实现 7–9。

| # | 节点 | reads | writes | 成功后 GraphState | 期 |
|---|---|---|---|---|---|
| 1 | `PreflightNode`（gh/opencli 只读探测） | — | — | `PREFLIGHT_PASSED`；失败 `BLOCKED` | 4 |
| 2 | `SyncCommitsNode` | — | —（副作用在 Store/游标） | `COMMITS_SYNCED` | 4 |
| 3 | `ExtractEventsNode` | — | `evidence_cards` | `EVENTS_EXTRACTED` | 4 |
| 4 | `CollectTweetsNode` | — | `candidates` | `TWEETS_COLLECTED` | 4 |
| 5 | `RecallNode` | `candidates`, `evidence_cards` | `ranked_candidates` | `CANDIDATES_RANKED` | 4 |
| 6 | `MatchEvidenceNode` | `ranked_candidates`, `evidence_cards` | `match_results` | `EVIDENCE_MATCHED` | 4 |
| 7 | `DraftNode` | `match_results`, `evidence_cards` | `drafts` | `DRAFTED` | 5 |
| 8 | `CritiqueNode`（内部 ≤ `max_rewrite_rounds`） | `drafts`, `match_results`, `evidence_cards` | `drafts` | `CRITIQUED` | 5 |
| 9 | `BriefNode` | `drafts`, `match_results` | `brief` | `WAITING_FOR_REVIEW` 或 `COMPLETED`（§3.8） | 5 |

人工审核（approve / revise / skip）是 Phase 6 CLI，不是每日 Graph 节点。

## 6. 定义完成（DoD，对齐主 plan §17）

- 节点间状态经产物 key 投影；跳过已成功节点时从 `NodeRecord` 恢复 GraphContext；缺必读 key 失败；空 Graph/失败/恢复/重放仍通过既有 Runtime 测试。
- 匹配分 Recall + Match 两节点、6 步；匹配阶段 Codex 1 次 batch；timing 缺失用 `timing_default`，不猜。
- 草稿主张：匹配集内的卡 + 蕴含 + `assertable`；确定性 hard_fail 与 Critic 语义审查职责分离。
- 每日 Graph 有稿停在 `WAITING_FOR_REVIEW`；Writer/Critic 重写在节点内，不改 Runtime 为环。
- `finch review` 三条路径（approve/revise/skip）可重放，Diff 可追溯。
- `$finch` Skill 只调 Finch CLI，不调用 Twitter 写命令。
- 全量 `pytest`、`ruff`、`mypy` 通过。
