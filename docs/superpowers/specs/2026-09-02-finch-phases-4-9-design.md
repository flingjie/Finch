# Finch Phase 4–9 设计文档

> 本文是 Phase 4–9 的跨阶段设计（spec）。逐阶段实现计划（task 级）另见 `docs/superpowers/plans/`。
> 需求与验收基线见 `docs/Finch-Codex-Development-Plan.md`（后称"主 plan"），本文不重复，只记录**新引入的设计决策**与**阶段分解**。

## 1. 范围

- Phase 4–9 全部实现；Phase 9 以「框架 + 试运行脚本 + 记录机制」交付，真实两周运行由用户手动执行。
- 内容类型：**英文回复 + 中文 Agent 实战日记**均实现（Phase 5 两套模板）。

## 2. 阶段分解与依赖

| 期 | 交付物 | 依赖 |
|---|---|---|
| 4 Evidence Matching | matcher（确定性召回）+ safety（敏感扫描）+ Codex judge 评分 + Runtime 公式排序（上限 5）+ `QualityGates` 建模 + **GraphContext** + 领域仓储骨架 | 新基础 |
| 5 Writer/Critic/Daily Brief | `content/`（writer/critic/claims/models）+ `prompts/draft-reply.md` `draft-original.md` `critique-draft.md` + Daily Brief Markdown/JSON + 最多 2 轮重写 | 4 |
| 6 Review & 反馈 | `review/`（service/feedback/models）+ `finch review list/show/approve/revise/skip` + Diff 保存 + 跳过原因 + 发布链接 | 5 |
| 7 Codex Skill | `skills/finch/SKILL.md` + references + scripts | 6 |
| 8 Scheduled Tasks | 每日/每周任务草案（手动 3 次 daily 起） | 7 |
| 9 试运行框架 | 两周运行脚本 + 记录机制 + 周复盘分析 | 8 |

## 3. 跨阶段架构决策

### 3.1 GraphContext：节点间状态传递（Phase 4 落地）

**现状缺口**：`GraphRuntime.run()` 调 `node.run({})`，节点间不共享状态，真实管线无法串起来。

**设计**：Runtime 维护 `outputs: dict[str, dict]`（按节点名累积输出）。`Node` 增加两个契约字段：

```python
class Node(BaseModel):
    name: str
    reads: list[str] = []   # 消费哪些节点（名）的输出
    writes: str = ""        # 本节点输出写入的 key；空则用 self.name
    # ... 既有字段不变：timeout_seconds / max_retries / idempotency_key / side_effect
```

Runtime 执行顺序：

1. 对节点 N：`ctx = {k: outputs[k] for k in N.reads if k in outputs}`（只暴露声明读取的 key，不向节点泄漏无关状态）。
2. `result = N.run(ctx)`。
3. `outputs[N.writes or N.name] = result.output`。

`NodeResult.output` 保持 `dict`，节点输出即其"贡献给上下文"的载荷。spec 6.3 的 Input/Output schema + Context policy 由此满足：`reads`=Input schema，`writes`=Output schema，`reads` 集合即 Context policy。

### 3.2 匹配分级（Phase 4）

按主 plan §8 三层，明确为：

```
candidates × cards
  → ① 确定性召回：candidate 文本 vs card.topics/claim 的关键词与 topic 重叠，产出 candidate→[card_id]
  → ② 预排序取 top-K（K 默认 10）：确定性相关性粗分 + 有无召回，无召回直接淘汰
  → ③ Codex judge：一次批量调用对 top-K 打分（relevance/evidence_strength/incremental_value/discussability）
  → ④ Runtime 公式：Score = 0.30*rel + 0.30*evid + 0.20*incr + 0.10*timing + 0.10*relation
  → ⑤ 门禁：min_candidate_score≥0.65、min_evidence_score≥0.75
  → ⑥ 排序降序，截断 max_daily_replies（5）
```

- **Codex 调用次数**：每天 ~10–20 次（只对 top-K 打分），非 100 次逐条。
- **timing**：`published_at` 缺失时只给保守默认值（主 plan §8 硬约束，模型不猜时间）。
- **relationship_value**：Phase 4 用中性默认 `0.5`；若作者命中可配置的 `high_value_authors` 列表则 `1.0`，被屏蔽则 `0.0`。持续对话记录由 Phase 9 记录机制回填后升级。

### 3.3 QualityGates 建模（Phase 4 落地）

`settings.py` 的 `quality_gates: dict` 改为 Pydantic 模型 `QualityGates`，字段对齐 `finch.yaml`：

```yaml
quality_gates:
  max_daily_replies: 5
  max_daily_original_posts: 1
  min_candidate_score: 0.65
  min_evidence_score: 0.75
  min_quality_score: 0.75
  max_rewrite_rounds: 2
```

缺省值即上述默认值；Runtime 门禁与 writer/critic 只从这里读，不散落魔法数。

### 3.4 领域仓储（Phase 4 骨架，Phase 5/6 填满）

`storage/repositories.py`（现占位）实现领域仓储。`Store` 仍只存 `RunRecord`/`NodeRecord`；新增表：

- `EvidenceCardRecord`（可选，Phase 4 用内存也可；先落表以支撑 Phase 9 周复盘）
- `DraftRecord`（Phase 5）
- `ReviewRecord`（Phase 6）
- `FeedbackRecord`（Phase 6）

SQLModel 模型 + 对应 repository 方法（`save_draft` / `get_draft` / `save_review` / …）。

### 3.5 Draft 绑定证据（Phase 5）

`content/models.py` 的 `Draft` 每条主张带 `evidence_card_id` 引用：

```python
class ClaimRef(BaseModel):
    statement: str
    evidence_card_id: str      # 硬绑定，无 card 则草稿不成立
    confidence: ClaimConfidence
```

硬门禁：`required` 含 `evidence_card`，无卡不生成、不进审核（主 plan §11）。

### 3.6 安全扫描（Phase 4 `evidence/safety.py`）

`hard_fail`（主 plan §11）落地为 `safety.py` 的确定性 + Codex 辅助扫描：

- `secret_detected`：正则扫密钥/token 形态（确定性）。
- `private_repo_content`：`RepoInfo.is_private` 或卡上 `publishable=false` 即命中（确定性）。
- `invented_personal_experience`：claim confidence 为 INFERRED/UNKNOWN 却写成第一人称亲历 → Codex 判断（辅助）。
- `nonexistent_commit`：Evidence 的 source URL 无法反查到已同步 commit → 确定性校验。
- `unsupported_metric`：引用未在 evidence 中出现的具体数字指标 → Codex 判断。
- `twitter_write_command`：允许名单之外命令即阻断（Phase 3 已实现，保持）。

外部 Tweet 文本一律视为不可信数据（主 plan §11），只作为待分析字段，不进系统指令区。

## 4. 数据模型新增汇总

| 模型 | 归属 | 期 |
|---|---|---|
| `GraphContext`（运行时累积） | `graph/context.py` | 4 |
| `QualityGates` | `settings.py` | 4 |
| `MatchResult`（candidate×cards 匹配 + 分数） | `evidence/models.py` | 4 |
| `SafetyReport` | `evidence/safety.py` | 4 |
| `Draft` / `ClaimRef` | `content/models.py` | 5 |
| `CritiqueResult` | `content/critic.py` | 5 |
| `DailyBrief` | `content/models.py` | 5 |
| `ReviewDecision` / `SkipReason` | `review/models.py` | 6 |
| `Feedback` | `review/feedback.py` | 6 |
| 各 `*Record`（SQLModel 表） | `storage/repositories.py` | 4–6 |

## 5. 真实节点清单（替代 Noop/Failing）

对应主 plan §6.2 每日 Graph：

1. `PreflightNode`（gh/opencli 只读探测，失败 → BLOCKED）
2. `SyncCommitsNode`（GitHub 增量同步）
3. `ExtractEventsNode`（Engineering Event + Evidence Card）
4. `CollectTweetsNode`（opencli 查询 → 规范化 → 去重）
5. `MatchEvidenceNode`（Phase 4 的匹配 + 评分 + 门禁）
6. `DraftNode`（Writer）
7. `CritiqueNode`（Critic，最多 2 轮）
8. `BriefNode`（渲染 Daily Brief）

## 6. 定义完成（DoD，对齐主 plan §17）

- 节点间状态经 `GraphContext` 确定性传递；空 Graph/失败/恢复/重放仍通过既有 Runtime 测试。
- 匹配分 6 步（§3.2），Codex 调用有界；timing 缺失不猜。
- 所有草稿绑定 Evidence Card；硬门禁全落地。
- `finch review` 三条路径（approve/revise/skip）可重放，Diff 可追溯。
- `$finch` Skill 只调 Finch CLI，不调用 Twitter 写命令。
- 全量 `pytest`、`ruff`、`mypy` 通过。
